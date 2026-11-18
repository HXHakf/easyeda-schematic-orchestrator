"""Reconcile interrupted placement and materialize only missing owned parts."""
import copy
import json
import math
import os
from pathlib import Path

from .adapter import result
from .bridge import observed_parts
from .core import Refused, check_confirmation, digest, dump, file_digest, load, now
from .planner import bind_plan
from .workflow import is_blank, snapshot_fingerprint, validate_plan, validate_queue


def checked_recovery(root, state):
    path = root / 'recovery.json'
    if not path.exists() and 'recovery_sha256' not in state:
        return None
    recovery = load(path)
    if state.get('recovery_sha256') != digest(recovery):
        raise Refused('recovery provenance changed')
    history = recovery.get('source_artifacts')
    if not isinstance(history, dict) or not history:
        raise Refused('recovery source evidence missing')
    for name, sha in history.items():
        if not isinstance(name, str) or Path(name).name != name:
            raise Refused('invalid recovery source evidence name')
        copied = root / ('source--' + name)
        if file_digest(copied) != sha:
            raise Refused('recovery source evidence changed: ' + name)
        if name.endswith('recovery.json'):
            parent_history = load(copied).get('source_artifacts')
            if not isinstance(parent_history, dict) or not parent_history:
                raise Refused('historical recovery evidence missing')
            prefix = name[:-len('recovery.json')] + 'source--'
            for ancestor, ancestor_sha in parent_history.items():
                if not isinstance(ancestor, str) or history.get(prefix + ancestor) != ancestor_sha:
                    raise Refused('incomplete recovery evidence ancestry')
    return recovery


def journal_ownership(root, target, references, state):
    owned = {}
    recovery = checked_recovery(root, state)
    if recovery is not None:
        known = recovery.get('known_primitive_ids')
        if not isinstance(known, dict):
            raise Refused('recovery ownership inventory missing')
        owned.update(known)
    journal = root / 'place.journal.jsonl'
    rows = []
    if journal.exists():
        try:
            rows = [json.loads(line) for line in journal.read_text(encoding='utf-8').splitlines() if line.strip()]
        except (ValueError, OSError) as error:
            raise Refused('placement journal is incomplete or unreadable') from error
        if not rows or any(not isinstance(row, dict) for row in rows):
            raise Refused('placement journal requires complete object records')
        # Upstream journals may omit doc. Their byte-bound queue always pins it.
        validate_queue(load(root / 'place.json'), target)
        header = rows[0]
        if (header.get('playbookSha256') != file_digest(root / 'place.json')
                or header.get('project') != target['project_uuid']
                or header.get('doc', target['document_uuid']) != target['document_uuid']):
            raise Refused('placement journal does not match its queue and target')
    for row in rows[1:]:
        captures = row.get('captured', {})
        if not isinstance(captures, dict):
            raise Refused('invalid placement captures')
        for ref, primitive_id in captures.items():
            if (ref not in references or not isinstance(primitive_id, str) or not primitive_id
                    or ref in owned and owned[ref] != primitive_id):
                raise Refused('ambiguous or unexpected placement capture')
            owned[ref] = primitive_id
    if (any(ref not in references or not isinstance(pid, str) or not pid for ref, pid in owned.items())
            or len(set(owned.values())) != len(owned)):
        raise Refused('invalid placement ownership inventory')
    return owned


def recover_stage(adapter, source_dir, output_dir):
    from .locking import target_lock
    target = load(Path(source_dir) / 'contract.json')['target']
    with target_lock(target):
        if load(Path(source_dir) / 'contract.json')['target'] != target:
            raise Refused('target changed while acquiring lock')
        return _recover_stage_locked(adapter, source_dir, output_dir)


def _recover_stage_locked(adapter, source_dir, output_dir):
    source, output = Path(source_dir).resolve(), Path(output_dir).resolve()
    if output.exists() or source in output.parents or output in source.parents:
        raise Refused('recovery requires a fresh separate output directory')
    names = ('contract.json', 'approval.json', 'plan.json', 'bindings.json', 'stage.json',
             'expected.json', 'before-placement.json', 'place.json', 'place.journal.jsonl', 'recovery.json')
    source_hashes = {name: file_digest(source / name) for name in names if (source / name).is_file()}
    contract, approval, plan, bindings, state = (
        load(source / (name + '.json')) for name in ('contract', 'approval', 'plan', 'bindings', 'stage'))
    check_confirmation(contract, approval)
    validate_plan(plan, contract)
    target = contract['target']
    if state.get('target') != target:
        raise Refused('staging target differs from confirmed contract')
    for name, value in (('contract', contract), ('plan', plan), ('bindings', bindings)):
        if state.get(name + '_sha256') != digest(value):
            raise Refused('staging input changed: ' + name)
    expected = bind_plan(plan, bindings, target)
    if digest(load(source / 'expected.json')) != digest(expected):
        raise Refused('original placement intent changed')
    original = load(source / 'before-placement.json')
    if not is_blank(result(original)):
        raise Refused('original placement must have started on a blank test page')
    original_context = original.get('context', {})
    if (original_context.get('projectUuid') != target['project_uuid']
            or original_context.get('documentUuid') != target['document_uuid']):
        raise Refused('original blank page target differs')
    references = {part['ref'] for part in plan['components']}
    owned = journal_ownership(source, target, references, state)
    parent = checked_recovery(source, state)
    if parent is not None:
        source_hashes.update({'source--' + name: sha for name, sha in parent['source_artifacts'].items()})
    snapshot = adapter.snapshot(target)
    inventory = result(snapshot).get('components')
    if not isinstance(inventory, list):
        raise Refused('current component inventory unavailable')
    actual = [part for part in inventory if part.get('componentType') == 'part']
    present = {part.get('designator'): part for part in actual}
    if len(present) != len(actual) or set(present) - references:
        raise Refused('unexpected or duplicate placed component references')
    present_plan = {**plan, 'components': [part for part in plan['components'] if part['ref'] in present]}
    parts = observed_parts(present_plan, snapshot, bindings, target)
    if any(owned.get(ref) != part['primitiveId'] for ref, part in parts.items()):
        raise Refused('placed instance lacks matching original journal ownership')
    missing = references - set(present)
    output.mkdir(parents=True)
    for name, sha in source_hashes.items():
        copied = output / ('source--' + name)
        copied.write_bytes((source / name).read_bytes())
        if file_digest(copied) != sha:
            raise Refused('source evidence changed while copying: ' + name)
    for name in ('contract.json', 'approval.json', 'plan.json', 'bindings.json', 'expected.json', 'before-placement.json'):
        (output / name).write_bytes((source / name).read_bytes())
    dump(output / 'before-recovery.json', snapshot)
    recovery = {'source_artifacts': source_hashes, 'known_primitive_ids': {
        ref: part['primitiveId'] for ref, part in parts.items()}, 'missing_refs': sorted(missing),
        'created_at': now(), 'scope': 'original confirmed placement; fresh queue for absent parts only'}
    dump(output / 'recovery.json', recovery)
    recovered = {key: state[key] for key in ('target', 'contract_sha256', 'plan_sha256', 'bindings_sha256')}
    recovered.update(state='placing', recovery_sha256=digest(recovery))
    dump(output / 'stage.json', recovered)
    placement_started = False
    try:
        if missing:
            libraries = {}
            for ref in sorted(missing):
                binding = bindings[ref]
                identity = (binding['libraryUuid'], binding['deviceUuid'])
                if identity not in libraries:
                    libraries[identity] = adapter.run(
                        ['lib', 'device', 'get', '--library', identity[0], '--uuid', identity[1]], target=target)
                raw = libraries[identity]
                device = result(raw).get('device', {})
                if (device.get('uuid') != binding['deviceUuid']
                        or device.get('libraryUuid') != binding['libraryUuid']
                        or device.get('name') != binding['expected_name']):
                    raise Refused(ref + ': current library identity differs from selection')
                dump(output / 'library' / (ref + '.json'), raw)
            remaining = copy.deepcopy(expected)
            remaining['components'] = [part for part in remaining['components'] if part['ref'] in missing]
            remaining_ids = {part['id'] for part in remaining['components']}
            remaining['connections'] = [edge for edge in remaining['connections'] if edge['componentId'] in remaining_ids]
            net_ids = {edge['netId'] for edge in remaining['connections']}
            remaining['nets'] = [net for net in remaining['nets'] if net['id'] in net_ids]
            remaining.pop('modules', None)
            dump(output / 'remaining.json', remaining)
            adapter.run(['sch', 'materialize', output / 'remaining.json', '--out', output / 'place.json'], json_output=False)
            validate_queue(load(output / 'place.json'), target)
            adapter.run(['sch', 'apply', output / 'place.json', '--dry-run'], json_output=False)
        if any(file_digest(source / name) != sha for name, sha in source_hashes.items()):
            raise Refused('original evidence changed during recovery preparation')
        fresh = adapter.snapshot(target)
        if snapshot_fingerprint(fresh) != snapshot_fingerprint(snapshot):
            raise Refused('page changed during recovery preparation')
        fd = os.open(output / 'placement-started.lock', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        if missing:
            placement_started = True
            adapter.run(['sch', 'apply', output / 'place.json', '--journal', output / 'place.journal.jsonl'],
                        target=target, json_output=False, timeout=600)
        after = adapter.snapshot(target)
        dump(output / 'after-placement.json', after)
        completed = observed_parts(plan, after, bindings, target)
        final_ownership = journal_ownership(output, target, references, recovered)
        if {ref: part['primitiveId'] for ref, part in completed.items()} != final_ownership:
            raise Refused('final placed instances differ from captured ownership')
        saved = adapter.run(['sch', 'save'], target=target)
        dump(output / 'placement-save.json', saved)
        if result(saved).get('saved') is not True:
            raise Refused('recovery save did not return boolean saved:true')
        recovered.update(state='placed', primitive_ids={ref: part['primitiveId'] for ref, part in completed.items()})
    except Exception as error:
        recovered.update(state='incomplete', error=str(error))
        dump(output / 'stage.json', recovered)
        try:
            dump(output / 'failure-readback.json', adapter.snapshot(target))
            if placement_started:
                dump(output / 'failure-save.json', adapter.run(['sch', 'save'], target=target))
        except Exception as read_error:
            dump(output / 'failure-readback-error.json', {'error': str(read_error)})
        raise
    dump(output / 'stage.json', recovered)
    return recovered


# Compose returns rich native rows, while its wire summary contains one row per
# segment. Only complete native IDs are eligible for the drawing-only reset.
PAGE_CLASSES = frozenset(('components', 'attributes', 'pins', 'wires', 'rectangles', 'texts',
                         'arcs', 'buses', 'circles', 'objects', 'polygons'))
DRAWING_CLASSES = frozenset(('wires', 'rectangles', 'texts'))
MARKERS = frozenset(('netflag', 'netport', 'netlabel'))
INSTANCE_FIELDS = ('uniqueId', 'name', 'subPartName', 'addIntoBom', 'addIntoPcb',
                   'manufacturer', 'manufacturerId', 'supplier', 'supplierId',
                   'otherProperty', 'component', 'symbol', 'footprint')
PHYSICAL_PIN_FIELDS = ('primitiveId', 'pinNumber', 'otherProperty')
POSE = ('x', 'y', 'rotation', 'mirror')
RICH_READ = dict.fromkeys(('includeBBox', 'includeConnectivitySummary', 'includeDeviceIdentity',
                           'includePagePrimitives', 'includePins', 'includeWires'), True)


def _id_rows(rows, key, scope):
    indexed = {}
    if not isinstance(rows, list):
        raise Refused(f'{scope}: complete inventory is missing')
    for row in rows:
        ident = row.get(key) if isinstance(row, dict) else None
        if not isinstance(ident, str) or not ident or ident in indexed:
            raise Refused(f'{scope}: invalid or duplicate {key} {ident!r}')
        indexed[ident] = row
    return indexed


def same_window(before, fresh):
    def window(snapshot):
        return snapshot.get('context', {}).get('windowId', snapshot.get('windowId'))
    return window(before) == window(fresh)


def revision_inventory(before, target):
    context = before.get('context', {})
    if (context.get('projectUuid'), context.get('documentUuid'), context.get('documentType')) != (
            target['project_uuid'], target['document_uuid'], 'schematic'):
        raise Refused('revision readback belongs to another project or schematic page')
    data = result(before)
    page = data.get('pagePrimitives')
    if (not isinstance(page, dict) or set(page) != PAGE_CLASSES
            or data.get('wiresAvailable') is not True or data.get('pinNetsAvailable') is not True):
        raise Refused('revision requires complete native classes and wire/pin-net readback')
    native = {kind: _id_rows(rows, 'primitiveId', kind) for kind, rows in page.items()}
    all_ids = set()
    drawing = {}
    for kind, rows in native.items():
        if all_ids & rows.keys():
            raise Refused(f'duplicate native primitive ID across classes: {sorted(all_ids & rows.keys())}')
        all_ids.update(rows)
        if kind in DRAWING_CLASSES:
            drawing.update(dict.fromkeys(rows, kind))
        elif kind not in ('components', 'pins', 'attributes') and rows:
            raise Refused(f'unowned primitive class {kind}: {sorted(rows)}')
    components = _id_rows(data.get('components'), 'primitiveId', 'rich components')
    if {pid: row.get('componentType') for pid, row in components.items()} != {
            pid: row.get('componentType') for pid, row in native['components'].items()}:
        raise Refused('native and rich component inventories differ')
    part_rows = []
    owners, physical_pins, marker_pins = set(), {}, set()
    for pid, part in components.items():
        kind = part['componentType']
        if kind not in MARKERS | {'sheet', 'part'}:
            raise Refused(f'unsupported component class {kind}: {pid}')
        if kind in MARKERS:
            drawing[pid] = kind
        else:
            owners.add(pid)
        if kind == 'sheet':
            continue
        for pin_id, pin in _id_rows(part.get('pins'), 'primitiveId', pid + ' pins').items():
            if pin_id in owners or pin_id in marker_pins or pin_id in all_ids - native['pins'].keys():
                raise Refused(f'duplicate or conflicting pin ID: {pin_id}')
            if kind == 'part':
                owners.add(pin_id)
                physical_pins[pin_id] = pid
            else:
                marker_pins.add(pin_id)
        if kind == 'part':
            part_rows.append(part)
    if physical_pins != {pid: row.get('componentId') for pid, row in native['pins'].items()}:
        raise Refused('native physical pin inventory and owners differ')
    attributes = {}
    for pid, row in native['attributes'].items():
        owner = row.get('ParentPrimitiveId')
        if owner in owners:
            attributes[pid] = row
        elif owner not in drawing and owner not in marker_pins:
            raise Refused(f'unknown owner of native attribute {pid}: {owner!r}')
    wires = data.get('wires')
    if (not isinstance(wires, list)
            or any(not isinstance(row, dict) for row in wires)
            or {row.get('primitiveId') for row in wires} != set(native['wires'])):
        raise Refused('wire segments do not cover the exact native wire IDs')
    counts = {'wires': len(native['wires']), 'buses': 0, 'shortSymbols': 0,
              **{kind + 's': list(drawing.values()).count(kind) for kind in MARKERS}}
    summary = data.get('connectivitySummary', {})
    if summary.get('scope') != 'activePage' or any(summary.get(k) != v for k, v in counts.items()):
        raise Refused('native drawing and active-page connectivity summary differ')
    return {'data': data, 'page': page, 'components': components, 'drawing': drawing, 'owners': owners,
            'parts': _id_rows(part_rows, 'designator', 'parts'), 'marker_pins': marker_pins,
            'attributes': attributes}


def _pin_intent(expected, inventory, *, partial):
    from .reconcile import checked_inventory
    components, edges = checked_inventory(expected)
    if set(inventory['parts']) != {part['ref'] for part in components.values()}:
        raise Refused('revision native designators differ from the confirmed parts')
    changes = []
    for cid, part in components.items():
        ref = part['ref']
        native = inventory['parts'][ref]
        device = native.get('device', {})
        if (device.get('uuid') != part['device']['deviceUuid']
                or device.get('libraryUuid') != part['device']['libraryUuid']
                or native.get('otherProperty', {}).get('EasyEDA Agent Component ID') != cid
                or native.get('pinsAvailable') is not True or native.get('netAmbiguous')):
            raise Refused(f'{ref}: native identity, ownership or pins differ from the parent')
        pins = {str(row['pinNumber']): row for row in native['pins']}
        if len(pins) != len(native['pins']) or set(pins) != {p['number'] for p in part['pins']}:
            raise Refused(f'{ref}: physical pin inventory changed')
        for pin in part['pins']:
            number = pin['number']
            observed = pins[number]
            net = edges[(cid, number)][0] if (cid, number) in edges else ''
            before = {'net': observed.get('net'), 'noConnected': observed.get('noConnected')}
            after = {'net': net, 'noConnected': pin.get('noConnected', False)}
            if (observed.get('pinName') != pin['name'] or observed.get('netAmbiguous')
                    or not isinstance(before['net'], str) or type(before['noConnected']) is not bool):
                raise Refused(f'{ref}.{number}: physical pin name or net/NC readback changed or unavailable')
            if before == after:
                continue
            if not partial or before != {'net': '', 'noConnected': False}:
                raise Refused(f'{ref}.{number}: unexpected native net/NC {before!r}; expected {after!r}')
            changes.append({'componentId': cid, 'ref': ref, 'pinNumber': number, 'pinName': pin['name'],
                            'before': before, 'after': after})
    return changes


def _canonical_intent(ir):
    from .reconcile import checked_inventory
    components, edges = checked_inventory(ir)
    geometry = {'x', 'y', 'bbox', 'rotation', 'mirror'}
    return {
        'parts': {cid: {**{k: v for k, v in part.items() if k not in geometry | {'pins'}},
                        'pins': {p['number']: {k: v for k, v in p.items() if k not in geometry}
                                 for p in part['pins']}} for cid, part in components.items()},
        'nets': sorted(digest({k: v for k, v in row.items() if k != 'id'}) for row in ir['nets']),
        'edges': edges,
    }


def check_revision_source(proof, before, expected, source, contract, approval, plan):
    if (proof['before'] != snapshot_fingerprint(before)
            or proof['contract'] != digest(contract) or proof['approval'] != digest(approval)
            or proof['plan'] != digest(plan)):
        raise Refused('revision source or parent confirmation changed')
    from .workflow import check_source
    if check_source(plan, source, contract['target']) != expected:
        raise Refused('revision composition and pinned expected circuit differ')
    if (_canonical_intent(proof['expected']) != _canonical_intent(expected)
            or proof['sheet_binding'] != {k: source[k] for k in ('sheet', 'sheetBorder', 'titleBlock', 'paperless')
                                          if k in source}):
        raise Refused('revision changes the parent circuit, component identity, or sheet properties')


def _protected_attributes(inventory):
    return {'owners': sorted(inventory['owners']),
            'attributes': {pid: {k: v for k, v in row.items() if k not in ('X', 'Y', 'Rotation')}
                           for pid, row in inventory['attributes'].items()}}


def _check_part_guard(guard, inventory, *, initial_pose=None, desired=None):
    if not isinstance(guard, dict) or guard.get('exactParts') is not True:
        raise Refused('revision queue lacks exact part verification')
    parts = guard.get('parts', {})
    if set(parts) != set(inventory['parts']) or guard.get('ownedAttributes') != _protected_attributes(inventory):
        raise Refused('revision queue does not protect all instances and owned attributes')
    for ref, native in inventory['parts'].items():
        protected = parts[ref]
        device = {k: native['device'][k] for k in ('libraryUuid', 'uuid')}
        if (protected.get('primitiveId') != native['primitiveId'] or protected.get('device') != device
                or protected.get('instance') != {k: native[k] for k in INSTANCE_FIELDS}):
            raise Refused(f'revision guard changed persistent instance fields for {ref}')
        if initial_pose and any(protected.get(k) != native[k] for k in (*POSE, 'bbox')):
            raise Refused(f'revision source/clear guard changed native pose for {ref}')
        pins = protected.get('pins', {})
        if set(pins) != {str(pin['pinNumber']) for pin in native['pins']}:
            raise Refused(f'revision guard missing physical pins of {ref}')
        for pin in native['pins']:
            number = str(pin['pinNumber'])
            entry = pins[number]
            if (entry.get('name') != pin['pinName']
                    or entry.get('persistent') != {k: pin[k] for k in PHYSICAL_PIN_FIELDS}):
                raise Refused(f'revision guard changed persistent pin {ref}.{number}')
            if desired is not None:
                net, nc = desired[ref, number]
                if entry.get('net') != net or entry.get('noConnected') is not nc:
                    raise Refused(f'revision guard changed net/NC on {ref}.{number}')
            if initial_pose and any(entry.get(k) != pin[k] for k in ('x', 'y', 'noConnected')):
                raise Refused(f'revision source/clear guard changed pin geometry or NC for {ref}.{number}')
            if (initial_pose == 'source' and entry.get('net') != pin['net']
                    or initial_pose == 'cleared' and 'net' in entry):
                raise Refused(f'revision guard has wrong native source net for {ref}.{number}')


def _guard(steps, name):
    step = steps.get(name, {})
    if (step.get('action') != 'schematic.components.list'
            or step.get('payload') != RICH_READ or not isinstance(step.get('expectSchematic'), dict)):
        raise Refused(f'revision queue lacks complete readback guard {name}')
    return step['expectSchematic']


def revision_validation(root, before, expected, proof):
    if proof is None:
        return None
    for name, sha in proof['evidence'].items():
        if Path(name).name != name or file_digest(root / name) != sha:
            raise Refused(f'revision provenance changed: {name}')
    edits = None
    if proof['pin_changes']:
        edits = load(root / 'revision-connectivity-edits.json')
        if edits != {'schemaVersion': 1, 'beforeSha256': file_digest(root / 'before.json'),
                     'sourceSha256': file_digest(root / 'composition.json'), 'pins': proof['pin_changes']}:
            raise Refused('revision restoration delta differs from owned evidence')
    return {'before': before, 'owned_drawing': proof['owned_drawing'], 'expected': expected,
            'edits': edits, 'title_block': proof['sheet_binding'].get('titleBlock', {})}


def _check_nc_step(step, expected):
    payload = step.get('payload', {})
    parts = {c['ref']: c for c in expected['components']}
    ref = payload.get('designator')
    pins = payload.get('pins')
    intended = {p['number'] for p in parts.get(ref, {}).get('pins', []) if p.get('noConnected') is True}
    if (set(payload) != {'designator', 'pins', 'noConnected'} or payload['noConnected'] is not True
            or not isinstance(pins, list) or any(p not in intended for p in pins)
            or step.get('assert') != {'$.notApplied': 'len==0'}):
        raise Refused('revision NC operation exceeds the approved pin intent')


def _check_title_block(step, inventory, title_block):
    try:
        patch = json.loads(step['flags']['data'])
    except (ValueError, TypeError, KeyError) as error:
        raise Refused('revision title block must preserve current fields') from error
    if set(step['flags']) != {'data'} or not patch or set(patch) != set(title_block):
        raise Refused('revision title block patch differs from the approved fields')
    sheets = {pid for pid, row in inventory['components'].items() if row['componentType'] == 'sheet'}
    for key, value in patch.items():
        matches = [row for row in inventory['attributes'].values()
                   if row.get('Key') == key and row['ParentPrimitiveId'] in sheets]
        authored = title_block[key] if isinstance(title_block[key], dict) else {'value': title_block[key]}
        if (len(matches) != 1 or value != authored or value['value'] != matches[0].get('Value')
                or any(value[k] != matches[0].get(native) for k, native in (
                    ('showTitle', 'KeyVisible'), ('showValue', 'ValueVisible')) if k in value)):
            raise Refused(f'revision title block would change protected native field {key}')


def validate_revision_queue(queue, target, revision):
    from .reconcile import checked_inventory
    inventory = revision_inventory(revision['before'], target)
    if inventory['drawing'] != revision['owned_drawing']:
        raise Refused('revision drawing IDs/classes differ from the proved inventory')
    steps = _id_rows(queue['steps'], 'id', 'revision queue steps')
    ordered = list(steps)
    if queue.get('requireFullExecution') is not True or queue.get('vars'):
        raise Refused('revision queue must execute fully with literal identities')
    deletes = []
    read_only = {'schematic.components.list', 'schematic.check', 'schematic.bridgeCheck'}
    permitted = read_only | {'schematic.primitives.delete', 'schematic.component.modify', 'schematic.wire.create',
                             'schematic.power.connect_pin', 'schematic.pin.set_no_connect', 'schematic.save'}
    commands = {'sch frame apply', 'sch frame check', 'sch group create', 'sch gate', 'sch titleblock'}
    native_ids = {p['primitiveId'] for p in inventory['parts'].values()}
    for step in steps.values():
        action, run = step.get('action'), step.get('run')
        if (any(k in step for k in ('args', 'verify', 'writeSet', 'checkpoint', 'when', 'unless'))
                or step.get('onFail') not in (None, 'stop')
                or set(step.get('payload', {})) & {'windowId', 'projectUuid', 'documentUuid', 'pageUuid',
                                                   'schematicPageUuid', 'schematicUuid'}
                or action and action not in permitted or run and run not in commands):
            raise Refused(f'revision step could change identities or bypass the target: {step["id"]}')
        if action == 'schematic.component.modify':
            payload = step.get('payload', {})
            if (set(payload) != {'primitiveId', 'patch', 'preserveInstance'}
                    or payload['primitiveId'] not in native_ids or payload['preserveInstance'] is not True
                    or set(payload['patch']) != set(POSE)
                    or any(type(payload['patch'][k]) not in (int, float) for k in ('x', 'y', 'rotation'))
                    or type(payload['patch']['mirror']) is not bool
                    or step.get('assert') != {'$.instancePreserved': '==true'}):
                raise Refused('revision can move only an owned instance with its pose fields')
        if action == 'schematic.primitives.delete':
            deletes.append(step)
        if run in ('sch frame apply', 'sch frame check'):
            flags = step.get('flags', {})
            if set(flags) != {'data'} or json.loads(flags['data']).get('documentId') != target['document_uuid']:
                raise Refused('revision frame operation must name the same exact schematic page')
        if run == 'sch titleblock':
            _check_title_block(step, inventory, revision.get('title_block', {}))
        if action == 'schematic.pin.set_no_connect':
            _check_nc_step(step, revision['expected'])
    ids = set(inventory['drawing'])
    if 'verify-existing-composition' in steps:
        if deletes or any(s.get('action') in {'schematic.component.modify', 'schematic.wire.create',
                                              'schematic.power.connect_pin', 'schematic.pin.set_no_connect'}
                          for s in steps.values()):
            raise Refused('matching revision cannot reset or redraw the circuit')
        _check_part_guard(_guard(steps, 'verify-existing-composition'), inventory)
    else:
        source = _guard(steps, 'verify-source-before-reset')
        scene = source.get('sourceScene')
        scoped = inventory['data']
        if (not isinstance(scene, dict) or scene.get('pagePrimitives') != inventory['page']
                or scene.get('connectivitySummary') != scoped['connectivitySummary']):
            raise Refused('revision source guard does not bind the complete native baseline')
        try:
            seen_wires = sorted(digest(json.loads(row)) for row in scene['wires'])
        except (TypeError, ValueError, KeyError) as error:
            raise Refused('revision source guard lacks complete wire segments') from error
        if seen_wires != sorted(digest(row) for row in scoped['wires']):
            raise Refused('revision source guard changed wire segments')
        if set(scene.get('components', {})) != set(inventory['components']):
            raise Refused('revision source guard changed native component IDs')
        for pid, row in inventory['components'].items():
            seen = scene['components'][pid]
            if row['componentType'] == 'part':
                if seen.get('pins') != {str(pin['pinNumber']): pin for pin in row['pins']}:
                    raise Refused(f'revision source guard changed native pin readback: {pid}')
            elif any(seen.get(k) != row.get(k) for k in ('primitiveId', 'componentType', 'bbox', 'pins')):
                raise Refused(f'revision source guard changed native component: {pid}')
        if source.get('sourceConnectivityEdits') != revision.get('edits'):
            raise Refused('revision source guard changed the approved restoration delta')
        _check_part_guard(source, inventory, initial_pose='source')
        if len(deletes) != bool(ids):
            raise Refused('revision requires one exact drawing-only reset when drawing is present')
        initial = ordered.index('verify-source-before-reset')
        if any(steps[name].get('action') != 'schematic.components.list' for name in ordered[:initial]):
            raise Refused('revision writes cannot precede the source guard')
        cleared_name = 'verify-preserved-parts-after-clear'
        if ids:
            delete, = deletes
            if (delete['id'] != 'reset-drawing-preserving-instances'
                    or ordered[initial:initial + 3] != ['verify-source-before-reset', delete['id'], cleared_name]
                    or set(delete) != {'id', 'action', 'payload', 'assert'}
                    or set(delete['payload']) != {'primitiveIds'}):
                raise Refused('drawing reset must be surrounded by its compiled source and cleared guards')
            declared = delete['payload']['primitiveIds']
            if (not isinstance(declared, list) or any(not isinstance(pid, str) for pid in declared)
                    or set(declared) != ids or len(declared) != len(ids)
                    or delete['assert'] != {'$.total': f'=={len(ids)}', '$.requested': f'=={len(ids)}'}):
                raise Refused('revision reset must enumerate exactly the proven native drawing IDs')
        if ids or cleared_name in steps:
            cleared = _guard(steps, cleared_name)
            remaining = {kind: [row for row in rows if row['primitiveId'] not in ids
                                and (kind != 'attributes' or row['primitiveId'] in inventory['attributes'])]
                         for kind, rows in inventory['page'].items()}
            if (cleared.get('pagePrimitives') != remaining or cleared.get('drawing') != {'wires': None, 'flags': None}
                    or 'sourceScene' in cleared or 'sourceConnectivityEdits' in cleared):
                raise Refused('revision cleared guard does not protect the exact residual inventory')
            _check_part_guard(cleared, inventory, initial_pose='cleared')
        before_wiring = ordered.index('verify-physical-pins-before-wiring')
        if any(step.get('action') in ('schematic.wire.create', 'schematic.power.connect_pin',
                                      'schematic.pin.set_no_connect')
               for step in list(steps.values())[:before_wiring]):
            raise Refused('revision wiring must follow the physical pin guard')
        _check_part_guard(_guard(steps, 'verify-physical-pins-before-wiring'), inventory)
    components, edges = checked_inventory(revision['expected'])
    desired = {(c['ref'], p['number']): (edges[(cid, p['number'])][0] if (cid, p['number']) in edges else '',
                                       p.get('noConnected', False))
               for cid, c in components.items() for p in c['pins']}
    for name in ('verify-all-pins-nets-nc', 'verify-saved-instance-preservation'):
        _check_part_guard(_guard(steps, name), inventory, desired=desired)


def add_ownership_captures(queue):
    """Capture actual IDs and the pre/post drawing inventory in new compose runs."""
    checkpoints = {
        'verify-physical-pins-before-wiring': 'eso-placed-readback',
        'verify-saved-instance-preservation': 'eso-saved-readback',
    }
    changed = False
    for step in queue['steps']:
        ident, action, run = step.get('id'), step.get('action'), step.get('run')
        captures = {}
        if ident in checkpoints and action == 'schematic.components.list':
            step['payload'].update(RICH_READ)
            captures[checkpoints[ident]] = '$'
        elif action == 'schematic.wire.create':
            captures['eso-wire'] = '$.primitiveId'
        elif action == 'schematic.power.connect_pin':
            captures.update({'eso-wire': '$.wirePrimitiveId', 'eso-marker': '$.flagPrimitiveId'})
        elif run == 'sch frame apply':
            captures['eso-frames'] = '$.frames'
        if captures:
            step.setdefault('capture', {}).update(captures)
            changed = True
    checkpointed = []
    existing = {step['id'] for step in queue['steps']}
    for step in queue['steps']:
        checkpointed.append(step)
        if step.get('action') == 'schematic.power.connect_pin' or step.get('run') == 'sch frame apply':
            ident = 'eso-read-drawing-after-' + step['id']
            if ident not in existing:
                checkpointed.append({'id': ident, 'action': 'schematic.components.list',
                                     'payload': RICH_READ.copy(), 'capture': {'eso-drawing-readback': '$'}})
                changed = True
    queue['steps'] = checkpointed
    if changed and not any('eso-saved-readback' in step.get('capture', {}) for step in queue['steps']):
        queue['steps'].append({'id': 'eso-read-saved-drawing', 'action': 'schematic.components.list',
                               'payload': RICH_READ, 'capture': {'eso-saved-readback': '$'}})
    return changed


def _apply_progress(parent, target, queue):
    journal = parent / 'apply.journal.jsonl'
    try:
        rows = [json.loads(line) for line in journal.read_text(encoding='utf-8').splitlines()]
    except (ValueError, OSError) as error:
        raise Refused('the original Apply journal is missing or incomplete') from error
    if not rows or any(not isinstance(row, dict) for row in rows):
        raise Refused('the original Apply journal has invalid rows')
    header = rows[0]
    if (header.get('playbookSha256') != file_digest(parent / 'apply.json')
            or header.get('project') != target['project_uuid']
            or header.get('doc', target['document_uuid']) != target['document_uuid']):
        raise Refused('Apply journal does not bind the original queue and target')
    if len(rows) > len(queue['steps']) + 1:
        raise Refused('Apply journal exceeds the original queue')
    accepted, stop_error = [], None
    for index, row in enumerate(rows[1:]):
        step = queue['steps'][index]
        captures = row.get('captured', {})
        if (type(row.get('idx')) is not int or row['idx'] != index + 1 or row.get('id') != step.get('id')
                or not isinstance(captures, dict) or set(captures) - set(step.get('capture', {}))):
            raise Refused('Apply journal progress or captures differ from the original queue')
        if row.get('status') == 'fail' and index == len(rows) - 2:
            if captures:
                raise Refused('failed Apply step has unexpected successful captures')
            stop_error = row.get('error', '')
            break
        if row.get('status') not in ('ok', 'ok(verified)'):
            raise Refused('original Apply journal is not an ordered stopped prefix')
        accepted.append((step, captures))
    pending = queue['steps'][len(accepted)] if len(accepted) < len(queue['steps']) else None
    return accepted, pending, stop_error


def _checkpoint(value, context, target):
    try:
        return revision_inventory({'ok': True, 'context': context, 'result': json.loads(value)}, target)
    except (ValueError, TypeError, KeyError) as error:
        raise Refused('original Apply checkpoint is incomplete: ' + str(error)) from error


def _without_pin_state(inventory):
    """Compare native parts, ignoring connectivity and library lookup diagnostics."""
    output = {}
    for pid, component in inventory['components'].items():
        if component['componentType'] not in ('part', 'sheet'):
            continue
        row = {k: v for k, v in component.items() if k not in (
            'netAmbiguous', 'netlistAvailable', 'deviceIdentityCandidates', 'deviceResolution')}
        if row['componentType'] == 'sheet':
            row = _sheet_state(row)
        if 'pins' in row:
            row['pins'] = {p['primitiveId']: {k: v for k, v in p.items() if k not in (
                'net', 'noConnected', 'wirePrimitiveIds', 'netAmbiguous')} for p in row['pins']}
        output[pid] = row
    return output


def _sheet_state(row):
    # Upstream sourceScene also excludes these auto-updated sheet properties.
    if 'otherProperty' in row and isinstance(row['otherProperty'], dict):
        return {**row, 'otherProperty': {k: v for k, v in row['otherProperty'].items()
                                         if k not in ('@Update Date', '@Update Time')}}
    return row


def _attribute_rows(inventory, *, omit_ids=False):
    rows = inventory['page']['attributes'] if omit_ids else inventory['attributes'].values()
    return sorted(digest({k: v for k, v in row.items() if not omit_ids or k != 'primitiveId'}) for row in rows)


def _native_state(inventory):
    # A documented save/reopen can regenerate attribute IDs. Keep every raw
    # attribute field, including owner and multiplicity, and all other IDs.
    return {kind: sorted(digest(_sheet_state(row) if row.get('componentType') == 'sheet' else row)
                         for row in rows)
            for kind, rows in inventory['page'].items() if kind != 'attributes'}


def _same_saved_drawing(saved, observed):
    return (_native_state(saved) == _native_state(observed)
            and _attribute_rows(saved, omit_ids=True) == _attribute_rows(observed, omit_ids=True)
            and _without_pin_state(saved) == _without_pin_state(observed)
            and _pin_states(saved) == _pin_states(observed))

def _pin_states(inventory):
    return {(ref, str(pin['pinNumber'])): (pin['net'], pin['noConnected'])
            for ref, part in inventory['parts'].items() for pin in part['pins']}


def _wire_segments(data):
    return [((row['x0'], row['y0']), (row['x1'], row['y1'])) for row in data['wires']]


def _step_segments(step):
    payload = step['payload']
    if step['action'] == 'schematic.wire.create':
        points = [tuple(p) for p in payload['points']]
        return list(zip(points, points[1:]))
    x, y, offset = (payload[k] for k in ('pinX', 'pinY', 'offset'))
    dx, dy = {'left': (-1, 0), 'right': (1, 0), 'up': (0, 1),
              'down': (0, -1)}[payload['direction']]
    def on_grid(value):
        return math.floor(value / 5 + 0.5) * 5

    return [((on_grid(x), on_grid(y)), (on_grid(x + dx * offset), on_grid(y + dy * offset)))]


def _wire_coverage(segments):
    """Compare geometry independent of native coalescing and segment ordering."""
    lines, diagonals = {}, set()
    for start, finish in segments:
        x, y = start
        end_x, end_y = finish
        if x == end_x:
            lines.setdefault(('v', x), []).append(tuple(sorted((y, end_y))))
        elif y == end_y:
            lines.setdefault(('h', y), []).append(tuple(sorted((x, end_x))))
        else:
            diagonals.add(tuple(sorted((start, finish))))
    coverage = {}
    for line, intervals in lines.items():
        merged = []
        for low, high in sorted(intervals):
            if merged and low <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(high, merged[-1][1]))
            else:
                merged.append((low, high))
        coverage[line] = merged
    return coverage, diagonals


def _drawing_semantics(inventory):
    """Retain native drawing content and owned attributes, without netlist caches."""
    result = {}
    for pid, kind in inventory['drawing'].items():
        if kind == 'wires':
            continue
        if kind in MARKERS:
            native = next(row for row in inventory['page']['components'] if row['primitiveId'] == pid)
            pins = inventory['components'][pid]['pins']
            owners = {pid} | {p['primitiveId'] for p in pins}
            persistent_pins = [{k: v for k, v in pin.items() if k not in ('net', 'wirePrimitiveIds', 'netAmbiguous')}
                               for pin in pins]
        else:
            native = next(row for row in inventory['page'][kind] if row['primitiveId'] == pid)
            owners, persistent_pins = {pid}, []
        attributes = sorted(digest(row) for row in inventory['page']['attributes']
                            if row.get('ParentPrimitiveId') in owners)
        result[pid] = {'native': native, 'pins': persistent_pins, 'attributes': attributes}
    return result


def _check_marker_intent(inventory, pid, step):
    """A raw checkpoint must also match the action that created the marker."""
    payload = step['payload']
    endpoint = _step_segments(step)[0][1]
    marker = inventory['components'].get(pid, {})
    native = next((row for row in inventory['page']['components'] if row['primitiveId'] == pid), {})
    expected_type = {'net_label': 'netlabel'}.get(payload['kind'],
        'netport' if payload['kind'].startswith('net_port') else 'netflag')
    pins = marker.get('pins', [])
    if (marker.get('componentType') != expected_type or marker.get('net') != payload['net']
            or native.get('net') != payload['net']
            or any((row.get('x'), row.get('y')) != endpoint for row in (marker, native))
            or len(pins) != 1 or (pins[0].get('x'), pins[0].get('y')) != endpoint):
        raise Refused(f'{pid}: original marker readback differs from its captured action net or position')


def _drawing_progress(accepted, pending, inventory, before, target, *, pending_may_have_landed):
    """Prove ownership from durable captures, with one bounded pending wire."""
    owned = {} if before is None else dict(before['drawing'])
    original_parts = {} if before is None else {ref: row['primitiveId'] for ref, row in before['parts'].items()}
    checkpoint = before
    final = None
    expected_wires = [] if before is None else _wire_segments(before['data'])
    allowed_nc = {}
    marker_actions = {}
    recorded_drawing = {} if before is None else _drawing_semantics(before)
    reset = False
    for step, captures in accepted:
        action, payload = step.get('action'), step.get('payload', {})
        if action == 'schematic.component.place':
            keys = [key for key, path in step.get('capture', {}).items() if path == '$.primitiveId']
            if len(keys) != 1 or not captures.get(keys[0]):
                raise Refused('original placement journal lacks its native part capture')
            original_parts[payload['designator']] = captures[keys[0]]
        elif action == 'schematic.primitives.delete':
            reset = True
            owned.clear()
            recorded_drawing.clear()
            marker_actions.clear()
            expected_wires.clear()
        elif action in ('schematic.wire.create', 'schematic.power.connect_pin'):
            expected_wires.extend(_step_segments(step))
            if not captures.get('eso-wire') or captures['eso-wire'] == 'null':
                raise Refused(f'{step["id"]}: original write has no native wire ownership capture')
            owned[captures['eso-wire']] = 'wires'
            if action == 'schematic.power.connect_pin':
                pid = captures.get('eso-marker')
                if not pid or pid == 'null':
                    raise Refused(f'{step["id"]}: original marker has no native ownership capture')
                marker_actions[pid] = step
                owned[pid] = {'net_label': 'netlabel'}.get(payload['kind'],
                    'netport' if payload['kind'].startswith('net_port') else 'netflag')
        elif action == 'schematic.pin.set_no_connect':
            allowed_nc.update({(payload['designator'], pin): payload['noConnected'] for pin in payload['pins']})
        elif step.get('run') == 'sch frame apply':
            if not captures.get('eso-frames'):
                raise Refused('original frame write has no verified native ownership capture')
            for frame in json.loads(captures['eso-frames']):
                owned[frame['rectangle']['id']] = 'rectangles'
                owned[frame['title']['id']] = 'texts'
        if 'eso-placed-readback' in captures:
            checkpoint = _checkpoint(captures['eso-placed-readback'], inventory['context'], target)
        if 'eso-saved-readback' in captures:
            final = _checkpoint(captures['eso-saved-readback'], inventory['context'], target)
        if 'eso-drawing-readback' in captures:
            captured = _checkpoint(captures['eso-drawing-readback'], inventory['context'], target)
            for pid, marker_step in marker_actions.items():
                _check_marker_intent(captured, pid, marker_step)
            for pid, semantic in _drawing_semantics(captured).items():
                if pid in recorded_drawing and recorded_drawing[pid] != semantic:
                    raise Refused(f'{pid}: native drawing changed between original checkpoints')
                recorded_drawing[pid] = semantic
    if original_parts != {ref: row['primitiveId'] for ref, row in inventory['parts'].items()}:
        raise Refused('current native parts lack the original captured or staged ownership')
    if pending and pending.get('action') == 'schematic.primitives.delete' and pending_may_have_landed:
        for kind in (*DRAWING_CLASSES, 'components'):
            previous = {row['primitiveId']: row for row in before['page'][kind]}
            for row in inventory['page'][kind]:
                pid = row['primitiveId']
                if pid in inventory['drawing'] and (owned.get(pid) != inventory['drawing'][pid]
                                                       or row != previous.get(pid)):
                    raise Refused(f'unexpected native primitive during interrupted reset: {kind}/{pid}')
        reset = True
        owned = dict(inventory['drawing'])
        expected_wires = _wire_segments(inventory['data'])
    if checkpoint is None:
        raise Refused('original run has no complete owned part/attribute readback')
    if final is not None:
        if not _same_saved_drawing(final, inventory):
            raise Refused('fresh page differs from the recorded post-Apply native drawing')
    elif (_without_pin_state(checkpoint) != _without_pin_state(inventory)
          or _attribute_rows(checkpoint) != _attribute_rows(inventory)):
        raise Refused('fresh instances, pose or retained attributes differ from the last owned checkpoint')
    actual = inventory['drawing']
    if final is None:
        for pid, marker_step in marker_actions.items():
            _check_marker_intent(inventory, pid, marker_step)
        current_drawing = _drawing_semantics(inventory)
        for pid, kind in actual.items():
            if kind != 'wires' and (pid not in recorded_drawing or recorded_drawing[pid] != current_drawing[pid]):
                raise Refused(f'{pid}: partial drawing content lacks matching original native readback')
    else:
        for pid, marker_step in marker_actions.items():
            _check_marker_intent(final, pid, marker_step)
    unknown = {pid: kind for pid, kind in actual.items() if owned.get(pid) != kind}
    landed = None
    if (len(unknown) == 1 and pending_may_have_landed and pending
            and pending.get('action') == 'schematic.wire.create'):
        pid, kind = next(iter(unknown.items()))
        actual_segments = _wire_segments({'wires': [row for row in inventory['data']['wires']
                                                    if row['primitiveId'] == pid]})
        segment = _step_segments(pending)
        if (kind == 'wires' and _wire_coverage(actual_segments) == _wire_coverage(segment)
                and _wire_coverage(expected_wires + segment) != _wire_coverage(expected_wires)):
            owned[pid] = kind
            expected_wires.extend(segment)
            unknown.clear()
            landed = pending['id']
    if unknown:
        raise Refused(f'native drawing IDs lack original journal or bounded pending-write ownership: {unknown}')
    if (_wire_coverage(expected_wires) != _wire_coverage(_wire_segments(inventory['data']))
            or any(kind != 'wires' and actual.get(pid) != kind for pid, kind in owned.items())):
        raise Refused('fresh native drawing geometry/markers differ from the original Apply prefix')
    if final is None:
        initial_nc = _pin_states(checkpoint)
        for key, (_, nc) in _pin_states(inventory).items():
            if nc is not allowed_nc.get(key, initial_nc[key][1]):
                raise Refused(f'{key}: unproved NC change after original checkpoint')
    return {'pending_step': pending['id'] if pending else None, 'landed_step': landed,
            'reset_seen': reset, 'completed_steps': len(accepted)}


def pin_staged_ownership(stage_root, root, target, stage, parts):
    """Retain the original blank-page placement proof for future wired revisions."""
    original = stage_root / 'before-placement.json'
    journal = stage_root / 'place.journal.jsonl'
    if not original.is_file() or not (journal.is_file() or 'recovery_sha256' in stage):
        return {}
    baseline = load(original)
    context = baseline.get('context', {})
    if (not is_blank(result(baseline)) or context.get('projectUuid') != target['project_uuid']
            or context.get('documentUuid') != target['document_uuid']):
        return {}
    names = ['before-placement.json', 'bindings.json']
    if journal.is_file():
        names.extend(('place.json', 'place.journal.jsonl'))
    hashes = {name: file_digest(stage_root / name) for name in names}
    owned = journal_ownership(stage_root, target, set(parts), stage)
    if owned != {ref: p['primitiveId'] for ref, p in parts.items()}:
        return {}
    artifacts = {}
    for name, sha in hashes.items():
        pinned = 'staging-origin--' + name
        (root / pinned).write_bytes((stage_root / name).read_bytes())
        if file_digest(root / pinned) != sha:
            raise Refused('original staging evidence changed while copying: ' + name)
        artifacts[pinned] = sha
    dump(root / 'staging-ownership.json', {'primitive_ids': owned})
    artifacts['staging-ownership.json'] = file_digest(root / 'staging-ownership.json')
    return artifacts


def _baseline_changes(expected, actual):
    changes = []
    for kind, rows in expected['page'].items():
        before = {row['primitiveId']: row for row in rows}
        after = {row['primitiveId']: row for row in actual['page'][kind]}
        changes.extend(f'{kind}/{pid}' for pid in sorted(before.keys() | after.keys())
                       if before.get(pid) != after.get(pid))
    for pid in sorted(expected['components'].keys() & actual['components'].keys()):
        if expected['components'][pid] != actual['components'][pid]:
            changes.append(f'component readback/{pid}')
    return ', '.join(changes) or 'complete rich snapshot fields or row order changed'


def prove_revision(parent, fresh, target, root, *, window=None):
    """Build a revision proof from this page's original run and fresh native state."""
    from .evidence import check_attempt
    from .workflow import VERIFICATION_CHECKS, Workflow, check_source
    parent_bundle = load(parent / 'bundle.json')
    Workflow(None, parent)._check_bundle(parent_bundle)
    if (parent_bundle.get('target') != target or parent_bundle.get('window') != window
            or not (parent / 'execution-started.lock').is_file()
            or parent_bundle.get('state') not in ('verified', 'applied-unverified', 'incomplete', 'verifying')):
        raise Refused('parent is not an executed run for the same schematic target and window')
    original_before = load(parent / 'before.json')
    if not same_window(original_before, fresh):
        raise Refused('current readback window differs from the parent run')
    if parent_bundle.get('baseline_fingerprint') != snapshot_fingerprint(original_before):
        raise Refused('parent baseline fingerprint differs from the original snapshot')
    original_source = load(parent / 'composition.json')
    plan = load(parent / 'plan.json')
    expected = load(parent / 'expected.json')
    if check_source(plan, original_source, target) != expected:
        raise Refused('parent composition and its original expected intent differ')
    queue = load(parent / 'apply.json')
    journal_sha = file_digest(parent / 'apply.journal.jsonl')
    accepted, pending, stop_error = _apply_progress(parent, target, queue)
    inventory = revision_inventory(fresh, target)
    inventory['context'] = fresh['context']
    from_root = is_blank(result(original_before))
    before = None if from_root else revision_inventory(original_before, target)
    if not from_root:
        if 'revision-proof.json' in parent_bundle['artifacts']:
            prior_proof = load(parent / 'revision-proof.json')
            if before['drawing'] != prior_proof['owned_drawing']:
                raise Refused('parent revision drawing ownership is inconsistent')
        elif 'staging-ownership.json' in parent_bundle['artifacts']:
            stage = load(parent / 'staging-proof.json')
            placement = load(parent / 'staging-ownership.json')
            original_blank = load(parent / 'staging-origin--before-placement.json')
            bindings = load(parent / 'staging-origin--bindings.json')
            owned_parts = observed_parts(plan, original_before, bindings, target)
            ids = {ref: part['primitiveId'] for ref, part in owned_parts.items()}
            if (stage.get('measured_scene_sha256') != snapshot_fingerprint(original_before)
                    or stage.get('primitive_ids') != ids or placement.get('primitive_ids') != ids
                    or not is_blank(result(original_blank)) or before['drawing']):
                raise Refused('parent staging proof does not establish an unwired owned drawing root')
        else:
            raise Refused('parent did not start from a blank drawing or an owned staging/revision run')
    evidence_roles = {name: parent / name for name in (
        'bundle.json', 'approval.json', 'contract.json', 'plan.json', 'expected.json', 'composition.json',
        'apply.json', 'apply.journal.jsonl', 'before.json', 'execution-started.lock')}
    if before is not None:
        if 'revision-proof.json' in parent_bundle['artifacts']:
            evidence_roles['revision-proof.json'] = parent / 'revision-proof.json'
            for name in prior_proof['evidence']:
                evidence_roles[name] = parent / name
        else:
            for name in parent_bundle['artifacts']:
                if name.startswith(('staging-', 'recovery-proof', 'recovery-source--')):
                    evidence_roles[name] = parent / name
    verified = parent_bundle['state'] == 'verified'
    if verified:
        report = load(parent / 'verification.json')
        checks = report.get('checks', {})
        if (pending or report.get('state') != 'verified' or report.get('attempt') != parent_bundle.get('verification_attempt')
                or report.get('bundle_artifacts_sha256') != digest(parent_bundle['artifacts'])
                or not VERIFICATION_CHECKS.issubset(checks)
                or any(checks[key].get('passed') is not True for key in VERIFICATION_CHECKS)):
            raise Refused('parent verification does not bind a complete original Apply and attempt')
        archive = check_attempt(parent, report)
        final = load(archive / 'final-snapshot.json')
        if not same_window(final, fresh) or snapshot_fingerprint(final) != snapshot_fingerprint(fresh):
            archived = revision_inventory(final, target)
            raise Refused(f'fresh baseline changed since verified parent snapshot: {_baseline_changes(archived, inventory)}')
        evidence_roles.update({name: archive / name for name in ('verification.json', 'final-snapshot.json')})
    ended_uncertainly = stop_error is None or any(
        term in str(stop_error).lower() for term in ('timeout', 'timed out', 'may have landed', 'unverified'))
    progress = _drawing_progress(accepted, pending, inventory, before, target,
                                 pending_may_have_landed=ended_uncertainly)
    failure = parent / 'failure-readback.json'
    if failure.is_file():
        failed_snapshot = load(failure)
        if (not same_window(failed_snapshot, fresh)
                or snapshot_fingerprint(failed_snapshot) != snapshot_fingerprint(fresh)
                and not (progress['landed_step'] or pending and pending.get('action') == 'schematic.primitives.delete'
                         and progress['reset_seen'])):
            raise Refused('current page changed after the recorded failure readback')
        evidence_roles['failure-readback.json'] = failure
    pin_changes = _pin_intent(expected, inventory, partial=pending is not None)
    expected_shas = {**parent_bundle['artifacts'], 'apply.journal.jsonl': journal_sha}
    if verified:
        expected_shas['final-snapshot.json'] = report['snapshot_sha256']
    evidence = {}
    roles = {}
    for name, path in evidence_roles.items():
        sha = file_digest(path)
        if name in expected_shas and sha != expected_shas[name]:
            raise Refused(f'parent revision evidence changed while preparing: {name}')
        pinned = 'revision-evidence--' + sha
        (root / pinned).write_bytes(path.read_bytes())
        if file_digest(root / pinned) != sha:
            raise Refused(f'parent revision evidence changed while copying: {name}')
        evidence[pinned] = sha
        roles[name] = pinned
    proof = {'schema_version': 1, 'before': snapshot_fingerprint(fresh), 'expected': expected,
             'contract': digest(load(parent / 'contract.json')), 'approval': digest(load(parent / 'approval.json')),
             'plan': digest(plan), 'owned_drawing': inventory['drawing'], 'pin_changes': pin_changes,
             'sheet_binding': {k: original_source[k] for k in ('sheet', 'sheetBorder', 'titleBlock', 'paperless')
                               if k in original_source},
             'parent_was_verified': verified, 'progress': progress, 'evidence': evidence, 'evidence_roles': roles}
    dump(root / 'revision-proof.json', proof)
    return proof


def require_revision_completion(root, snapshot, target):
    """A successful verification needs the current revision's completed Apply."""
    accepted, pending, _ = _apply_progress(root, target, load(root / 'apply.json'))
    baseline = load(root / 'before.json')
    if pending or not same_window(baseline, snapshot):
        raise Refused('revision Apply or its target window changed before verification')
    final = [captures['eso-saved-readback'] for _, captures in accepted if 'eso-saved-readback' in captures]
    if len(final) != 1:
        raise Refused('revision Apply did not capture its saved native drawing')
    saved = _checkpoint(final[0], baseline['context'], target)
    current = revision_inventory(snapshot, target)
    if not _same_saved_drawing(saved, current):
        raise Refused('current native drawing differs from the completed revision Apply')
