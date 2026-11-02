import copy
import tempfile
import unittest
from pathlib import Path
from eso.adapter import EasyEDA
from eso.bridge import DESIGNATOR_SOURCE, DesignBridge, build_layout_input, observed_parts
from eso.core import Refused, confirm, digest, dump, file_digest, load
from eso.requirements import contract_from
from eso.planner import bind_plan
from eso.workflow import Workflow, validate_plan

TARGET = {'project_name': 'ESO-TEST-BRIDGE', 'project_uuid': 'synthetic-project', 'document_uuid': 'synthetic-page'}


def fixture(root):
    contract = contract_from({'schema_version': 1, 'circuit_type': 'generic', 'function_description': 'SYNTHETIC resistor fixture',
                              'input_voltage_v': 3.3, 'power_source': 'external-regulated', 'target_project': TARGET['project_name']}, TARGET)
    plan = {'schema_version': 1, 'contract_sha256': digest(contract), 'template': 'synthetic-test-only',
            'components': [{'id': 'fixture-resistor', 'ref': 'R1', 'query': 'SYNTHETIC', 'role': 'test only', 'module': 'fixture-module',
                            'pins': [{'number': '1', 'name': '1', 'net': 'A', 'nc_reason': None}, {'number': '2', 'name': '2', 'net': 'B', 'nc_reason': None}]}],
            'modules': [{'id': 'fixture-module', 'core_ref': 'R1', 'title': 'TEST ONLY'}],
            'evidence': [{'url': 'synthetic:test-only', 'section': 'no hardware claim'}], 'notes': ['SYNTHETIC: never execute on real EDA']}
    bindings = {'R1': {'libraryUuid': '1' * 32, 'deviceUuid': '2' * 32, 'verified_model': 'SYNTHETIC',
                        'expected_name': 'SYNTHETIC', 'evidence': 'synthetic:test-only'}}
    sheet = {'componentType': 'sheet', 'primitiveId': 'fixture-sheet', 'bbox': {'minX': 0, 'minY': 0, 'maxX': 1170, 'maxY': 825}}
    part = {'componentType': 'part', 'primitiveId': 'fixture-native-part', 'designator': 'R1', 'uniqueId': 'fixture-unique-id',
            'device': {'libraryUuid': '1' * 32, 'uuid': '2' * 32}, 'otherProperty': {'EasyEDA Agent Component ID': 'fixture-resistor'},
            'x': 100, 'y': 100, 'rotation': 0, 'mirror': False, 'bbox': {'minX': 90, 'minY': 90, 'maxX': 110, 'maxY': 110},
            'pinsAvailable': True, 'pins': [{'primitiveId': 'fixture-pin-1', 'pinNumber': '1', 'pinName': '1', 'x': 70, 'y': 100, 'rotation': 180, 'net': '', 'noConnected': False},
                                          {'primitiveId': 'fixture-pin-2', 'pinNumber': '2', 'pinName': '2', 'x': 130, 'y': 100, 'rotation': 0, 'net': '', 'noConnected': False}]}
    page = {k: [] for k in ('arcs', 'attributes', 'buses', 'circles', 'objects', 'polygons', 'rectangles', 'texts', 'wires')}
    page['components'] = [sheet, part]
    snapshot = {'ok': True, 'context': {'projectUuid': TARGET['project_uuid'], 'documentUuid': TARGET['document_uuid'], 'documentType': 'schematic'},
                'result': {'components': [sheet, part], 'wires': [], 'pagePrimitives': page, 'pinNetsAvailable': True, 'wiresAvailable': True}}
    labels = {'schemaVersion': 1, 'projectId': TARGET['project_uuid'], 'documentId': TARGET['document_uuid'], 'count': 1,
              'designators': [{'id': 'fixture-designator', 'parentId': part['primitiveId'], 'key': 'Designator', 'value': 'R1',
                               'visible': True, 'source': DESIGNATOR_SOURCE, 'bbox': {'minX': 90, 'minY': 125, 'maxX': 110, 'maxY': 135}}]}
    svg = root / 'original.svg'; svg.write_text('<svg>synthetic-test-only</svg>')
    geometry = {'ok': True, 'result': {'sourceSha256': file_digest(svg), 'coordinateSystem': 'schematic-raw-y-up-vector-centerlines',
                                     'sheet': {'bbox': sheet['bbox'], 'innerBBox': {'minX': 10, 'minY': 10, 'maxX': 1160, 'maxY': 815}}, 'keepouts': []}}
    return contract, plan, bindings, snapshot, labels, geometry, svg


class BridgeTests(unittest.TestCase):
    def test_duplicate_native_pin_ids_and_cross_part_owner_swaps_refuse(self):
        for duplicate in (True, False):
            with tempfile.TemporaryDirectory() as directory:
                c,p,b,s,l,g,svg=fixture(Path(directory));first=s['result']['components'][1]
                if duplicate:
                    first['pins'][1]['primitiveId']=first['pins'][0]['primitiveId']
                else:
                    second=copy.deepcopy(first);second.update(primitiveId='fixture-native-part-2',designator='R2')
                    second['otherProperty']['EasyEDA Agent Component ID']='fixture-resistor-2'
                    for pin in second['pins']:pin['primitiveId']='second-'+pin['primitiveId']
                    s['result']['components'].append(second)
                    planned=copy.deepcopy(p['components'][0]);planned.update(id='fixture-resistor-2',ref='R2');p['components'].append(planned)
                    b['R2']=copy.deepcopy(b['R1'])
                page=s['result']['pagePrimitives'];page['components']=s['result']['components']
                page['pins']=[{'primitiveId':pin['primitiveId'],'componentId':part['primitiveId']}
                              for part in s['result']['components'] if part['componentType']=='part' for pin in part['pins']]
                if duplicate:page['pins']=page['pins'][:1]
                else:
                    page['pins'][0]['componentId'],page['pins'][2]['componentId']=page['pins'][2]['componentId'],page['pins'][0]['componentId']
                with self.subTest(duplicate=duplicate),self.assertRaises(Refused):observed_parts(p,s,b,TARGET)

    def test_bridge_retains_raw_snapshot_and_authors_target_nets(self):
        with tempfile.TemporaryDirectory() as directory:
            c,p,b,s,l,g,svg = fixture(Path(directory)); original = copy.deepcopy(s)
            source = build_layout_input(c,p,b,s,l,g,svg)
            self.assertEqual(s, original)
            self.assertEqual([x['net'] for x in source['measurements'][0]['pins']], ['A', 'B'])
            self.assertEqual(source['measurements'][0]['textBboxes'][0], l['designators'][0]['bbox'])

    def test_missing_geometry_or_wrong_pin_identity_refuses(self):
        for mutation in ('bbox', 'pin', 'identity', 'label', 'svg'):
            with tempfile.TemporaryDirectory() as directory:
                c,p,b,s,l,g,svg = fixture(Path(directory))
                part = s['result']['components'][1]
                if mutation == 'bbox': del part['bbox']
                if mutation == 'pin': part['pins'].pop()
                if mutation == 'identity': part['device']['uuid'] = '3' * 32
                if mutation == 'label': l['designators'][0]['visible'] = False
                if mutation == 'svg': svg.write_text('changed')
                with self.subTest(mutation=mutation), self.assertRaises(Refused): build_layout_input(c,p,b,s,l,g,svg)

    def test_unknown_pin_net_is_not_filled_with_a_fake_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            c,p,b,s,l,g,svg = fixture(Path(directory)); s['result']['components'][1]['pins'][0]['net'] = None
            with self.assertRaises(Refused): build_layout_input(c,p,b,s,l,g,svg)

    def test_gpio_numbers_are_exact_but_separated_function_aliases_are_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            c,p,b,s,l,g,svg=fixture(Path(directory))
            p['components'][0]['pins'][0]['name']='PA1'
            s['result']['components'][1]['pins'][0]['pinName']='PA10'
            with self.assertRaisesRegex(Refused,'function differs'):build_layout_input(c,p,b,s,l,g,svg)
            s['result']['components'][1]['pins'][0]['pinName']='PA1/ADC'
            build_layout_input(c,p,b,s,l,g,svg)

    def test_changed_measurement_evidence_refuses_prepare(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);c,p,b,s,l,g,svg=fixture(root);a=FakeBridgeAdapter(c,p,b,s,l,g)
            approval=confirm(c,digest(c),'SYNTHETIC HUMAN');bridge=DesignBridge(a,root/'stage')
            bridge.stage(c,approval,p,b);bridge.collect()
            (bridge.root/'measured-sheet.svg').write_text('changed after collection')
            with self.assertRaisesRegex(Refused,'measurement evidence changed'):
                Workflow(a,root/'run').prepare(c,approval,p,bridge.root/'composition.json',bridge.root/'measured-snapshot.json',bridge.root)

    def test_collect_rejects_incomplete_or_unowned_page(self):
        for mutation in ('missing-page', 'unknown-wires', 'unowned-object'):
            with tempfile.TemporaryDirectory() as directory:
                root=Path(directory);c,p,b,s,l,g,svg=fixture(root);a=FakeBridgeAdapter(c,p,b,s,l,g)
                bridge=DesignBridge(a,root/'stage')
                bridge.stage(c,confirm(c,digest(c),'SYNTHETIC HUMAN'),p,b)
                if mutation == 'missing-page': del a.snapshot_value['result']['pagePrimitives']
                if mutation == 'unknown-wires': a.snapshot_value['result']['wiresAvailable']=False; a.snapshot_value['result'].pop('wires')
                if mutation == 'unowned-object': a.snapshot_value['result']['pagePrimitives']['objects']=[{'primitiveId':'not-owned'}]
                with self.subTest(mutation=mutation),self.assertRaises(Refused): bridge.collect()
                self.assertEqual(load(bridge.root/'stage.json')['state'],'measurement-incomplete')

    def test_partial_placement_captures_actual_state_and_prevents_replay(self):
        class Partial(FakeBridgeAdapter):
            def run(self,args,**kwargs):
                if args[:2]==['sch','apply'] and '--dry-run' not in args:
                    self.placed=True; raise Refused('synthetic partial placement')
                return super().run(args,**kwargs)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);c,p,b,s,l,g,svg=fixture(root);a=Partial(c,p,b,s,l,g)
            bridge=DesignBridge(a,root/'stage');approval=confirm(c,digest(c),'SYNTHETIC HUMAN')
            with self.assertRaises(Refused):bridge.stage(c,approval,p,b)
            self.assertEqual(load(bridge.root/'stage.json')['state'],'incomplete')
            self.assertTrue((bridge.root/'failure-readback.json').exists())
            with self.assertRaises(Refused):bridge.stage(c,approval,p,b)

    def test_incompatible_upstream_is_refused_before_placement(self):
        with tempfile.TemporaryDirectory() as directory:
            c,p,b,s,l,g,svg = fixture(Path(directory)); a = FakeBridgeAdapter(c,p,b,s,l,g)
            a.old_incompatible = True
            with self.assertRaisesRegex(Refused, 'before placement'):
                DesignBridge(a, Path(directory)/'stage').stage(c, confirm(c,digest(c),'SYNTHETIC HUMAN'),p,b)
            self.assertFalse(a.placed)
            self.assertFalse(any(call[:2] == ['sch','apply'] and '--dry-run' not in call for call in a.calls))

    def test_simulated_stage_collect_prepare_preserves_instances(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);c,p,b,s,l,g,svg=fixture(root);a=FakeBridgeAdapter(c,p,b,s,l,g)
            approval=confirm(c,digest(c),'SYNTHETIC HUMAN'); bridge=DesignBridge(a,root/'stage')
            self.assertEqual(bridge.stage(c,approval,p,b)['state'],'placed')
            self.assertEqual(bridge.collect()['state'],'layout-compiled')
            workflow=Workflow(a,root/'run')
            bundle=workflow.prepare(c,approval,p,bridge.root/'composition.json',bridge.root/'measured-snapshot.json',bridge.root)
            self.assertEqual(bundle['state'],'prepared')
            self.assertTrue(any(call[:2] == ['sch','compose'] and '--preserve-instances' in call for call in a.calls))
            self.assertIn('staging-proof.json',bundle['artifacts'])


class FakeBridgeAdapter:
    def __init__(self,c,p,b,s,l,g):
        self.contract,self.plan,self.bindings,self.snapshot_value,self.labels,self.geometry=c,p,b,s,l,g
        self.placed=False;self.old_incompatible=False;self.calls=[]
    def guard(self,target): pass
    def snapshot(self,target):
        raw=copy.deepcopy(self.snapshot_value)
        if not self.placed:
            raw['result']['components']=raw['result']['components'][:1]
            raw['result']['pagePrimitives']['components']=raw['result']['components']
            if self.old_incompatible:raw['result']['pagePrimitives']['pins']=[]
        return raw
    def run(self,args,**kwargs):
        self.calls.append(list(map(str,args)))
        if args==['--version']:return {'stdout':'easyeda-agent v1.16.0' if self.old_incompatible else 'easyeda-agent synthetic-compatible'}
        if args[:3]==['lib','device','get']:return {'ok':True,'result':{'device':{'uuid':'2'*32,'libraryUuid':'1'*32,'name':'SYNTHETIC'}}}
        if args[:2]==['sch','materialize']:
            dump(args[args.index('--out')+1],{'version':1,'meta':{'project':TARGET['project_uuid'],'doc':TARGET['document_uuid']},'steps':[{'id':'save','action':'schematic.save'}]})
        elif args[:2]==['sch','apply'] and '--dry-run' not in args:self.placed=True
        elif args[:2]==['sch','save']:return {'ok':True,'result':{'saved':True}}
        elif args[:2]==['sch','designator-geometry']:return self.labels
        elif args[:2]==['sch','export-image']:
            Path(args[args.index('--out')+1]).write_text('<svg>synthetic-test-only</svg>')
            return {'ok':True,'result':{},'context':self.snapshot_value['context']}
        elif args[:2]==['sch','sheet-geometry']:return self.geometry
        elif args[:2]==['sch','lib-layout']:
            source=load(args[args.index('--from')+1]);dump(args[args.index('--out')+1],{'schemaVersion':1,'connectivity':source['connectivity']})
        elif args[:2]==['sch','compose']:
            dump(args[args.index('--out')+1],{'synthetic':True})
            dump(args[args.index('--playbook')+1],{'version':1,'meta':{'project':TARGET['project_uuid'],'doc':TARGET['document_uuid']},'steps':[{'id':'save','action':'schematic.save'}]})
        return {'exit_code':0,'stdout':'synthetic receipt','stderr':''}
