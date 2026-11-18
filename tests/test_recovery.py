"""Placement and drawing recovery with synthetic ownership and no live EDA."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from eso.core import Refused, confirm, digest, dump, file_digest, load
from eso.planner import bind_plan
from test_bridge import fixture, TARGET
from eso.recovery import recover_stage

def setup_source(root, present=('R1',)):
    c, p, b, s, l, g, svg = fixture(root)
    second = copy.deepcopy(p['components'][0])
    second.update(id='fixture-second', ref='R2')
    p['components'].append(second)
    b['R2'] = copy.deepcopy(b['R1'])
    part = copy.deepcopy(s['result']['components'][1])
    part.update(designator='R2', primitiveId='second-native', x=300)
    part['otherProperty']['EasyEDA Agent Component ID'] = 'fixture-second'
    for pin in part['pins']:
        pin['primitiveId'] = 'second-' + pin['primitiveId']
        pin['x'] += 200
    full = copy.deepcopy(s)
    full['result']['components'].append(part)
    full['result']['pagePrimitives']['components'] = full['result']['components']
    blank = copy.deepcopy(s)
    blank['result']['components'] = blank['result']['components'][:1]
    blank['result']['pagePrimitives']['components'] = blank['result']['components']
    source = root / 'interrupted'
    source.mkdir()
    values = {'contract': c, 'approval': confirm(c, digest(c), 'SYNTHETIC confirmed fixture'), 'plan': p, 'bindings': b, 'expected': bind_plan(p, b, TARGET), 'before-placement': blank, 'stage': {'state': 'incomplete', 'target': TARGET, 'contract_sha256': digest(c), 'plan_sha256': digest(p), 'bindings_sha256': digest(b)}}
    for name, value in values.items():
        dump(source / (name + '.json'), value)
    queue = {'version': 1, 'meta': {'project': TARGET['project_uuid'], 'doc': TARGET['document_uuid']}, 'steps': [{'id': 'original', 'action': 'schematic.save'}]}
    dump(source / 'place.json', queue)
    rows = [{'playbookSha256': file_digest(source / 'place.json'), 'project': TARGET['project_uuid'], 'doc': TARGET['document_uuid']}]
    for part in full['result']['components'][1:]:
        if part['designator'] in present:
            rows.append({'id': 'place-' + part['designator'], 'status': 'ok', 'captured': {part['designator']: part['primitiveId']}})
    (source / 'place.journal.jsonl').write_text(''.join((json.dumps(row) + '\n' for row in rows)))
    (source / 'placement-started.lock').write_text('synthetic prior attempt')
    actual = copy.deepcopy(full)
    actual['result']['components'] = [v for v in actual['result']['components'] if v.get('componentType') == 'sheet' or v['designator'] in present]
    actual['result']['pagePrimitives']['components'] = actual['result']['components']
    return (source, RecoveryAdapter(actual, full))

class RecoveryAdapter:

    def __init__(self, actual, full):
        self.actual = actual
        self.full = full
        self.calls = []
        self.fail_after_place = False
        self.saved = True

    def snapshot(self, target):
        return copy.deepcopy(self.actual)

    def guard(self, target):
        pass

    def run(self, args, **kwargs):
        self.calls.append(list(map(str, args)))
        if args[:3] == ['lib', 'device', 'get']:
            return {'ok': True, 'result': {'device': {'libraryUuid': '1' * 32, 'uuid': '2' * 32, 'name': 'SYNTHETIC'}}}
        if args[:2] == ['sch', 'materialize']:
            ir = load(args[2])
            steps = [{'id': 'place-' + v['ref'], 'action': 'schematic.component.place', 'payload': {'designator': v['ref']}, 'capture': {v['ref']: '$.primitiveId'}} for v in ir['components']]
            dump(args[args.index('--out') + 1], {'version': 1, 'meta': {'project': TARGET['project_uuid'], 'doc': TARGET['document_uuid']}, 'steps': steps})
        elif args[:2] == ['sch', 'apply'] and '--dry-run' not in args:
            queue = load(args[2])
            rows = [{'playbookSha256': file_digest(args[2]), 'project': TARGET['project_uuid'], 'doc': TARGET['document_uuid']}]
            for step in queue['steps']:
                part = next((v for v in self.full['result']['components'] if v.get('designator') == step['payload']['designator']))
                self.actual['result']['components'].append(copy.deepcopy(part))
                rows.append({'id': step['id'], 'status': 'ok', 'captured': {part['designator']: part['primitiveId']}})
            self.actual['result']['pagePrimitives']['components'] = self.actual['result']['components']
            Path(args[args.index('--journal') + 1]).write_text(''.join((json.dumps(row) + '\n' for row in rows)))
            if self.fail_after_place:
                raise Refused('synthetic acknowledgement failure')
        elif args[:2] == ['sch', 'save']:
            return {'ok': True, 'result': {'saved': self.saved}}
        return {'exit_code': 0, 'stdout': 'synthetic offline result', 'stderr': ''}

class RecoveryTests(unittest.TestCase):

    def test_upstream_journal_without_doc_uses_its_hashed_queue_target(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, adapter = setup_source(root, present=('R1', 'R2'))
            journal = source / 'place.journal.jsonl'
            rows = [json.loads(line) for line in journal.read_text().splitlines()]
            del rows[0]['doc']
            journal.write_text(''.join(json.dumps(row) + '\n' for row in rows))
            recovered = recover_stage(adapter, source, root / 'recovered')
            self.assertEqual(recovered['state'], 'placed')
            self.assertFalse(any(call[:2] == ['sch', 'apply'] for call in adapter.calls))

    def test_upstream_missing_doc_requires_exact_queue_target(self):
        for queue_doc, header_doc in [('other-page', None), (TARGET['document_uuid'], 'other-page')]:
            with self.subTest(queue_doc=queue_doc, header_doc=header_doc), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                source, adapter = setup_source(root, present=('R1', 'R2'))
                queue_path = source / 'place.json'
                queue = load(queue_path)
                queue['meta']['doc'] = queue_doc
                dump(queue_path, queue)
                journal = source / 'place.journal.jsonl'
                rows = [json.loads(line) for line in journal.read_text().splitlines()]
                rows[0]['playbookSha256'] = file_digest(queue_path)
                if header_doc is None:
                    del rows[0]['doc']
                else:
                    rows[0]['doc'] = header_doc
                journal.write_text(''.join(json.dumps(row) + '\n' for row in rows))
                with self.assertRaises(Refused):
                    recover_stage(adapter, source, root / 'recovered')
                self.assertEqual(adapter.calls, [])

    def test_partial_placement_only_materializes_missing_part_and_preserves_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, adapter = setup_source(root)
            before = {f.name: f.read_bytes() for f in source.iterdir()}
            report = recover_stage(adapter, source, root / 'recovered')
            self.assertEqual(report['state'], 'placed')
            self.assertEqual(report['primitive_ids'], {'R1': 'fixture-native-part', 'R2': 'second-native'})
            remaining = load(root / 'recovered/remaining.json')
            self.assertEqual([v['ref'] for v in remaining['components']], ['R2'])
            self.assertEqual(before, {f.name: f.read_bytes() for f in source.iterdir()})
            self.assertEqual(sum((call[:2] == ['sch', 'apply'] and '--dry-run' not in call for call in adapter.calls)), 1)

    def test_complete_placement_is_saved_without_any_new_placement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, adapter = setup_source(root, ('R1', 'R2'))
            recover_stage(adapter, source, root / 'recovered')
            self.assertFalse(any((call[:2] in [['sch', 'apply'], ['sch', 'materialize']] for call in adapter.calls)))

    def test_changed_identity_unowned_drawing_and_unknown_pins_refuse_before_writes(self):
        for mutation in ['instance', 'binding', 'drawing', 'pin', 'journal']:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source, adapter = setup_source(root)
                part = adapter.actual['result']['components'][1]
                if mutation == 'instance':
                    part['primitiveId'] = 'replaced-instance'
                if mutation == 'binding':
                    part['device']['uuid'] = '9' * 32
                if mutation == 'drawing':
                    adapter.actual['result']['pagePrimitives']['texts'] = [{'primitiveId': 'user-text'}]
                if mutation == 'pin':
                    part['pins'][0]['net'] = None
                if mutation == 'journal':
                    (source / 'place.journal.jsonl').write_text('incomplete json')
                with self.assertRaises(Refused):
                    recover_stage(adapter, source, root / 'recovered')
                self.assertEqual(adapter.calls, [])

    def test_failed_recovery_can_be_reconciled_again_without_duplicate_placement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, adapter = setup_source(root)
            adapter.fail_after_place = True
            with self.assertRaises(Refused):
                recover_stage(adapter, source, root / 'attempt2')
            adapter.fail_after_place = False
            adapter.calls = []
            report = recover_stage(adapter, root / 'attempt2', root / 'attempt3')
            self.assertEqual(report['state'], 'placed')
            self.assertFalse(any((c[:2] == ['sch', 'apply'] for c in adapter.calls)))

    def test_repeated_recovery_protects_the_original_queue_and_capture_history(self):
        for name in ('place.json', 'place.journal.jsonl'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source, adapter = setup_source(root)
                adapter.fail_after_place = True
                with self.assertRaises(Refused):
                    recover_stage(adapter, source, root / 'attempt2')
                (root / 'attempt2' / ('source--' + name)).write_text('changed original evidence')
                adapter.fail_after_place = False
                adapter.calls = []
                with self.assertRaises(Refused):
                    recover_stage(adapter, root / 'attempt2', root / 'attempt3')
                self.assertEqual(adapter.calls, [])
                self.assertFalse((root / 'attempt3').exists())

    def test_repeated_recovery_carries_all_prior_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, adapter = setup_source(root)
            adapter.fail_after_place = True
            with self.assertRaises(Refused):
                recover_stage(adapter, source, root / 'attempt2')
            adapter.fail_after_place = False
            recover_stage(adapter, root / 'attempt2', root / 'attempt3')
            history = load(root / 'attempt3/recovery.json')['source_artifacts']
            for name in ('place.json', 'place.journal.jsonl'):
                self.assertIn('source--' + name, history)
                self.assertEqual((root / 'attempt3' / ('source--source--' + name)).read_bytes(),
                                 (source / name).read_bytes())

    def test_incomplete_ancestry_manifest_is_refused_even_with_matching_state_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, adapter = setup_source(root)
            recover_stage(adapter, source, root / 'attempt2')
            recover_stage(adapter, root / 'attempt2', root / 'attempt3')
            recovery = load(root / 'attempt3/recovery.json')
            recovery['source_artifacts'].pop('source--place.json')
            dump(root / 'attempt3/recovery.json', recovery)
            state = load(root / 'attempt3/stage.json')
            state['recovery_sha256'] = digest(recovery)
            dump(root / 'attempt3/stage.json', state)
            adapter.calls = []
            with self.assertRaisesRegex(Refused, 'ancestry'):
                recover_stage(adapter, root / 'attempt3', root / 'attempt4')
            self.assertEqual(adapter.calls, [])

    def test_false_save_remains_incomplete_and_existing_destination_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, adapter = setup_source(root, ('R1', 'R2'))
            adapter.saved = False
            with self.assertRaisesRegex(Refused, 'save'):
                recover_stage(adapter, source, root / 'attempt2')
            self.assertEqual(load(root / 'attempt2/stage.json')['state'], 'incomplete')
            with self.assertRaises(Refused):
                recover_stage(adapter, source, root / 'attempt2')

class RecoveryCommandTests(unittest.TestCase):

    def test_cli_recovers_from_actual_inventory_and_reports_new_stage(self):
        import contextlib
        import io
        from unittest.mock import patch
        from eso.cli import main
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, adapter = setup_source(root)
            stdout = io.StringIO()
            with patch('eso.cli.EasyEDA', return_value=adapter), contextlib.redirect_stdout(stdout):
                code = main(['recover-stage', '--run-dir', str(source), '--out', str(root / 'new-stage')])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stdout.getvalue())['state'], 'placed')
            self.assertEqual(load(root / 'new-stage/remaining.json')['components'][0]['ref'], 'R2')

    def test_diagnosis_keeps_markers_out_of_component_inventory(self):
        from eso.diagnostics import diagnose
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, adapter = setup_source(root, ('R1', 'R2'))
            marker = {'componentType': 'netflag', 'primitiveId': 'synthetic-ground', 'net': 'GND'}
            adapter.actual['result']['components'].append(marker)
            report = diagnose(source, snapshot=adapter.actual)
            self.assertEqual(report['components']['unexpected'], [])
            self.assertNotIn('missing or duplicate observed designators', report['issues'])

    def test_empty_page_places_all_parts_with_one_lookup_per_library_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, adapter = setup_source(root, ())
            report = recover_stage(adapter, source, root / 'new-stage')
            self.assertEqual(set(report['primitive_ids']), {'R1', 'R2'})
            lookups = [call for call in adapter.calls if call[:3] == ['lib', 'device', 'get']]
            self.assertEqual(len(lookups), 1)

    def test_page_change_during_preparation_refuses_actual_placement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, adapter = setup_source(root)
            original_snapshot = adapter.snapshot
            reads = []

            def changing_snapshot(target):
                reads.append(target)
                if len(reads) == 2:
                    adapter.actual['result']['components'][1]['x'] += 5
                return original_snapshot(target)
            adapter.snapshot = changing_snapshot
            with self.assertRaisesRegex(Refused, 'page changed'):
                recover_stage(adapter, source, root / 'new-stage')
            self.assertFalse(any((call[:2] == ['sch', 'apply'] and '--dry-run' not in call for call in adapter.calls)))


def revision_fixture():
    from test_upstream_cli import TARGET as target, joint_drawing_fixture
    from eso.requirements import contract_from

    logical, primary, source, wired = joint_drawing_fixture()
    ir = source['connectivity']
    for net in ir['nets']:
        net.update(scope='local', role='signal')
    edges = {(c['componentId'], c['pinNumber']): c['netId'] for c in ir['connections']}
    contract = contract_from({'schema_version': 1, 'circuit_type': 'generic',
                              'function_description': 'Synthetic recovery', 'input_voltage_v': 3.3,
                              'power_source': 'external-regulated', 'target_project': target['project_name']}, target)
    owners = {m[key]: m['id'] for m in logical for key in ('core', 'peripheral')}
    plan = {'schema_version': 1, 'contract_sha256': digest(contract), 'template': 'synthetic-revision',
            'components': [
                {'id': c['id'], 'ref': c['ref'], 'query': 'SYNTHETIC', 'role': 'fixture',
                 'module': owners[c['id']],
                 'pins': [{'number': p['number'], 'name': p['name'], 'net': edges.get((c['id'], p['number'])),
                           'nc_reason': 'intentionally not connected' if p['noConnected'] else None}
                          for p in c['pins']]}
                for c in ir['components']],
            'modules': [{'id': m['id'], 'core_ref': next(c['ref'] for c in ir['components'] if c['id'] == m['core']),
                         'title': m['id']} for m in logical],
            'drawing_groups': [{'id': ir['modules'][0]['id'], 'title': ir['modules'][0]['name'],
                                'modules': [m['id'] for m in logical]}],
            'primary_paths': [primary],
            'evidence': [{'url': 'synthetic:only', 'section': 'test'}], 'notes': []}
    wired['context']['windowId'] = 'synthetic-window'
    return target, contract, plan, source, wired


def unwire(snapshot, *, remove_parts=False, count=0):
    snap = copy.deepcopy(snapshot)
    data, page = snap['result'], snap['result']['pagePrimitives']
    page['rectangles'] = []
    page['texts'] = []
    page['wires'] = page['wires'][:count]
    data['wires'] = data['wires'][:count]
    data['connectivitySummary']['wires'] = count
    if remove_parts:
        data['components'] = page['components'] = [c for c in data['components'] if c['componentType'] == 'sheet']
        page['pins'] = []
        page['attributes'] = []
        data['count'] = len(data['components'])
    else:
        for part in data['components']:
            for pin in part.get('pins', []):
                pin.update(net='', noConnected=False)
    return snap


def compiled_revision(before, after, target, *, edits=None):
    from eso.recovery import INSTANCE_FIELDS, PHYSICAL_PIN_FIELDS, POSE

    read = dict.fromkeys(('includeBBox', 'includeConnectivitySummary', 'includeDeviceIdentity',
                         'includePagePrimitives', 'includePins', 'includeWires'), True)
    page = before['result']['pagePrimitives']
    drawing_ids = {obj['primitiveId'] for kind in ('wires', 'rectangles', 'texts') for obj in page[kind]}
    drawing_ids.update(obj['primitiveId'] for obj in page['components']
                       if obj.get('componentType') in ('netflag', 'netport', 'netlabel'))
    marker_owners = {pid for part in before['result']['components'] if part['componentType'] in ('netflag', 'netport', 'netlabel')
                     for pid in [part['primitiveId'], *[pin['primitiveId'] for pin in part['pins']]]}
    retained = {k: [row for row in rows if row['primitiveId'] not in drawing_ids
                    and (k != 'attributes' or row.get('ParentPrimitiveId') not in marker_owners)] for k, rows in page.items()}

    def guard(snapshot, *, clear=False):
        data = snapshot['result']
        parts = [row for row in data['components'] if row['componentType'] == 'part']
        geometry = {}
        for p in parts:
            geometry[p['designator']] = {
                **{k: p[k] for k in ('primitiveId', 'device', 'bbox', *POSE)},
                'instance': {k: p[k] for k in INSTANCE_FIELDS},
                'pins': {q['pinNumber']: {**{k: q[k] for k in ('x', 'y', 'noConnected')},
                                          **({} if clear else {'net': q['net']}),
                                          'name': q['pinName'],
                                          'persistent': {k: q[k] for k in PHYSICAL_PIN_FIELDS}}
                         for q in p['pins']},
            }
        owners = sorted([row['primitiveId'] for row in data['components'] if row['componentType'] in ('part', 'sheet')] +
                        [pin['primitiveId'] for part in parts for pin in part['pins']])
        attributes = {row['primitiveId']: {k: v for k, v in row.items() if k not in ('X', 'Y', 'Rotation')}
                      for row in data['pagePrimitives']['attributes'] if row.get('ParentPrimitiveId') in owners}
        return {'exactParts': True, 'parts': geometry,
                'ownedAttributes': {'owners': owners, 'attributes': attributes}}

    def check(ident, expectation):
        return {'id': ident, 'action': 'schematic.components.list', 'payload': read.copy(),
                'expectSchematic': expectation}

    source = guard(before)
    components = {row['primitiveId']: copy.deepcopy(row) for row in before['result']['components']}
    for part in components.values():
        if part['componentType'] == 'part':
            part['pins'] = {pin['pinNumber']: pin for pin in part['pins']}
            part.pop('bbox')
    source['sourceScene'] = {'components': components,
                             'pagePrimitives': page,
                             'connectivitySummary': before['result']['connectivitySummary'],
                             'wires': [json.dumps(row) for row in before['result']['wires']]}
    if edits:
        source['sourceConnectivityEdits'] = edits
    cleared = dict(guard(before, clear=True), pagePrimitives=retained, drawing={'wires': None, 'flags': None})
    steps = [check('verify-source-before-reset', source)]
    if drawing_ids:
        count = len(drawing_ids)
        steps += [{'id': 'reset-drawing-preserving-instances', 'action': 'schematic.primitives.delete',
                   'payload': {'primitiveIds': sorted(drawing_ids)},
                   'assert': {'$.requested': f'=={count}', '$.total': f'=={count}'}}]
    steps.append(check('verify-preserved-parts-after-clear', cleared))
    for n, p in enumerate(p for p in after['result']['components'] if p['componentType'] == 'part'):
        steps.append({'id': 'move-preserved-' + str(n), 'action': 'schematic.component.modify',
                      'payload': {'primitiveId': p['primitiveId'], 'preserveInstance': True,
                                  'patch': {k: p[k] for k in POSE}},
                      'assert': {'$.instancePreserved': '==true'}})
    steps.append(check('verify-physical-pins-before-wiring', guard(after)))
    for wire in after['result']['wires']:
        steps.append({'id': 'wire-' + wire['primitiveId'], 'action': 'schematic.wire.create',
                      'payload': {'points': [[wire['x0'], wire['y0']], [wire['x1'], wire['y1']]]}})
    steps.append({'id': 'frame', 'run': 'sch frame apply',
                  'flags': {'data': json.dumps({'documentId': target['document_uuid'], 'frames': []})}})
    steps.extend((check('verify-all-pins-nets-nc', guard(after)), {'id': 'save', 'action': 'schematic.save'},
                  check('verify-saved-instance-preservation', guard(after))))
    return {'version': 1, 'requireFullExecution': True, 'meta': {'project': target['project_uuid'],
                                                                'doc': target['document_uuid']},
            'steps': steps}


class SimulatedDrawingAdapter:
    window = 'synthetic-window'

    def __init__(self, target, before, final, expected, *, timeout=False):
        self.target = target
        self.current = before
        self.final = final
        self.expected = expected
        self.timeout = timeout
        self.rekey_attributes = False
        self.calls = []

    def guard(self, target):
        if target != self.target:
            raise Refused('synthetic target differs')

    def snapshot(self, target):
        self.guard(target)
        return copy.deepcopy(self.current)

    def run(self, args, **kwargs):
        self.calls.append(tuple(map(str, args)))
        if args[:2] == ['sch', 'compose']:
            before = load(args[args.index('--before') + 1])
            if '--preserve-instances' in args:
                edits = load(args[args.index('--connectivity-edits') + 1]) if '--connectivity-edits' in args else None
                playbook = compiled_revision(before, self.final, self.target, edits=edits)
            else:
                playbook = self.first_queue()
            dump(args[args.index('--playbook') + 1], playbook)
            dump(args[args.index('--out') + 1], {})
        elif args[:2] == ['sch', 'apply'] and '--journal' in args:
            self.apply(args[2], args[args.index('--journal') + 1])
        elif args[:2] == ['sch', 'save']:
            return {'ok': True, 'result': {'saved': True}}
        elif args[:2] == ['doc', 'reload']:
            if self.rekey_attributes:
                for attribute in self.current['result']['pagePrimitives']['attributes']:
                    attribute['primitiveId'] = 'reloaded-' + attribute['primitiveId']
            return {'status': 'passed', 'saved': True, 'closed': True, 'reloaded': self.target['document_uuid']}
        elif args[:2] == ['sch', 'connectivity']:
            return self.expected
        elif kwargs.get('check') is False:
            data = {'ok': True} if args[1] == 'layout-lint' else {'ok': True, 'result': {'passed': True}}
            return {'exit_code': 0, 'stdout': json.dumps(data), 'stderr': ''}
        return {'exit_code': 0, 'stdout': 'synthetic', 'stderr': ''}

    def first_queue(self):
        meta = {'project': self.target['project_uuid'], 'doc': self.target['document_uuid']}
        steps = []
        for part in self.final['result']['components']:
            if part['componentType'] == 'part':
                ref = part['designator']
                steps.append({'id': 'place-' + ref, 'action': 'schematic.component.place',
                              'payload': {'designator': ref}, 'capture': {ref: '$.primitiveId'}})
        steps.append({'id': 'verify-physical-pins-before-wiring', 'action': 'schematic.components.list', 'payload': {}})
        for wire in self.final['result']['wires']:
            steps.append({'id': 'wire-' + wire['primitiveId'], 'action': 'schematic.wire.create',
                          'payload': {'points': [[wire['x0'], wire['y0']], [wire['x1'], wire['y1']]]}})
        for part in self.final['result']['components']:
            for pin in part.get('pins', []):
                if pin['noConnected']:
                    steps.append({'id': 'nc-' + pin['primitiveId'], 'action': 'schematic.pin.set_no_connect',
                                  'payload': {'designator': part['designator'], 'pins': [pin['pinNumber']],
                                              'noConnected': True}})
        steps.append({'id': 'frame', 'run': 'sch frame apply', 'flags': {'data': '{}'}})
        return {'version': 1, 'requireFullExecution': True, 'meta': meta, 'steps': steps}

    def apply(self, queue_path, journal_path):
        queue = load(queue_path)
        rows = [{'playbookSha256': file_digest(queue_path), 'project': self.target['project_uuid']}]
        parts = {part.get('designator'): part for part in self.final['result']['components']}
        for position, step in enumerate(queue['steps'], 1):
            action, ident = step.get('action'), step['id']
            capture = {}
            if action == 'schematic.component.place':
                ref = step['payload']['designator']
                capture[ref] = parts[ref]['primitiveId']
            elif action == 'schematic.wire.create':
                capture['eso-wire'] = ident.removeprefix('wire-')
                if self.timeout:
                    self.current = unwire(self.final, count=1)
                    self.current['result']['components'][1]['pins'][0]['net'] = 'IN'
                    break
            elif 'eso-placed-readback' in step.get('capture', {}):
                capture['eso-placed-readback'] = json.dumps(unwire(self.final)['result'])
            elif 'eso-drawing-readback' in step.get('capture', {}):
                capture['eso-drawing-readback'] = json.dumps(self.final['result'])
            elif 'eso-saved-readback' in step.get('capture', {}):
                capture['eso-saved-readback'] = json.dumps(self.final['result'])
            elif step.get('run') == 'sch frame apply':
                page = self.final['result']['pagePrimitives']
                capture['eso-frames'] = json.dumps([{'rectangle': {'id': page['rectangles'][0]['primitiveId']},
                                                     'title': {'id': page['texts'][0]['primitiveId']}}])
            rows.append({'idx': position, 'id': ident, 'status': 'ok', 'ms': 1, 'captured': capture})
        Path(journal_path).write_text(''.join(json.dumps(row) + '\n' for row in rows))
        if self.timeout:
            raise Refused('CLI timed out: write may have landed')
        self.current = copy.deepcopy(self.final)


class PartialMarkerAdapter(SimulatedDrawingAdapter):
    """A captured marker succeeds, then a later wire and failure readback fail."""
    fail_read = False
    omit_drawing_checkpoint = False
    wrong_created_net = False
    recheckpoint_mutation = False

    def first_queue(self):
        queue = super().first_queue()
        position = next(i for i, step in enumerate(queue['steps'])
                        if step['id'] == 'verify-physical-pins-before-wiring') + 1
        queue['steps'].insert(position, {'id': 'marker-main', 'action': 'schematic.power.connect_pin',
            'payload': {'pinX': 200, 'pinY': 100, 'direction': 'up', 'offset': 20,
                        'kind': 'net_port_bi', 'net': 'MAIN'}})
        if self.recheckpoint_mutation:
            queue['steps'].insert(position + 1, {'id': 'extra-drawing-read', 'action': 'schematic.components.list',
                'payload': {}, 'capture': {'eso-drawing-readback': '$'}})
        return queue

    def snapshot(self, target):
        if self.fail_read:
            self.fail_read = False
            raise OSError('synthetic readback unavailable')
        return super().snapshot(target)

    def apply(self, queue_path, journal_path):
        queue = load(queue_path)
        rows = [{'playbookSha256': file_digest(queue_path), 'project': self.target['project_uuid'],
                 'doc': self.target['document_uuid']}]
        parts = {part.get('designator'): part for part in self.final['result']['components']}
        for position, step in enumerate(queue['steps'], 1):
            captures = {}
            if step.get('action') == 'schematic.component.place':
                ref = step['payload']['designator']
                captures[ref] = parts[ref]['primitiveId']
            elif 'eso-placed-readback' in step.get('capture', {}):
                captures['eso-placed-readback'] = json.dumps(unwire(self.final)['result'])
            elif step['id'] == 'marker-main':
                self.current = unwire(self.final)
                data, page = self.current['result'], self.current['result']['pagePrimitives']
                page['components'] = copy.deepcopy(page['components'])
                data['wires'] = [{'primitiveId': 'captured-wire', 'net': 'MAIN',
                                 'x0': 200, 'y0': 100, 'x1': 200, 'y1': 120}]
                page['wires'] = [{'primitiveId': 'captured-wire', 'Net': 'MAIN', 'Line': [200, 100, 200, 120]}]
                marker = {'primitiveId': 'captured-marker', 'componentType': 'netport',
                          'net': 'WRONG' if self.wrong_created_net else 'MAIN', 'x': 200, 'y': 120,
                          'bbox': {'minX': 195, 'minY': 115, 'maxX': 205, 'maxY': 125},
                          'pins': [{'primitiveId': 'marker-pin', 'x': 200, 'y': 120, 'net': None}]}
                data['components'].append(marker)
                page['components'].append({k: v for k, v in marker.items() if k not in ('pins', 'bbox')})
                page['attributes'].extend([
                    {'primitiveId': 'marker-text', 'ParentPrimitiveId': 'captured-marker', 'Key': 'Net',
                     'Value': 'MAIN', 'FontSize': 10, 'ValueVisible': True},
                    {'primitiveId': 'marker-pin-text', 'ParentPrimitiveId': 'marker-pin', 'Key': 'Pin', 'Value': '1'}])
                data['connectivitySummary'].update(wires=1, netports=1)
                captures = {'eso-wire': 'captured-wire', 'eso-marker': 'captured-marker'}
            elif 'eso-drawing-readback' in step.get('capture', {}) and not self.omit_drawing_checkpoint:
                captures['eso-drawing-readback'] = json.dumps(self.current['result'])
                if self.recheckpoint_mutation and step['id'].startswith('eso-read-drawing-after-'):
                    next(a for a in self.current['result']['pagePrimitives']['attributes']
                         if a['primitiveId'] == 'marker-text')['Value'] = 'MANUAL'
            else:
                rows.append({'idx': position, 'id': step['id'], 'status': 'fail',
                             'error': 'synthetic known failure', 'captured': {}})
                break
            rows.append({'idx': position, 'id': step['id'], 'status': 'ok', 'captured': captures})
        Path(journal_path).write_text(''.join(json.dumps(row) + '\n' for row in rows))
        self.fail_read = True
        raise Refused('synthetic known failure')


class DrawingRevisionTests(unittest.TestCase):
    def start(self, directory, *, timeout=False):
        from eso.workflow import Workflow
        root = Path(directory)
        target, contract, plan, source, wired = revision_fixture()
        before = unwire(wired, remove_parts=True)
        approval = confirm(contract, digest(contract), 'synthetic human')
        adapter = SimulatedDrawingAdapter(target, before, wired, source['connectivity'], timeout=timeout)
        source_path, blank_path = root / 'source.json', root / 'blank.json'
        dump(source_path, source)
        dump(blank_path, before)
        parent = Workflow(adapter, root / 'parent')
        parent.prepare(contract, approval, plan, source_path, blank_path)
        if timeout:
            with self.assertRaisesRegex(Refused, 'timed out'):
                parent.execute()
        else:
            parent.execute()
            self.assertEqual(parent.verify()['state'], 'verified')
        return target, source_path, adapter, parent

    def test_partial_marker_requires_unchanged_native_semantics(self):
        from eso.workflow import Workflow
        for mutation in (None, 'pin net cache', 'net', 'position', 'attribute', 'pin attribute',
                         'missing checkpoint', 'wrong created net', 'attribute recheckpoint'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                target, contract, plan, source, wired = revision_fixture()
                blank = unwire(wired, remove_parts=True)
                adapter = PartialMarkerAdapter(target, blank, wired, source['connectivity'])
                adapter.omit_drawing_checkpoint = mutation == 'missing checkpoint'
                adapter.wrong_created_net = mutation == 'wrong created net'
                adapter.recheckpoint_mutation = mutation == 'attribute recheckpoint'
                dump(root / 'source.json', source)
                dump(root / 'blank.json', blank)
                parent = Workflow(adapter, root / 'parent')
                parent.prepare(contract, confirm(contract, digest(contract), 'synthetic human'), plan,
                               root / 'source.json', root / 'blank.json')
                with self.assertRaisesRegex(Refused, 'synthetic known failure'):
                    parent.execute()
                marker = next(p for p in adapter.current['result']['components'] if p['primitiveId'] == 'captured-marker')
                native = next(p for p in adapter.current['result']['pagePrimitives']['components'] if p['primitiveId'] == 'captured-marker')
                if mutation == 'net':
                    marker['net'] = native['net'] = 'MANUAL'
                if mutation == 'position':
                    marker.update(x=900, y=900)
                    native.update(x=900, y=900)
                    marker['pins'][0].update(x=900, y=900)
                if mutation == 'pin net cache':
                    marker['pins'][0]['net'] = 'MAIN'
                if mutation in ('attribute', 'pin attribute'):
                    pid = 'marker-text' if mutation == 'attribute' else 'marker-pin-text'
                    next(a for a in adapter.current['result']['pagePrimitives']['attributes'] if a['primitiveId'] == pid)['Value'] = 'MANUAL'
                dump(root / 'fresh.json', adapter.current)
                child = Workflow(adapter, root / 'child')
                if mutation in (None, 'pin net cache'):
                    child.prepare_revision(parent.root, root / 'source.json', root / 'fresh.json')
                    self.assertIn('captured-marker', load(child.root / 'revision-proof.json')['owned_drawing'])
                    self.assertTrue((child.root / 'bundle.json').is_file())
                else:
                    with self.assertRaises(Refused):
                        child.prepare_revision(parent.root, root / 'source.json', root / 'fresh.json')
                    self.assertFalse((child.root / 'bundle.json').exists())
                self.assertEqual(sum('--journal' in call for call in adapter.calls), 1)

    def test_normal_verified_revision_uses_new_apply_and_verification(self):
        from eso.workflow import Workflow
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, source, adapter, parent = self.start(root)
            old_final = copy.deepcopy(adapter.current)
            after = adapter.final
            after['result']['components'][1]['x'] += 5
            after['result']['pagePrimitives']['attributes'][0]['X'] += 5
            before_path = root / 'fresh.json'
            dump(before_path, old_final)
            original_bundle = (parent.root / 'bundle.json').read_bytes()
            revised = Workflow(adapter, root / 'revised')
            revised.prepare_revision(parent.root, source, before_path)
            self.assertEqual(load(revised.root / 'revision-proof.json')['parent_was_verified'], True)
            self.assertFalse((revised.root / 'verification.json').exists())
            revised.execute()
            adapter.rekey_attributes = True
            report = revised.verify()
            self.assertEqual(report['state'], 'verified')
            self.assertNotEqual(report['attempt'], load(parent.root / 'verification.json')['attempt'])
            self.assertEqual((parent.root / 'bundle.json').read_bytes(), original_bundle)
            dump(before_path, adapter.current)
            adapter.final = copy.deepcopy(adapter.current)
            next_revision = Workflow(adapter, root / 'second')
            next_revision.prepare_revision(revised.root, source, before_path)
            self.assertTrue(load(next_revision.root / 'revision-proof.json')['parent_was_verified'])
            self.assertEqual(adapter.current['result']['components'][1]['primitiveId'],
                             old_final['result']['components'][1]['primitiveId'])
            self.assertEqual(adapter.current['result']['pagePrimitives']['attributes'][0]['Value'],
                             old_final['result']['pagePrimitives']['attributes'][0]['Value'])
            self.assertNotEqual(adapter.current['result']['pagePrimitives']['attributes'][0]['primitiveId'],
                                old_final['result']['pagePrimitives']['attributes'][0]['primitiveId'])

    def test_timeout_landed_wire_recovers_to_prepared_without_replaying(self):
        from eso.workflow import Workflow
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, source, adapter, parent = self.start(root, timeout=True)
            fresh = root / 'fresh.json'
            dump(fresh, adapter.current)
            adapter.timeout = False
            for name in ('recovery-1', 'recovery-2'):
                recovered = Workflow(adapter, root / name)
                recovered.prepare_revision(parent.root, source, fresh)
                proof = load(recovered.root / 'revision-proof.json')
                self.assertEqual(proof['owned_drawing'], {'old-wire-0': 'wires'})
                self.assertEqual(proof['progress']['landed_step'], 'wire-old-wire-0')
                changes = load(recovered.root / 'revision-connectivity-edits.json')
                self.assertIn({'net': 'MAIN', 'noConnected': False}, [row['after'] for row in changes['pins']])
                self.assertEqual(changes['beforeSha256'], file_digest(recovered.root / 'before.json'))
                self.assertEqual(changes['sourceSha256'], file_digest(recovered.root / 'composition.json'))
            self.assertEqual(sum(call[:2] == ('sch', 'apply') and '--journal' in call for call in adapter.calls), 1)
            recovered.execute()
            self.assertEqual(recovered.verify()['state'], 'verified')

    def test_readback_mutations_and_wrong_window_leave_new_run_incomplete(self):
        from eso.workflow import Workflow
        for mutation in ('unknown drawing', 'part attribute', 'pin net', 'window', 'unbound journal'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                _, source, adapter, parent = self.start(root, timeout=True)
                if mutation == 'unknown drawing':
                    adapter.current['result']['pagePrimitives']['texts'] = [
                        {'primitiveId': 'unknown-native-text', 'Content': 'Manual note'}]
                elif mutation == 'part attribute':
                    adapter.current['result']['pagePrimitives']['attributes'][0]['Value'] = 'Manual edit'
                elif mutation == 'pin net':
                    adapter.current['result']['components'][1]['pins'][0]['net'] = 'WRONG'
                elif mutation == 'window':
                    adapter.current['context']['windowId'] = 'different-window'
                else:
                    journal = parent.root / 'apply.journal.jsonl'
                    journal.write_text(journal.read_text().replace('wire', 'other-wire'))
                    rows = [json.loads(row) for row in journal.read_text().splitlines()]
                    rows[0]['playbookSha256'] = '0' * 64
                    journal.write_text(''.join(json.dumps(row) + '\n' for row in rows))
                before = root / 'fresh.json'
                dump(before, adapter.current)
                output = Workflow(adapter, root / 'refused')
                with self.assertRaises(Refused):
                    output.prepare_revision(parent.root, source, before)
                self.assertEqual(load(output.root / 'revision-status.json')['state'], 'incomplete')
                self.assertFalse((output.root / 'bundle.json').exists())


    def test_cli_revision_reads_parent_inputs(self):
        from io import StringIO
        from unittest.mock import patch
        from eso.cli import main

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, source, adapter, parent = self.start(root)
            before = root / 'fresh.json'
            dump(before, adapter.current)
            args = ['prepare', '--revision-of', str(parent.root), '--composition', str(source),
                    '--before', str(before), '--run-dir', str(root / 'cli-revision')]
            with patch('eso.cli.EasyEDA', return_value=adapter), patch('sys.stdout', new_callable=StringIO) as out:
                self.assertEqual(main(args), 0)
                self.assertEqual(json.loads(out.getvalue())['state'], 'prepared')
            with patch('sys.stderr', new_callable=StringIO), self.assertRaises(SystemExit):
                main([*args, '--placement-run', str(parent.root)])

    def test_baseline_change_blocks_execute_and_old_geometry_cannot_verify(self):
        from eso.workflow import Workflow
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, source, adapter, parent = self.start(root)
            before = root / 'fresh.json'
            dump(before, adapter.current)
            output = Workflow(adapter, root / 'revised')
            output.prepare_revision(parent.root, source, before)
            adapter.current['result']['pagePrimitives']['attributes'][0]['Value'] = 'out-of-band'
            writes = sum('--journal' in call for call in adapter.calls)
            with self.assertRaisesRegex(Refused, 'target changed after preparation'):
                output.execute()
            self.assertEqual(sum('--journal' in call for call in adapter.calls), writes)
            with self.assertRaises(Refused):
                output.verify()
            self.assertEqual(load(output.root / 'verification.json')['state'], 'incomplete')

    def test_source_change_before_compile_stops_candidate(self):
        from eso.workflow import Workflow
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, source, adapter, parent = self.start(root)
            altered_source = load(source)
            altered_source['connectivity']['components'][0]['device']['deviceUuid'] = 'f' * 32
            dump(source, altered_source)
            before = root / 'fresh.json'
            dump(before, adapter.current)
            compiles = sum(call[:2] == ('sch', 'compose') for call in adapter.calls)
            with self.assertRaisesRegex(Refused, 'changes the parent circuit'):
                Workflow(adapter, root / 'refused').prepare_revision(parent.root, source, before)
            self.assertEqual(sum(call[:2] == ('sch', 'compose') for call in adapter.calls), compiles)
