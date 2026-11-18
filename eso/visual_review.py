"""Bind an independent reviewer to the exact drawings they reviewed."""
from collections import Counter
import json
import math
from pathlib import Path
import struct
import xml.etree.ElementTree as ET
import zlib

from .core import Refused, digest, dump, file_digest, load
from .drawing_checks import check_visual_review


def check_invocation(review, subject, author_context, *, outcome, reference_base=None):
    reviewer = review.get('reviewer')
    if (not isinstance(author_context, str) or not author_context.strip()
            or not isinstance(reviewer, dict) or reviewer.get('independent') is not True
            or not all(isinstance(reviewer.get(key), str) and reviewer[key].strip() for key in ('id', 'context_id'))
            or reviewer['context_id'] == author_context):
        raise Refused('independent visual reviewer and separate author context required')
    binding = digest(subject)
    if review.get('subject_sha256') != binding:
        raise Refused('independent review refers to another drawing or candidate set')
    from .design_review import resolve_artifact
    invocation = review.get('invocation')
    if reference_base is not None and isinstance(invocation, dict) and isinstance(invocation.get('path'), str):
        invocation = dict(invocation, path=str((Path(reference_base) / invocation['path']).resolve()))
    record = load(resolve_artifact(invocation))
    if (not isinstance(record, dict) or record.get('subject_sha256') != binding
            or record.get('reviewer_id') != reviewer['id'] or record.get('context_id') != reviewer['context_id']
            or record.get('author_context_id') != author_context
            or not isinstance(record.get('request'), str) or not record['request'].strip()):
        raise Refused('independent drawing review invocation mismatch')
    response = record.get('response')
    if isinstance(response, str):
        def unique_fields(fields):
            decoded = dict(fields)
            if len(decoded) != len(fields):
                raise Refused('independent drawing review response repeats a field')
            return decoded

        try:
            response = json.loads(response, object_pairs_hook=unique_fields)
        except ValueError as error:
            raise Refused('independent drawing review response must contain a JSON outcome') from error
    if not isinstance(response, dict) or digest(response) != digest(outcome):
        raise Refused('independent drawing review outcome differs from the recorded response')
    return reviewer


def comparison_subject(candidates, author_context):
    """Hash full source and rendered bytes per candidate and pin one preview scale."""
    subjects, scales = {}, set()
    for candidate in candidates:
        cid = candidate['id']
        preview = candidate.get('preview')
        if not isinstance(preview, dict):
            raise Refused('modern comparison requires a preview and its source binding')
        scale = preview.get('pixels_per_raw')
        if type(scale) not in (int, float) or not math.isfinite(scale) or scale <= 0:
            raise Refused('candidate preview requires its actual drawing scale')
        if not isinstance(preview.get('render_method'), str) or not preview['render_method'].strip():
            raise Refused('candidate preview requires a render method')
        source_sha = file_digest(candidate['source'])
        scene_sha = file_digest(candidate['scene'])
        if preview.get('source_sha256') != source_sha or preview.get('scene_sha256') != scene_sha:
            raise Refused('candidate preview source or scene changed')
        image = Path(preview['path'])
        validate_image(image, image.suffix.lower()[1:])
        scales.add(scale)
        subjects[cid] = {'source_sha256': source_sha, 'scene_sha256': scene_sha,
                         'target_sha256': file_digest(candidate['target']),
                         'preview_sha256': file_digest(image), 'render_method': preview['render_method']}
        if candidate.get('run_dir'):
            bundle = load(Path(candidate['run_dir']) / 'bundle.json')
            subjects[cid]['prepared_artifacts_sha256'] = digest(bundle['artifacts'])
    if len(scales) != 1:
        raise Refused('candidate previews must use the same drawing scale')
    return {'author_context_id': author_context, 'pixels_per_raw': scales.pop(), 'candidates': subjects}


def review_comparison(review, subject, machine_passing, *, reference_base=None):
    if not isinstance(review, dict) or review.get('scope') != 'independent-layout-comparison':
        raise Refused('modern comparison requires an independent visual comparison')
    evaluations = review.get('evaluations')
    if not isinstance(evaluations, dict) or set(evaluations) != set(subject['candidates']):
        raise Refused('independent review must assess each compared candidate')
    check_invocation(review, subject, subject['author_context_id'], outcome={'evaluations': evaluations}, reference_base=reference_base)
    ranks = {}
    for cid, item in evaluations.items():
        if (not isinstance(item, dict) or type(item.get('accepted')) is not bool
                or not isinstance(item.get('assessment'), str) or not item['assessment'].strip()):
            raise Refused('comparison requires a specific reading-quality judgment for ' + cid)
        if item['accepted'] and cid in machine_passing:
            rank = item.get('clarity_rank')
            if type(rank) is not int or rank < 1:
                raise Refused('passing candidate requires an independent clarity ranking')
            check_visual_review(item.get('drawing_review'))
            ranks[cid] = rank
    return ranks


def validate_image(path, image_format):
    """Check the retained image container before treating its bytes as a drawing."""
    data = Path(path).read_bytes()
    if image_format == 'svg':
        try:
            root = ET.fromstring(data)
        except ET.ParseError as error:
            raise Refused('drawing SVG is unreadable') from error
        drawable = {'line', 'polyline', 'polygon', 'path', 'text', 'rect', 'circle', 'ellipse', 'use', 'image'}
        if root.tag not in ('svg', '{http://www.w3.org/2000/svg}svg') or not any(
                item.tag.split('}')[-1] in drawable for item in root.iter()):
            raise Refused('drawing SVG contains no renderable geometry')
        return
    if image_format != 'png' or data[:8] != b'\x89PNG\r\n\x1a\n':
        raise Refused('drawing preview requires a valid PNG or SVG')
    offset, chunks = 8, []
    while offset + 12 <= len(data):
        size, kind = struct.unpack_from('>I4s', data, offset)
        end = offset + 8 + size
        if end + 4 > len(data) or zlib.crc32(data[offset + 4:end]) != struct.unpack_from('>I', data, end)[0]:
            raise Refused('drawing PNG contains an invalid chunk')
        if not chunks and (kind != b'IHDR' or size != 13 or 0 in struct.unpack_from('>II', data, offset + 8)):
            raise Refused('drawing PNG dimensions unavailable')
        chunks.append(kind)
        offset = end + 4
        if kind == b'IEND':
            if size == 0 and b'IDAT' in chunks and offset == len(data):
                return
            break
    raise Refused('drawing PNG is incomplete')


IMAGE_EXPORTS = {'svg': ('native-page.svg', 'native-svg-export.json'),
                 'png': ('native-page.png', 'native-png-export.json')}


def capture_attempt_images(adapter, target, snapshot, archive):
    """Keep the official whole-page exports inside the current verification attempt."""
    from .adapter import result
    from .bridge import check_target

    issues = []
    for image_format, (output, receipt_name) in IMAGE_EXPORTS.items():
        image = archive / output
        try:
            receipt = adapter.run(['sch', 'export-image', '--scope', 'page', '--format', image_format, '--out', image],
                                  target=target)
            dump(archive / receipt_name, receipt)
            check_target(receipt, target)
            value = result(receipt)
            if (receipt['context'].get('documentType') != 'schematic'
                    or snapshot.get('windowId') is not None and receipt.get('windowId') != snapshot['windowId']
                    or value.get('format') != image_format or value.get('scope') != 'page'
                    or type(value.get('bytes')) is not int or value['bytes'] != image.stat().st_size):
                raise Refused('native image export receipt does not match this attempt')
            validate_image(image, image_format)
        except (OSError, ValueError, KeyError, TypeError) as error:
            issues.append(image_format + ': ' + str(error))
    return {'passed': not issues, 'issues': issues}


def check_attempt_images(report, files):
    export = report.get('native_export', {})
    if not isinstance(export, dict) or export.get('passed') is not True:
        detail = export.get('issues', []) if isinstance(export, dict) else []
        raise Refused('current verification requires official native SVG and PNG exports' +
                      (': ' + ', '.join(detail) if detail else ''))
    attempt_files = report.get('attempt_files', {})
    for role, image_format in (('svg', 'svg'), ('preview', 'png')):
        name, receipt = IMAGE_EXPORTS[image_format]
        if receipt not in attempt_files or attempt_files.get(name) != files[role]['sha256']:
            raise Refused('visual source differs from the current verification attempt native export: ' + role)
        validate_image(files[role]['path'], image_format)


def check_candidate(candidate, target, scene, *, basis_contract=None, basis_plan=None):
    """Bind audited geometry and source to a confirmed, offline-compiled candidate."""
    from .design_review import electrical_intent, validate_design_evidence
    from .main_paths import check_audit_target, check_scene_page
    from .workflow import Workflow, check_source, validate_plan
    from .planner import net_definitions

    if not isinstance(candidate.get('run_dir'), (str, Path)) or not candidate['run_dir']:
        raise Refused('modern candidate requires its prepared run directory')
    root = Path(candidate['run_dir'])
    bundle = load(root / 'bundle.json')
    contract = load(root / 'contract.json')
    plan = load(root / 'plan.json')
    Workflow(None, root)._check_bundle(bundle)
    validate_plan(plan, contract, evidence_root=root)
    if basis_contract is not None and digest(contract) != digest(basis_contract):
        raise Refused('candidate contract differs from the comparison contract')
    intent = electrical_intent(plan)
    if basis_plan is not None and digest(intent) != digest(electrical_intent(basis_plan)):
        raise Refused('candidate changes the protected electrical plan')
    source_name = 'layout-page.json' if 'layout-page.json' in bundle['artifacts'] else 'composition.json'
    if file_digest(candidate['source']) != bundle['artifacts'].get(source_name):
        raise Refused('candidate source differs from the protected composition or selected layout page')
    computed = load(root / 'upstream-plan.json')
    if check_source(plan, load(root / 'composition.json'), bundle['target']) != load(root / 'expected.json'):
        raise Refused('prepared composition differs from its protected intent')
    check_audit_target(plan, target, modern=True)
    parts = {row['ref']: row for row in plan['components']}
    wanted_pins = {(c['id'], p['number']): p for c in parts.values() for p in c['pins']}
    if (Counter((c['id'], c['ref'], tuple(sorted(c['pins']))) for c in target['components']) !=
            Counter((c['id'], c['ref'], tuple(sorted(p['number'] for p in c['pins']))) for c in parts.values())
            or Counter(tuple(p) for p in target.get('nc', [])) !=
            Counter(key for key, p in wanted_pins.items() if p['net'] is None)):
        raise Refused('candidate target component, physical pin or NC inventory differs from the protected plan')
    expected_nets = Counter((name, 'global' if details['scope'] == 'global' else 'page',
                             tuple(sorted(key for key, pin in wanted_pins.items() if pin['net'] == name)))
                            for name, details in net_definitions(plan).items())
    actual_nets = Counter((net['name'], net['scope'], tuple(sorted(tuple(pin) for pin in net['pins'])))
                          for net in target['nets'])
    if expected_nets != actual_nets:
        raise Refused('candidate target net names, scope or endpoints differ from the protected plan')
    schematic = bundle['target']
    check_scene_page(scene, {'context': {'projectUuid': schematic['project_uuid'],
                                         'documentUuid': schematic['document_uuid']}}, schematic)
    check_candidate_geometry(computed['layout'], scene, parts, target)
    reviewed = validate_design_evidence(contract, plan, search_paths=(root,))
    author = reviewed['design']['author_context_id'] if reviewed['review_mode'] != 'legacy' else None
    return (digest(contract), digest(intent)), author


def check_candidate_geometry(layout, scene, parts, target):
    """The candidate scene must depict the protected compiled page, including marker leads."""
    def box(raw):
        return tuple(raw[k] for k in ('minX', 'minY', 'maxX', 'maxY'))

    def pin_key(part, pin):
        return parts[part['designator']]['id'], pin['number']

    def segment(a, b):
        return tuple(sorted((tuple(a), tuple(b))))

    pins = Counter((pin_key(part, pin), (pin['x'], pin['y']))
                   for part in layout['placements'] for pin in part['pins'])
    bodies = Counter((parts[part['designator']]['id'], box(part['bbox'])) for part in layout['placements'])
    frames = Counter((frame['id'], box(frame['rect'])) for frame in layout['frames'])
    if (pins != Counter((tuple(pin['pin']), tuple(pin['at'])) for pin in scene['pins'])
            or bodies != Counter((part['componentId'], tuple(part['bbox'])) for part in scene['bodies'])
            or frames != Counter((frame['drawingGroupId'], tuple(frame['bbox'])) for frame in scene['moduleFrames'])):
        raise Refused('candidate pin/body/frame geometry differs from the protected compiled layout')
    compiled_text = Counter((parts[part['designator']]['id'], box(bounds))
                            for part in layout['placements'] for bounds in part.get('textBboxes', []))
    observed_text = Counter((text.get('componentId'), tuple(text['bbox'])) for text in scene.get('texts', []))
    if compiled_text - observed_text:
        raise Refused('candidate scene omits protected placement text geometry')
    wires = Counter(segment(a, b) for wire in layout['wires'] for a, b in zip(wire['points'], wire['points'][1:]))
    labels = Counter()
    directions = {'left': (-1, 0), 'right': (1, 0), 'up': (0, 1), 'down': (0, -1)}
    for flag in layout['flags']:
        x, y, offset = flag['pinX'], flag['pinY'], flag['offset']
        dx, dy = directions[flag['direction']]
        at = (x + dx * offset, y + dy * offset)
        labels[flag['net'], at] += 1
        wires[segment((x, y), at)] += 1
    nets = {net['id']: net['name'] for net in target['nets']}
    if (wires != Counter(segment(a, b) for wire in scene['wires'] for a, b in zip(wire['points'], wire['points'][1:]))
            or labels != Counter((nets[label['net']], tuple(label['at'])) for label in scene.get('labels', []))):
        raise Refused('candidate wires or markers differ from the protected compiled layout')
