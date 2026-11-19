"""Validate immutable attempt copies and index optional host-reviewed evidence."""
import re
from pathlib import Path
from .core import Refused, digest, file_digest, load


def check_attempt(root, report):
    from .workflow import VERIFICATION_FILES
    attempt = report.get('attempt')
    if not isinstance(attempt, str) or not re.fullmatch(r'[0-9a-f]{32}', attempt):
        raise Refused('invalid verification attempt')
    archive = Path(root) / 'verification-history' / attempt
    files = report.get('attempt_files')
    bundle = load(archive / 'bundle.json')
    preflight = bundle.get('drawing_preflight', {})
    required = (set(bundle['artifacts']) | set(preflight) | set(VERIFICATION_FILES)
                | {'bundle.json', 'initial-snapshot.json', 'final-snapshot.json'})
    if preflight:
        required.add('native-pin-strokes.json')
    if not isinstance(files, dict) or not required.issubset(files):
        raise Refused('attempt evidence inventory incomplete')
    for name, sha in files.items():
        if Path(name).name != name or file_digest(archive / name) != sha:
            raise Refused('archived attempt evidence changed: ' + name)
    from .workflow import snapshot_fingerprint
    if snapshot_fingerprint(load(archive / 'initial-snapshot.json')) != snapshot_fingerprint(load(archive / 'final-snapshot.json')):
        raise Refused('archived verification observations are inconsistent')
    if report.get('snapshot_sha256') != files.get('final-snapshot.json'):
        raise Refused('final snapshot digest differs from attempt report')
    if report.get('bundle_artifacts_sha256') != digest(bundle['artifacts']):
        raise Refused('attempt report input digest changed')
    if any(files.get(name) != sha for name, sha in {**bundle['artifacts'], **preflight}.items()):
        raise Refused('attempt input differs from prepared input')
    if digest(load(archive / 'verification.json')) != digest(report):
        raise Refused('archived report differs from current report')
    return archive


def evidence_index(run_dir, reviews=None, attempt=None):
    from .cli import delivery_eligibility
    root = Path(run_dir)
    basis_issues = []
    historical = attempt is not None
    if historical:
        if not isinstance(attempt, str) or not re.fullmatch(r'[0-9a-f]{32}', attempt):
            raise Refused('invalid historical attempt')
        report = load(root / 'verification-history' / attempt / 'verification.json')
        check_attempt(root, report)
        from .workflow import VERIFICATION_CHECKS
        if report.get('state') != 'verified' or not VERIFICATION_CHECKS.issubset(report.get('checks', {})) or not all(c.get('passed') is True for c in report['checks'].values()):
            raise Refused('historical attempt has no complete passing verification')
        root = root / 'verification-history' / attempt
        from .knowledge import check_contract_knowledge
        try:
            check_contract_knowledge(load(root / 'contract.json'), evidence_root=root)
        except (OSError, ValueError) as error:
            basis_issues.append(str(error))
        passed, issues = False, ['historical evidence; current target has not been read back']
        core_state = 'historical-verified'
    else:
        report = load(root / 'verification.json')
        passed, issues = delivery_eligibility(root, report)
        core_state = 'verified' if passed else 'incomplete'
    electrical = 'unverified'
    author_context = None
    contract = load(root / 'contract.json')
    if 'design_evidence' in contract:
        from .core import check_confirmation
        from .design_review import validate_design_evidence
        try:
            check_confirmation(contract, load(root / 'approval.json'), evidence_root=root)
            evidence = validate_design_evidence(contract, load(root / 'plan.json'), search_paths=(root,))
            author_context = evidence['design']['author_context_id']
            electrical = 'independently-reviewed'
        except (OSError, ValueError, KeyError, TypeError) as error:
            basis_issues.append('electrical design evidence unavailable: ' + str(error))
            passed = False
    layers = {'core': core_state, 'geometry': 'not_checked', 'visual': 'not_checked'}
    files = {}
    if reviews is not None:
        if not isinstance(reviews, dict) or not isinstance(reviews.get('layers'), dict):
            raise Refused('reviews and layers must be objects')
        if (reviews.get('attempt') != report.get('attempt')
                or reviews.get('contract_sha256') != digest(load(root / 'contract.json'))
                or not report.get('snapshot_sha256')
                or reviews.get('snapshot_sha256') != report['snapshot_sha256']):
            raise Refused('supplementary reviews belong to another attempt, contract or snapshot')
        for layer, entry in reviews['layers'].items():
            if (layer not in ('geometry', 'visual') or not isinstance(entry, dict)
                    or not isinstance(entry.get('files'), dict) or not entry['files']):
                raise Refused('unknown or empty review layer')
            for name, item in entry['files'].items():
                if not isinstance(item, dict) or not isinstance(item.get('path'), str) or not isinstance(item.get('sha256'), str):
                    raise Refused('review files require path and sha256')
                if file_digest(item['path']) != item['sha256']:
                    raise Refused('review evidence changed: ' + name)
            _check_review_layer(layer, entry, report, root, current=not historical,
                                independent='design_evidence' in contract, author_context=author_context)
            raw = load(entry['report'])
            if not any(Path(v['path']).resolve() == Path(entry['report']).resolve() for v in entry['files'].values()):
                raise Refused('review report must be included in its hashed file inventory')
            layers[layer] = 'host-reviewed' if raw.get('passed') is True else 'incomplete'
            files[layer] = entry['files']
        if (not historical and 'design_evidence' in contract and {'geometry', 'visual'}.issubset(files)
                and files['geometry']['svg']['sha256'] != files['visual']['svg']['sha256']):
            raise Refused('geometry and independent visual review require the same native SVG')
    return {'state': 'evidence-index', 'fresh_readback': False, 'historical': historical, 'attempt': report.get('attempt'), 'layers': layers,
            'eda_evidence_complete': passed and all(v == 'host-reviewed' for k,v in layers.items() if k != 'core'),
            'files': files, 'issues': issues, 'current_basis_issues': basis_issues, 'electrical': electrical,
            'hardware_validation': 'unverified',
            'review_scope': 'host binds supplementary reports to the observed attempt; original core report retained'}


def design_qualification(run_dir, reviews):
    """Evaluate current design quality independently of any optimization round."""
    try:
        index = evidence_index(run_dir, reviews)
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {'qualified': False, 'issues': ['drawing evidence unavailable: ' + str(error)], 'evidence': None}
    issues = index['issues'] + index['current_basis_issues']
    if not index['eda_evidence_complete'] and not issues:
        issues.append('drawing evidence is incomplete')
    return {'qualified': index['eda_evidence_complete'], 'issues': issues, 'evidence': index}


def _check_review_layer(layer, entry, report, root, *, current=True, independent=False, author_context=None):
    from .adapter import result
    files = entry['files']
    required = {'snapshot', 'report', 'svg', 'preview'} if layer == 'visual' else {
        'snapshot', 'target', 'scene', 'report', 'readability', 'native_texts', 'svg'}
    if not required.issubset(files):
        raise Refused('supplementary review file inventory incomplete')
    if files['snapshot']['sha256'] != report['snapshot_sha256']:
        raise Refused('supplementary snapshot differs from current attempt')
    raw = load(entry['report'])
    if not isinstance(raw, dict):
        raise Refused('review report must be an object')
    binding = {'attempt': report['attempt'], 'snapshot_sha256': report['snapshot_sha256'],
               'contract_sha256': digest(load(root / 'contract.json'))}
    if raw.get('binding') != binding:
        raise Refused('review report has no current attempt binding')
    if layer == 'visual':
        if raw.get('scope') != 'host-visual-review' or not raw.get('reviewer'):
            raise Refused('visual report requires an identified host review and explicit scope')
        if any(raw.get('files', {}).get(key) != files[key]['sha256'] for key in ('snapshot','svg','preview')):
            raise Refused('visual report refers to other source files')
        required_coverage = {'primary_paths','module_ownership','junctions','external_text','internal_text','paper'}
        if not required_coverage.issubset(raw.get('coverage', {})):
            raise Refused('visual review coverage incomplete')
        if not all(raw['coverage'][key] is True for key in required_coverage):
            raise Refused('visual review has unresolved coverage')
        if current:
            from .drawing_checks import check_visual_review
            try:
                check_visual_review(raw.get('drawing_review'))
            except ValueError as error:
                raise Refused(str(error)) from error
            if independent:
                from .visual_review import check_attempt_images, check_invocation
                check_attempt_images(report, files)
                record = raw.get('independent_review')
                if not isinstance(record, dict) or record.get('invocation') != files.get('invocation'):
                    raise Refused('current visual review requires an independent invocation in the evidence files')
                subject = {'binding': binding, 'files': {key: files[key]['sha256'] for key in ('snapshot', 'svg', 'preview')}}
                outcome = {key: raw[key] for key in ('passed', 'coverage', 'drawing_review')}
                reviewer = check_invocation(record, subject, author_context, outcome=outcome)
                if raw['reviewer'] != reviewer['id']:
                    raise Refused('visual reviewer differs from independent reviewer record')
        return
    readability = load(files['readability']['path'])
    if (not isinstance(readability, dict) or raw.get('scope') != 'declared-topology-pin-exit-and-obstacle-geometry'
            or raw.get('geometryScope') != 'full' or readability.get('scope') != 'offline-readability'
            or readability.get('strict') is not True or readability.get('passed') is not True):
        raise Refused('full geometry and strict readability evidence required')
    hashes = {files['target']['sha256'], files['scene']['sha256']}
    if any(not hashes.issubset(set(r.get('inputSha256', {}).values())) for r in (raw, readability)):
        raise Refused('review report refers to another target or scene')
    data = result(load(files['snapshot']['path']))
    target, scene = load(files['target']['path']), load(files['scene']['path'])
    if current:
        from .main_paths import check_scene_page
        check_scene_page(scene, load(files['snapshot']['path']), load(root / 'contract.json')['target'])
    if current:
        from .drawing_checks import drawing_obstacles
        from .native_text import validate_junctions
        validate_junctions(files['svg']['path'], scene.get('junctions', []))
        if any(drawing_obstacles(scene).values()):
            raise Refused('current drawing has crossings, frame collisions or body/text overlaps')
    plan = load(root / 'plan.json')
    actual = {c['designator']:c for c in data['components'] if c.get('componentType') == 'part'}
    expected = {c['id']:c for c in plan['components']}
    if {c['id']:c['ref'] for c in target['components']} != {cid:c['ref'] for cid,c in expected.items()}:
        raise Refused('supplementary target component inventory differs')
    pins = {(c['id'],p['number']): p for c in plan['components'] for p in c['pins']}
    if {tuple(p) for p in target.get('nc',[])} != {key for key,p in pins.items() if p['net'] is None}:
        raise Refused('supplementary target NC inventory differs')
    scene_pins = {tuple(p['pin']):p for p in scene['pins']}
    if len(scene_pins) != len(scene['pins']) or set(scene_pins) != set(pins):
        raise Refused('supplementary physical pin inventory differs')
    for (cid, number), pin in scene_pins.items():
        part = actual[expected[cid]['ref']]
        physical = next(p for p in part['pins'] if p['pinNumber'] == number)
        if pin['at'] != [physical['x'],physical['y']]:
            raise Refused('scene pin geometry differs from final snapshot')
    from .main_paths import check_audit_target, check_native_frames
    modern = ('primary_paths' in plan or 'drawing_groups' in plan
              or load(root / 'contract.json').get('design_evidence', {}).get('policy_version') == 1)
    check_audit_target(plan, target, modern=modern)
    check_native_frames(plan, scene, data, modern=modern)
    if current and modern:
        from .main_paths import audit_tools
        audit, readability_audit = audit_tools()
        for checked in (audit.audit_scene(target, scene), readability_audit.audit_readability(target, scene, strict=True)):
            if not checked['passed']:
                raise Refused('current primary path/group audit failed: ' + str(checked['findings']))
    from collections import Counter
    def segment(a, b):
        return tuple(sorted((tuple(a), tuple(b))))
    actual_segments = Counter(segment([w['x0'],w['y0']], [w['x1'],w['y1']]) for w in data['wires'])
    scene_segments = Counter(segment(a,b) for w in scene['wires'] for a,b in zip(w['points'],w['points'][1:]))
    if actual_segments != scene_segments:
        raise Refused('scene wires differ from final snapshot')
    scene_bodies = {b['componentId']: b['bbox'] for b in scene['bodies']}
    actual_bodies = {cid:[actual[c['ref']]['bbox'][k] for k in ('minX','minY','maxX','maxY')] for cid,c in expected.items()}
    if scene_bodies != actual_bodies or len(scene['bodies']) != len(actual_bodies):
        raise Refused('scene bodies differ from final snapshot')
    actual_labels = {c['primitiveId']:(c['net'],[c['pins'][0]['x'],c['pins'][0]['y']])
                     for c in data['components'] if c.get('componentType') in ('netflag','netport')}
    net_names = {n['id']: n['name'] for n in target['nets']}
    if {l['id']:(net_names.get(l['net']),l['at']) for l in scene['labels']} != actual_labels:
        raise Refused('scene labels differ from final snapshot')
    target_edges = {(pair[0],pair[1],n['name']) for n in target['nets'] for pair in n['pins']}
    if target_edges != {(cid,num,p['net']) for (cid,num),p in pins.items() if p['net'] is not None}:
        raise Refused('supplementary target net inventory differs')
    measurements = load(files['native_texts']['path'])
    if measurements.get('svgSha256') != files['svg']['sha256']:
        raise Refused('native text measurements refer to another SVG')
    if current:
        # Wire names rendered by EDA may have no component attribute or marker in the scene.
        from .drawing_checks import branch_text_contacts
        page = load(root / 'contract.json')['target']['document_uuid']
        segments = [(w['id'], w['page'], tuple(a), tuple(b)) for w in scene['wires']
                    for a, b in zip(w['points'], w['points'][1:])]
        junctions = [(j['page'], tuple(j['at'])) for j in scene['junctions']]
        names = set(net_names.values())
        wire_texts = [dict(t, page=page) for t in measurements['texts'] if t.get('text', '').strip() in names]
        contacts = branch_text_contacts(segments, junctions, wire_texts)
        if contacts:
            raise Refused(f'native network text covers junction: {contacts}')
    measured = {t['id']:t['bbox'] for t in measurements['texts']}
    if set(scene.get('textInventory',[])) != {t['id'] for t in scene['texts']}:
        raise Refused('scene text inventory incomplete')
    if any(measured.get(t['id']) != t['bbox'] for t in scene['texts']):
        raise Refused('scene text bounds differ from native measurements')
    pin_ids = {p['primitiveId'] for c in data['components'] for p in c.get('pins',[])}
    attrs = {a['primitiveId']:a for a in data['pagePrimitives']['attributes']}
    free = {t['primitiveId'] for t in data['pagePrimitives']['texts']}
    external = {tid for tid in measured if tid in free or tid in attrs and attrs[tid]['ParentPrimitiveId'] not in pin_ids}
    if set(scene['textInventory']) != external:
        raise Refused('external native text coverage incomplete')
