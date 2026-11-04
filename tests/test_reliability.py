import copy
import tempfile
import unittest
from pathlib import Path

from eso.core import Refused
from eso.reconcile import reconcile
from eso.workflow import is_blank, validate_plan
from eso.planner import bind_plan
from eso.bridge import build_layout_input
from test_workflow import design, blank, TARGET
from test_bridge import fixture


class WindowRoutingTests(unittest.TestCase):
    def test_explicit_window_routes_live_calls_without_changing_offline_commands(self):
        from unittest.mock import patch
        from eso.adapter import EasyEDA
        with patch('eso.adapter.shutil.which', return_value='fake-easyeda'), patch('eso.adapter.subprocess.run') as run:
            run.return_value.returncode = 0
            run.return_value.stdout = '{}'
            run.return_value.stderr = ''
            adapter = EasyEDA('fake-easyeda', window='chosen-window')
            adapter.run(['sch','connectivity'], target=TARGET)
            live = run.call_args.args[0]
            self.assertEqual(live[live.index('--window')+1], 'chosen-window')
            self.assertEqual(live[live.index('--project')+1], TARGET['project_uuid'])
            self.assertEqual(live[live.index('--doc')+1], TARGET['document_uuid'])
            adapter.run(['sch','apply','probe-only.json','--dry-run'], json_output=False)
            self.assertNotIn('--window', run.call_args.args[0])


class ReconciliationTests(unittest.TestCase):
    def test_invalid_references_nc_edges_and_missing_arrays_fail(self):
        _, _, expected = design()
        for mutation in ('component', 'pin', 'net', 'nc', 'missing', 'duplicate-net', 'duplicate-name', 'split'):
            actual = copy.deepcopy(expected)
            if mutation == 'component': actual['connections'][0]['componentId'] = 'absent'
            if mutation == 'pin': actual['connections'][0]['pinNumber'] = '999'
            if mutation == 'net': actual['connections'][0]['netId'] = 'absent'
            if mutation == 'nc':
                c, p = next((c, p) for c in actual['components'] for p in c['pins'] if p.get('noConnected'))
                actual['connections'].append(dict(componentId=c['id'], pinNumber=p['number'], netId='absent'))
            if mutation == 'missing': del actual['connections']
            if mutation == 'duplicate-net': actual['nets'].append(copy.deepcopy(actual['nets'][0]))
            if mutation == 'duplicate-name': actual['nets'].append(dict(actual['nets'][0], id='other-net'))
            if mutation == 'split':
                original = actual['nets'][0]
                actual['nets'].append(dict(original, id='split-net', scope='local'))
                actual['connections'][0]['netId'] = 'split-net'
            with self.subTest(mutation=mutation): self.assertFalse(reconcile(expected, actual)['passed'])

    def test_extra_edge_and_nc_connection_to_valid_net_fail(self):
        _, _, expected = design()
        for nc in (True, False):
            actual = copy.deepcopy(expected)
            cid, num = next((c['id'], p['number']) for c in actual['components'] for p in c['pins'] if p.get('noConnected')) if nc else ('absent', '1')
            actual['connections'].append(dict(componentId=cid, pinNumber=num, netId=actual['nets'][0]['id']))
            self.assertFalse(reconcile(expected, actual)['passed'])

    def test_extra_unused_net_and_source_scope_drift_fail(self):
        from eso.workflow import check_source
        _, plan, expected = design(); actual = copy.deepcopy(expected)
        actual['nets'].append({'id': 'extra', 'name': 'EXTRA', 'scope': 'global'})
        self.assertFalse(reconcile(expected, actual)['passed'])
        actual = copy.deepcopy(expected); actual['nets'][0]['scope'] = 'local'
        with self.assertRaises(Refused): check_source(plan, {'connectivity': actual}, TARGET)

    def test_net_ids_may_change_but_scope_cannot(self):
        _, _, expected = design(); actual = copy.deepcopy(expected)
        remap = {n['id']: 'new-' + n['id'] for n in actual['nets']}
        for n in actual['nets']: n['id'] = remap[n['id']]
        for edge in actual['connections']: edge['netId'] = remap[edge['netId']]
        self.assertTrue(reconcile(expected, actual)['passed'])
        actual['nets'][0]['scope'] = 'local'
        self.assertFalse(reconcile(expected, actual)['passed'])


class BlankTests(unittest.TestCase):
    def test_native_sheet_inventory_can_differ_from_hydrated_details(self):
        data = blank()['result']
        data['components'] = [{'componentType':'sheet', 'primitiveId':'sheet-1', 'bbox':{'minX':0}}]
        data['pagePrimitives']['components'] = [{'componentType':'sheet', 'primitiveId':'sheet-1'}]
        self.assertTrue(is_blank(data))

    def test_unknown_missing_and_inconsistent_inventory_refused(self):
        for change in ('unknown', 'missing', 'mismatch', 'missing-components', 'missing-wires', 'missing-attributes'):
            data = blank()['result']; data['pagePrimitives']['components'] = []
            if change == 'unknown': data['pagePrimitives']['future'] = []
            if change == 'missing': del data['pagePrimitives']['components']
            if change == 'mismatch': data['pagePrimitives']['components'] = [{'componentType': 'part', 'primitiveId': 'hidden'}]
            if change == 'missing-components': del data['components']
            if change == 'missing-wires': del data['wires']
            if change == 'missing-attributes': del data['pagePrimitives']['attributes']
            with self.subTest(change=change): self.assertFalse(is_blank(data))


class MeasurementAndPowerTests(unittest.TestCase):
    def test_all_visible_attribute_bounds_and_unknown_visibility(self):
        with tempfile.TemporaryDirectory() as directory:
            c,p,b,s,l,g,svg = fixture(Path(directory))
            attr = {'primitiveId': 'value-label', 'ParentPrimitiveId': 'fixture-native-part',
                    'Key': 'Name', 'Value': 'SYNTHETIC', 'KeyVisible': False, 'ValueVisible': True,
                    'bbox': {'minX': 120, 'minY': 125, 'maxX': 170, 'maxY': 135}}
            s['result']['pagePrimitives']['attributes'].append(attr)
            source = build_layout_input(c,p,b,s,l,g,svg)
            self.assertIn(attr['bbox'], source['measurements'][0]['textBboxes'])
            del attr['bbox']
            with self.assertRaises(Refused): build_layout_input(c,p,b,s,l,g,svg)
            attr['ValueVisible'] = None
            with self.assertRaises(Refused): build_layout_input(c,p,b,s,l,g,svg)

    def test_designator_visible_key_requires_full_attribute_measurement(self):
        with tempfile.TemporaryDirectory() as directory:
            c,p,b,s,l,g,svg = fixture(Path(directory))
            s['result']['pagePrimitives']['attributes'].append({
                'primitiveId': l['designators'][0]['id'], 'ParentPrimitiveId': 'fixture-native-part',
                'KeyVisible': True, 'ValueVisible': True})
            with self.assertRaises(Refused): build_layout_input(c,p,b,s,l,g,svg)

    def test_explicit_power_domains_and_unknown_definitions(self):
        with tempfile.TemporaryDirectory() as directory:
            c,p,b,s,l,g,svg = fixture(Path(directory))
            p['nets'] = [{'name': 'A', 'role': 'power', 'scope': 'global', 'voltage_v': 5},
                         {'name': 'B', 'role': 'ground', 'scope': 'local', 'voltage_v': 0}]
            validate_plan(p,c)
            ir = bind_plan(p,b,dict(project_uuid='p', document_uuid='d'))
            self.assertEqual(ir['nets'][0]['role'], 'power')
            self.assertEqual(ir['nets'][0]['scope'], 'global')
            source = build_layout_input(c,p,b,s,l,g,svg)
            self.assertEqual(source['layoutModules'][0]['netPolicies']['net-A'], 'local_power')
            p['nets'].append(dict(name='unused', role='signal', scope='local'))
            with self.assertRaises(Refused): validate_plan(p,c)


class CompatibilityTests(unittest.TestCase):
    def test_probe_uses_actual_paper_bounds(self):
        from eso.compatibility import probe_snapshot
        from eso.core import load
        from test_bridge import FakeBridgeAdapter, TARGET as BRIDGE_TARGET
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); c,p,b,s,l,g,svg=fixture(root)
            s['result']['components'][0]['bbox'] = {'minX':-100, 'minY':-100, 'maxX':1500, 'maxY':1000}
            adapter=FakeBridgeAdapter(c,p,b,s,l,g)
            before=adapter.snapshot(BRIDGE_TARGET)
            probe_snapshot(adapter,before,BRIDGE_TARGET,root/'probe')
            self.assertEqual(load(root/'probe'/'synthetic-composition.json')['sheet'],before['result']['components'][0]['bbox'])

    def test_probe_uses_unmodified_snapshot_and_never_executes(self):
        from eso.compatibility import probe_snapshot
        from test_bridge import FakeBridgeAdapter, TARGET as BRIDGE_TARGET
        from eso.core import load
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); c,p,b,s,l,g,svg = fixture(root)
            adapter = FakeBridgeAdapter(c,p,b,s,l,g)
            before = adapter.snapshot(BRIDGE_TARGET); original = copy.deepcopy(before)
            report = probe_snapshot(adapter, before, BRIDGE_TARGET, root/'probe')
            self.assertTrue(report['passed'])
            self.assertEqual(before, original)
            self.assertEqual(load(root/'probe'/'before.json'), original)
            self.assertTrue(any(call[:2] == ['sch', 'compose'] for call in adapter.calls))
            self.assertFalse(any(call[:2] == ['sch', 'apply'] and '--dry-run' not in call for call in adapter.calls))

    def test_failed_probe_keeps_receipt_and_stops_before_placement(self):
        from test_bridge import FakeBridgeAdapter, DesignBridge
        from eso.core import confirm, digest, load
        class Incompatible(FakeBridgeAdapter):
            def run(self,args,**kwargs):
                if args[:2] == ['sch', 'compose']: raise Refused('new version but unsupported schema')
                return super().run(args,**kwargs)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); c,p,b,s,l,g,svg = fixture(root)
            adapter = Incompatible(c,p,b,s,l,g)
            with self.assertRaisesRegex(Refused, 'before placement'):
                DesignBridge(adapter, root/'stage').stage(c,confirm(c,digest(c),'SYNTHETIC'),p,b)
            self.assertFalse(adapter.placed)
            self.assertFalse(load(root/'stage'/'compatibility'/'report.json')['passed'])


class DiagnosticsTests(unittest.TestCase):
    def test_truncated_utf8_journal_preserves_prior_records(self):
        from eso.diagnostics import diagnose
        from eso.core import dump
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); c,p,_ = design()
            dump(root/'contract.json',c); dump(root/'plan.json',p)
            (root/'apply.journal.jsonl').write_bytes(b'{"step":"first"}\n{"error":"\xe4\xb8')
            report = diagnose(root)
            journal = report['journals']['apply.journal.jsonl']
            self.assertEqual(journal['valid_records'],1)
            self.assertEqual(journal['errors'][0]['line'],2)

    def test_live_diagnosis_uses_only_reads_and_preserves_run(self):
        from eso.diagnostics import diagnose
        from eso.core import dump
        class ReadOnlyAdapter:
            def __init__(self, ir): self.ir,self.calls=ir,[]
            def snapshot(self,target): self.calls.append('snapshot'); return blank()
            def run(self,args,**kwargs): self.calls.append(args); return self.ir
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); c,p,ir = design()
            dump(root/'contract.json',c); dump(root/'plan.json',p); dump(root/'expected.json',ir)
            before = {f.name:f.read_bytes() for f in root.iterdir()}
            adapter = ReadOnlyAdapter(ir)
            report = diagnose(root,adapter=adapter)
            self.assertEqual(adapter.calls,['snapshot',['sch','connectivity']])
            self.assertEqual(report['level'],'live-read-only-diagnosis')
            self.assertEqual(before,{f.name:f.read_bytes() for f in root.iterdir()})

    def test_diagnosis_reads_partial_state_and_preserves_files(self):
        from eso.diagnostics import diagnose
        from eso.core import dump
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); c,p,ir = design()
            dump(root/'contract.json', c); dump(root/'plan.json', p); dump(root/'expected.json', ir)
            dump(root/'bundle.json', {'state': 'incomplete', 'target': TARGET})
            (root/'execution-started.lock').write_text('keep')
            (root/'apply.journal.jsonl').write_text('{"step":"first"}\n{"partial":')
            snapshot = blank(); actual = copy.deepcopy(ir); actual['connections'].pop()
            before = {f.name: f.read_bytes() for f in root.iterdir()}
            report = diagnose(root, snapshot=snapshot, connectivity=actual)
            self.assertEqual(set(report['components']['missing']), {c['ref'] for c in p['components']})
            self.assertFalse(report['reconciliation']['passed'])
            self.assertEqual(report['journals']['apply.journal.jsonl']['valid_records'], 1)
            self.assertTrue(report['issues'])
            self.assertFalse(report['resume_allowed'])
            self.assertEqual(before, {f.name: f.read_bytes() for f in root.iterdir()})

    def test_wrong_target_and_unavailable_snapshot_are_explicit(self):
        from eso.diagnostics import diagnose
        from eso.core import dump
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); c,p,ir = design()
            dump(root/'contract.json', c); dump(root/'plan.json', p)
            report = diagnose(root)
            self.assertTrue(report['unverified'])
            snapshot = blank(); snapshot['context']['documentUuid'] = 'different'
            report = diagnose(root, snapshot=snapshot)
            self.assertTrue(any('target' in issue for issue in report['issues']))
            self.assertNotIn('components', report)

    def test_cli_offline_diagnosis_needs_no_upstream(self):
        import contextlib
        import io
        import json
        from eso.cli import main
        from eso.core import dump
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); c,p,ir = design()
            dump(root/'contract.json', c); dump(root/'plan.json', p)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(['--easyeda', 'MISSING', 'diagnose', '--run-dir', str(root)])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())['state'], 'diagnostic-only')
