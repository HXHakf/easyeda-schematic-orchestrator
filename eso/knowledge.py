"""Local, source-linked circuit catalog and host-reviewed material evidence.

No PDF semantic inference, network retrieval, UUID invention, or live EDA writes.
"""
import re
from contextlib import contextmanager
from contextvars import ContextVar
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from .core import Refused, digest, file_digest, load

CATALOG = Path(__file__).parent / 'data' / 'knowledge.json'


_DOCUMENT_ROOTS = ContextVar('knowledge_document_roots', default=())


@contextmanager
def knowledge_document_scope(requirements, roots=()):
    """Only new-policy operations may resolve reviewed documents from pinned runs."""
    if requirements.get('design_review', {}).get('policy_version') != 1:
        yield
        return
    token = _DOCUMENT_ROOTS.set(tuple(roots) or _DOCUMENT_ROOTS.get())
    try:
        yield
    finally:
        _DOCUMENT_ROOTS.reset(token)


def pinned_knowledge_document(sha):
    for root in _DOCUMENT_ROOTS.get():
        candidate = Path(root) / ('design-evidence--' + sha)
        if candidate.is_file():
            if file_digest(candidate) != sha:
                raise Refused('pinned knowledge document changed: ' + sha)
            return str(candidate.resolve())
    return None


def normalized(value):
    return re.sub(r'[^\w]', '', unicodedata.normalize('NFKC', value).casefold())


def catalog():
    data = load(CATALOG)
    check_catalog(data)
    return data


def check_catalog(data):
    sources, aliases, ids, urls = {}, {}, set(), set()
    for s in data['sources']:
        parsed = urlsplit(s['url'])
        url = urlunsplit((parsed.scheme.lower(),parsed.netloc.lower(),parsed.path.rstrip('/'),parsed.query,''))
        if s['id'] in sources or url in urls:
            raise Refused('duplicate source ID/URL')
        if s['ecosystem'] != 'jlc' and s['role'] != 'auxiliary':
            raise Refused('non-JLC sources must be auxiliary')
        sources[s['id']] = s; urls.add(url)
    assets, asset_locations = {}, set()
    for asset in data['upstream_candidates'] + data['community_discovery_candidates']:
        if asset['id'] in assets or asset['source_id'] not in sources:
            raise Refused('duplicate asset or dangling asset source')
        location = asset.get('url') or (asset['source_id'],asset.get('path'))
        if location in asset_locations: raise Refused('duplicate asset location')
        asset_locations.add(location)
        assets[asset['id']] = asset
    for rid, rule in data['rules'].items():
        if not rule['summary'] or not set(rule['source_ids']) <= sources.keys():
            raise Refused('invalid shared rule: ' + rid)
    for t in data['topics']:
        if t['id'] in ids: raise Refused('duplicate topic ID')
        ids.add(t['id'])
        for word in [t['id'], t['title'], *t['aliases']]:
            key = normalized(word)
            if key in aliases and aliases[key] != t['id']: raise Refused('duplicate topic alias: ' + word)
            aliases[key] = t['id']
        primary = sources.get(t['primary_source_id'])
        if t['primary_source_id'] and (not primary or primary['ecosystem'] != 'jlc' or primary['role'] != 'core'):
            raise Refused('topic primary source must be JLC core')
        if t['status'] == 'executable-bounded' and (not primary or t['conflicts']):
            raise Refused('executable topic needs a resolved JLC primary source')
        if not set(t['asset_ids']) <= assets.keys(): raise Refused('dangling topic asset')
        refs = t['related_core_source_ids'] + t['auxiliary_source_ids'] + [e['source_id'] for e in t['evidence']]
        if not set(refs) <= sources.keys() or not set(t['rule_ids']) <= data['rules'].keys():
            raise Refused('dangling topic source/rule')
        if len({v['id'] for v in t['variants']}) != len(t['variants']):
            raise Refused('duplicate variant')
    return {'valid': True, 'topic_count': len(ids), 'source_count': len(sources),
            'executable_topics': [t['id'] for t in data['topics'] if t['status'] == 'executable-bounded'],
            'primary_pending': [t['id'] for t in data['topics'] if not t['primary_source_id']]}


def search(query, parameters=None):
    if parameters is not None and not isinstance(parameters,dict):
        raise Refused('search parameters must be a JSON object')
    data = catalog(); q = normalized(query)
    if not q: return {'matches': []}
    exact, partial = [], []
    for t in data['topics']:
        names = [normalized(s) for s in [t['id'], t['title'], *t['aliases']]]
        if q in names: exact.append(t)
        elif any(q in name for name in names): partial.append(t)
    return {'matches': [{**t, 'missing_parameters': [p for p in t['required_parameters'] if p not in (parameters or {})]}
                        for t in (exact or partial)]}


def assess_materials(materials):
    """Aggregate problems. Extracted identities remain an explicit host review claim."""
    issues, records = [], {}
    def issue(field, message): issues.append({'field': field, 'question': message, 'reason': '资料核验未通过'})
    if not isinstance(materials, list) or not materials:
        issue('knowledge.materials', '需要已选器件资料清单'); return records, issues
    for index, m in enumerate(materials):
        prefix = f'knowledge.materials.{index}'
        if not isinstance(m, dict): issue(prefix, '资料项必须为对象'); continue
        for k in ('id', 'model', 'package', 'kind'):
            if not isinstance(m.get(k), str) or not m[k].strip(): issue(prefix + '.' + k, '需要非空 ' + k)
        mid = m.get('id')
        if not isinstance(mid, str): continue
        if mid in records: issue(prefix + '.id', '重复器件资料 ID'); continue
        records[mid] = m
        ds = m.get('datasheet', {})
        if not isinstance(ds, dict): issue(prefix + '.datasheet', '需要数据手册记录'); continue
        for key in ('path','sha256','model','package','revision','pinout_section','reviewed_by'):
            if not isinstance(ds.get(key), str) or not ds[key].strip():
                issue(prefix + '.datasheet.' + key, '需要手册路径、版本及宿主审阅定位 ' + key)
        for key in ('model','package'):
            if ds.get(key) != m.get(key): issue(prefix + '.datasheet.' + key, '手册摘录与已选 ' + key + ' 不一致')
        path = ds.get('path')
        if isinstance(path, str) and path:
            try:
                path = pinned_knowledge_document(ds.get('sha256', '')) or path
                if not Path(path).is_absolute() or not Path(path).is_file(): raise OSError('需要可读绝对文件路径')
                if file_digest(path) != ds.get('sha256'): issue(prefix + '.datasheet.sha256', '手册内容变更，需要重新审阅')
            except OSError as error: issue(prefix + '.datasheet.path', str(error))
        pins = m.get('pins')
        if not isinstance(pins, dict) or not pins or not all(isinstance(k,str) and k and isinstance(v,str) and v for k,v in pins.items()):
            issue(prefix + '.pins', '需要语义引脚到全部物理编号的映射')
        elif len(set(pins.values())) != len(pins): issue(prefix + '.pins', '物理引脚编号重复')
        lib = m.get('library', {})
        if not isinstance(lib, dict): issue(prefix + '.library', '需要真实库绑定'); continue
        for key in ('libraryUuid', 'deviceUuid'):
            if not isinstance(lib.get(key), str) or not re.fullmatch('[a-fA-F0-9]{32}', lib[key]):
                issue(prefix + '.library.' + key, '需要已核验的 32 位真实库标识')
        for key in ('model','package'):
            if lib.get('verified_' + key) != m.get(key): issue(prefix + '.library.' + key, '库与已选 ' + key + ' 不一致')
        if lib.get('pins') != pins: issue(prefix + '.library.pins', '库引脚与手册不一致')
        if not isinstance(lib.get('expected_name'), str) or not lib['expected_name'].strip(): issue(prefix + '.library.expected_name', '需要精确的真实库名称')
        if not isinstance(lib.get('evidence'), str) or not lib['evidence'].strip(): issue(prefix + '.library.evidence', '需要器件库查询/核验依据')
        if not isinstance(m.get('facts'), dict) or not isinstance(m.get('fact_sections'), dict):
            issue(prefix + '.facts', '需要参数及逐项章节定位')
    return records, issues


def assess_knowledge(knowledge):
    if isinstance(knowledge, dict) and knowledge.get('schema_version') == 2:
        from .knowledge_v2 import assess_v2
        return assess_v2(knowledge)
    from .knowledge_rules import check_switch
    issues, calculations, cards, nets, controls = [], {}, {}, {}, {}
    def issue(field, message): issues.append({'field': 'knowledge.' + field, 'question': message, 'reason': '电路条件未通过'})
    if not isinstance(knowledge, dict):
        issue('', '知识库输入必须为对象'); return {'ready': False, 'questions': issues}
    from .knowledge_input import input_issues
    structural = input_issues(knowledge)
    if structural:
        return {'ready': False, 'questions': structural}
    materials, material_issues = assess_materials(knowledge.get('materials'))
    issues.extend(material_issues)
    data = catalog(); by_topic = {t['id']:t for t in data['topics']}
    circuits = knowledge.get('circuits')
    if not isinstance(circuits, list) or not circuits:
        issue('circuits', '至少需要一个电路实例'); circuits = []
    seen, used, internal = set(), set(), set()
    floating_controls = {}
    for i, c in enumerate(circuits):
        prefix = f'circuits.{i}'
        if not isinstance(c, dict): issue(prefix, '实例必须为对象'); continue
        cid = c.get('id')
        if not isinstance(cid, str) or not re.fullmatch('[A-Za-z][A-Za-z0-9_]*', cid):
            issue(prefix + '.id', '实例 ID 需为字母开头的字母数字下划线'); continue
        if cid in seen: issue(prefix + '.id', '电路实例 ID 重复')
        seen.add(cid); internal.update((cid + '_DRIVE', cid + '_LOAD'))
        t = by_topic.get(c.get('topic'))
        if not t: issue(prefix + '.topic', '未知主题，先检索唯一主题 ID'); continue
        cards[t['id']] = t
        if t['status'] != 'executable-bounded' or t['conflicts']:
            issue(prefix + '.topic', '候选主题、资料冲突或缺少嘉立创主来源，不能自动生成'); continue
        variant = next((v for v in t['variants'] if v['id'] == c.get('variant') and v['status'] == 'executable'), None)
        if not variant: issue(prefix + '.variant', '该变体尚未实现参数与拓扑核验'); continue
        roles, ports, params = c.get('roles',{}), c.get('ports',{}), c.get('parameters',{})
        if not all(isinstance(v,dict) for v in (roles,ports,params)):
            issue(prefix, 'roles、ports、parameters 必须为对象'); continue
        if set(roles) != set(variant['roles']): issue(prefix + '.roles', '器件角色必须完整且没有额外项')
        chosen = {}
        for role, kind in variant['roles'].items():
            mid = roles.get(role)
            if not isinstance(mid,str) or mid not in materials: issue(prefix + '.roles.' + role, '缺少对应器件资料'); continue
            used.add(mid); chosen[role] = materials[mid]
            if materials[mid].get('kind') != kind: issue(prefix + '.roles.' + role, '器件类型不匹配 ' + kind)
            expected_pins = set(variant['topology'][role])
            if not isinstance(materials[mid].get('pins'),dict) or set(materials[mid]['pins']) != expected_pins:
                issue(prefix + '.roles.' + role + '.pins', '引脚角色必须覆盖且仅覆盖本变体 ' + str(sorted(expected_pins)))
        if set(ports) != {'supply','ground','control'} or not all(isinstance(v,str) and re.fullmatch('[A-Za-z][A-Za-z0-9_]*',v) for v in ports.values()):
            issue(prefix + '.ports', '需 supply/ground/control 三个有效网络名'); continue
        if len(set(ports.values())) != 3: issue(prefix + '.ports', '供电、地与控制网络不能短接')
        calc, errors = check_switch(t['id'], chosen, params)
        for field, message in errors: issue(prefix + '.' + field, message)
        calculations[cid] = calc
        for role, name in ports.items():
            spec = (role, (params.get('supply_voltage_v'), ports['ground']) if role == 'supply' else 0 if role == 'ground' else
                    (ports['ground'],params.get('control_high_min_v'),params.get('control_high_max_v'),params.get('control_low_max_v'),params.get('gpio_source_limit_ma')))
            if name in nets and nets[name] != spec: issue(prefix + '.ports', '共享网络角色、电压或控制参考地冲突 ' + name)
            nets[name] = spec
        if calc:
            switch = chosen['switch']['facts']
            bias = chosen['bias']['facts']
            series = chosen['series']['facts']
            floating_controls.setdefault(ports['control'], []).append({
                'leakage':switch['input_leakage_max_a'],
                'gpio_leakage':params['gpio_leakage_max_ua']/1e6,
                'path_resistance':bias['resistance_ohm']*(1+bias['tolerance_fraction']) + series['resistance_ohm']*(1+series['tolerance_fraction']),
                'off_limit':switch['off_input_max_v']})
            controls.setdefault(ports['control'], []).append((calc['gpio_current_max_a'],params.get('gpio_source_limit_ma',0)))
    for name in internal & nets.keys(): issue('circuits.ports', '外部端口与实例内部网络冲突 ' + name)
    for name, loads in controls.items():
        if sum(x[0] for x in loads) > min(x[1] for x in loads) / 1000:
            issue('circuits.ports', '共享控制端总电流超过 GPIO 限值 ' + name)
    for name, branches in floating_controls.items():
        if len(branches) > 1:
            # Each injection has a path to ground. The sum of I*Rpath gives a
            # conservative node-voltage bound without assuming isolated inputs.
            bound = sum(b['leakage']*b['path_resistance'] for b in branches)
            bound += max(b['gpio_leakage'] for b in branches)*max(b['path_resistance'] for b in branches)
            if bound > min(b['off_limit'] for b in branches):
                issue('circuits.ports', '共控高阻态漏电关断上界超过某支路限值 ' + name)
    if materials.keys() - used: issue('materials', '存在未使用器件资料，请移入候选清单 ' + ', '.join(sorted(materials.keys()-used)))
    evidence = {'catalog_sha256': digest(data),
                'cards': [{'id': t['id'],'version': t['version'],'sha256':digest(t)} for t in sorted(cards.values(), key=lambda t:t['id'])],
                'materials_sha256': digest(knowledge) if not issues else None,
                'documents': [{'material_id': mid,'sha256':m.get('datasheet',{}).get('sha256'),
                               'revision':m.get('datasheet',{}).get('revision')} for mid,m in sorted(materials.items())],
                'calculations': calculations}
    return {'ready': not issues, 'questions': issues, 'evidence': evidence,
            'semantic_review_required': True, 'level': 'offline-checked' if not issues else 'incomplete'}


def check_contract_knowledge(contract, *, evidence_root=None):
    roots = (evidence_root,) if evidence_root else ()
    with knowledge_document_scope(contract['requirements'], roots):
        return _check_contract_knowledge(contract)


def _check_contract_knowledge(contract):
    k = contract['requirements'].get('knowledge')
    if k is None:
        if 'knowledge_evidence' in contract: raise Refused('orphan knowledge evidence')
        return
    report = assess_knowledge(k)
    if not report['ready']: raise Refused('knowledge inputs stale/incomplete: ' + str(report['questions']))
    if 'composition' in contract['requirements']:
        from .knowledge_composition import assess_composition
        composition = assess_composition(contract['requirements'], report)
        if not composition['ready']: raise Refused('composition inputs stale/incomplete: ' + str(composition['questions']))
        report['evidence'].update(composition['evidence'])
    if contract.get('knowledge_evidence') != report['evidence']:
        raise Refused('knowledge evidence changed; regenerate and review contract')
