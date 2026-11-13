"""Full state-machine exercise with synthetic transport, never a live acceptance claim."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from test_knowledge import fixture as inputs
from test_bridge import fixture as geometry_fixture, FakeBridgeAdapter, TARGET, DESIGNATOR_SOURCE
from eso.bridge import DesignBridge
from eso.core import confirm, digest, load, Refused
from eso.knowledge_design import make_bindings
from eso.requirements import contract_from
from eso.planner import make_plan, bind_plan
from eso.workflow import Workflow
from eso.cli import delivery_manifest


def design(root, topic):
    r=inputs(root,topic); r['target_project']=TARGET['project_name']
    for i,m in enumerate(r['knowledge']['materials'],1): m['library']['deviceUuid']=f'{i:032x}'
    c=contract_from(r,TARGET); p=make_plan(c); b=make_bindings(c)
    _,_,_,s,l,g,_=geometry_fixture(root)
    original=s['result']['components'][1]; parts=[]; labels=[]
    for i,part in enumerate(p['components']):
        observed=copy.deepcopy(original)
        observed.update(primitiveId='synthetic-'+part['id'],designator=part['ref'],uniqueId='unique-'+part['id'],x=100+i*150)
        observed['device']={'libraryUuid':b[part['ref']]['libraryUuid'],'uuid':b[part['ref']]['deviceUuid']}
        observed['otherProperty']['EasyEDA Agent Component ID']=part['id']
        observed['pins']=[{'primitiveId':observed['primitiveId']+'-'+pin['number'],
                           'pinNumber':pin['number'],'pinName':pin['name'],
                           'x':80+i*150+j*20,'y':100,'rotation':0,'net':'','noConnected':False}
                          for j,pin in enumerate(part['pins'])]
        parts.append(observed)
        labels.append({'id':'label-'+part['id'],'parentId':observed['primitiveId'],
                       'key':'Designator','value':part['ref'],'visible':True,'source':DESIGNATOR_SOURCE,
                       'bbox':{'minX':90+i*150,'minY':125,'maxX':110+i*150,'maxY':135}})
    s['result']['components']=[s['result']['components'][0],*parts]
    s['result']['pagePrimitives']['components']=s['result']['components']
    l.update(count=len(labels),designators=labels)
    return c,p,b,s,l,g


class SwitchTransport(FakeBridgeAdapter):
    def run(self,args,**kwargs):
        if args[:3]==['lib','device','get']:
            self.calls.append(list(map(str,args)))
            entry=next(b for b in self.bindings.values() if b['deviceUuid']==args[args.index('--uuid')+1])
            return {'ok':True,'result':{'device':{'uuid':entry['deviceUuid'],'libraryUuid':entry['libraryUuid'],'name':entry['expected_name']}}}
        if args[:2]==['doc','reload']:
            self.calls.append(list(map(str,args)))
            return {'status':'passed','saved':True,'closed':True,'reloaded':TARGET['document_uuid']}
        if args[:2]==['sch','connectivity']:
            self.calls.append(list(map(str,args)))
            return self.actual
        if args[0]=='sch' and kwargs.get('check') is False:
            raw={'ok':True} if args[1]=='layout-lint' else {'ok':True,'result':{'passed':True}}
            return {'exit_code':0,'stdout':json.dumps(raw),'stderr':''}
        return super().run(args,**kwargs)


class KnowledgePipelineTests(unittest.TestCase):
    def test_both_switches_reach_save_reload_and_readback_in_simulation(self):
        for topic in ('npn-low-side-switch','nmos-low-side-switch'):
            with self.subTest(topic=topic), tempfile.TemporaryDirectory() as directory:
                root=Path(directory); c,p,b,s,l,g=design(root,topic)
                adapter=SwitchTransport(c,p,b,s,l,g)
                approval=confirm(c,digest(c),'SYNTHETIC HUMAN')
                stage=DesignBridge(adapter,root/'stage')
                self.assertEqual(stage.stage(c,approval,p,b)['state'],'placed')
                self.assertEqual(stage.collect()['state'],'layout-compiled')
                flow=Workflow(adapter,root/'run')
                flow.prepare(c,approval,p,stage.root/'composition.json',stage.root/'measured-snapshot.json',stage.root)
                adapter.actual=bind_plan(p,b,TARGET)
                self.assertEqual(flow.execute()['state'],'applied-unverified')
                self.assertEqual(flow.verify()['state'],'verified')
                self.assertIn(['doc','reload',TARGET['document_uuid'],'--json'],adapter.calls)
                self.assertEqual(delivery_manifest(flow.root,root/'delivery.json')['state'],'verified')
                # A changed physical net must invalidate the previous passing readback.
                adapter.actual['connections'][0]['netId']='net-GND'
                self.assertEqual(flow.verify()['state'],'incomplete')
                self.assertEqual(delivery_manifest(flow.root,root/'delivery.json')['state'],'incomplete')

    def test_changed_document_prevents_stage_and_revokes_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); c,p,b,s,l,g=design(root,'npn-low-side-switch')
            a=SwitchTransport(c,p,b,s,l,g); approval=confirm(c,digest(c),'SYNTHETIC HUMAN')
            path=Path(c['requirements']['knowledge']['materials'][0]['datasheet']['path'])
            path.write_text('changed source')
            with self.assertRaises(Refused): DesignBridge(a,root/'stage').stage(c,approval,p,b)
            self.assertEqual(a.calls,[])

    @unittest.skipUnless(__import__('os').environ.get('EASYEDA_BIN'), 'set EASYEDA_BIN for real offline compiler')
    def test_switch_materialization_uses_real_upstream_offline(self):
        import os
        from eso.adapter import EasyEDA
        from eso.core import dump
        from eso.workflow import validate_queue
        for topic in ('npn-low-side-switch','nmos-low-side-switch'):
            with self.subTest(topic=topic), tempfile.TemporaryDirectory() as directory:
                root=Path(directory); c,p,b,_,_,_=design(root,topic)
                dump(root/'input.json',bind_plan(p,b,TARGET))
                adapter=EasyEDA(os.environ['EASYEDA_BIN'],root/'receipts')
                adapter.run(['sch','materialize',root/'input.json','--out',root/'queue.json'],json_output=False)
                queue=load(root/'queue.json'); validate_queue(queue,TARGET)
                receipt=adapter.run(['sch','apply',root/'queue.json','--dry-run'],json_output=False)
                self.assertEqual(receipt['exit_code'],0)
                placed=[s for s in queue['steps'] if s.get('action')=='schematic.component.place']
                self.assertEqual(len(placed),4)
