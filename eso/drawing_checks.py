"""Drawing checks shared by layout selection, supplementary audits and delivery."""
import math

EPS = 1e-7
VISUAL_CHECKS = ('reading_order', 'peripheral_grouping', 'repeated_branches',
                 'orientations_and_labels', 'typography', 'page_balance')


def coordinates(values, count):
    if (not isinstance(values, (list, tuple)) or len(values) != count
            or any(type(value) not in (int, float) or not math.isfinite(value) for value in values)):
        raise ValueError('drawing geometry requires finite coordinates')
    return tuple(values)


def rectangle(values):
    bounds = coordinates(values, 4)
    if bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
        raise ValueError('drawing rectangle must have positive dimensions')
    return bounds


def on(p, a, b):
    return (min(a[0], b[0]) - EPS <= p[0] <= max(a[0], b[0]) + EPS
            and min(a[1], b[1]) - EPS <= p[1] <= max(a[1], b[1]) + EPS
            and abs((b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])) <= EPS)


def boxes_touch(a, b):
    return a[0] <= b[2] + EPS and b[0] <= a[2] + EPS and a[1] <= b[3] + EPS and b[1] <= a[3] + EPS


def segment_hits_box(a, b, rect, closed=True):
    # Accepted wire segments are axis aligned. Body interior is open, text is closed.
    if closed:
        return (max(a[0], b[0]) >= rect[0] - EPS and min(a[0], b[0]) <= rect[2] + EPS
                and max(a[1], b[1]) >= rect[1] - EPS and min(a[1], b[1]) <= rect[3] + EPS)
    return (max(a[0], b[0]) > rect[0] + EPS and min(a[0], b[0]) < rect[2] - EPS
            and max(a[1], b[1]) > rect[1] + EPS and min(a[1], b[1]) < rect[3] - EPS)


def object_collisions(scene, segments):
    """Reuse the full geometry audit's body/text rules at every drawing gate."""
    texts, bodies = scene.get('texts', []), scene.get('bodies', [])
    boxes = [(obj, rectangle(obj['bbox'])) for obj in bodies + texts]
    text_ids = {text['id'] for text in texts}
    if {body['id'] for body in bodies} & text_ids:
        raise ValueError('body/text object IDs must be disjoint')
    labels = {label['id']: label for label in scene.get('labels', [])}
    findings = {'object_overlaps': [], 'wire_text_overlaps': [],
                'wire_body_overlaps': [], 'invalid_wire_exemptions': []}
    for i, (obj, rect) in enumerate(boxes):
        exemptions = obj.get('allowWireIds', [])
        marker = labels.get(obj.get('markerId'))
        valid_exemption = (obj['id'] in text_ids and obj.get('role') == 'net-label' and marker is not None
                           and marker['page'] == obj['page'] and len(exemptions) == 1
                           and any(wid == exemptions[0] and page == obj['page'] and on(coordinates(marker['at'], 2), a, b)
                                   for wid, page, a, b in segments))
        if exemptions and not valid_exemption:
            findings['invalid_wire_exemptions'].append({'objects': [obj['id']]})
            exemptions = []
        for other, rect2 in boxes[:i]:
            if obj['page'] == other['page'] and boxes_touch(rect, rect2):
                findings['object_overlaps'].append({'objects': [obj['id'], other['id']]})
        for wid, page, a, b in segments:
            if wid in exemptions:
                continue
            if obj['page'] == page and segment_hits_box(a, b, rect, obj['id'] in text_ids):
                rule = 'wire_text_overlaps' if obj['id'] in text_ids else 'wire_body_overlaps'
                findings[rule].append({'objects': [wid, obj['id']]})
    return findings


def drawing_obstacles(scene):
    """Recheck crossings, frames and full body/text geometry from current inputs."""
    segments = []
    for wire in scene['wires']:
        points = []
        for value in wire['points']:
            point = coordinates(value, 2)
            if points and all(abs(a - b) <= EPS for a, b in zip(points[-1], point)):
                raise ValueError('zero length drawing wire segment')
            while len(points) >= 2:
                a, b = points[-2:]
                if (min(a[0], point[0]) - EPS <= b[0] <= max(a[0], point[0]) + EPS
                        and min(a[1], point[1]) - EPS <= b[1] <= max(a[1], point[1]) + EPS
                        and abs((point[0] - a[0]) * (b[1] - a[1]) - (point[1] - a[1]) * (b[0] - a[0])) <= EPS):
                    points.pop()
                else:
                    break
            points.append(point)
        if len(points) < 2:
            raise ValueError('drawing wire requires at least two points')
        for a, b in zip(points, points[1:]):
            if abs(a[0] - b[0]) > EPS and abs(a[1] - b[1]) > EPS:
                raise ValueError('drawing wire must be orthogonal: ' + wire['id'])
            segments.append((wire['id'], wire['page'], a, b))
    junctions = [(item['page'], coordinates(item['at'], 2)) for item in scene.get('junctions', [])]
    horizontal = [item for item in segments if abs(item[2][1] - item[3][1]) <= EPS]
    vertical = [item for item in segments if abs(item[2][0] - item[3][0]) <= EPS]
    crossings = []
    for hid, page, a, b in horizontal:
        for vid, other_page, c, d in vertical:
            x, y = c[0], a[1]
            if (page != other_page or not min(a[0], b[0]) + EPS < x < max(a[0], b[0]) - EPS
                    or not min(c[1], d[1]) + EPS < y < max(c[1], d[1]) - EPS):
                continue
            if any(jpage == page and abs(at[0] - x) <= EPS and abs(at[1] - y) <= EPS
                   for jpage, at in junctions):
                continue
            crossings.append({'wires': [hid, vid], 'page': page, 'at': [x, y]})
    collisions = []
    for text in scene.get('texts', []):
        left, bottom, right, top = rectangle(text['bbox'])
        for frame in scene.get('moduleFrames') or []:
            if frame.get('page') not in (None, text['page']):
                continue
            x0, y0, x1, y1 = rectangle(frame['bbox'])
            touches_vertical = ((left - EPS <= x0 <= right + EPS or left - EPS <= x1 <= right + EPS)
                                and max(bottom, y0) <= min(top, y1) + EPS)
            touches_horizontal = ((bottom - EPS <= y0 <= top + EPS or bottom - EPS <= y1 <= top + EPS)
                                  and max(left, x0) <= min(right, x1) + EPS)
            if touches_vertical or touches_horizontal:
                collisions.append({'text': text['id'], 'module': frame.get('drawingGroupId', frame.get('moduleId')),
                                   'page': text['page'], 'bbox': list(text['bbox'])})
    return {'crossings': crossings, 'text_frame_collisions': collisions,
            **object_collisions(scene, segments)}


def check_visual_review(review):
    """Require object-specific assessments and no unresolved drawing findings."""
    if not isinstance(review, dict) or review.get('open_findings') != []:
        raise ValueError('drawing review requires an empty open_findings list')
    checks = review.get('checks')
    if not isinstance(checks, dict) or not set(VISUAL_CHECKS).issubset(checks):
        raise ValueError('drawing review requires all six visual checks')
    for name, check in checks.items():
        if (not isinstance(check, dict) or check.get('passed') is not True
                or not isinstance(check.get('assessment'), str) or not check['assessment'].strip()
                or not isinstance(check.get('objects'), list) or not check['objects']
                or any(not isinstance(obj, str) or not obj.strip() for obj in check['objects'])):
            raise ValueError('drawing review requires a passing assessment and named objects: ' + name)
