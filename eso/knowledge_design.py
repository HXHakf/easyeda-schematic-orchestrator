"""Translate checked knowledge instances into the existing ESO plan/binding interfaces."""
from .core import Refused, digest
from .knowledge import catalog, check_contract_knowledge


def make_knowledge_plan(contract):
    check_contract_knowledge(contract)
    k = contract['requirements']['knowledge']
    data = catalog()
    topics = {t['id']:t for t in data['topics']}
    materials = {m['id']:m for m in k['materials']}
    components, modules, nets, evidence = [], [], {}, {}
    counters = {'Q':0,'R':0}
    bindings, identities = {}, {}
    for c in k['circuits']:
        t = topics[c['topic']]; variant = next(v for v in t['variants'] if v['id']==c['variant'])
        for role, kind in variant['roles'].items():
            m = materials[c['roles'][role]]
            letter = 'Q' if role=='switch' else 'R'
            counters[letter] += 1; ref = letter + str(counters[letter])
            pin_nets = variant['topology'][role]
            pins = []
            for name, net in pin_nets.items():
                net_name = c['id'] + '_' + net[1:].upper() if net.startswith('$') else c['ports'][net]
                pins.append({'number':m['pins'][name],'name':name,'net':net_name,'nc_reason':None})
                definition = {'name':net_name,'role':'signal','scope':'local'}
                if net in ('supply','ground'):
                    definition.update(role='power' if net=='supply' else 'ground',scope='global',
                                      voltage_v=c['parameters']['supply_voltage_v'] if net=='supply' else 0)
                elif net == 'control': definition['scope'] = 'global'
                nets[net_name] = definition
            components.append({'id':c['id']+'-'+role,'ref':ref,'query':m['model'],
                               'role':role,'module':c['id'],'pins':pins})
            bindings[ref] = dict(m['library'])
            identities[c['id']+'-'+role] = {key:m['library'][key] for key in ('libraryUuid','deviceUuid')}
            if role=='switch': modules.append({'id':c['id'],'core_ref':ref,'title':t['title']})
            ds=m['datasheet']
            evidence['material:'+m['id']]={'url':'urn:sha256:'+ds['sha256'],
                                         'section':m['model']+' / '+m['package']+' / '+ds['revision']+' / '+ds['pinout_section']}
        for e in t['evidence']:
            s=next(s for s in data['sources'] if s['id']==e['source_id'])
            evidence[e['source_id']]={'url':s['url'],'section':e['section']}
    return {'schema_version':1,'contract_sha256':digest(contract),'template':'knowledge-low-side-v1',
            'components':components,'modules':modules,'nets':sorted(nets.values(),key=lambda n:n['name']),
            'evidence':list(evidence.values()),'knowledge_evidence':{**contract['knowledge_evidence'], 'bindings_sha256':digest(bindings), 'library_identities':identities},
            'notes':['静态电阻负载，共地，默认关闭；端口网络由外部电源/控制电路连接。',
                     '工作限值、温度与引脚为宿主审阅摘录；需现场库身份与连接回读。',
                     '仿真、PCB、硬件实测未完成。']}


def make_bindings(contract):
    plan=make_knowledge_plan(contract)
    k=contract['requirements']['knowledge']
    materials={m['id']:m for m in k['materials']}
    circuits={c['id']:c for c in k['circuits']}
    return {c['ref']:dict(materials[circuits[c['module']]['roles'][c['role']]]['library']) for c in plan['components']}


def check_knowledge_plan(plan, contract):
    if 'knowledge' not in contract['requirements']:
        if 'knowledge_evidence' in plan: raise Refused('orphan knowledge plan evidence')
        return
    expected=make_knowledge_plan(contract)
    # Deterministic topology, provenance and model selection must survive host edits.
    if plan != expected:
        raise Refused('knowledge plan differs from checked topology/evidence; regenerate from requirements')
