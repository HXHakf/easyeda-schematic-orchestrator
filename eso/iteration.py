"""Durable host-driven iteration for one confirmed schematic target."""
import math
from pathlib import Path

from .core import Refused, digest, dump, file_digest, load, now
from .evidence import check_attempt, design_qualification
from .locking import file_lock, target_lock
from .workflow import snapshot_fingerprint


def iteration_status(root):
    state = load(Path(root) / 'iteration.json')
    seen = set()
    for item in state['events']:
        if digest(item['payload']) != item['sha256']:
            raise Refused('iteration event contents changed')
        payload = item['payload']
        if payload['id'] in seen or payload['contract_sha256'] != state['contract_sha256']:
            raise Refused('iteration history has duplicate events or a different contract')
        seen.add(payload['id'])
    return state


def _run(event, state):
    path = Path(event['run_dir']).resolve(strict=True)
    if (digest(load(path / 'contract.json')) != state['contract_sha256']
            or load(path / 'bundle.json')['target'] != state['target']):
        raise Refused('iteration run belongs to a different confirmed contract')
    return path


def _artifact(item):
    from .design_review import resolve_artifact
    return resolve_artifact(item)


def _source(path):
    bundle = load(path / 'bundle.json')
    name = 'layout-page.json' if 'layout-page.json' in bundle['artifacts'] else 'composition.json'
    actual = file_digest(path / name)
    if bundle['artifacts'].get(name) != actual:
        raise Refused('iteration source differs from the protected prepared input')
    return actual


def _observe_candidate(event, state):
    root = _run(event, state)
    verification = root / 'verification.json'
    if not verification.is_file():
        bundle = root / 'bundle.json'
        if (load(bundle)['state'] not in ('incomplete', 'applied-unverified')
                or not (root / 'execution-started.lock').is_file()
                or file_digest(bundle) != event.get('execution_sha256')):
            raise Refused('incomplete candidate requires its retained execution evidence')
        return {'id': event['candidate_id'], 'run_dir': str(root), 'attempt': None, 'qualified': False,
                'issues': ['candidate execution did not yield a verified attempt'], 'source_sha256': _source(root)}
    report = load(verification)
    if file_digest(verification) != event['verification_sha256']:
        raise Refused('iteration references a stale verification report')
    reviews = load(_artifact(event['reviews'])) if event.get('reviews') is not None else None
    quality = design_qualification(root, reviews)
    source = _source(root)
    record = {'id': event['candidate_id'], 'run_dir': str(root), 'attempt': report.get('attempt'),
              'qualified': quality['qualified'], 'issues': quality['issues'], 'source_sha256': source}
    if quality['qualified']:
        archive = check_attempt(root, report)
        record['snapshot_fingerprint'] = snapshot_fingerprint(load(archive / 'final-snapshot.json'))
        record['snapshot_sha256'] = report['snapshot_sha256']
        record['reviews_sha256'] = event['reviews']['sha256']
    return record


def _comparison(event, state):
    from .assistance import compare_layouts
    manifest = load(_artifact(event['manifest']))
    report = load(_artifact(event['comparison']))
    retained_root = Path(state['best']['run_dir'])
    contract = load(retained_root / 'contract.json')
    from .design_review import validate_design_evidence
    evidence = validate_design_evidence(contract, search_paths=(state['best']['run_dir'],))
    author = evidence['design']['author_context_id']
    expected_ids = {state['best']['id'], state['extra']['candidate_id']}
    if (set(item['id'] for item in manifest['candidates']) != expected_ids
            or len(manifest['candidates']) != 2 or manifest['author_context_id'] != author):
        raise Refused('extra comparison must review the retained and one new candidate')
    calculated = compare_layouts(manifest['candidates'], manifest['max_candidates'],
                                 visual_comparison=manifest['visual_comparison'], author_context_id=author,
                                 contract=contract, plan=load(retained_root / 'plan.json'))
    if report != calculated or not report.get('comparison_subject_sha256'):
        raise Refused('extra comparison report no longer matches the actual review and inputs')
    rows = {item['id']: item for item in report['candidates']}
    if rows[state['best']['id']]['inputs']['source_sha256'] != state['best']['source_sha256']:
        raise Refused('comparison uses another source for the retained candidate')
    return report, rows


def _search_exhausted(event):
    receipt = load(_artifact(event['search_receipt']))
    source = _artifact(event['search_input'])
    args = receipt.get('argv')
    if (not isinstance(args, list) or len(args) < 5 or args[1:3] not in (['sch', 'lib-layout'], ['sch', 'layout-plan'])
            or args[3] != '--from' or Path(args[4]).resolve() != source.resolve()
            or type(receipt.get('exit_code')) is not int or receipt['exit_code'] == 0):
        raise Refused('extra round needs the retained nonzero upstream layout-search receipt')
    maximum = load(source).get('maxCandidates', 20000)
    if type(maximum) is not int or not 1 <= maximum <= 1000000:
        raise Refused('extra search must use a supported finite candidate budget')
    detail = (str(receipt.get('stderr', '')) + str(receipt.get('stdout', ''))).casefold()
    if not any(phrase in detail for phrase in ('maxcandidates', 'search budget', 'no safe candidate',
                                                'no legal candidate', 'no valid layout')):
        raise Refused('extra layout search did not report budget exhaustion or lack of a usable layout')


def _finish_extra(event, state):
    best = state['best']
    extra = state['extra']
    outcome = event.get('outcome')
    if outcome == 'search-exhausted':
        if event.get('run_dir') is not None:
            raise Refused('use the evaluated outcome when an extra candidate was written')
        _search_exhausted(event)
        return 'complete', 'bounded extra search did not yield a usable candidate'
    if outcome != 'evaluated':
        raise Refused('extra round requires an evaluated result or a retained unsuccessful search')
    report, rows = _comparison(event, state)
    if event.get('run_dir') is None:
        if rows[extra['candidate_id']]['passed'] and report['selected'] == extra['candidate_id']:
            raise Refused('an improved candidate must fully qualify in EDA before selection')
        return 'complete', 'retained qualified drawing after comparing the extra candidate'
    candidate = _observe_candidate(event, state)
    if (candidate['id'] != extra['candidate_id'] or candidate['run_dir'] == best['run_dir']
            or rows[candidate['id']]['inputs']['source_sha256'] != candidate['source_sha256']):
        raise Refused('evaluated extra round does not match its protected candidate source')
    state['candidates'].append(candidate)
    if candidate['qualified'] and report['selected'] == candidate['id']:
        state['best'] = candidate
        return 'complete', 'clearer candidate fully qualified on the current target'
    return 'restoring', 'extra candidate was written; restore the retained qualified source'


def _transition(event, state):
    phase = state['state']
    kind = event['kind']
    if kind == 'attempt' and phase == 'designing':
        candidate = _observe_candidate(event, state)
        if any(row['run_dir'] == candidate['run_dir'] and row['attempt'] == candidate['attempt']
               for row in state['candidates']):
            raise Refused('this attempt has already been recorded')
        state['candidates'].append(candidate)
        if candidate['qualified']:
            state['first_qualified'] = state['best'] = candidate
            return 'qualified', None
        return 'designing', 'candidate requires correction'
    if kind == 'extra-start' and phase == 'qualified':
        if event['candidate_id'] in {row['id'] for row in state['candidates']}:
            raise Refused('extra round requires a new candidate ID')
        state['extra'] = {'candidate_id': event['candidate_id'], 'started_by': event['id']}
        return 'extra-comparison', None
    if kind == 'extra-result' and phase == 'extra-comparison':
        if event['candidate_id'] != state['extra']['candidate_id']:
            raise Refused('wrong extra-round candidate')
        result = _finish_extra(event, state)
        state['extra']['finished_by'] = event['id']
        return result
    if kind == 'restored' and phase == 'restoring':
        best = state['best']
        restored = _observe_candidate(event, state)
        if (restored['id'] != best['id'] or restored['source_sha256'] != best['source_sha256']
                or restored['run_dir'] in {item['run_dir'] for item in state['candidates']}):
            raise Refused('restore the exact retained source in a new protected run')
        restored_root = Path(restored['run_dir'])
        proof_path = restored_root / 'revision-proof.json'
        if load(restored_root / 'bundle.json')['artifacts'].get(proof_path.name) != file_digest(proof_path):
            raise Refused('restore run requires protected revision proof')
        roles = load(proof_path)['evidence_roles']
        observed_parent = state['candidates'][-1]['run_dir']
        parent_queue = file_digest(Path(observed_parent) / 'apply.json')
        if roles.get('apply.json') != 'revision-evidence--' + parent_queue:
            raise Refused('restore revision must start from the most recent attempted drawing')
        state['candidates'].append(restored)
        if restored['qualified']:
            state['best'] = restored
            return 'complete', 'retained source restored and independently qualified again'
        return 'restoring', 'restored attempt still requires correction'
    if kind == 'blocked' and phase != 'complete':
        state['resume_state'] = phase if phase != 'incomplete' else state['resume_state']
        return 'incomplete', event['summary']
    if kind == 'resume' and phase == 'incomplete':
        return state.pop('resume_state'), None
    raise Refused(f'iteration event {kind} is invalid while {phase}')


def record_iteration(root, event):
    """Record an exact idempotent transition and retain every evaluated attempt."""
    root = Path(root).resolve()
    if not isinstance(event, dict) or any(not isinstance(event.get(key), str) or not event[key].strip()
                                          for key in ('id', 'kind', 'contract_sha256', 'summary')):
        raise Refused('iteration event requires id, kind, confirmed contract digest and summary')
    if event['kind'] in ('attempt', 'extra-start', 'extra-result', 'restored'):
        if not isinstance(event.get('candidate_id'), str) or not event['candidate_id'].strip():
            raise Refused('iteration event requires a candidate ID')
    elapsed = event.get('elapsed_seconds', 0)
    if type(elapsed) not in (int, float) or not math.isfinite(elapsed) or elapsed < 0:
        raise Refused('iteration elapsed_seconds must be finite and nonnegative')
    with file_lock(root / '.iteration.lock', blocking=True):
        path = root / 'iteration.json'
        if path.exists():
            state = iteration_status(root)
        else:
            if event['kind'] != 'attempt':
                raise Refused('initialize iteration with a new-policy schematic attempt')
            contract = load(Path(event['run_dir']) / 'contract.json')
            if contract.get('design_evidence', {}).get('policy_version') != 1:
                raise Refused('iteration requires a new-policy schematic contract')
            state = {'schema_version': 1, 'state': 'designing', 'events': [], 'candidates': [],
                     'target': contract['target'], 'contract_sha256': digest(contract),
                     'first_qualified': None, 'best': None, 'extra': None, 'stop_reason': None}
        if state['contract_sha256'] != event['contract_sha256']:
            raise Refused('contract changed; begin a new iteration after confirmation')
        sha = digest(event)
        for item in state['events']:
            if item['payload']['id'] == event['id']:
                if item['sha256'] != sha:
                    raise Refused('iteration event ID was already used for different content')
                return state
        with target_lock(state['target']):
            state['state'], state['stop_reason'] = _transition(event, state)
            state['events'].append({'sha256': sha, 'payload': event, 'recorded_at': now()})
            dump(path, state)
        return state


def current_iteration(root, run_dir, reviews_path, adapter, report):
    """Require the retained attempt to still be the same drawing in the editor."""
    state = iteration_status(root)
    selected = state['best']
    if state['state'] != 'complete' or not state['extra'].get('finished_by'):
        raise Refused('complete one extra comparison after the first fully qualified attempt')
    if (str(Path(run_dir).resolve()) != selected['run_dir'] or report.get('attempt') != selected['attempt']
            or report.get('snapshot_sha256') != selected['snapshot_sha256']
            or file_digest(reviews_path) != selected['reviews_sha256']
            or digest(load(Path(run_dir) / 'contract.json')) != state['contract_sha256']):
        raise Refused('delivery differs from the selected iteration attempt')
    extra_event = next((item['payload'] for item in state['events']
                       if item['payload']['id'] == state['extra']['finished_by']), None)
    if extra_event is None or extra_event.get('kind') != 'extra-result':
        raise Refused('iteration is missing its recorded extra comparison')
    if extra_event['outcome'] == 'search-exhausted':
        _search_exhausted(extra_event)
    else:
        _comparison(extra_event, dict(state, best=state['first_qualified']))
    fresh = adapter.snapshot(state['target'])
    if (snapshot_fingerprint(fresh) != selected['snapshot_fingerprint']
            or snapshot_fingerprint(load(check_attempt(run_dir, report) / 'final-snapshot.json')) != selected['snapshot_fingerprint']):
        raise Refused('current schematic differs from the selected saved drawing; restore or reverify')
    return {'state': 'complete', 'attempt': selected['attempt'], 'candidate_id': selected['id'],
            'stop_reason': state['stop_reason'], 'fresh_readback': True,
            'snapshot_fingerprint': snapshot_fingerprint(fresh)}
