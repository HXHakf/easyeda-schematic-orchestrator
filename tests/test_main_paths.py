"""Exact main-path authorization and shared drawing projection regressions."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest

from eso.core import Refused, digest, dump, file_digest, load, validate
from eso.main_paths import (audit_projection, check_audit_target, check_native_frames,
                            project_drawing_groups, validate_primary_paths)
from eso.planner import bind_plan
from eso.workflow import check_source, validate_electrical_inventory
import test_upstream_cli as upstream
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'extras/schematic-verified-layout/scripts'))
import circuit_audit as ca
import readability_audit as ra


def fixture():
    logical, path, source, before = upstream.joint_drawing_fixture()
    components = []
    edges = {(e['componentId'], e['pinNumber']): e['netId'] for e in source['connectivity']['connections']}
    for c in source['connectivity']['components']:
        module = next(m['id'] for m in logical if c['id'] in (m['core'], m['peripheral']))
        row = {'id': c['id'], 'ref': c['ref'], 'query': 'SYNTHETIC ONLY', 'role': 'synthetic', 'module': module,
               'pins': [{'number': p['number'], 'name': p['name'], 'net': edges.get((c['id'], p['number'])),
                         'nc_reason': 'synthetic unused' if p['noConnected'] else None} for p in c['pins']]}
        if c['id'] == 'peripheral-a':
            row['attach'] = {'pin': '1', 'ref': 'U1', 'pin_number': '1'}
        if c['id'] == 'peripheral-b':
            row['attach'] = {'pin': '2', 'ref': 'U2', 'pin_number': '2'}
        components.append(row)
    plan = {'schema_version': 1, 'contract_sha256': '0' * 64, 'template': 'synthetic',
            'components': components, 'modules': [{'id': m['id'], 'core_ref': r, 'title': m['id']}
                                                    for m, r in zip(logical, ['U1', 'U2'])],
            'primary_paths': [path], 'drawing_groups': [{'id': 'joint-group', 'title': 'SYNTHETIC JOINT GROUP',
                                                        'modules': ['logic-a', 'logic-b']}],
            'evidence': [{'url': 'synthetic:fixture', 'section': 'test only'}], 'notes': []}
    bindings = {c['ref']: {'libraryUuid': '1' * 32, 'deviceUuid': '2' * 32,
                          'verified_model': c['query'], 'evidence': 'synthetic'} for c in components}
    return plan, bindings, source, before


def topology():
    target = {'schemaVersion': 1, 'components': [{'id': c, 'ref': r, 'pins': ['1']} for c, r in [('a','U1'),('b','U2')]],
              'nets': [{'id': 'opaque-main', 'name': 'MAIN', 'scope': 'page', 'pins': [['a','1'],['b','1']]}], 'nc': [],
              'modules': [{'id': c, 'members': [c], 'core': [c], 'direct': []} for c in ('a','b')],
              'primary_paths': [{'id': 'p', 'title': 'MAIN', 'links': [{'net': 'opaque-main',
                 'from': {'componentId': 'a', 'pinNumber': '1'}, 'to': {'componentId': 'b', 'pinNumber': '1'}}]}],
              'drawing_groups': [{'id': 'g', 'title': 'MAIN', 'modules': ['a','b']}]}
    scene = {'schemaVersion': 1, 'units': 'raw', 'grid': 5, 'evidence': 'synthetic-fixture', 'geometryScope': 'topology-only',
             'pins': [{'pin': [c,'1'], 'at': [x,0], 'page': 'P1', 'direction': d} for c,x,d in [('a',0,'right'),('b',40,'left')]],
             'wires': [{'id':'w','page':'P1','points': [[0,0],[40,0]]}], 'labels': [], 'junctions': [],
             'bodies': [], 'texts': [], 'textInventory': []}
    return target, scene


class MainPathTests(unittest.TestCase):
    def test_shared_projection_and_legacy(self):
        p,b,source,_ = fixture()
        validate(p,'plan')
        validate_electrical_inventory(p)
        projected = project_drawing_groups(p)
        self.assertEqual(projected[0]['coreComponents'], ['core-a','core-b'])
        canonical = bind_plan(p,b,upstream.TARGET)
        self.assertEqual(canonical['modules'], projected)
        check_source(p, {'connectivity': canonical, 'modules': source['modules']}, upstream.TARGET)
        canonical['modules'][0]['coreComponents'].pop()
        with self.assertRaises(Refused): check_source(p, {'connectivity': canonical, 'modules': source['modules']}, upstream.TARGET)
        del p['drawing_groups']; del p['primary_paths']
        self.assertEqual(len(project_drawing_groups(p)), 2)

    def test_source_paths_keep_explicit_global_rail_scope(self):
        p, bindings, source, _ = fixture()
        p['nets'] = [{'name': name, 'scope': 'global' if name == 'IN' else 'local',
                      'role': 'power' if name == 'IN' else 'signal',
                      **({'voltage_v': 3.3} if name == 'IN' else {})} for name in ('IN', 'MAIN', 'OUT')]
        canonical = bind_plan(p, bindings, upstream.TARGET)
        check_source(p, {'connectivity': canonical, 'modules': source['modules']}, upstream.TARGET)

    def test_source_gate_rejects_unapproved_extra_main_branch(self):
        p, bindings, source, _ = fixture()
        p['modules'].append({'id': 'extra', 'core_ref': 'C1', 'title': 'Extra'})
        p['drawing_groups'][0]['modules'].append('extra')
        p['components'].append({'id': 'extra', 'ref': 'C1', 'query': 'SYNTHETIC ONLY', 'role': 'synthetic',
                                'module': 'extra', 'pins': [{'number': '1', 'name': '1', 'net': 'MAIN', 'nc_reason': None}]})
        bindings['C1'] = copy.deepcopy(bindings['U1'])
        source['modules'][0]['placements'].append({'designator': 'C1', 'pins': [{'number': '1', 'x': 200, 'y': 140}]})
        source['modules'][0]['wires'].append({'net': 'MAIN', 'points': [[200, 100], [200, 140]]})
        canonical = bind_plan(p, bindings, upstream.TARGET)
        with self.assertRaisesRegex(Refused, 'module-physical-crossing'):
            check_source(p, {'connectivity': canonical, 'modules': source['modules']}, upstream.TARGET)

    def test_bad_endpoint_net_nc_group_and_logical_attach(self):
        for defect in ('pin','component','net','nc','duplicate-group','missing-group','attach'):
            p,_,_,_ = fixture()
            link=p['primary_paths'][0]['links'][0]
            if defect=='pin': link['from']['pinNumber']='99'
            if defect=='component': link['from']['componentId']='absent'
            if defect=='net': link['net']='OUT'
            if defect=='nc': link['from']={'componentId':'peripheral-a','pinNumber':'2'}
            if defect=='duplicate-group': p['drawing_groups']*=2
            if defect=='missing-group': p['drawing_groups'][0]['modules'].pop()
            if defect=='attach': p['components'][1]['attach']={'pin':'1','ref':'U2','pin_number':'1'}
            with self.subTest(defect=defect), self.assertRaises(Refused): validate_electrical_inventory(p)

    def test_exact_physical_links_and_unapproved_branches(self):
        target,scene=topology()
        self.assertTrue(ca.audit_scene(target,scene)['passed'])
        readable = ra.audit_readability(target, scene)
        self.assertTrue(readable['passed'])
        self.assertEqual(readable['metrics']['modulePhysicalBridges'], 1)
        self.assertFalse(ca.physical_path_findings(target,scene['pins'],ca.scene_segments(scene),
            ca.contact_graph(ca.scene_segments(scene),scene['pins'],[],[],with_labels=False)))
        for defect in ('extra-module','same-group-branch','another-net','page','labels'):
            t,s=copy.deepcopy(target),copy.deepcopy(scene)
            if defect in ('extra-module','same-group-branch'):
                t['components'].append({'id':'c','ref':'R1','pins':['1']})
                if defect=='extra-module':
                    t['modules'].append({'id':'c','members':['c'],'core':['c'],'direct':[]})
                    t['drawing_groups'][0]['modules'].append('c')
                else:t['modules'][0]['members'].append('c')
                t['nets'][0]['pins'].append(['c','1'])
                s['pins'].append({'pin':['c','1'],'page':'P1','at':[20,20],'direction':'down'})
                s['wires'].append({'id':'branch','page':'P1','points':[[20,0],[20,20]]})
            if defect=='another-net':
                for c in t['components']:c['pins'].append('2')
                t['nets'].append({'id':'second','name':'SECOND','scope':'page','pins':[['a','2'],['b','2']]})
                for c,x,d in [('a',0,'right'),('b',40,'left')]:s['pins'].append({'pin':[c,'2'],'page':'P1','at':[x,20],'direction':d})
                s['wires'].append({'id':'second','page':'P1','points':[[0,20],[40,20]]})
            if defect=='page':s['pins'][1]['page']='P2'
            if defect=='labels':
                s['wires']=[{'id':'a','page':'P1','points':[[0,0],[10,0]]},{'id':'b','page':'P1','points':[[30,0],[40,0]]}]
                s['labels']=[{'id':c,'net':'opaque-main','page':'P1','at':[x,0]} for c,x in [('a',10),('b',30)]]
            with self.subTest(defect=defect):
                self.assertFalse(ca.audit_scene(t,s)['passed'])
                self.assertFalse(ra.audit_readability(t,s)['passed'])

    def test_reviewed_local_direct_allows_decoupling_endpoint_only(self):
        t,s=topology()
        t['components'].append({'id':'c','ref':'C1','pins':['1']})
        t['modules'][0]['members'].append('c')
        t['modules'][0]['direct']=[[['a','1'],['c','1']]]
        t['nets'][0]['pins'].append(['c','1'])
        s['pins'].append({'pin':['c','1'],'page':'P1','at':[20,20],'direction':'down'})
        s['wires'].append({'id':'branch','page':'P1','points':[[20,0],[20,20]]})
        self.assertTrue(ca.audit_scene(t,s)['passed'])
        t['modules'][0]['direct']=[]
        self.assertFalse(ca.audit_scene(t,s)['passed'])

    def test_declared_chain_does_not_authorize_same_name_other_page(self):
        target, scene = topology()
        target['components'].append({'id': 'c', 'ref': 'U3', 'pins': ['1']})
        target['modules'].append({'id': 'c', 'members': ['c'], 'core': ['c'], 'direct': []})
        target['drawing_groups'][0]['modules'].append('c')
        target['nets'][0]['pins'].append(['c', '1'])
        target['primary_paths'][0]['links'].append({'net': 'opaque-main',
            'from': {'componentId': 'b', 'pinNumber': '1'},
            'to': {'componentId': 'c', 'pinNumber': '1'}})
        scene['pins'].append({'pin': ['c', '1'], 'page': 'P1', 'at': [20, 20], 'direction': 'down'})
        scene['wires'].append({'id': 'branch', 'page': 'P1', 'points': [[20, 0], [20, 20]]})
        self.assertTrue(ca.audit_scene(target, scene)['passed'])
        target['primary_paths'][0]['links'][-1]['net'] = 'other-page'
        target['nets'].append({'id': 'other-page', 'name': 'MAIN', 'scope': 'page', 'pins': [['c', '1']]})
        self.assertFalse(ca.audit_scene(target, scene)['passed'])

    def test_target_reconciliation_rejects_permission_changes(self):
        p,_,_,_=fixture()
        nets=[{'id':'opaque-'+name,'name':name,'scope':'page','pins':[[c['id'],x['number']] for c in p['components'] for x in c['pins'] if x['net']==name]} for name in ('IN','MAIN','OUT')]
        target=dict(audit_projection(p,nets),nets=nets)
        check_audit_target(p,target)
        self.assertEqual(target['primary_paths'][0]['links'][0]['net'],'opaque-MAIN')
        for defect in ('members','core','direct','path','group'):
            t=copy.deepcopy(target)
            if defect in ('members','core'):t['modules'][0][defect].append('core-b')
            if defect=='direct':t['modules'][0]['direct'].append([['core-a','2'],['core-b','1']])
            if defect=='path':t['primary_paths'][0]['links'][0]['to']['pinNumber']='2'
            if defect=='group':t['drawing_groups'][0]['title']='changed'
            with self.subTest(defect=defect),self.assertRaises(Refused):check_audit_target(p,t)

    def test_native_frame_identity_bounds_count_and_real_title(self):
        p,_,_,_=fixture()
        scene={'moduleFrames':[{'drawingGroupId':'joint-group','primitiveId':'rect','bbox':[0,0,100,100],'titleTextId':'title'}]}
        data={'pagePrimitives':{'rectangles':[{'primitiveId':'rect','TopLeftX':0,'TopLeftY':100,'Width':100,'Height':100,'Rotation':0}],
                                'texts':[{'primitiveId':'title','Content':'SYNTHETIC JOINT GROUP'}]}}
        check_native_frames(p,scene,data)
        for defect in ('count','bbox','title','identity'):
            s,d=copy.deepcopy(scene),copy.deepcopy(data)
            if defect=='count':d['pagePrimitives']['rectangles']=[]
            if defect=='bbox':s['moduleFrames'][0]['bbox'][0]=5
            if defect=='title':d['pagePrimitives']['texts'][0]['Content']='invented'
            if defect=='identity':s['moduleFrames'][0]['moduleId']='logic-a'
            with self.subTest(defect=defect),self.assertRaises(Refused):check_native_frames(p,s,d)

    @unittest.skipUnless(os.environ.get('EASYEDA_BIN'), 'requires installed offline CLI')
    def test_measured_joint_bridge_real_layout_and_compose(self):
        from eso.bridge import build_layout_input, DESIGNATOR_SOURCE
        from eso.requirements import contract_from
        from eso.adapter import EasyEDA
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            p,b,_,snapshot=fixture()
            contract=contract_from({'schema_version':1,'circuit_type':'generic','function_description':'synthetic joint path','target_project':upstream.TARGET['project_name']},upstream.TARGET)
            p['contract_sha256']=digest(contract)
            native=snapshot['result'];native['wires']=[]
            for key in ('wires','rectangles','texts','attributes'):native['pagePrimitives'][key]=[]
            labels=[]
            for part in native['components']:
                if part['componentType']!='part':continue
                for pin in part['pins']:pin.update(net='',noConnected=False)
                labels.append({'id':'label-'+part['designator'],'parentId':part['primitiveId'],'key':'Designator','value':part['designator'],
                               'visible':True,'source':DESIGNATOR_SOURCE,'bbox':{'minX':part['x']-10,'minY':part['y']+25,'maxX':part['x']+10,'maxY':part['y']+35}})
            svg=root/'sheet.svg';svg.write_text('<svg/>')
            sheet=native['components'][0]['bbox']
            geometry={'ok':True,'result':{'sourceSha256':file_digest(svg),'coordinateSystem':'schematic-raw-y-up-vector-centerlines',
                                'sheet':{'bbox':sheet,'innerBBox':sheet},'keepouts':[]}}
            designators={'projectId':upstream.TARGET['project_uuid'],'documentId':upstream.TARGET['document_uuid'],'count':len(labels),'designators':labels}
            source=build_layout_input(contract,p,b,snapshot,designators,geometry,svg)
            self.assertEqual(source['layoutModules'][0]['coreComponentId'],'core-a')
            self.assertEqual(source['layoutModules'][0]['netPolicies']['net-MAIN'],'direct')
            dump(root/'input.json',source)
            adapter=EasyEDA(os.environ['EASYEDA_BIN'],root/'receipts')
            adapter.run(['sch','lib-layout','--from',root/'input.json','--out',root/'source.json'],json_output=False)
            composition=load(root/'source.json')
            check_source(p,composition,upstream.TARGET)
            self.assertEqual(composition['connectivity']['modules'][0]['coreComponents'],['core-a','core-b'])
            blank = copy.deepcopy(snapshot)
            blank['result']['components'] = blank['result']['components'][:1]
            blank['result']['pagePrimitives']['components'] = blank['result']['components']
            blank['result']['pagePrimitives']['pins'] = []
            blank['result']['connectivitySummary']['wires'] = 0
            dump(root/'before.json', blank)
            adapter.run(['sch','compose','--from',root/'source.json','--before',root/'before.json','--out',root/'computed.json','--playbook',root/'queue.json','--replace'],json_output=False)
            computed=load(root/'computed.json')
            self.assertEqual(len(computed['layout']['frames']),1)
            queue = load(root/'queue.json')
            final = next(step['expectSchematic'] for step in queue['steps'] if step['id'] == 'verify-all-pins-nets-nc')
            self.assertEqual(final['drawing']['wires'], computed['layout']['wires'])
            self.assertTrue(any(wire['net'] == 'MAIN' for wire in final['drawing']['wires']))
            self.assertEqual(final['ownership']['modules'], source['connectivity']['modules'])
            adapter.run(['sch','apply',root/'queue.json','--dry-run'],json_output=False)
