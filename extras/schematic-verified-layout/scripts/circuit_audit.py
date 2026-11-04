"""Independent, offline schematic checks. No EDA writes; Python standard library only."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path

VERSION = '1.1.0'
EPS = 1e-7


def report(findings, scope):
    return {'checkerVersion': VERSION, 'passed': not findings, 'scope': scope,
            'status': 'offline-verified' if not findings else 'failed',
            'findings': findings, 'liveVerified': False}


def finding(items, rule, *objects):
    items.append({'rule': rule, 'objects': list(objects)})


def pin_key(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError('pin must be [componentId, physicalPinNumber]')
    if not all(isinstance(v, str) and v for v in value):
        raise ValueError('pin identifiers must be nonempty strings')
    return tuple(value)


def unique(items, key, kind):
    values = [x[key] for x in items]
    if any(not isinstance(x, str) or not x for x in values) or len(set(values)) != len(values):
        raise ValueError(f'duplicate/empty {kind}')


def target_index(target):
    if target['schemaVersion'] != 1 or not target['components'] or not target['modules']:
        raise ValueError('expected schemaVersion 1, nonempty components and modules')
    unique(target['components'], 'id', 'component id')
    unique(target['components'], 'ref', 'designator')
    unique(target['nets'], 'id', 'net id')
    unique(target['modules'], 'id', 'module id')
    inventory = set()
    for c in target['components']:
        if not c['pins'] or len(c['pins']) != len(set(c['pins'])):
            raise ValueError('empty/duplicate physical pin inventory')
        inventory.update(pin_key([c['id'], p]) for p in c['pins'])
    states = defaultdict(list)
    for net in target['nets']:
        if net['scope'] not in ('global', 'page') or not net['name'] or not net['pins']:
            raise ValueError('net requires name, global/page scope, and pins')
        for p in net['pins']:
            states[pin_key(p)].append(net['id'])
    for p in target['nc']:
        states[pin_key(p)].append(None)
    return inventory, states


def validate_target(target):
    issues = []
    try:
        inventory, states = target_index(target)
        for p in sorted(inventory | set(states)):
            if p not in inventory or len(states[p]) != 1:
                finding(issues, 'pin-state', list(p))
        members = Counter(x for m in target['modules'] for x in m['members'])
        component_ids = {c['id'] for c in target['components']}
        for c in sorted(component_ids | set(members)):
            if c not in component_ids or members[c] != 1:
                finding(issues, 'ownership', c)
        for module in target['modules']:
            if not module['core'] or not set(module['core']) <= set(module['members']):
                finding(issues, 'module-core', module['id'])
            for group in module['direct']:
                pins = [pin_key(p) for p in group]
                nets = {states[p][0] for p in pins if len(states[p]) == 1}
                if (len(set(pins)) < 2 or any(p not in inventory or p[0] not in module['members'] for p in pins)
                        or len(nets) != 1 or None in nets):
                    finding(issues, 'direct-declaration', module['id'], group)
        # Different global identities with the same native name cannot be isolated in EDA.
        names = Counter(n['name'] for n in target['nets'] if n['scope'] == 'global')
        for name, count in names.items():
            if count > 1:
                finding(issues, 'duplicate-global-net-name', name)
    except (KeyError, ValueError, TypeError) as exc:
        finding(issues, 'invalid-input', str(exc))
    return report(issues, 'target-pin-inventory-and-declared-ownership')


class DSU:
    def __init__(self, size):
        self.parent = list(range(size))

    def root(self, x):
        while x != self.parent[x]:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def join(self, a, b):
        self.parent[self.root(a)] = self.root(b)


def point(p):
    if len(p) != 2 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in p):
        raise ValueError('coordinates must be two finite numbers')
    return tuple(p)


def same(a, b):
    return all(abs(x - y) <= EPS for x, y in zip(a, b))


def on(p, a, b):
    return (min(a[0], b[0]) - EPS <= p[0] <= max(a[0], b[0]) + EPS
            and min(a[1], b[1]) - EPS <= p[1] <= max(a[1], b[1]) + EPS
            and abs((b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])) <= EPS)


def normalized_points(points):
    result = []
    for p in map(point, points):
        if result and same(result[-1], p):
            raise ValueError('zero length wire segment')
        while len(result) >= 2 and on(result[-1], result[-2], p):
            result.pop()  # A redundant polyline waypoint is not a physical junction.
        result.append(p)
    if len(result) < 2:
        raise ValueError('wire needs at least two points')
    return result


def scene_segments(scene):
    """Normalized, orthogonality-checked wire segments: (wireId, page, a, b)."""
    segments = []
    for w in scene['wires']:
        pts = normalized_points(w['points'])
        for a, b in zip(pts, pts[1:]):
            if abs(a[0] - b[0]) > EPS and abs(a[1] - b[1]) > EPS:
                raise ValueError(f'non-orthogonal segment: {w["id"]}')
            segments.append((w['id'], w['page'], a, b))
    return segments


def contact_graph(segments, pins, labels, junctions, with_labels=True):
    """Single source of truth for the declared contact model.

    Conducts: shared endpoints, T contact, collinear overlap, explicit junctions, pins on
    segments, coincident pins. A strict interior X is NOT connected. Node ids are stable:
    segments [0, nseg), pins [nseg, nseg+npin), labels [nseg+npin, ...) when labels are
    included. Readability analysis reuses this so both tools cannot drift apart.
    """
    nseg, npin = len(segments), len(pins)
    size = nseg + npin + (len(labels) if with_labels else 0)
    dsu = DSU(size)
    for i, (_, page, a, b) in enumerate(segments):
        for j in range(i):
            _, page2, c, d = segments[j]
            if page == page2 and (on(a, c, d) or on(b, c, d) or on(c, a, b) or on(d, a, b)):
                dsu.join(i, j)
    for junction in junctions:
        at = point(junction['at'])
        hits = [i for i, (_, page, a, b) in enumerate(segments) if page == junction['page'] and on(at, a, b)]
        for i in hits[1:]:
            dsu.join(hits[0], i)
    for i, pin in enumerate(pins):
        at = point(pin['at'])
        for j, (_, page, a, b) in enumerate(segments):
            if page == pin['page'] and on(at, a, b):
                dsu.join(nseg + i, j)
        for j in range(i):
            if pins[j]['page'] == pin['page'] and same(point(pins[j]['at']), at):
                dsu.join(nseg + i, nseg + j)
    if with_labels:
        for i, label in enumerate(labels):
            for j, (_, page, a, b) in enumerate(segments):
                if page == label['page'] and on(point(label['at']), a, b):
                    dsu.join(nseg + npin + i, j)
    return dsu


def box(value):
    if len(value) != 4:
        raise ValueError('bbox needs [minX,minY,maxX,maxY]')
    a, b = point(value[:2]), point(value[2:])
    if a[0] >= b[0] or a[1] >= b[1]:
        raise ValueError('empty or inverted bbox')
    return (*a, *b)


def boxes_touch(a, b):
    return a[0] <= b[2] + EPS and b[0] <= a[2] + EPS and a[1] <= b[3] + EPS and b[1] <= a[3] + EPS


def segment_hits_box(a, b, rect, closed=True):
    # All accepted wire segments are axis aligned. Body interior is open; text is closed.
    if closed:
        return (max(a[0], b[0]) >= rect[0] - EPS and min(a[0], b[0]) <= rect[2] + EPS
                and max(a[1], b[1]) >= rect[1] - EPS and min(a[1], b[1]) <= rect[3] + EPS)
    return (max(a[0], b[0]) > rect[0] + EPS and min(a[0], b[0]) < rect[2] - EPS
            and max(a[1], b[1]) > rect[1] + EPS and min(a[1], b[1]) < rect[3] - EPS)


def audit_scene(target, scene):
    base = validate_target(target)
    if not base['passed']:
        return base
    issues = []
    try:
        if scene['schemaVersion'] != 1 or scene['units'] != 'raw':
            raise ValueError('scene must use schemaVersion 1 and raw coordinates')
        if scene['evidence'] not in ('synthetic-fixture', 'measured'):
            raise ValueError('declare geometry evidence')
        geometry_scope = scene.get('geometryScope', 'full')
        if geometry_scope not in ('full', 'topology-only'):
            raise ValueError('geometryScope must be full or topology-only')
        if geometry_scope == 'topology-only' and scene['evidence'] != 'synthetic-fixture':
            raise ValueError('topology-only is limited to synthetic test fixtures')
        grid = scene['grid']
        if isinstance(grid, bool) or not math.isfinite(grid) or grid <= 0:
            raise ValueError('positive finite grid required')
        inventory, states = target_index(target)
        nets = {n['id']: n for n in target['nets']}
        pins = scene['pins']
        seen_pins = [pin_key(p['pin']) for p in pins]
        if set(seen_pins) != inventory or len(seen_pins) != len(inventory):
            raise ValueError('scene physical pins differ from target inventory')
        pin_pages = {pin_key(p['pin']): p['page'] for p in pins}
        for i, net in enumerate(target['nets']):
            pages = {pin_pages[pin_key(p)] for p in net['pins']}
            for other in target['nets'][:i]:
                other_pages = {pin_pages[pin_key(p)] for p in other['pins']}
                if net['name'] == other['name'] and (pages & other_pages or net['scope'] == other['scope'] == 'global'):
                    finding(issues, 'net-name-alias', net['id'], other['id'], net['name'])
        for key in ('wires', 'labels', 'bodies', 'texts'):
            unique(scene[key], 'id', key)
        segments = scene_segments(scene)
        nseg, npin = len(segments), len(pins)
        labels = scene['labels']
        for wid, _, a, b in segments:
            for coordinate in (*a, *b):
                if abs(coordinate / grid - round(coordinate / grid)) > EPS:
                    finding(issues, 'off-grid', wid)
                    break
        for junction in scene['junctions']:
            at = point(junction['at'])
            hits = [i for i, (_, page, a, b) in enumerate(segments) if page == junction['page'] and on(at, a, b)]
            if len(hits) < 2:
                finding(issues, 'orphan-junction', junction)
        dsu = contact_graph(segments, pins, labels, scene['junctions'])
        directions = {'right': (1, 0), 'left': (-1, 0), 'up': (0, 1), 'down': (0, -1)}
        for i, pin in enumerate(pins):
            at = point(pin['at'])
            direction = directions[pin['direction']]
            if any(abs(v / grid - round(v / grid)) > EPS for v in at):
                finding(issues, 'off-grid', pin['pin'])
            hits = []
            for j, (_, page, a, b) in enumerate(segments):
                if page != pin['page'] or not on(at, a, b):
                    continue
                hits.append(j)
                end = b if same(at, a) else a
                delta = (end[0] - at[0], end[1] - at[1])
                if (not (same(at, a) or same(at, b)) or
                        delta[0] * direction[0] + delta[1] * direction[1] <= EPS or
                        abs(delta[0] * direction[1] - delta[1] * direction[0]) > EPS):
                    finding(issues, 'pin-exit', pin['pin'], segments[j][0])
            if states[pin_key(pin['pin'])] == [None] and hits:
                finding(issues, 'nc-connected', pin['pin'])
            if states[pin_key(pin['pin'])] != [None] and not hits:
                finding(issues, 'floating-pin', pin['pin'])
            for j in range(i):
                if pins[j]['page'] == pin['page'] and same(point(pins[j]['at']), at):
                    finding(issues, 'pin-coincidence', pin['pin'], pins[j]['pin'])
        for i, label in enumerate(labels):
            if label['net'] not in nets:
                raise ValueError('marker refers to unknown net')
            hits = [j for j, (_, page, a, b) in enumerate(segments)
                    if page == label['page'] and on(point(label['at']), a, b)]
            if not hits:
                finding(issues, 'orphan-marker', label['id'])
        pin_nodes = {pin_key(p['pin']): nseg + i for i, p in enumerate(pins)}
        pin_roots = {dsu.root(node) for node in pin_nodes.values()}
        for i, (wid, page, a, b) in enumerate(segments):
            if dsu.root(i) not in pin_roots:
                finding(issues, 'orphan-wire-island', wid)
            for at in (a, b):
                if any(p['page'] == page and same(point(p['at']), at) for p in pins + labels):
                    continue
                if not any(j != i and page2 == page and on(at, c, d)
                           for j, (_, page2, c, d) in enumerate(segments)):
                    finding(issues, 'dangling-wire', wid, list(at))
        for module in target['modules']:
            for group in module['direct']:
                if len({dsu.root(pin_nodes[pin_key(p)]) for p in group}) != 1:
                    finding(issues, 'direct-required', module['id'], group)
        # A second graph adds semantic label edges; the physical graph above is kept independent.
        logical = DSU(len(dsu.parent))
        logical.parent = dsu.parent.copy()
        label_groups = {}
        for i, label in enumerate(labels):
            net = nets[label['net']]
            key = (net['name'], '*' if net['scope'] == 'global' else label['page'])
            node = nseg + npin + i
            if key in label_groups:
                logical.join(node, label_groups[key])
            label_groups[key] = node
        root_nets = defaultdict(set)
        for p, node in pin_nodes.items():
            if states[p][0] is not None:
                root_nets[logical.root(node)].add(states[p][0])
        for i, label in enumerate(labels):
            root_nets[logical.root(nseg + npin + i)].add(label['net'])
        for names in root_nets.values():
            if len(names) > 1:
                finding(issues, 'short', sorted(names))
        for net in target['nets']:
            if len({logical.root(pin_nodes[pin_key(p)]) for p in net['pins']}) != 1:
                finding(issues, 'open-net', net['id'])
        if (len(scene['textInventory']) != len(set(scene['textInventory'])) or
                set(scene['textInventory']) != {t['id'] for t in scene['texts']}):
            finding(issues, 'text-coverage', 'inventory and measured boxes differ')
        if geometry_scope == 'full':
            component_ids = {c['id'] for c in target['components']}
            body_owners = Counter(b.get('componentId') for b in scene['bodies'])
            if set(body_owners) != component_ids or any(v != 1 for v in body_owners.values()):
                finding(issues, 'body-coverage', 'require one bound body bbox per component')
            designator_owners = Counter(t.get('componentId') for t in scene['texts'] if t.get('role') == 'designator')
            if set(designator_owners) != component_ids or any(v != 1 for v in designator_owners.values()):
                finding(issues, 'text-coverage', 'require one bound designator bbox per component')
        boxes = [(obj, box(obj['bbox'])) for obj in scene['bodies'] + scene['texts']]
        text_ids = {t['id'] for t in scene['texts']}
        body_ids = {b['id'] for b in scene['bodies']}
        if body_ids & text_ids:
            raise ValueError('body/text object IDs must be disjoint')
        label_by_id = {label['id']: label for label in labels}
        for i, (obj, rect) in enumerate(boxes):
            exemptions = obj.get('allowWireIds', [])
            marker = label_by_id.get(obj.get('markerId'))
            valid_exemption = (obj['id'] in text_ids and obj.get('role') == 'net-label' and marker is not None
                               and marker['page'] == obj['page'] and len(exemptions) == 1
                               and any(wid == exemptions[0] and page == obj['page'] and on(point(marker['at']), a, b)
                                       for wid, page, a, b in segments))
            if exemptions and not valid_exemption:
                finding(issues, 'invalid-wire-exemption', obj['id'])
                exemptions = []
            for other, rect2 in boxes[:i]:
                if obj['page'] == other['page'] and boxes_touch(rect, rect2):
                    finding(issues, 'object-overlap', obj['id'], other['id'])
            for wid, page, a, b in segments:
                if wid in exemptions:
                    continue  # Explicit object-ID exemption, e.g. a native label on its own wire.
                if obj['page'] == page and segment_hits_box(a, b, rect, obj['id'] in text_ids):
                    finding(issues, 'wire-text-overlap' if obj['id'] in text_ids else 'wire-body-overlap', wid, obj['id'])
        result = report(issues, 'declared-topology-pin-exit-and-obstacle-geometry')
        result['geometryEvidence'] = scene['evidence']
        result['geometryScope'] = geometry_scope
        result['coverage'] = {'pins': len(pins), 'segments': len(segments), 'texts': len(scene['texts']),
                              'unverified': ['measurement authenticity/completeness', 'paper boundaries',
                                             'native DRC', 'save/reload', 'electrical ratings/simulation']}
        return result
    except (KeyError, ValueError, TypeError, IndexError) as exc:
        finding(issues, 'invalid-input', str(exc))
        return report(issues, 'declared-geometry')


def compare_readback(target, snapshot):
    base = validate_target(target)
    if not base['passed']:
        return base
    issues = []
    try:
        if snapshot['ok'] is not True:
            raise ValueError('readback envelope must have ok:true')
        # This comparator accepts exactly one page: native net names must be unambiguous on it.
        name_counts = Counter(n['name'] for n in target['nets'])
        for name, count in name_counts.items():
            if count > 1:
                finding(issues, 'net-name-alias', name)
        context = snapshot.get('context', {})
        for field in ('projectUuid', 'documentUuid'):
            if field in target.get('context', {}) and context.get(field) != target['context'][field]:
                finding(issues, 'wrong-target', field)
        actual = [c for c in snapshot['result']['components'] if c['componentType'] == 'part']
        unique(actual, 'designator', 'readback designator')
        actual = {c['designator']: c for c in actual}
        expected_refs = {c['ref'] for c in target['components']}
        if set(actual) != expected_refs:
            finding(issues, 'component-inventory', sorted(expected_refs), sorted(actual))
        _, states = target_index(target)
        net_names = {n['id']: n['name'] for n in target['nets']}
        for comp in target['components']:
            if comp['ref'] not in actual:
                continue
            observed = actual[comp['ref']]
            if observed.get('pinsAvailable') is not True or observed.get('netlistAvailable') is not True:
                finding(issues, 'pins-unavailable', comp['ref'])
            if 'device' in comp:
                for key in ('libraryUuid', 'uuid'):
                    if observed.get('device', {}).get(key) != comp['device'][key]:
                        finding(issues, 'device-identity', comp['ref'], key)
                if observed.get('deviceIdentityError'):
                    finding(issues, 'device-identity', comp['ref'], observed['deviceIdentityError'])
            unique(observed['pins'], 'pinNumber', 'readback pin number')
            observed_pins = {p['pinNumber']: p for p in observed['pins']}
            if set(observed_pins) != set(comp['pins']):
                finding(issues, 'pin-inventory', comp['ref'])
            for number in comp['pins']:
                if number not in observed_pins:
                    continue
                p = observed_pins[number]
                expected = states[(comp['id'], number)][0]
                if type(p.get('noConnected')) is not bool or not isinstance(p.get('net'), str) or p.get('netAmbiguous'):
                    finding(issues, 'pin-state-unknown', comp['ref'], number)
                    continue
                if p['noConnected'] != (expected is None) or p['net'] != ('' if expected is None else net_names[expected]):
                    finding(issues, 'pin-net', comp['ref'], number, p['net'])
    except (KeyError, ValueError, TypeError) as exc:
        finding(issues, 'invalid-input', str(exc))
    result = report(issues, 'single-page-readback-identity-and-pin-nets')
    result['coverage'] = {'unverified': ['physical direct wires', 'all geometry/text', 'DRC', 'save/reload', 'hardware']}
    return result


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['target', 'scene', 'compare'])
    parser.add_argument('--target', required=True)
    parser.add_argument('--scene')
    parser.add_argument('--readback')
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    output = Path(args.out)
    if output.exists():
        parser.error('output already exists; use a new revision filename')
    try:
        target = load(args.target)
        inputs = [args.target]
        if args.command == 'target':
            result = validate_target(target)
        elif args.command == 'scene':
            inputs.append(args.scene)
            result = audit_scene(target, load(args.scene))
        else:
            inputs.append(args.readback)
            result = compare_readback(target, load(args.readback))
        result['inputSha256'] = {str(Path(p).resolve()): hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in inputs}
    except (OSError, ValueError, TypeError) as exc:
        result = report([{'rule': 'invalid-input', 'objects': [str(exc)]}], 'input')
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'passed': result['passed'], 'scope': result['scope'], 'findings': len(result['findings'])}))
    return 0 if result['passed'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
