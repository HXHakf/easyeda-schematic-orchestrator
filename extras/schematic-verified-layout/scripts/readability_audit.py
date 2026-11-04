"""Offline readability gate for EasyEDA Pro schematic geometry. Standard library only.

Electrical correctness and drawing quality are different questions. A strict interior X
crossing between two nets is electrically legal under the documented contact model (it does
not conduct) yet it is unreadable; a drawing can be perfectly laid out and still mis-wired.
Run circuit_audit.py for the first question and this checker for the second, and never let
one result stand in for the other.

Input is the same target.json / scene.json documented in references/audit-data.md, plus
optional scene.paper and scene.moduleFrames. Contact rules come from
circuit_audit.contact_graph, so the two checkers share one connectivity model and cannot
silently disagree. No EDA writes.

Module discipline is part of the gate, not a style preference: parts of different modules
may only meet through labels/netports, never through a continuous wire; a declared module
frame must contain everything that belongs to that module and must not overlap another
frame.
"""
import argparse
from collections import Counter, defaultdict, deque
import hashlib
import json
import math
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import circuit_audit as ca  # noqa: E402  (same directory, shared contact model)

VERSION = '1.1.0'
EPS = ca.EPS
OBJECT_CAP = 40
POWER_PATTERNS = (
    r'GND', r'VSS', r'VDD', r'VCC', r'AVDD', r'DVDD', r'VBAT', r'VBUS', r'VIN', r'VREF',
    r'^[+-]', r'^\d+(\.\d+)?V\d*$', r'^V\d',
)


def is_power_name(name):
    text = re.sub(r'[\s_\-]', '', name.upper())
    return any(re.search(pattern, text) for pattern in POWER_PATTERNS)


def add(findings, rule, severity, objects):
    findings.append({'rule': rule, 'severity': severity, 'objects': list(objects)[:OBJECT_CAP]})


def orientation(a, b):
    return 'h' if abs(a[1] - b[1]) <= EPS else 'v'


def resolve_nets(segments, pins, labels, junctions, states):
    """Attach one net id to each segment, or None when unresolved/ambiguous."""
    nseg, npin = len(segments), len(pins)
    full = ca.contact_graph(segments, pins, labels, junctions, with_labels=True)
    phys = ca.contact_graph(segments, pins, labels, junctions, with_labels=False)
    root_nets = defaultdict(set)
    for i in range(nseg):
        root_nets[full.root(i)]
    for i, pin in enumerate(pins):
        for net_id in states[ca.pin_key(pin['pin'])]:
            if net_id is not None:
                root_nets[full.root(nseg + i)].add(net_id)
    for i, label in enumerate(labels):
        root_nets[full.root(nseg + npin + i)].add(label['net'])
    seg_net = {}
    for i in range(nseg):
        nets = root_nets[full.root(i)]
        seg_net[i] = next(iter(nets)) if len(nets) == 1 else None
    return full, phys, seg_net, root_nets


def find_crossings(segments, seg_net):
    """Strict interior X between resolved segments of different nets (legal, unreadable)."""
    horizontal = [i for i, (_, _, a, b) in enumerate(segments) if orientation(a, b) == 'h']
    vertical = [i for i, (_, _, a, b) in enumerate(segments) if orientation(a, b) == 'v']
    found = []
    for h in horizontal:
        _, hpage, ha, hb = segments[h]
        x0, x1 = min(ha[0], hb[0]), max(ha[0], hb[0])
        for v in vertical:
            _, vpage, va, vb = segments[v]
            if vpage != hpage:
                continue
            x, y = va[0], ha[1]
            y0, y1 = min(va[1], vb[1]), max(va[1], vb[1])
            if not (x0 + EPS < x < x1 - EPS and y0 + EPS < y < y1 - EPS):
                continue  # endpoints/T contacts are connectivity, not crossings
            if seg_net[h] is None or seg_net[v] is None or seg_net[h] == seg_net[v]:
                continue
            found.append((h, v, (x, y), hpage))
    return found


def split_pairs(pairs, split_points):
    out = []
    for a, b in pairs:
        pts = [a, b]
        for p in split_points:
            if ca.same(p, a) or ca.same(p, b) or not ca.on(p, a, b):
                continue
            pts.append(p)
        pts.sort(key=(lambda q: q[1]) if orientation(a, b) == 'v' else (lambda q: q[0]))
        out.extend(zip(pts, pts[1:]))
    return out


def path_bends(pairs, start, goal):
    """Minimum-bend orthogonal path over split segments; None when not connected."""
    if start == goal:
        return 0
    adjacency = defaultdict(list)
    for a, b in pairs:
        adjacency[a].append(b)
        adjacency[b].append(a)
    if start not in adjacency or goal not in adjacency:
        return None
    previous = {start: None}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        for nxt in adjacency[current]:
            if nxt in previous:
                continue
            previous[nxt] = current
            if nxt == goal:
                path = [nxt]
                while previous[path[-1]] is not None:
                    path.append(previous[path[-1]])
                path.reverse()
                return sum(1 for i in range(2, len(path))
                           if orientation(path[i - 2], path[i - 1]) != orientation(path[i - 1], path[i]))
            queue.append(nxt)
    return None


def parallel_spacing(segments, seg_net, grid, limit):
    """Minimum centre distance between unrelated parallel wires; violations below limit grid."""
    minimum, violations = None, []
    for i in range(len(segments)):
        wida, pagea, a1, b1 = segments[i]
        for j in range(i):
            widb, pageb, a2, b2 = segments[j]
            if pagea != pageb or seg_net.get(i) is None or seg_net.get(j) is None:
                continue
            if seg_net[i] == seg_net[j] or orientation(a1, b1) != orientation(a2, b2):
                continue
            if orientation(a1, b1) == 'h':
                gap = abs(a1[1] - a2[1])
                overlap = min(max(a1[0], b1[0]), max(a2[0], b2[0])) - max(min(a1[0], b1[0]), min(a2[0], b2[0]))
            else:
                gap = abs(a1[0] - a2[0])
                overlap = min(max(a1[1], b1[1]), max(a2[1], b2[1])) - max(min(a1[1], b1[1]), min(a2[1], b2[1]))
            if gap <= EPS or overlap <= EPS:
                continue
            if minimum is None or gap < minimum:
                minimum = gap
            if gap < limit * grid - EPS:
                violations.append((wida, widb, gap))
    return minimum, violations


def short_pin_exits(segments, seg_net, pins, states, grid, limit):
    """Pins whose outgoing stub bends before limit*grid (ASG O5 spacing rule, advisory)."""
    out = []
    for pin in pins:
        net_ids = [n for n in states[ca.pin_key(pin['pin'])] if n is not None]
        if not net_ids:
            continue
        at = ca.point(pin['at'])
        for k, (wid, page, a, b) in enumerate(segments):
            if page != pin['page'] or seg_net.get(k) not in net_ids:
                continue
            if not (ca.same(at, a) or ca.same(at, b)):
                continue
            far = b if ca.same(at, a) else a
            length = abs(far[0] - at[0]) + abs(far[1] - at[1])
            bends = any(m != k and page2 == page and seg_net.get(m) == seg_net.get(k)
                        and orientation(c, d) != orientation(a, b) and ca.on(far, c, d)
                        for m, (_, page2, c, d) in enumerate(segments))
            if bends and length < limit * grid - EPS:
                out.append((pin['pin'], wid, length))
    return out


def redundant_overlaps(segments, seg_net):
    """Same-net collinear overlap: EasyEDA merges these silently, so duplicates are clutter."""
    out = []
    for i in range(len(segments)):
        wida, pagea, a1, b1 = segments[i]
        for j in range(i):
            widb, pageb, a2, b2 = segments[j]
            if pagea != pageb or seg_net.get(i) is None or seg_net.get(i) != seg_net.get(j):
                continue
            if orientation(a1, b1) != orientation(a2, b2):
                continue
            if orientation(a1, b1) == 'h':
                if abs(a1[1] - a2[1]) > EPS:
                    continue
                overlap = min(max(a1[0], b1[0]), max(a2[0], b2[0])) - max(min(a1[0], b1[0]), min(a2[0], b2[0]))
            else:
                if abs(a1[0] - a2[0]) > EPS:
                    continue
                overlap = min(max(a1[1], b1[1]), max(a2[1], b2[1])) - max(min(a1[1], b1[1]), min(a2[1], b2[1]))
            if overlap > EPS:
                out.append((wida, widb))
    return out


def content_box(segments, pins, scene):
    xs, ys = [], []
    for _, _, a, b in segments:
        xs += [a[0], b[0]]
        ys += [a[1], b[1]]
    for pin in pins:
        at = ca.point(pin['at'])
        xs.append(at[0])
        ys.append(at[1])
    for obj in scene.get('bodies', []) + scene.get('texts', []):
        rect = ca.box(obj['bbox'])
        xs += [rect[0], rect[2]]
        ys += [rect[1], rect[3]]
    if not xs:
        return None
    return [min(xs), min(ys), max(xs), max(ys)]


def module_owners(target):
    """component id -> module id; validate_target already guarantees exactly one owner."""
    return {component: module['id'] for module in target.get('modules', []) for component in module['members']}


def rect_inside(rect, x, y):
    return (rect[0] - EPS <= x <= rect[2] + EPS) and (rect[1] - EPS <= y <= rect[3] + EPS)


def box_inside(rect, other):
    return (rect[0] - EPS <= other[0] and rect[1] - EPS <= other[1]
            and other[2] <= rect[2] + EPS and other[3] <= rect[3] + EPS)


def rect_gap(a, b):
    """Chebyshev separation between two boxes; <= 0 means touching or overlapping."""
    return max(max(a[0] - b[2], b[0] - a[2]), max(a[1] - b[3], b[1] - a[3]))


def module_structure(target, scene, owner, pins, segments, phys, grid, gutter_grid, findings):
    """Modular layout gate: modules stay physically separate, framed, and self-contained."""
    nseg = len(segments)
    tree_modules = defaultdict(set)
    module_pages = defaultdict(set)
    for i, pin in enumerate(pins):
        module = owner.get(pin['pin'][0])
        if module is None:
            continue
        tree_modules[phys.root(nseg + i)].add(module)
        module_pages[module].add(pin['page'])
    bridges = sorted({'|'.join(sorted(modules)) for modules in tree_modules.values() if len(modules) > 1})
    if bridges:
        add(findings, 'module-physical-crossing', 'hard', bridges)
    split = sorted(module for module, pages in module_pages.items() if len(pages) > 1)
    if split:
        add(findings, 'module-split-pages', 'hard', split)

    metrics = {'modules': len(target.get('modules', [])),
               'moduleFrames': 0 if scene.get('moduleFrames') is None else len(scene['moduleFrames']),
               'modulePhysicalBridges': len(bridges), 'minModuleGapRaw': None, 'minModuleGapGrid': None}
    frames = scene.get('moduleFrames')
    if frames is None:
        return metrics
    known = {module['id'] for module in target.get('modules', [])}
    by_module = {}
    for frame in frames:
        module = frame['moduleId']
        if module not in known:
            raise ValueError(f'module frame refers to unknown module: {module}')
        if module in by_module:
            raise ValueError(f'duplicate module frame: {module}')
        by_module[module] = frame
    missing = sorted(set(module_pages) - set(by_module))
    if missing:
        add(findings, 'module-frame-missing', 'hard', missing)

    ids = sorted(by_module)
    minimum, tight = None, []
    for i, left in enumerate(ids):
        for right in ids[:i]:
            gap = rect_gap(ca.box(by_module[left]['bbox']), ca.box(by_module[right]['bbox']))
            if gap <= EPS:
                add(findings, 'module-overlap', 'hard', [f'{left}|{right}'])
                continue
            minimum = gap if minimum is None else min(minimum, gap)
            if gap < gutter_grid * grid - EPS:
                tight.append(f'{left}|{right}:{gap:g}raw')
    if tight:
        add(findings, 'module-gutter', 'advisory', tight)

    for module in ids:
        frame = by_module[module]
        rect = ca.box(frame['bbox'])
        page = frame.get('page')
        outside = []
        for pin in pins:
            if owner.get(pin['pin'][0]) != module or (page and pin['page'] != page):
                continue
            if not rect_inside(rect, *ca.point(pin['at'])):
                outside.append(f'pin:{pin["pin"][0]}.{pin["pin"][1]}')
        for obj in scene.get('bodies', []) + scene.get('texts', []):
            component = obj.get('componentId')
            if component and owner.get(component) == module and not box_inside(rect, ca.box(obj['bbox'])):
                outside.append(obj['id'])
        for i, (wid, wpage, a, b) in enumerate(segments):
            if page and wpage != page:
                continue
            if tree_modules.get(phys.root(i)) == {module} and not (rect_inside(rect, *a) and rect_inside(rect, *b)):
                outside.append(wid)
        if outside:
            add(findings, 'module-out-of-frame', 'hard', [f'{module}:{item}' for item in outside])
        title = frame.get('titleTextId')
        text = next((t for t in scene.get('texts', []) if t['id'] == title), None) if title else None
        if text is None:
            add(findings, 'module-title-missing', 'advisory', [module])
        elif not box_inside(rect, ca.box(text['bbox'])):
            add(findings, 'module-title-missing', 'advisory', [f'{module}:{title}'])
    metrics['minModuleGapRaw'] = minimum
    metrics['minModuleGapGrid'] = None if minimum is None else round(minimum / grid, 4)
    return metrics


def report(findings, thresholds, metrics, strict):
    hard = [f for f in findings if f['severity'] == 'hard']
    passed = not hard and not (strict and findings)
    return {'checkerVersion': VERSION, 'passed': passed, 'scope': 'offline-readability',
            'status': 'offline-verified' if passed else 'failed', 'strict': strict,
            'thresholds': thresholds, 'findings': findings, 'metrics': metrics,
            'lifecycle': {'hardRules': 'gate', 'advisoryRules': 'gate only with --strict'},
            'liveVerified': False,
            'coverage': {'unverified': ['rendered reading path (O1/O2/O3/O5)', 'paper/title block without scene.paper',
                                        'module grouping intent and frame semantics',
                                        'symbol-internal text', 'native DRC', 'save/reload', 'electrical ratings']}}


def audit_readability(target, scene, strict=False, min_spacing_grid=2.0, max_bends=2, max_crossings=0,
                      min_module_gutter_grid=4.0):
    """Deterministic drawing-quality gate. Returns a report dict; never mutates inputs."""
    base = ca.validate_target(target)
    if not base['passed']:
        return base
    if min_spacing_grid <= 0 or max_bends < 0 or max_crossings < 0 or min_module_gutter_grid <= 0:
        thresholds = {'minSpacingGrid': min_spacing_grid, 'maxBends': max_bends, 'maxCrossings': max_crossings,
                      'minModuleGutterGrid': min_module_gutter_grid}
        return report([{'rule': 'invalid-input', 'severity': 'hard',
                        'objects': ['thresholds must be positive/zero-or-positive']}], thresholds, {}, strict)
    thresholds = {'minSpacingGrid': min_spacing_grid, 'maxBends': max_bends, 'maxCrossings': max_crossings,
                  'minModuleGutterGrid': min_module_gutter_grid}
    findings, metrics = [], {}
    try:
        if scene['schemaVersion'] != 1 or scene['units'] != 'raw':
            raise ValueError('scene must use schemaVersion 1 and raw coordinates')
        if scene['evidence'] not in ('synthetic-fixture', 'measured'):
            raise ValueError('declare geometry evidence')
        grid = scene['grid']
        if isinstance(grid, bool) or not math.isfinite(grid) or grid <= 0:
            raise ValueError('positive finite grid required')
        pins, labels, wires = scene['pins'], scene['labels'], scene['wires']
        inventory, states = ca.target_index(target)
        nets = {n['id']: n for n in target['nets']}
        if {ca.pin_key(p['pin']) for p in pins} != inventory or len(pins) != len(inventory):
            raise ValueError('scene physical pins differ from target inventory')
        segments = ca.scene_segments(scene)
        full, phys, seg_net, root_nets = resolve_nets(segments, pins, labels, scene['junctions'], states)

        # 1. Connectivity this checker depends on. Duplicated findings with circuit_audit are
        #    intentional: this tool must refuse to judge a drawing it cannot resolve.
        ambiguous = sorted(root for root, ids in root_nets.items() if len(ids) > 1)
        if ambiguous:
            add(findings, 'net-short-in-geometry', 'hard',
                ['+'.join(sorted(root_nets[root])) for root in ambiguous])
        orphan = [segments[i][0] for i in range(len(segments))
                  if not root_nets[full.root(i)]]
        if orphan:
            add(findings, 'wire-without-net', 'hard', sorted(set(orphan)))
        if any(label['net'] not in nets for label in labels):
            raise ValueError('marker refers to unknown net')

        # 2. Foreign-net crossings: legal geometry, unreadable drawing.
        crossings = find_crossings(segments, seg_net)
        if len(crossings) > max_crossings:
            add(findings, 'foreign-net-crossing', 'hard',
                [f'{segments[h][0]}x{segments[v][0]}@{x:g},{y:g}' for h, v, (x, y), _ in crossings])

        # 3. Two-terminal nets must be traceable with few bends.
        pins_by_net = defaultdict(list)
        for pin in pins:
            for net_id in states[ca.pin_key(pin['pin'])]:
                if net_id is not None:
                    pins_by_net[net_id].append(pin)
        bend_histogram, unresolved_bends = Counter(), []
        for net_id, net_pins in sorted(pins_by_net.items()):
            if len(net_pins) != 2 or len({p['page'] for p in net_pins}) != 1:
                continue
            page = net_pins[0]['page']
            starts = [ca.point(p['at']) for p in net_pins]
            pairs = [(a, b) for i, (wid, wpage, a, b) in enumerate(segments)
                     if wpage == page and seg_net.get(i) == net_id]
            if not pairs:
                continue
            split = split_pairs(pairs, starts + [p for pair in pairs for p in pair])
            bends = path_bends(split, starts[0], starts[1])
            if bends is None:
                unresolved_bends.append(net_id)
                continue
            bend_histogram[bends] += 1
            if bends > max_bends:
                add(findings, 'two-terminal-bends', 'hard',
                    [f'{net_id}:{bends}' + ''.join(f'@{p["pin"][0]}.{p["pin"][1]}' for p in net_pins)])

        # 4. Labels must be boundaries, not a way to hide a missing wire.
        nseg = len(segments)
        pin_node = {ca.pin_key(p['pin']): nseg + i for i, p in enumerate(pins)}
        bridged = []
        for net_id, net_pins in sorted(pins_by_net.items()):
            if net_id not in nets or is_power_name(nets[net_id]['name']):
                continue
            if len({p['page'] for p in net_pins}) != 1:
                continue
            trees = {phys.root(pin_node[ca.pin_key(p['pin'])]) for p in net_pins}
            if len(trees) < 2:
                continue
            carries = any(label['net'] == net_id for label in labels)
            if carries:
                bridged.append(f'{net_id}:{len(trees)}trees')
            else:
                add(findings, 'net-not-physically-connected', 'hard', [f'{net_id}:{len(trees)}trees'])
        if bridged:
            add(findings, 'label-bridged-net', 'advisory', bridged)

        # 5. Spacing, pin exits and redundant copper (advisory: engineering defaults).
        minimum_gap, spacing = parallel_spacing(segments, seg_net, grid, min_spacing_grid)
        if spacing:
            add(findings, 'parallel-spacing', 'advisory',
                [f'{a}/{b}:{gap:g}raw' for a, b, gap in spacing])
        stub_limit = 2.0
        stubs = short_pin_exits(segments, seg_net, pins, states, grid, stub_limit)
        if stubs:
            add(findings, 'short-pin-exit', 'advisory',
                [f'{pin[0]}.{pin[1]}/{wid}:{length:g}raw' for pin, wid, length in stubs])
        overlaps = redundant_overlaps(segments, seg_net)
        if overlaps:
            add(findings, 'redundant-overlap', 'advisory', [f'{a}/{b}' for a, b in overlaps])

        # 6. Modular layout: a module owns its parts, its frame, and its own local wiring.
        #    Cross-module contact is a label-only boundary, never a continuous wire.
        module_metrics = module_structure(target, scene, module_owners(target), pins, segments, phys,
                                          grid, min_module_gutter_grid, findings)

        # 7. Page fit against a real sheet extent, when one was measured.
        content = content_box(segments, pins, scene)
        paper = scene.get('paper')
        if paper:
            usable = ca.box(paper['usable'])
            if content and (content[0] < usable[0] - EPS or content[1] < usable[1] - EPS
                            or content[2] > usable[2] + EPS or content[3] > usable[3] + EPS):
                add(findings, 'out-of-usable', 'hard', [f'content={content} usable={list(usable)}'])
        metrics = {
            'pages': len({w['page'] for w in wires} | {p['page'] for p in pins}),
            'wires': len(wires), 'segments': len(segments), 'nets': len(target['nets']),
            'twoTerminalNets': sum(1 for net_pins in pins_by_net.values()
                                   if len(net_pins) == 2 and len({p['page'] for p in net_pins}) == 1),
            'crossings': len(crossings), 'crossingsByPage': dict(Counter(item[3] for item in crossings)),
            'bendsHistogram': dict(sorted(bend_histogram.items())),
            'maxBends': max(bend_histogram, default=None),
            'unresolvedBends': unresolved_bends,
            'minParallelGapRaw': minimum_gap,
            'minParallelGapGrid': None if minimum_gap is None else round(minimum_gap / grid, 4),
            'wireIslands': len({full.root(i) for i in range(len(segments))}),
            'longestSegmentRaw': max((abs(a[0] - b[0]) + abs(a[1] - b[1]) for _, _, a, b in segments), default=0),
            'labels': len(labels), 'labelBridgedNets': bridged,
            'contentBBox': content,
            'paperName': paper.get('name') if paper else None,
            'occupancy': (None if not (paper and content) else
                          round((content[2] - content[0]) * (content[3] - content[1])
                                / ((usable[2] - usable[0]) * (usable[3] - usable[1])), 4)),
        }
        metrics.update(module_metrics)
    except (KeyError, ValueError, TypeError, IndexError, ZeroDivisionError) as exc:
        findings.append({'rule': 'invalid-input', 'severity': 'hard', 'objects': [str(exc)]})
    return report(findings, thresholds, metrics, strict)


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', required=True)
    parser.add_argument('--scene', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--strict', action='store_true', help='advisory findings also fail')
    parser.add_argument('--min-spacing-grid', type=float, default=2.0)
    parser.add_argument('--max-bends', type=int, default=2)
    parser.add_argument('--max-crossings', type=int, default=0)
    parser.add_argument('--min-module-gutter-grid', type=float, default=4.0,
                        help='minimum clear gap between module frames, in grid cells')
    args = parser.parse_args()
    output = Path(args.out)
    if output.exists():
        parser.error('output already exists; use a new revision filename')
    try:
        target, scene = load(args.target), load(args.scene)
        result = audit_readability(target, scene, args.strict, args.min_spacing_grid,
                                   args.max_bends, args.max_crossings, args.min_module_gutter_grid)
        result['inputSha256'] = {str(Path(p).resolve()): hashlib.sha256(Path(p).read_bytes()).hexdigest()
                                 for p in (args.target, args.scene)}
    except (OSError, ValueError, TypeError) as exc:
        result = report([{'rule': 'invalid-input', 'severity': 'hard', 'objects': [str(exc)]}], {}, {}, args.strict)
        result['inputSha256'] = {}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'passed': result['passed'], 'scope': result['scope'],
                      'hard': sum(1 for f in result['findings'] if f['severity'] == 'hard'),
                      'advisory': sum(1 for f in result['findings'] if f['severity'] == 'advisory')}))
    return 0 if result['passed'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
