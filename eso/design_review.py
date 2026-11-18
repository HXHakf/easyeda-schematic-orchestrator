"""Source-backed generic design review. The host supplies reasoning and independent calls."""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import os
import re
import tempfile
from .core import Refused, digest, file_digest, load

DOMAINS = ('power', 'load', 'interfaces', 'assembly')
REVIEW_CHECKS = ('requirements', 'selection', 'pinout', 'peripherals', 'electrical', 'bom')


def electrical_intent(plan):
    """Project electrical fields only, before attaching a contract digest or geometry."""
    components = []
    for original in sorted(plan.get('components', []), key=lambda c: c['id']):
        component = {k: deepcopy(original[k]) for k in ('id', 'ref', 'query', 'role', 'module', 'attach') if k in original}
        component['pins'] = [{k: deepcopy(pin[k]) for k in ('number', 'name', 'net', 'nc_reason') if k in pin}
                             for pin in sorted(original.get('pins', []), key=lambda p: p['number'])]
        components.append(component)
    return {
        'components': components,
        'modules': [{k: deepcopy(m[k]) for k in ('id', 'core_ref') if k in m}
                    for m in sorted(plan.get('modules', []), key=lambda m: m['id'])],
        'nets': sorted(deepcopy(plan.get('nets', [])), key=lambda n: n['name']),
        'primary_paths': deepcopy(plan.get('primary_paths', [])),
    }


def requirements_digest(requirements):
    return digest({k: v for k, v in requirements.items() if k not in ('design_review', 'default_provenance')})


def _require(condition, message):
    if not condition:
        raise Refused('design review: ' + message)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _rows(value, name, nonempty=True):
    _require(isinstance(value, list) and (bool(value) or not nonempty)
             and all(isinstance(row, dict) for row in value), name + ' requires explicit records')
    return value


def resolve_artifact(record, search_paths=(), *, pinned_paths=()):
    """Resolve an exact content digest. Search is bounded to explicitly supplied directories."""
    _require(isinstance(record, dict) and _text(record.get('path')) and isinstance(record.get('sha256'), str)
             and re.fullmatch(r'[0-9a-f]{64}', record['sha256']), 'artifact path/hash required')
    for directory in pinned_paths:
        pinned = Path(directory) / ('design-evidence--' + record['sha256'])
        if pinned.is_file():
            _require(file_digest(pinned) == record['sha256'], 'pinned evidence content changed')
            return pinned
    original = Path(record['path'])
    if original.is_file():
        _require(file_digest(original) == record['sha256'], 'artifact content changed: ' + original.name)
        return original
    for directory in search_paths:
        root = Path(directory)
        if root.is_dir():
            for candidate in sorted(root.iterdir()):
                if candidate.is_file() and file_digest(candidate) == record['sha256']:
                    return candidate
    raise Refused('design review: missing artifact ' + original.name)


def _sources(design, search_paths, pinned_paths):
    sources = {}
    for source in _rows(design.get('sources'), 'sources'):
        _require(_text(source.get('id')) and source['id'] not in sources, 'unique source IDs required')
        _require(_text(source.get('url')) and _text(source.get('section')), 'original source and section required')
        resolve_artifact(source, search_paths, pinned_paths=pinned_paths)
        sources[source['id']] = source
    return sources


def _references(row, sources):
    refs = row.get('source_ids')
    _require(isinstance(refs, list) and refs and all(ref in sources for ref in refs), 'resolvable source_ids required')


def bom_status(design, sources):
    """Quotes use the exact purchase quantity for this build, including external parts."""
    builds = design.get('build_quantity')
    _require(type(builds) is int and builds > 0, 'positive build_quantity required')
    components = {c['id']: c for c in design['electrical_intent']['components']}
    covered, totals, missing = set(), {}, []
    for row in _rows(design.get('bom'), 'bom'):
        ids = row.get('component_ids')
        _require(isinstance(ids, list) and len(ids) == len(set(ids)), 'BOM component_ids required')
        _require(ids or row.get('external') is True, 'BOM external part must be explicit')
        _require(not covered.intersection(ids) and set(ids) <= set(components), 'BOM duplicate/unknown component')
        _require(_text(row.get('model')) and _text(row.get('package')), 'BOM model/package required')
        quantity = row.get('quantity')
        _require(type(quantity) is int and quantity > 0 and (not ids or quantity == len(ids)), 'BOM quantity differs from inventory')
        _references(row, sources)
        for cid in ids:
            selection = next(s for s in design['selections'] if cid in s['component_ids'])
            _require((row['model'], row['package']) == (selection['model'], selection['package']), 'BOM selection model/package mismatch')
        covered.update(ids)
        quote = row.get('quote')
        if quote is None:
            missing.append(row['model'] + ' / ' + row['package'])
            continue
        _require(isinstance(quote, dict), 'quote must be object or null')
        _require(type(quote.get('purchase_quantity')) is int and quote['purchase_quantity'] == quantity * builds, 'quote purchase quantity differs between candidates/build')
        tier = quote.get('tier_quantity')
        _require(type(tier) is int and 0 < tier <= quantity * builds, 'invalid quote pricing tier')
        price = quote.get('unit_price')
        _require(type(price) in (int, float) and price >= 0, 'invalid unit price')
        _require(_text(quote.get('currency')) and quote.get('source_id') in sources, 'quote currency/source required')
        _require(_text(quote.get('queried_at')), 'quote queried_at must be ISO timestamp')
        try:
            datetime.fromisoformat(quote['queried_at'].replace('Z', '+00:00'))
        except (KeyError, TypeError, ValueError):
            raise Refused('design review: quote queried_at must be ISO timestamp')
        currency = quote['currency']
        totals[currency] = totals.get(currency, Decimal(0)) + Decimal(str(price)) * quantity * builds
    _require(covered == set(components), 'BOM missing necessary peripheral/component')
    budget = design.get('binding_budget')
    _require(type(design.get('complete_cost_comparison', False)) is bool, 'complete_cost_comparison must be boolean')
    if 'binding_budget' in design or design.get('complete_cost_comparison'):
        _require(not missing, 'binding budget/cost comparison has unknown quotes')
    if 'binding_budget' in design:
        _require(isinstance(budget, dict) and type(budget.get('amount')) in (int, float)
                 and budget['amount'] >= 0 and _text(budget.get('currency')), 'invalid binding budget')
        _require(set(totals) == {budget['currency']} and totals[budget['currency']] <= Decimal(str(budget['amount'])), 'budget exceeded or incompatible currency')
    return {'complete': not missing, 'missing_quotes': missing,
            'known_subtotal': {k: float(v) for k, v in totals.items()}, 'build_quantity': builds}



def passive_inventory(intent):
    """Standard schematic passive designators define the package-controlled inventory."""
    return {c['id'] for c in intent['components']
            if re.fullmatch(r'(?:R|RN|C|CP|L|FB)[0-9]+', c['ref'])}


def _check_assembly(design, sources):
    assembly = design['assembly']
    expected = passive_inventory(design['electrical_intent'])
    declared = assembly.get('passive_component_ids')
    _require(isinstance(declared, list) and len(declared) == len(set(declared))
             and set(declared) == expected, 'complete passive_component_ids inventory required')
    exceptions = {}
    for exception in _rows(assembly.get('package_exceptions', []), 'package exceptions', False):
        cid = exception.get('component_id')
        _require(cid in expected and cid not in exceptions and _text(exception.get('package'))
                 and _text(exception.get('reason')), 'invalid passive package exception')
        _references(exception, sources)
        exceptions[cid] = exception['package']
    for selection in design['selections']:
        for cid in set(selection['component_ids']) & expected:
            wanted = exceptions.get(cid, assembly['passive_package'])
            _require(selection['package'] == wanted, 'passive package contradicts assembly decision')


def knowledge_documents(requirements):
    """Enumerate every bounded material and composition controller datasheet dependency."""
    knowledge = requirements.get('knowledge')
    if not knowledge:
        return []
    if knowledge.get('schema_version') == 2:
        from .materials import MaterialStore
        store = MaterialStore()
        records = list(knowledge['materials'])
        controller = requirements.get('composition', {}).get('controller')
        if controller:
            records.append(controller)
        return [{'path': store.check(m['record_sha256'], m['record']),
                 'sha256': m['record']['datasheet']['sha256']} for m in records]
    return [{'path': m['datasheet']['path'], 'sha256': m['datasheet']['sha256']}
            for m in knowledge['materials']]


def _check_bounded_intent(requirements, design):
    if 'knowledge' not in requirements:
        return
    from .requirements import contract_from
    from .planner import make_plan
    legacy_input = deepcopy(requirements)
    legacy_input.pop('design_review')
    expected = electrical_intent(make_plan(contract_from(legacy_input)))
    # Path declarations are validated separately against this same physical topology.
    expected['primary_paths'] = design['electrical_intent']['primary_paths']
    _require(expected == design['electrical_intent'], 'reviewed intent differs from bounded knowledge/composition topology')

def _check_design(design, requirements, search_paths, pinned_paths=()):
    _require(isinstance(design, dict) and type(design.get('policy_version')) is int and design['policy_version'] == 1, 'design policy_version required')
    _require(not requirements.get('unresolved'), 'unresolved user decisions remain')
    _require(design.get('requirements_sha256') == requirements_digest(requirements), 'requirements changed; repeat affected analysis')
    _require(_text(design.get('function')) and _text(design.get('operating_conditions')), 'function and operating conditions required')
    _require(_text(design.get('author_context_id')), 'author_context_id required')
    applicability = design.get('applicability', {})
    _require(isinstance(applicability, dict), 'applicability requires an object')
    for domain in DOMAINS:
        row = applicability.get(domain, {})
        _require(isinstance(row, dict), domain + ' applicability requires an object')
        _require(type(row.get('applicable')) is bool and _text(row.get('reason')) and _text(row.get('decision')), domain + ' applicability/decision required')
    sources = _sources(design, search_paths, pinned_paths)
    defaults = _rows(design.get('defaults', []), 'defaults', False)
    for default in defaults:
        _require(_text(default.get('field')) and 'value' in default and _text(default.get('reason')), 'default reason/value required')
        _references(default, sources)
        dependencies = default.get('depends_on')
        _require(isinstance(dependencies, dict) and dependencies, 'default dependencies required')
        _require(all(requirements.get(k) == v for k, v in dependencies.items()), 'default dependency changed')
    _require(isinstance(design.get('assembly'), dict) and _text(design['assembly'].get('method'))
             and _text(design['assembly'].get('passive_package')), 'assembly method/passive package required')
    intent = design.get('electrical_intent')
    _require(isinstance(intent, dict) and intent == electrical_intent(intent), 'canonical electrical_intent required')
    components = _rows(intent.get('components'), 'electrical components')
    # Reuse the regular plan schema for physical pin/net/attachment data.
    from .core import validate
    probe = {**intent, 'schema_version': 1, 'contract_sha256': '0' * 64,
             'template': 'reviewed-generic', 'evidence': [{'url': s['url'], 'section': s['section']} for s in sources.values()], 'notes': []}
    probe['modules'] = [dict(m, title=m['id']) for m in probe['modules']]
    probe.pop('primary_paths')  # Task 3 owns path schema/physical continuity checks.
    validate(probe, 'plan')
    from .workflow import validate_electrical_inventory
    validate_electrical_inventory(probe)
    if requirements.get('passive_package'):
        _require(design['assembly']['passive_package'] == requirements['passive_package'], 'assembly contradicts explicit passive package')
    ids = {c['id'] for c in components}
    _require(len(ids) == len(components), 'duplicate electrical component IDs')
    selected = set()
    for row in _rows(design.get('selections'), 'selections'):
        _require(_text(row.get('role')) and _text(row.get('model')) and _text(row.get('package')), 'selection role/model/package required')
        _references(row, sources)
        _require(_text(row.get('pinout_section')) and _text(row.get('application_section')), 'selection pinout/application locator required')
        own = row.get('component_ids')
        _require(isinstance(own, list) and own and len(own) == len(set(own)) and set(own) <= ids
                 and not selected.intersection(own), 'selection inventory invalid')
        selected.update(own)
        _require(isinstance(row.get('required_capabilities'), list) and isinstance(row.get('capabilities'), list)
                 and set(row['required_capabilities']) <= set(row['capabilities']), 'selected device misses required capability')
        for component in components:
            if component['id'] in own:
                _require(component['query'] == row['model'], 'intent model differs from selection')
    _require(selected == ids, 'selection missing necessary peripheral/component')
    _check_assembly(design, sources)
    for row in _rows(design.get('required_parts'), 'required_parts'):
        own = row.get('component_ids', [])
        external = row.get('external_parts', [])
        _require(_text(row.get('role')) and isinstance(own, list) and set(own) <= ids
                 and isinstance(external, list) and (own or external), 'required peripheral omitted')
        for part in external:
            _require(isinstance(part, dict) and any(b.get('external') is True
                     and (b['model'], b['package']) == (part.get('model'), part.get('package'))
                     for b in design.get('bom', [])), 'required external part omitted')
        _references(row, sources)
    _require(_text(design.get('calculations')), 'electrical calculations/limits or justified applicability required')
    return sources, bom_status(design, sources)


def review_subject(design):
    """Independent review binds all reasoning, BOM, source hashes and intent, without contract."""
    return digest(design)


def _check_review(review, design, search_paths, pinned_paths=()):
    _require(isinstance(review, dict) and type(review.get('policy_version')) is int and review['policy_version'] == 1, 'independent review required')
    reviewer = review.get('reviewer', {})
    _require(isinstance(reviewer, dict), 'reviewer identity requires an object')
    _require(_text(reviewer.get('id')) and _text(reviewer.get('context_id'))
             and reviewer['context_id'] != design['author_context_id'] and reviewer.get('independent') is True, 'independent reviewer identity/context required')
    _require(review.get('subject_sha256') == review_subject(design), 'review subject changed')
    invocation = load(resolve_artifact(review.get('invocation'), search_paths, pinned_paths=pinned_paths))
    _require(isinstance(invocation, dict), 'invocation record requires an object')
    _require(invocation.get('reviewer_id') == reviewer['id'] and invocation.get('context_id') == reviewer['context_id']
             and invocation.get('author_context_id') == design['author_context_id']
             and invocation.get('subject_sha256') == review_subject(design)
             and _text(invocation.get('request')) and _text(invocation.get('response')), 'independent invocation record mismatch')
    checks = _rows(review.get('checks'), 'independent per-item checks')
    _require({c.get('id') for c in checks} == set(REVIEW_CHECKS) and len(checks) == len(REVIEW_CHECKS), 'independent review coverage incomplete')
    for check in checks:
        _require(check.get('status') == 'closed' and _text(check.get('conclusion'))
                 and isinstance(check.get('source_ids'), list) and check['source_ids']
                 and set(check['source_ids']) <= {s['id'] for s in design['sources']}, 'independent review has open/empty checks')
    _require(review.get('issues') == [], 'independent electrical issues remain open')


def _read_evidence(requirements, evidence=None, search_paths=()):
    policy = requirements.get('design_review', {})
    _require(policy.get('policy_version') == 1, 'unsupported/orphan policy')
    records = evidence or policy
    paths = tuple(search_paths) + tuple(policy.get('search_paths', []))
    design_path = resolve_artifact(records.get('design'), paths, pinned_paths=search_paths)
    design = load(design_path)
    paths += (str(design_path.parent),)
    from .knowledge import knowledge_document_scope
    with knowledge_document_scope(requirements, search_paths):
        sources, cost = _check_design(design, requirements, paths, search_paths)
        _check_bounded_intent(requirements, design)
    review = load(resolve_artifact(records.get('review'), paths, pinned_paths=search_paths))
    _check_review(review, design, paths, search_paths)
    return design, review, sources, cost, paths


def assess_design_review(requirements):
    """Questions are user decisions. Classification, model choice and documents are host work."""
    questions, research, selections = [], [], []
    if not requirements.get('function_description') and not requirements.get('raw_request'):
        questions.append({'field': 'function_description', 'question': '需要实现什么功能及使用条件？', 'reason': '确定功能与工况'})
    policy = requirements['design_review']
    if not policy.get('design'):
        for domain in DOMAINS:
            research.append({'field': domain, 'action': '分析适用性，复用已有答案，查证器件资料及应用电路'})
        selections.append({'role': 'functional-components', 'action': '按用途提出完整选型、外围及外接件 BOM'})
        return {'questions': questions[:3], 'research_tasks': research, 'selection_tasks': selections,
                'defaults': [], 'ready': False}
    try:
        design, review, sources, cost, paths = _read_evidence(requirements)
    except (Refused, OSError, ValueError, KeyError, TypeError) as error:
        research.append({'field': 'design_evidence', 'action': str(error)})
        return {'questions': questions[:3], 'research_tasks': research, 'selection_tasks': selections,
                'defaults': [], 'ready': False}
    return {'questions': [], 'research_tasks': [], 'selection_tasks': [], 'defaults': design.get('defaults', []),
            'ready': True, 'cost': cost, 'evidence': {'policy_version': 1, 'design': deepcopy(policy['design']),
            'review': deepcopy(policy['review']), 'electrical_intent_sha256': digest(design['electrical_intent'])}}


def validate_design_evidence(contract, plan=None, *, search_paths=()):
    """Revalidate immutable evidence and current electrical intent. Returns legacy/new mode."""
    policy = contract['requirements'].get('design_review')
    evidence = contract.get('design_evidence')
    if policy is None and evidence is None:
        return {'review_mode': 'legacy'}
    _require(policy is not None and evidence is not None and evidence.get('policy_version') == 1, 'legacy/new policy mixing')
    _require(all(evidence.get(k) == policy.get(k) for k in ('design', 'review')), 'policy/evidence mismatch')
    design, review, sources, cost, paths = _read_evidence(contract['requirements'], evidence, search_paths)
    _require(digest(design['electrical_intent']) == evidence.get('electrical_intent_sha256'), 'fixed electrical intent changed')
    if plan is not None:
        _require(electrical_intent(plan) == design['electrical_intent'], 'electrical intent changed; repeat review and confirmation')
    return {'review_mode': 'new', 'design': design, 'review': review, 'cost': cost}


def pin_design_evidence(contract, root, *, search_paths=()):
    """Copy exact reviewed bytes before dependent writes, then audit pinned copies."""
    root = Path(root)
    checked = validate_design_evidence(contract, search_paths=(root, *search_paths))
    if checked['review_mode'] == 'legacy':
        return {}
    policy = contract['requirements']['design_review']
    paths = (*search_paths, *policy.get('search_paths', []), root, Path(policy['design']['path']).parent)
    from .knowledge import knowledge_document_scope
    with knowledge_document_scope(contract['requirements'], (root, *search_paths)):
        dependent_documents = knowledge_documents(contract['requirements'])
    records = [*dependent_documents, contract['design_evidence']['design'], contract['design_evidence']['review'],
               *checked['design']['sources'], checked['review']['invocation']]
    artifacts = {}
    for record in records:
        source = resolve_artifact(record, paths, pinned_paths=(root, *search_paths))
        relative = 'design-evidence--' + record['sha256']
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != target.resolve():
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=root, delete=False) as copied:
                    temporary = copied.name
                    copied.write(source.read_bytes())
                    copied.flush()
                    os.fsync(copied.fileno())
                os.replace(temporary, target)
            finally:
                if temporary and os.path.exists(temporary):
                    os.unlink(temporary)
        _require(file_digest(target) == record['sha256'], 'pinned evidence digest mismatch')
        artifacts[relative] = record['sha256']
    validate_design_evidence(contract, search_paths=(root,))
    return artifacts


def design_summary(contract):
    checked = validate_design_evidence(contract)
    if checked['review_mode'] == 'legacy':
        return []
    design, cost = checked['design'], checked['cost']
    lines = ['', '## 功能框图', '', design['function']]
    lines += [f"- {m['id']} {m.get('title', m['id'])}" for m in design['electrical_intent']['modules']]
    lines += ['', '## 工况、供电与接口', '', design['operating_conditions']]
    lines += [f"- {domain} {row['decision']}（{row['reason']}）" for domain, row in design['applicability'].items()]
    lines += ['', '## 核心与外围选型', '']
    lines += [f"- {s['role']} {s['model']} / {s['package']}" for s in design['selections']]
    lines += ['', '## 装配', '', str(design['assembly']), '', '## 完整 BOM', '']
    for row in design['bom']:
        lines.append(f"- {row['model']} / {row['package']} × {row['quantity']}，报价 {row.get('quote') or '未知'}")
    lines += ['', f"采购套数 {cost['build_quantity']}，已知小计 {cost['known_subtotal']}，缺报价 {cost['missing_quotes']}",
              '', '## 主路径', '', str(design['electrical_intent']['primary_paths']), '', '## 来源与默认假设', '']
    lines += [f"- {s['id']} {s['url']} {s['section']} SHA256 {s['sha256']}" for s in design['sources']]
    lines += [str(d) for d in design.get('defaults', [])]
    lines += ['', '独立电气审阅已完成，逐项结论及调用记录随设计依据保存。']
    return lines


def plan_from_design(contract):
    """Materialize the already reviewed intent after the contract has its final digest."""
    checked = validate_design_evidence(contract)
    _require(checked['review_mode'] == 'new', 'reviewed plan requires new policy')
    design = checked['design']
    if 'knowledge' in contract['requirements']:
        from .planner import make_plan
        plan = make_plan(contract)
        validate_design_evidence(contract, plan)
        return plan
    intent = deepcopy(design['electrical_intent'])
    intent['modules'] = [dict(m, title=m['id']) for m in intent['modules']]
    if not intent['primary_paths']:
        intent.pop('primary_paths')
    return {**intent, 'schema_version': 1, 'contract_sha256': digest(contract),
            'template': 'reviewed-generic',
            'evidence': [{'url': s['url'], 'section': s['section']} for s in design['sources']],
            'notes': [design['operating_conditions'], design['calculations']]}


def compare_bom_costs(designs):
    """Compare complete quotes under the same build quantity and currency conditions."""
    _require(isinstance(designs, list) and len(designs) >= 2, 'two cost candidates required')
    _require(len({d.get('build_quantity') for d in designs}) == 1, 'candidate build quantities differ')
    costs = []
    for design in designs:
        checked = deepcopy(design)
        checked['complete_cost_comparison'] = True
        costs.append(bom_status(checked, {s['id']: s for s in checked['sources']}))
    currencies = [set(c['known_subtotal']) for c in costs]
    _require(all(c == currencies[0] and len(c) == 1 for c in currencies), 'candidate quote currencies differ')
    return costs
