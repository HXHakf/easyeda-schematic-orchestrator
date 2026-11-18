"""Task 3 review regressions. All real upstream calls remain offline."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from eso.adapter import EasyEDA
from eso.core import Refused, confirm, digest, dump, load
from eso.main_paths import check_audit_target, check_compiled_paths, check_scene_page
from eso.planner import bind_plan
from eso.requirements import contract_from
from eso.workflow import check_source
from test_main_paths import fixture, topology
import test_upstream_cli as upstream


def prepared_inputs(root, defect=None):
    plan, bindings, source, before = fixture()
    contract = contract_from({'schema_version': 1, 'circuit_type': 'generic',
                              'function_description': 'Synthetic primary-path fix regression',
                              'target_project': upstream.TARGET['project_name']}, upstream.TARGET)
    plan['contract_sha256'] = digest(contract)
    module = source['modules'][0]
    if defect == 'marker-branch':
        component = next(c for c in plan['components'] if c['ref'] == 'R1')
        component['pins'][1].update(net='MAIN', nc_reason=None)
        next(p for p in module['placements'] if p['designator'] == 'R1')['pins'][1]['net'] = 'MAIN'
        module['wires'].append({'net': 'MAIN', 'points': [[130,200],[155,200],[155,110],[200,110]]})
        next(f for f in module['flags'] if f['net'] == 'MAIN').update(direction='up', offset=40)
    if defect == 'local-open':
        source['connectionMode'] = 'net-labels'
        module['wires'] = [w for w in module['wires'] if w['net'] != 'IN']
        module['wires'] += [{'net': 'IN', 'points': [[70,y],[50,y]]} for y in (100,200)]
        module['flags'] = [f for f in module['flags'] if f['net'] != 'IN']
        module['flags'] += [{'net': 'IN', 'kind': 'net_port_bi', 'pinX': 50, 'pinY': y, 'direction': 'left', 'offset':20} for y in (100,200)]
    if defect == 'terminal':
        plan['components'].append({'id':'terminal','ref':'R3','query':'SYNTHETIC ONLY','role':'synthetic',
                                   'module':'terminal','pins':[{'number':'1','name':'1','net':'TERM','nc_reason':None}]})
        plan['modules'].append({'id':'terminal','core_ref':'R3','title':'Terminal'})
        plan['drawing_groups'].append({'id':'terminal','title':'Terminal','modules':['terminal']})
        bindings['R3'] = copy.deepcopy(bindings['U1'])
        source['modules'].append({'id':'terminal','title':'Terminal','wires':[],'flags':[],
            'placements':[{'designator':'R3','value':'SYNTHETIC ONLY','x':100,'y':100,'rotation':0,'mirror':False,
                'bbox':{'minX':90,'minY':90,'maxX':110,'maxY':110},
                'pins':[{'number':'1','name':'1','net':'TERM','x':70,'y':100}]}],
            'terminals':[{'designator':'R3','pin':'1','direction':'left','kind':'net_port_bi'}]})
    source['connectivity'] = bind_plan(plan, bindings, upstream.TARGET)
    page = before['result']['pagePrimitives']
    for key in page:
        page[key] = page[key][:1] if key == 'components' else []
    before['result'].update(components=page['components'], wires=[], count=1)
    before['result']['connectivitySummary']['wires'] = 0
    for name, data in [('plan',plan),('contract',contract),('approval',confirm(contract,digest(contract),'synthetic test actor')),
                       ('source',source),('before',before)]:
        dump(root / (name+'.json'), data)
    return plan, source


class MainPathFixTests(unittest.TestCase):
    def test_effective_scope_is_fixed_for_signals_and_default_global_rails(self):
        plan, _, _, _ = fixture()
        from eso.main_paths import audit_projection
        nets = [{'id': n, 'name': n, 'scope':'page', 'pins':[[c['id'],p['number']] for c in plan['components'] for p in c['pins'] if p['net']==n]} for n in ('IN','MAIN','OUT')]
        target = dict(audit_projection(plan,nets), nets=nets)
        target['nets'][0]['scope'] = 'global'
        with self.assertRaisesRegex(Refused,'scope'): check_audit_target(plan,target)
        for c in plan['components']:
            for p in c['pins']:
                if p['net'] == 'IN': p['net'] = 'GND'
        nets[0].update(id='GND',name='GND',scope='global')
        target = dict(audit_projection(plan,nets),nets=nets)
        target['nets'][0]['scope'] = 'page'
        with self.assertRaisesRegex(Refused,'scope'): check_audit_target(plan,target)

    def test_single_document_binding_covers_every_geometry_class(self):
        target = upstream.TARGET
        snapshot = {'context':{'projectUuid':target['project_uuid'],'documentUuid':target['document_uuid']}}
        scene = {key:[{'page':'alias'}] for key in ('pins','wires','junctions','labels','bodies','texts','moduleFrames')}
        check_scene_page(scene,snapshot,target)
        for key in scene:
            changed=copy.deepcopy(scene);changed[key][0]['page']='invented'
            with self.subTest(key=key),self.assertRaisesRegex(Refused,'single observed'):check_scene_page(changed,snapshot,target)

    def test_both_source_group_title_formats_are_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            plan,source=prepared_inputs(Path(directory))
            for key in ('canonical','authored'):
                changed=copy.deepcopy(source)
                if key=='canonical':changed['connectivity']['modules'][0]['name']='UNRELATED'
                else:changed['modules'][0]['title']='UNRELATED'
                with self.subTest(key=key),self.assertRaisesRegex(Refused,'group'):check_source(plan,changed,upstream.TARGET)

    def test_standalone_auditor_commands_need_only_the_standard_library(self):
        scripts=Path(__file__).resolve().parents[1]/'extras/schematic-verified-layout/scripts'
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for modern in (True,False):
                target,scene=topology()
                if not modern:
                    target.pop('primary_paths');target.pop('drawing_groups')
                    target['modules']=[{'id':'legacy','members':['a','b'],'core':['a'],'direct':[]}]
                dump(root/'target.json',target);dump(root/'scene.json',scene)
                common=['--target',str(root/'target.json'),'--scene',str(root/'scene.json')]
                commands=[[str(scripts/'circuit_audit.py'),'scene',*common,'--out',str(root/('circuit-'+str(modern)+'.json'))],
                          [str(scripts/'readability_audit.py'),*common,'--out',str(root/('readability-'+str(modern)+'.json'))]]
                for args in commands:
                    result=subprocess.run([sys.executable,'-S',*args],capture_output=True,text=True,cwd=root)
                    self.assertEqual(result.returncode,0,result.stderr+result.stdout)
                    self.assertTrue(json.loads(result.stdout)['passed'])

    @unittest.skipUnless(os.environ.get('EASYEDA_BIN'),'requires actual offline CLI')
    def test_actual_compiler_marker_leads_and_local_direct(self):
        for defect in (None,'terminal','marker-branch','local-open'):
            with self.subTest(defect=defect),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);plan,source=prepared_inputs(root,defect)
                adapter=EasyEDA(os.environ['EASYEDA_BIN'],root/'receipts')
                adapter.run(['sch','compose','--from',root/'source.json','--before',root/'before.json',
                             '--out',root/'computed.json','--playbook',root/'queue.json','--replace'],json_output=False)
                computed,queue=load(root/'computed.json'),load(root/'queue.json')
                # Actual typed dry-run accepts these topologies. ESO adds the exact endpoint gate.
                adapter.run(['sch','apply',root/'queue.json','--dry-run'],json_output=False)
                if defect=='local-open':
                    with self.assertRaisesRegex(Refused,'direct-required'):check_source(plan,source,upstream.TARGET)
                else:check_source(plan,source,upstream.TARGET)
                if defect in ('marker-branch','local-open'):
                    with self.assertRaisesRegex(Refused,'compiled physical'):check_compiled_paths(plan,computed,queue,upstream.TARGET['document_uuid'])
                else:
                    check_compiled_paths(plan,computed,queue,upstream.TARGET['document_uuid'])
                    self.assertTrue(computed['layout']['flags'])
                changed=copy.deepcopy(computed);changed['layout']['frames'][0]['title']='UNRELATED'
                with self.assertRaisesRegex(Refused,'titles'):check_compiled_paths(plan,changed,queue,upstream.TARGET['document_uuid'])
