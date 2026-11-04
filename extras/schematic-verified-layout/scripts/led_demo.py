"""Generate a reproducible LED circuit model and synthetic geometry, then verify both."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
from circuit_audit import audit_scene, validate_target
from readability_audit import audit_readability


def build(spacing=120):
    if spacing < 100 or spacing % 5:
        raise ValueError('spacing must be >=100 raw and on the 5-raw grid')
    lib = '0819f05c4eef4c71ace90d822a990e87'
    components = [
        {'id':'input', 'ref':'J1', 'pins':['1','2'], 'part':'KF301-5.0-2P', 'lcsc':'C474881',
         'device':{'libraryUuid':lib,'uuid':'41ddecaaf01b4c1aa92ec4bfe22149b7'}},
        {'id':'limit', 'ref':'R1', 'pins':['1','2'], 'part':'0402WGF1001TCE', 'lcsc':'C11702',
         'device':{'libraryUuid':lib,'uuid':'65abbad5fa864933b552e0f016ece0db'}},
        {'id':'indicator', 'ref':'LED1', 'pins':['1','2'], 'part':'KT-0805Y', 'lcsc':'C2296',
         'device':{'libraryUuid':lib,'uuid':'06303f8c50b646d88d0dd08d2ec9692c'}},
    ]
    target = {'schemaVersion':1, 'components':components,
              'nets':[
                  {'id':'power','name':'3V3','scope':'global','pins':[['input','1'],['limit','1']]},
                  {'id':'anode','name':'LED1_N2','scope':'page','pins':[['limit','2'],['indicator','1']]},
                  {'id':'ground','name':'GND','scope':'global','pins':[['input','2'],['indicator','2']]},
              ], 'nc':[],
              'modules':[
                  {'id':'supply','members':['input'],'core':['input'],'direct':[]},
                  {'id':'lamp','members':['limit','indicator'],'core':['indicator'],
                   'direct':[[['limit','2'],['indicator','1']]]},
              ],
              'assumptions':{'supplyV':3.3,'resistanceOhm':1000,'ledVfRangeV':[1.8,2.4],
                             'note':'Nominal DC estimate only; not SPICE or measured hardware.'}}
    # Explicit synthetic template, not measured symbol geometry or an EDA Apply file.
    y, jx, rx = 300, 100, 300
    dx = rx + spacing
    pins = []
    for cid, number, x, py, direction in [
        ('input','1',jx-20,y+5,'left'),('input','2',jx-20,y-5,'left'),
        ('limit','1',rx-20,y,'left'),('limit','2',rx+20,y,'right'),
        ('indicator','1',dx-20,y,'left'),('indicator','2',dx+20,y,'right')]:
        pins.append({'pin':[cid,number],'at':[x,py],'page':'P1','direction':direction})
    wires = [
        {'id':'j-power','points':[[jx-40,y+5],[jx-20,y+5]]},
        {'id':'j-ground','points':[[jx-70,y-5],[jx-20,y-5]]},
        {'id':'r-power','points':[[rx-40,y],[rx-20,y]]},
        {'id':'series','points':[[rx+20,y],[dx-20,y]]},
        {'id':'d-ground','points':[[dx+20,y],[dx+40,y]]},
    ]
    labels = [
        {'id':'jp','at':[jx-40,y+5],'net':'power'},
        {'id':'jg','at':[jx-70,y-5],'net':'ground'},
        {'id':'rp','at':[rx-40,y],'net':'power'},
        {'id':'dg','at':[dx+40,y],'net':'ground'},
    ]
    bodies = [{'id':'J1','componentId':'input','bbox':[jx-10,y-15,jx+10,y+15]},
              {'id':'R1','componentId':'limit','bbox':[rx-10,y-5,rx+10,y+5]},
              {'id':'LED1','componentId':'indicator','bbox':[dx-10,y-10,dx+10,y+10]}]
    texts = []
    for name, cid, x in [('J1','input',jx),('R1','limit',rx),('LED1','indicator',dx)]:
        texts += [{'id':name+'-ref','componentId':cid,'role':'designator','bbox':[x-10,y+20,x+20,y+30]},
                  {'id':name+'-value','componentId':cid,'role':'value','bbox':[x-10,y-35,x+60,y-25]}]
    # Two modules: the input terminal feeds the lamp through labels, never through a
    # wire that leaves one module and enters the other.
    texts += [{'id':'title-supply','componentId':'input','role':'module-title','bbox':[22,y+36,110,y+44]},
              {'id':'title-lamp','componentId':'indicator','role':'module-title','bbox':[252,y+36,340,y+44]}]
    for items in (wires, labels, bodies, texts):
        for item in items:
            item['page'] = 'P1'
    scene = {'schemaVersion':1,'units':'raw','grid':5,'evidence':'synthetic-fixture','geometryScope':'full',
             'pins':pins,'wires':wires,'labels':labels,'junctions':[], 'bodies':bodies,
             'textInventory':[t['id'] for t in texts],'texts':texts,
             'moduleFrames':[
                 {'moduleId':'supply','page':'P1','bbox':[20,y-45,170,y+45],'titleTextId':'title-supply'},
                 {'moduleId':'lamp','page':'P1','bbox':[250,y-45,485,y+45],'titleTextId':'title-lamp'}]}
    return target, scene


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir', required=True)
    parser.add_argument('--spacing', type=int, default=120)
    args = parser.parse_args()
    out = Path(args.out_dir)
    if out.exists():
        parser.error('output directory exists; use a new run directory')
    target, scene = build(args.spacing)
    target_result = validate_target(target)
    scene_result = audit_scene(target, scene)
    broken = copy.deepcopy(scene)
    broken['wires'] = [w for w in broken['wires'] if w['id'] != 'series']
    broken_result = audit_scene(target, broken)
    # Same netlist, two drawings: the modular one passes, the one that crosses the
    # module boundary with a continuous wire must be named and rejected.
    readable = audit_readability(target, scene, strict=True)
    bridged = copy.deepcopy(scene)
    bridged['wires'] += [
        {'id':'bridge-a','page':'P1','points':[[80,305],[180,305]]},
        {'id':'bridge-b','page':'P1','points':[[180,305],[180,300]]},
        {'id':'bridge-c','page':'P1','points':[[180,300],[280,300]]}]
    bridged_read = audit_readability(target, bridged, strict=True)
    bridged_rules = {f['rule'] for f in bridged_read['findings']}
    a = target['assumptions']
    electrical = {'assumptions':a,
                  'nominalCurrentRangeMA':[(a['supplyV']-vf)/a['resistanceOhm']*1000 for vf in reversed(a['ledVfRangeV'])],
                  'hardwareVerified':False, 'spiceSimulated':False}
    if not target_result['passed'] or not scene_result['passed'] or broken_result['passed']:
        raise RuntimeError('demo positive/negative verification failed')
    if not readable['passed'] or bridged_read['passed'] or 'module-physical-crossing' not in bridged_rules:
        raise RuntimeError('readability positive/negative verification failed')
    out.mkdir(parents=True)
    files = {'target.json':target,'scene.json':scene,'target-report.json':target_result,
             'scene-report.json':scene_result,'broken-scene.json':broken,'broken-report.json':broken_result,
             'readability-report.json':readable,'bridged-scene.json':bridged,
             'bridged-readability-report.json':bridged_read,'electrical-estimate.json':electrical}
    hashes = {}
    for name, value in files.items():
        raw = (json.dumps(value, ensure_ascii=False, indent=2)+'\n').encode('utf-8')
        (out/name).write_bytes(raw)
        hashes[name] = hashlib.sha256(raw).hexdigest()
    (out/'manifest.json').write_text(json.dumps({'status':'offline-verified','geometry':'synthetic-fixture',
        'parameters':{'spacingRaw':args.spacing},'sha256':hashes,
        'codeSha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in
                      (Path(__file__),Path(__file__).with_name('circuit_audit.py'),
                       Path(__file__).with_name('readability_audit.py'))}},indent=2),encoding='utf-8')
    print(json.dumps({'status':'offline-verified','goodCircuitPassed':True,'brokenCircuitRejected':True,
                      'readableDrawingPassed':True,'bridgedDrawingRejected':True,
                      'out':str(out.resolve())}))


if __name__ == '__main__':
    main()
