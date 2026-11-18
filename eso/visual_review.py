"""Bind an independent reviewer to the exact drawings they reviewed."""
import math

from .core import Refused, digest, file_digest, load
from .drawing_checks import check_visual_review


def check_invocation(review, subject, author_context):
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
    record = load(resolve_artifact(review.get('invocation')))
    if (not isinstance(record, dict) or record.get('subject_sha256') != binding
            or record.get('reviewer_id') != reviewer['id'] or record.get('context_id') != reviewer['context_id']
            or record.get('author_context_id') != author_context
            or any(not isinstance(record.get(key), str) or not record[key].strip() for key in ('request', 'response'))):
        raise Refused('independent drawing review invocation mismatch')
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
        scales.add(scale)
        subjects[cid] = {'source_sha256': source_sha, 'scene_sha256': scene_sha,
                         'target_sha256': file_digest(candidate['target']),
                         'preview_sha256': file_digest(preview['path']), 'render_method': preview['render_method']}
    if len(scales) != 1:
        raise Refused('candidate previews must use the same drawing scale')
    return {'author_context_id': author_context, 'pixels_per_raw': scales.pop(), 'candidates': subjects}


def review_comparison(review, subject, machine_passing):
    if not isinstance(review, dict) or review.get('scope') != 'independent-layout-comparison':
        raise Refused('modern comparison requires an independent visual comparison')
    check_invocation(review, subject, subject['author_context_id'])
    evaluations = review.get('evaluations')
    if not isinstance(evaluations, dict) or set(evaluations) != set(subject['candidates']):
        raise Refused('independent review must assess each compared candidate')
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
