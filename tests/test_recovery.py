"""Interrupted placement recovery with synthetic ownership and no live EDA."""
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
