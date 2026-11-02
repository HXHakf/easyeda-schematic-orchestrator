"""Convert audited observations to upstream layout input; never invent geometry."""
import math
import os
import re
from copy import deepcopy
from .adapter import result
from .core import Refused, check_confirmation, digest, dump, file_digest, load
from .planner import bind_plan
from .workflow import is_blank, snapshot_fingerprint, validate_plan, validate_queue

DESIGNATOR_SOURCE = 'eda.sch_PrimitiveAttribute.getAll(parentId)+eda.sch_Primitive.getPrimitivesBBox([attributeId])'


def number(value, where):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise Refused(f'{where}: finite measured number required')
    return value


def bbox(value, where):
    if not isinstance(value, dict): raise Refused(f'{where}: measured bbox missing')
    b = {k: number(value.get(k), where + '.' + k) for k in ('minX', 'minY', 'maxX', 'maxY')}
    if b['maxX'] <= b['minX'] or b['maxY'] <= b['minY']: raise Refused(f'{where}: invalid measured bbox')
    return b


def check_target(snapshot, target):
    c = snapshot.get('context', {})
    if c.get('projectUuid') != target['project_uuid'] or c.get('documentUuid') != target['document_uuid']:
        raise Refused('measurement target does not match the contract')


def observed_parts(plan, snapshot, bindings, target):
    check_target(snapshot, target)
    data = result(snapshot)
    if data.get('wiresAvailable') is not True or data.get('pinNetsAvailable') is not True or data.get('wires') != []:
        raise Refused('complete, unwired page evidence is required before layout')
    page = data.get('pagePrimitives')
    classes = {'components', 'wires', 'buses', 'arcs', 'circles', 'rectangles', 'polygons', 'texts', 'attributes', 'objects'}
    if not isinstance(page, dict) or set(page) not in (classes, classes | {'pins'}):
        raise Refused('complete supported pagePrimitives inventory is required')
    if any(not isinstance(rows, list) for rows in page.values()):
        raise Refused('page primitive classes must be explicit arrays')
    for kind in classes - {'components', 'attributes'}:
        if page[kind] != []: raise Refused('staged page contains unowned drawing: ' + kind)
    components = data.get('components')
    if not isinstance(components, list) or any(c.get('componentType') not in ('part', 'sheet') for c in components):
        raise Refused('staged page contains an unexpected primitive component')
    def component_inventory(rows):
        out = {}
        for c in rows:
            if not c.get('primitiveId') or c['primitiveId'] in out: raise Refused('missing or duplicate native component ID')
            out[c['primitiveId']] = c.get('componentType')
        return out
    native = component_inventory(components)
    if component_inventory(page['components']) != native:
        raise Refused('native component inventories differ')
    parent_ids = set(native)
    expected_pin_owners = {}
    for c in components:
        if c['componentType'] != 'part': continue
        for pin in c.get('pins', []):
            pin_id = pin.get('primitiveId')
            if not pin_id or pin_id in expected_pin_owners:
                raise Refused('missing or duplicate native pin ID in component inventory')
            expected_pin_owners[pin_id] = c['primitiveId']
    if 'pins' in page:
        pin_ids = set()
        for p in page['pins']:
            if (not p.get('primitiveId') or p['primitiveId'] in pin_ids
                    or expected_pin_owners.get(p['primitiveId']) != p.get('componentId')):
                raise Refused('missing, duplicate or unowned native pin')
            pin_ids.add(p['primitiveId'])
        if pin_ids != set(expected_pin_owners):
            raise Refused('native pin inventories differ')
        parent_ids |= pin_ids
    attribute_ids = set()
    for a in page['attributes']:
        if not a.get('primitiveId') or a['primitiveId'] in attribute_ids or a.get('ParentPrimitiveId') not in parent_ids:
            raise Refused('unowned or ambiguous attribute on staged page')
        attribute_ids.add(a['primitiveId'])
    rows = [c for c in result(snapshot).get('components', []) if c.get('componentType') == 'part']
    by_ref = {c.get('designator'): c for c in rows}
    if len(by_ref) != len(rows) or set(by_ref) != {c['ref'] for c in plan['components']}:
        raise Refused('observed parts must match exactly the owned plan inventory')
    for c in plan['components']:
        actual = by_ref[c['ref']]
        b = bindings[c['ref']]
        device = actual.get('device', {})
        if device.get('uuid') != b['deviceUuid'] or device.get('libraryUuid') != b['libraryUuid']:
            raise Refused(c['ref'] + ': library identity missing or changed')
        if actual.get('otherProperty', {}).get('EasyEDA Agent Component ID') != c['id']:
            raise Refused(c['ref'] + ': canonical ownership binding is missing')
        if not actual.get('primitiveId') or actual.get('pinsAvailable') is not True or actual.get('netAmbiguous'):
            raise Refused(c['ref'] + ': incomplete or ambiguous observation')
        pins = {str(p.get('pinNumber')): p for p in actual.get('pins', [])}
        if len(pins) != len(actual.get('pins', [])) or set(pins) != {p['number'] for p in c['pins']}:
            raise Refused(c['ref'] + ': physical pin inventory differs from the plan')
        for pin in c['pins']:
            observed = pins[pin['number']]
            if observed.get('netAmbiguous') or observed.get('net') is None:
                raise Refused(c['ref'] + ':' + pin['number'] + ': unknown pin-net evidence')
            if observed.get('net') != '' or observed.get('noConnected') is not False:
                raise Refused('staged physical pins must be explicitly unwired and not NC')
            # Supply suffixes and alternate-function names remain allowed, but
            # pin numbers alone must not hide an IC pin-function mismatch.
            wanted, name = pin['name'].upper(), str(observed.get('pinName', '')).upper()
            # PA1 and PA10 are different functions. Numbered GPIO/function names
            # allow only separated aliases; unnumbered supply names allow VDD_1/VDD1.
            suffix = r'(?:[-_/().].*)?' if re.search(r'\d$', wanted) else r'(?:[-_/().0-9].*)?'
            if not wanted.isdigit() and not re.fullmatch(re.escape(wanted) + suffix, name):
                raise Refused(c['ref'] + ':' + pin['number'] + ': physical pin function differs')
    return by_ref


def build_layout_input(contract, plan, bindings, snapshot, designators, sheet, svg_path):
    validate_plan(plan, contract)
    target = contract.get('target')
    if not target: raise Refused('measurement bridge requires an exact target')
    canonical = bind_plan(plan, bindings, target)
    parts = observed_parts(plan, snapshot, bindings, target)
    if designators.get('projectId') != target['project_uuid'] or designators.get('documentId') != target['document_uuid']:
        raise Refused('designator geometry target mismatch')
    rows = designators.get('designators', [])
    labels = {r.get('parentId'): r for r in rows}
    if (len(labels) != len(rows) or designators.get('count') != len(rows)
            or set(labels) != {p['primitiveId'] for p in parts.values()}):
        raise Refused('one measured designator per owned part is required')
    geometry = result(sheet)
    if geometry.get('sourceSha256') != file_digest(svg_path):
        raise Refused('sheet vectors do not match the retained original SVG bytes')
    if geometry.get('coordinateSystem') != 'schematic-raw-y-up-vector-centerlines':
        raise Refused('exact official SVG coordinate system required')
    paper = bbox(geometry.get('sheet', {}).get('bbox'), 'sheet')
    border = bbox(geometry.get('sheet', {}).get('innerBBox'), 'inner sheet border')
    if not (paper['minX'] <= border['minX'] < border['maxX'] <= paper['maxX'] and
            paper['minY'] <= border['minY'] < border['maxY'] <= paper['maxY']):
        raise Refused('inner sheet border must lie within the measured paper')
    measurements = []
    for c in plan['components']:
        actual = parts[c['ref']]; label = labels[actual['primitiveId']]
        if (label.get('key') != 'Designator' or label.get('visible') is not True
                or label.get('value') != c['ref'] or label.get('source') != DESIGNATOR_SOURCE):
            raise Refused(c['ref'] + ': official visible designator geometry missing')
        p = {'primitiveId': actual['primitiveId'], 'designator': c['ref'], 'value': c['query'],
             'x': number(actual.get('x'), c['ref'] + '.x'), 'y': number(actual.get('y'), c['ref'] + '.y'),
             'rotation': number(actual.get('rotation'), c['ref'] + '.rotation'),
             'mirror': actual.get('mirror'), 'bbox': bbox(actual.get('bbox'), c['ref']),
             'textBboxes': [bbox(label.get('bbox'), c['ref'] + '.Designator')], 'pins': []}
        if type(p['mirror']) is not bool: raise Refused('measured mirror state must be boolean')
        pins = {str(x['pinNumber']): x for x in actual['pins']}
        for desired in c['pins']:
            obs = pins[desired['number']]
            pin = {'number': desired['number'], 'name': str(obs.get('pinName', '')),
                   'net': desired['net'] or '', 'x': number(obs.get('x'), 'pin.x'),
                   'y': number(obs.get('y'), 'pin.y')}
            if 'rotation' in obs: pin['rotation'] = number(obs['rotation'], 'pin.rotation')
            p['pins'].append(pin)
        measurements.append(p)
    layout_modules = []
    refs = {c['ref']: c for c in plan['components']}
    net_ids = {n['name']: n['id'] for n in canonical['nets']}
    for m in plan['modules']:
        members = [c for c in plan['components'] if c['module'] == m['id']]
        policies = {}
        for c in members:
            for p in c['pins']:
                net = p['net']
                if net is None: continue
                external = any(x['module'] != m['id'] and any(q['net'] == net for q in x['pins']) for x in plan['components'])
                role = next(n['role'] for n in canonical['nets'] if n['name'] == net)
                policies[net_ids[net]] = ('local_power' if role == 'power' else 'local_ground' if role == 'ground'
                                           else 'module_port' if external else 'direct')
        peripherals = []
        for c in members:
            if 'attach' in c:
                a = c['attach']
                peripherals.append({'componentId': c['id'], 'pinNumber': a['pin'],
                                    'attachTo': {'componentId': refs[a['ref']]['id'], 'pinNumber': a['pin_number']}})
        layout_modules.append({'id': m['id'], 'title': m['title'], 'coreComponentId': refs[m['core_ref']]['id'],
                               'netPolicies': policies, 'peripherals': peripherals})
    # Raw snapshot is unchanged. Pin net annotations below are authored target
    # intent in a derived source, never substituted as observed readback.
    return {'schemaVersion': 1, 'connectivity': canonical, 'sheet': paper, 'sheetBorder': border,
            'keepouts': [bbox(k.get('bbox'), 'keepout') for k in geometry.get('keepouts', [])],
            'measurements': measurements, 'layoutModules': layout_modules, 'maxCandidates': 20000}


class DesignBridge:
    def __init__(self, adapter, root):
        from pathlib import Path
        self.adapter, self.root = adapter, Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def stage(self, contract, approval, plan, bindings):
        check_confirmation(contract, approval); validate_plan(plan, contract)
        if (self.root / 'stage.json').exists(): raise Refused('placement already started; inspect actual state before retrying')
        target = contract.get('target')
        if not target: raise Refused('placement requires an exact approved target')
        ir = bind_plan(plan, bindings, target)
        self.adapter.guard(target)
        before = self.adapter.snapshot(target)
        if not is_blank(result(before)): raise Refused('initial placement requires a blank test page')
        version = self.adapter.run(['--version'], json_output=False)['stdout'].strip()
        # Fail before any placement on the exact schema mismatch reproduced in
        # our pinned release. Never strip pins from the original snapshot.
        if version == 'easyeda-agent v1.16.0' and 'pins' in result(before)['pagePrimitives']:
            raise Refused('upstream v1.16.0 compose rejects this 11-class snapshot; use a verified compatible upstream build before placement')
        seen = {}
        for c in plan['components']:
            b = bindings[c['ref']]
            if not b.get('expected_name'): raise Refused(c['ref'] + ': exact reviewed library name required')
            key = (b['libraryUuid'], b['deviceUuid'])
            raw = seen.get(key)
            if raw is None:
                raw = self.adapter.run(['lib', 'device', 'get', '--library', key[0], '--uuid', key[1]], target=target)
                seen[key] = raw
            device = result(raw).get('device', {})
            if device.get('uuid') != key[1] or device.get('libraryUuid') != key[0] or device.get('name') != b['expected_name']:
                raise Refused(c['ref'] + ': live library identity/name differs from selection')
            dump(self.root / 'library' / (key[1] + '.json'), raw)
        for name, data in (('contract', contract), ('approval', approval), ('plan', plan), ('bindings', bindings), ('expected', ir), ('before-placement', before)):
            dump(self.root / (name + '.json'), data)
        self.adapter.run(['sch', 'materialize', self.root / 'expected.json', '--out', self.root / 'place.json'], json_output=False)
        validate_queue(load(self.root / 'place.json'), target)
        self.adapter.run(['sch', 'apply', self.root / 'place.json', '--dry-run'], json_output=False)
        state = {'state': 'placing', 'contract_sha256': digest(contract), 'plan_sha256': digest(plan),
                 'bindings_sha256': digest(bindings), 'target': target}
        try:
            fd = os.open(self.root / 'placement-started.lock', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        except FileExistsError as error:
            raise Refused('placement already started; reconcile actual state before retrying') from error
        dump(self.root / 'stage.json', state)
        try:
            fresh = self.adapter.snapshot(target)
            if snapshot_fingerprint(fresh) != snapshot_fingerprint(before): raise Refused('target changed before initial placement')
            self.adapter.run(['sch', 'apply', self.root / 'place.json', '--journal', self.root / 'place.journal.jsonl'], target=target, json_output=False, timeout=600)
            after = self.adapter.snapshot(target); dump(self.root / 'after-placement.json', after)
            parts = observed_parts(plan, after, bindings, target)
            if result(after).get('wires') or any(p.get('net') or p.get('noConnected') is not False for c in parts.values() for p in c['pins']):
                raise Refused('staging must contain only owned, fully unconnected parts')
            saved = self.adapter.run(['sch', 'save'], target=target); dump(self.root / 'placement-save.json', saved)
            if result(saved).get('saved') is not True: raise Refused('staging save did not return boolean true')
            state.update(state='placed', primitive_ids={ref: p['primitiveId'] for ref, p in parts.items()})
        except Exception as error:
            state.update(state='incomplete', error=str(error)); dump(self.root / 'stage.json', state)
            try: dump(self.root / 'failure-readback.json', self.adapter.snapshot(target))
            except Exception as failure: dump(self.root / 'failure-readback-error.json', {'error': str(failure)})
            raise
        dump(self.root / 'stage.json', state)
        return state

    def collect(self):
        state = load(self.root / 'stage.json')
        if state['state'] not in ('placed', 'measurement-incomplete', 'layout-compiled', 'measuring'):
            raise Refused('only successfully staged owned parts can be measured; partial placement must first be reconciled')
        state['state'] = 'measuring'
        dump(self.root / 'stage.json', state)
        try:
            return self._collect_once(state)
        except Exception as error:
            state.update(state='measurement-incomplete', error=str(error))
            dump(self.root / 'stage.json', state)
            raise

    def _collect_once(self, state):
        contract, plan, bindings = (load(self.root / (n + '.json')) for n in ('contract', 'plan', 'bindings'))
        for name, data in (('contract', contract), ('plan', plan), ('bindings', bindings)):
            if digest(data) != state[name + '_sha256']: raise Refused('staging input changed: ' + name)
        check_confirmation(contract, load(self.root / 'approval.json'))
        target = state['target']; snapshot = self.adapter.snapshot(target)
        parts = observed_parts(plan, snapshot, bindings, target)
        if {ref: p['primitiveId'] for ref, p in parts.items()} != state['primitive_ids']: raise Refused('staged native instances changed')
        dump(self.root / 'measured-snapshot.json', snapshot)
        labels = self.adapter.run(['sch', 'designator-geometry'], target=target)
        dump(self.root / 'designators.json', labels)
        svg = self.root / 'measured-sheet.svg'
        receipt = self.adapter.run(['sch', 'export-image', '--scope', 'page', '--format', 'svg', '--out', svg], target=target)
        dump(self.root / 'sheet-export.json', receipt)
        check_target(receipt, target)
        sheet = self.adapter.run(['sch', 'sheet-geometry', '--from-svg', svg, '--json'])
        dump(self.root / 'sheet-geometry.json', sheet)
        # Verify no edit occurred while collecting separate getter results.
        fresh = self.adapter.snapshot(target)
        if snapshot_fingerprint(snapshot) != snapshot_fingerprint(fresh): raise Refused('target changed during measurement collection')
        source = build_layout_input(contract, plan, bindings, snapshot, labels, sheet, svg)
        dump(self.root / 'layout-input.json', source)
        names = ('measured-snapshot.json', 'designators.json', 'measured-sheet.svg', 'sheet-export.json', 'sheet-geometry.json', 'layout-input.json')
        dump(self.root / 'measurement-evidence.json', {'files': {n: file_digest(self.root / n) for n in names},
                                                     'authored_net_intent': True, 'original_snapshot_modified': False})
        self.adapter.run(['sch', 'lib-layout', '--from', self.root / 'layout-input.json', '--out', self.root / 'composition.json'], json_output=False, timeout=120)
        state.update(state='layout-compiled', composition_sha256=file_digest(self.root / 'composition.json'),
                     measured_scene_sha256=snapshot_fingerprint(snapshot),
                     measurement_manifest_sha256=file_digest(self.root / 'measurement-evidence.json'))
        dump(self.root / 'stage.json', state)
        return {'state': 'layout-compiled', 'composition': str(self.root / 'composition.json'), 'level': 'offline-layout-only'}
