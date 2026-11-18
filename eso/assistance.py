"""Host assistance using retained catalog, measurements and existing audit reports."""
import math
from pathlib import Path
from .adapter import result
from .core import Refused, digest, file_digest, load
from .workflow import snapshot_fingerprint


def catalog_review(bindings, devices):
    rows = []
    for raw in devices:
        device = result(raw)['device'] if 'result' in raw else raw
        props, association = device.get('property', {}), device.get('association', {})
        fields = {key: props.get(key) for key in ('manufacturer', 'manufacturerId', 'supplier', 'supplierId')}
        fields.update(symbol=association.get('symbolUuid'), footprint=association.get('footprintUuid'))
        rows.append({'libraryUuid': device.get('libraryUuid'), 'uuid': device.get('uuid'),
                     'name': device.get('name'), 'fields': fields,
                     'missing': [key for key, value in fields.items() if not value], 'raw': device})
    components = {}
    for ref, binding in bindings.items():
        selected = [r for r in rows if r['uuid'] == binding['deviceUuid'] and r['libraryUuid'] == binding['libraryUuid']]
        if len(selected) != 1:
            raise Refused(ref + ': one exact catalog record required')
        row = selected[0]
        supplier_part = row['fields']['supplierId']
        components[ref] = {'selected': {k: row[k] for k in ('libraryUuid', 'uuid', 'name')},
                           'identity_matches': row['name'] == binding['expected_name'],
                           'missing': row['missing'], 'binding_evidence': binding.get('evidence'),
                           'same_supplier_part': [r['uuid'] for r in rows if supplier_part
                               and r['fields']['supplierId'] == supplier_part
                               and r['fields']['supplier'] == row['fields']['supplier']]}
    return {'state': 'review-required', 'pin_review': 'host-required', 'devices': rows,
            'components': components, 'bindings_sha256': digest(bindings)}


def text_restore_preview(reference, current):
    if reference.get('context', {}).get('projectUuid') != current.get('context', {}).get('projectUuid') or reference.get('context', {}).get('documentUuid') != current.get('context', {}).get('documentUuid'):
        raise Refused('reference and current text snapshots must have the same target')
    old, fresh = result(reference), result(current)
    def parts(data):
        rows = [c for c in data['components'] if c.get('componentType') == 'part']
        found = {c['designator']: c for c in rows}
        if len(found) != len(rows):
            raise Refused('duplicate component designators')
        return found
    before, after = parts(old), parts(fresh)
    if set(before) != set(after):
        raise Refused('text restoration requires the same complete part inventory')
    def attributes(data):
        found = {}
        for a in data['pagePrimitives']['attributes']:
            if a.get('Key') not in ('Name', 'Designator') or a.get('ValueVisible') is not True:
                continue
            key = (a['ParentPrimitiveId'], a['Key'])
            if key in found:
                raise Refused('ambiguous visible attribute')
            found[key] = a
        return found
    old_attrs, new_attrs = attributes(old), attributes(fresh)
    moves = []
    for ref, source in before.items():
        destination = after[ref]
        if any(source.get(k) != destination.get(k) for k in ('primitiveId', 'device', 'rotation', 'mirror', 'manufacturer', 'manufacturerId', 'supplier', 'supplierId', 'symbol', 'footprint')):
            raise Refused(ref + ': instance, identity or orientation changed; new measurement required')
        for key in ('Name', 'Designator'):
            a = old_attrs.get((source['primitiveId'], key))
            b = new_attrs.get((destination['primitiveId'], key))
            if (a is None) != (b is None):
                raise Refused(ref + ': visible text inventory changed')
            if a is None:
                continue
            ignored = {'primitiveId', 'X', 'Y', 'bbox'}
            if {k:v for k,v in a.items() if k not in ignored} != {k:v for k,v in b.items() if k not in ignored}:
                raise Refused(ref + ': text content or appearance changed; remeasure')
            coords = [a.get('X'), a.get('Y'), b.get('X'), b.get('Y'), source.get('x'), source.get('y'), destination.get('x'), destination.get('y')]
            if any(type(v) not in (float, int) or not math.isfinite(v) for v in coords):
                raise Refused('text restoration requires measured finite coordinates')
            x = a['X'] + destination['x'] - source['x']
            y = a['Y'] + destination['y'] - source['y']
            if (x, y) != (b['X'], b['Y']):
                moves.append({'primitiveId': b['primitiveId'], 'expected': b, 'x': x, 'y': y, 'ref': ref})
    return {'state': 'preview', 'reference_sha256': digest(reference),
            'baseline_fingerprint': snapshot_fingerprint(current), 'moves': moves}


def compare_layouts(candidates, budget, *, visual_comparison=None, author_context_id=None, contract=None, plan=None, reference_base=None):
    if type(budget) is not int or budget < 1 or len(candidates) > budget or not candidates:
        raise Refused('candidate count must fit a positive explicit budget')
    from .visual_review import check_candidate, comparison_subject, review_comparison

    if (contract is None) != (plan is None):
        raise Refused('candidate comparison basis requires both contract and plan')
    if contract is not None:
        contract = contract if isinstance(contract, dict) else load(contract)
        plan = plan if isinstance(plan, dict) else load(plan)
        if plan.get('contract_sha256') != digest(contract):
            raise Refused('candidate comparison plan belongs to another contract')
    rows, ids, targets, intentions = [], set(), set(), set()
    target_data = [load(candidate['target']) for candidate in candidates]
    modern = (visual_comparison is not None
              or (contract or {}).get('requirements', {}).get('design_review', {}).get('policy_version') == 1
              or any({'drawing_groups', 'primary_paths'} & set(data) for data in [plan or {}, *target_data])
              or any({'source', 'run_dir', 'preview'} & set(candidate) for candidate in candidates))
    if modern and visual_comparison is None:
        raise Refused('modern layout selection requires independent preview comparison')
    for candidate, target in zip(candidates, target_data):
        cid = candidate['id']
        if not isinstance(cid, str) or not cid or cid in ids:
            raise Refused('candidate IDs must be nonempty and unique')
        ids.add(cid)
        scene = load(candidate['scene'])
        if modern:
            intent, author = check_candidate(candidate, target, scene, basis_contract=contract, basis_plan=plan)
            intentions.add(intent)
            if author is not None and author != author_context_id:
                raise Refused('candidate comparison author differs from the reviewed design author')
        hashes = {file_digest(candidate['target']), file_digest(candidate['scene'])}
        if not modern:
            targets.add(file_digest(candidate['target']))
        reports = [load(candidate[key]) for key in ('geometry_report', 'readability_report')]
        if (reports[0].get('scope') != 'declared-topology-pin-exit-and-obstacle-geometry'
                or reports[0].get('geometryScope') != 'full'
                or reports[1].get('scope') != 'offline-readability'):
            raise Refused('separate full geometry and strict readability reports required')
        for report in reports:
            if not hashes.issubset(set(report.get('inputSha256', {}).values())):
                raise Refused('candidate reports do not bind the current target and scene')
        from .drawing_checks import drawing_obstacles
        obstacles = drawing_obstacles(scene)
        passed = (all(r.get('passed') is True for r in reports) and reports[1].get('strict') is True
                  and not any(obstacles.values()))
        bends = sum(max(0, len(w['points']) - 2) for w in scene['wires'])
        length = sum(abs(a[0]-b[0]) + abs(a[1]-b[1]) for w in scene['wires'] for a,b in zip(w['points'],w['points'][1:]))
        boxes = [f['bbox'] for f in scene.get('moduleFrames', [])]
        area = sum((b[2]-b[0])*(b[3]-b[1]) for b in boxes)
        if modern and passed:
            from .main_paths import audit_tools
            circuit, readability = audit_tools()
            geometry_now = circuit.audit_scene(target, scene)
            readability_now = readability.audit_readability(target, scene, strict=True)
            passed = geometry_now['passed'] and readability_now['passed']
            if passed:
                area = readability_now['metrics']['contentAreaRaw2']
        if any(type(v) not in (float, int) or not math.isfinite(v) or v < 0 for v in (bends,length,area)):
            raise Refused('invalid candidate geometry metrics')
        rows.append({'id': cid, 'passed': passed, 'drawing_findings': obstacles, 'score': [bends,length,area],
                     'inputs': {key: file_digest(candidate[key]) for key in ('target','scene','geometry_report','readability_report')}})
    if len(intentions if modern else targets) != 1:
        raise Refused('layout candidates must share the same target intent')
    visual = {}
    if modern:
        subject = comparison_subject(candidates, author_context_id)
        ranks = review_comparison(load(visual_comparison), subject, {r['id'] for r in rows if r['passed']},
                                  reference_base=reference_base)
        for row in rows:
            row['passed'] = row['passed'] and row['id'] in ranks
            row['clarity_rank'] = ranks.get(row['id'])
            row['inputs'].update(subject['candidates'][row['id']])
        visual = {'comparison_subject_sha256': digest(subject), 'comparison_review_sha256': file_digest(visual_comparison)}
    valid = sorted((r for r in rows if r['passed']), key=lambda r: (r.get('clarity_rank', 0), r['score'], r['id']))
    return {'state': 'candidate-comparison', 'selected': valid[0]['id'] if valid else None, **visual,
            'execution_ready': False, 'stop_reason': 'declared candidate set evaluated' if valid else 'no passing candidate',
            'budget': budget, 'candidates': rows,
            'next_step': ('execute the selected prepared run and independently verify actual readback' if modern else
                          'compile the selected complete source/page using prepare and independently verify actual readback')}


def apply_text_restore(adapter, run_dir, preview_path, output_dir):
    from .core import dump
    from .locking import target_lock
    from .reconcile import reconcile
    from .workflow import Workflow
    root, output = Path(run_dir).resolve(), Path(output_dir).resolve()
    if output.exists() or root == output or output in root.parents:
        raise Refused('text restoration requires a fresh evidence directory')
    preview = load(preview_path)
    target = load(root / 'contract.json')['target']
    with target_lock(target):
        workflow = Workflow(adapter, root)
        bundle = load(root / 'bundle.json')
        workflow._check_bundle(bundle)
        if bundle.get('state') not in ('verified', 'incomplete', 'applied-unverified'):
            raise Refused('text restoration requires a previously executed run')
        if bundle['target'] != target or preview.get('contract_sha256') != digest(load(root / 'contract.json')):
            raise Refused('text preview contract changed')
        current = adapter.snapshot(target)
        if preview.get('baseline_fingerprint') != snapshot_fingerprint(current):
            raise Refused('text preview is stale; read and preview again')
        checked = reconcile(load(root / 'expected.json'), adapter.run(['sch','connectivity'], target=target))
        if not checked['passed']:
            raise Refused('physical identity/pin/net/NC must match before text restoration')
        # Recompute moves from retained source, rather than trusting editable commands.
        recomputed = text_restore_preview(preview['reference'], current)
        if recomputed['moves'] != preview['moves'] or recomputed['reference_sha256'] != preview['reference_sha256']:
            raise Refused('text restoration moves differ from measured source')
        output.mkdir(parents=True)
        dump(output / 'preview.json', preview)
        dump(output / 'before.json', current)
        bundle['state'] = 'incomplete'
        dump(root / 'bundle.json', bundle)
        try:
            for i, move in enumerate(preview['moves']):
                params = {k:v for k,v in move.items() if k != 'ref'}
                params.update(projectUuid=target['project_uuid'], documentUuid=target['document_uuid'])
                path = output / (str(i) + '-move.json')
                dump(path, params)
                dump(output / (str(i) + '-receipt.json'), adapter.run(['sch','attribute-move','--from',path], target=target))
            return workflow._verify_locked()
        except Exception as error:
            dump(output / 'failure.json', {'error': str(error)})
            dump(output / 'failure-readback.json', adapter.snapshot(target))
            raise
