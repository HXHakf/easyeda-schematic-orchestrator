"""Logical intent, drawing projection and exact endpoint authorization data."""
from copy import deepcopy
from .core import Refused


def endpoint(value):
    if not isinstance(value, dict) or set(value) != {'componentId', 'pinNumber'}:
        raise Refused('path endpoint requires componentId and physical pinNumber')
    if any(not isinstance(v, str) or not v for v in value.values()):
        raise Refused('path endpoint identifiers must be nonempty strings')
    return value['componentId'], value['pinNumber']


def project_drawing_groups(plan):
    modules = {m['id']: m for m in plan['modules']}
    if len(modules) != len(plan['modules']):
        raise Refused('duplicate logical module IDs')
    refs = {c['ref']: c for c in plan['components']}
    ids = {c['id'] for c in plan['components']}
    if len(refs) != len(plan['components']) or len(ids) != len(plan['components']):
        raise Refused('drawing projection requires unique physical component identities')
    if any(c['module'] not in modules for c in plan['components']):
        raise Refused('drawing projection contains unknown logical ownership')
    for module in modules.values():
        core = refs.get(module['core_ref'])
        if core is None or core['module'] != module['id']:
            raise Refused('drawing projection core must belong to its original logical module')
    groups = plan.get('drawing_groups', [dict(id=m['id'], title=m['title'], modules=[m['id']]) for m in plan['modules']])
    seen, ids, result = set(), set(), []
    for group in groups:
        members = group['modules']
        if (not group['id'] or group['id'] in ids or not members or len(set(members)) != len(members)
                or set(members) - set(modules) or seen & set(members)):
            raise Refused('drawing groups require unique IDs and unique declared module ownership')
        seen.update(members)
        ids.add(group['id'])
        core_refs = {modules[m]['core_ref'] for m in members}
        components = [c for c in plan['components'] if c['module'] in members]
        result.append({'id': group['id'], 'name': group['title'],
                       'coreComponents': [c['id'] for c in components if c['ref'] in core_refs],
                       'peripheralComponents': [c['id'] for c in components if c['ref'] not in core_refs]})
    if seen != set(modules):
        raise Refused('drawing groups must cover every logical module exactly once')
    return result


def validate_primary_paths(plan):
    groups = project_drawing_groups(plan)
    pins = {(c['id'], p['number']): p for c in plan['components'] for p in c['pins']}
    ids = set()
    for path in plan.get('primary_paths', []):
        if not path['id'] or path['id'] in ids or not path['links']:
            raise Refused('primary paths require unique IDs and nonempty links')
        ids.add(path['id'])
        for link in path['links']:
            a, b = endpoint(link['from']), endpoint(link['to'])
            if a == b or a not in pins or b not in pins:
                raise Refused('primary path refers to absent or identical physical endpoints')
            if not link['net'] or any(pins[p]['net'] != link['net'] for p in (a, b)):
                raise Refused('primary path endpoints must be non-NC on the declared net')
    return groups


def direct_groups(plan):
    """Only reviewed local attachments extend exact primary endpoint authorization."""
    refs = {c['ref']: c for c in plan['components']}
    result = {m['id']: [] for m in plan['modules']}
    for c in plan['components']:
        if 'attach' in c:
            a = c['attach']
            result[c['module']].append([[c['id'], a['pin']], [refs[a['ref']]['id'], a['pin_number']]])
    return result


def audit_projection(plan, nets):
    """Map plan net names to actual audit identities by the complete physical inventory."""
    validate_primary_paths(plan)
    mapped = {}
    for name in {p['net'] for c in plan['components'] for p in c['pins'] if p['net'] is not None}:
        wanted = {(c['id'], p['number']) for c in plan['components'] for p in c['pins'] if p['net'] == name}
        matches = [n for n in nets if n['name'] == name and {tuple(p) for p in n['pins']} == wanted]
        if len(matches) != 1:
            raise Refused('supplementary target net identity differs: ' + name)
        if 'nets' in plan:
            definition = next(n for n in plan['nets'] if n['name'] == name)
            scope = 'global' if definition['scope'] == 'global' else 'page'
            if matches[0]['scope'] != scope:
                raise Refused('supplementary target net scope differs: ' + name)
        mapped[name] = matches[0]['id']
    paths = deepcopy(plan.get('primary_paths', []))
    for path in paths:
        for link in path['links']:
            link['net'] = mapped[link['net']]
    local = direct_groups(plan)
    modules = [{'id': m['id'], 'members': [c['id'] for c in plan['components'] if c['module'] == m['id']],
                'core': [c['id'] for c in plan['components'] if c['ref'] == m['core_ref']],
                'direct': local[m['id']]} for m in plan['modules']]
    groups = plan.get('drawing_groups', [dict(id=m['id'], title=m['title'], modules=[m['id']]) for m in plan['modules']])
    return {'modules': modules, 'primary_paths': paths, 'drawing_groups': deepcopy(groups)}


def check_audit_target(plan, target, *, modern=None):
    """Reconcile supplementary permissions against the protected plan."""
    expected = audit_projection(plan, target['nets'])
    def modules(rows, include_direct):
        result = {}
        for row in rows:
            if row['id'] in result:
                raise Refused('duplicate supplementary module ID')
            item = {'members': sorted(row['members']), 'core': sorted(row['core'])}
            if include_direct:
                item['direct'] = sorted(sorted(tuple(p) for p in group) for group in row['direct'])
            result[row['id']] = item
        return result
    if modern is None:
        modern = 'primary_paths' in plan or 'drawing_groups' in plan
    if modules(target['modules'], modern) != modules(expected['modules'], modern):
        raise Refused('supplementary target logical members/core/direct differs from protected plan')
    if target.get('primary_paths', []) != expected['primary_paths']:
        raise Refused('supplementary target primary paths differ from protected plan')
    groups = expected['drawing_groups'] if 'drawing_groups' in plan or modern else None
    if target.get('drawing_groups') != groups:
        raise Refused('supplementary target drawing groups differ from protected plan')
    return expected


def check_native_frames(plan, scene, data, *, modern=None):
    """Bind actual group frames/titles to the native primitive inventory."""
    if modern is None:
        modern = 'primary_paths' in plan or 'drawing_groups' in plan
    groups = project_drawing_groups(plan)
    frames = scene.get('moduleFrames', [])
    field = 'drawingGroupId' if modern else 'moduleId'
    if (len(frames) != len(groups) or any(set(f) & {'moduleId', 'drawingGroupId'} != {field} for f in frames)
            or {f[field] for f in frames} != {g['id'] for g in groups}):
        raise Refused('supplementary frame inventory differs from drawing projection')
    if not modern:
        return
    native = data['pagePrimitives']
    rectangles = {r['primitiveId']: r for r in native['rectangles']}
    if len(rectangles) != len(native['rectangles']) or len(rectangles) != len(frames):
        raise Refused('native frame count differs from drawing groups')
    titles = {g['id']: g['name'] for g in groups}
    texts = {t['primitiveId']: t for t in native['texts']}
    used = set()
    for frame in frames:
        primitive = frame.get('primitiveId')
        if primitive not in rectangles or primitive in used:
            raise Refused('frame requires a unique observed native rectangle primitiveId')
        used.add(primitive)
        r = rectangles[primitive]
        try:
            if r['Rotation'] != 0:
                raise Refused('rotated native frame is unsupported')
            x, y, w, h = (r[k] for k in ('TopLeftX', 'TopLeftY', 'Width', 'Height'))
            bounds = [x, y - h, x + w, y]
        except (KeyError, TypeError) as error:
            raise Refused('native frame geometry unavailable') from error
        from .drawing_checks import rectangle
        rectangle(bounds)
        if frame['bbox'] != bounds:
            raise Refused('frame bounds differ from native rectangle')
        title = texts.get(frame.get('titleTextId'))
        if title is None or title.get('Content') != titles[frame[field]]:
            raise Refused('frame title differs from native text')


def audit_tools():
    """Load the bundled offline auditors in installed and source checkouts alike."""
    import importlib.util
    import sys
    from pathlib import Path
    scripts = Path(__file__).resolve().parents[1] / 'extras/schematic-verified-layout/scripts'
    modules = []
    for name in ('circuit_audit', 'readability_audit'):
        spec = importlib.util.spec_from_file_location(name, scripts / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules[name] = module
        modules.append(module)
    return modules


def check_source_paths(plan, source):
    """Check authored module-local physical geometry before it enters a write queue."""
    if not plan.get('primary_paths') and not any(len(g['modules']) > 1 for g in plan.get('drawing_groups', [])):
        return
    parts = {c['ref']: c for c in plan['components']}
    from .planner import net_definitions
    definitions = net_definitions(plan)
    nets = [{'id': n, 'name': n, 'scope': 'global' if definitions[n]['scope'] == 'global' else 'page',
             'pins': [[c['id'], p['number']] for c in plan['components'] for p in c['pins'] if p['net'] == n]}
            for n in sorted({p['net'] for c in plan['components'] for p in c['pins'] if p['net'] is not None})]
    target = {'schemaVersion': 1, 'components': [{'id': c['id'], 'ref': c['ref'], 'pins': [p['number'] for p in c['pins']]}
                                               for c in plan['components']],
              'nets': nets, 'nc': [[c['id'], p['number']] for c in plan['components'] for p in c['pins'] if p['net'] is None],
              **audit_projection(plan, nets)}
    pins, wires = [], []
    for group in source.get('modules', []):
        for part in group['placements']:
            component = parts.get(part['designator'])
            if component is None:
                raise Refused('source path geometry contains unknown designator')
            for pin in part['pins']:
                pins.append({'pin': [component['id'], pin['number']], 'page': group['id'], 'at': [pin['x'], pin['y']]})
        for wire in group['wires']:
            wires.append({'id': 'wire-' + str(len(wires)), 'page': group['id'], 'points': wire['points']})
    audit, _ = audit_tools()
    if len(pins) != sum(len(c['pins']) for c in plan['components']) or {tuple(p['pin']) for p in pins} != {
            (c['id'], p['number']) for c in plan['components'] for p in c['pins']}:
        raise Refused('complete authored source physical pin geometry required for primary paths')
    segments = audit.scene_segments({'wires': wires})
    physical = audit.contact_graph(segments, pins, [], [], with_labels=False)
    issues = audit.physical_path_findings(target, pins, segments, physical)
    if issues:
        raise Refused('source primary path audit failed: ' + str(issues))
