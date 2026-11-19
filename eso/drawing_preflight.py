"""Run the pre-write gates on the exact compiled schematic."""
from collections import Counter

from .adapter import result
from .core import Refused, file_digest, load
from .drawing_checks import drawing_obstacles
from .pin_strokes import compiled_lines, native_pin_strokes, projected_strokes, stroke_contacts


STAGED_SVG = 'measurement--measured-sheet.svg'
REVISION_SVG = 'revision-measured-sheet.svg'


def measurement_file(bundle):
    artifacts = bundle['artifacts']
    if STAGED_SVG in artifacts:
        return STAGED_SVG
    if REVISION_SVG in artifacts:
        return REVISION_SVG
    return None


def preflight(root, bundle, scene):
    """Recompute geometry; a supplied pass/fail report cannot authorize writes."""
    from .main_paths import audit_projection, audit_tools
    from .planner import net_definitions
    from .visual_review import check_candidate_geometry

    compiled = load(root / 'upstream-plan.json')
    layout = compiled['layout']
    before = result(load(root / 'before.json'))['components']
    svg_name = measurement_file(bundle)
    if svg_name is None:
        raise Refused('current measured drawing needs its native pre-write SVG')
    svg = root / svg_name
    if file_digest(svg) != bundle['artifacts'][svg_name]:
        raise Refused('pre-write native SVG changed')
    measured = native_pin_strokes(svg, before)
    wires, markers = compiled_lines(layout)
    stems = projected_strokes(measured, before, layout['placements'])
    hits = stroke_contacts(stems + markers, wires)
    report = {'passed': False, 'pin_strokes': len(stems), 'marker_stroke_profiles': len(markers),
              'wire_pin_contacts': hits}
    if hits:
        return report
    if not isinstance(scene, dict):
        raise Refused('execute requires --drawing-scene for a measured current drawing')
    if (scene.get('geometryScope') != 'full' or scene.get('evidence') != 'measured'
            or scene.get('grid') != 5):
        raise Refused('pre-write scene must provide full measured geometry on the compiled 5-raw grid')
    page = bundle['target']['document_uuid']
    fields = ('pins', 'wires', 'labels', 'bodies', 'texts', 'moduleFrames')
    if any(row.get('page') != page for field in fields for row in scene[field]):
        raise Refused('pre-write scene must stay on the bound schematic page')
    # Compiled wires encode endpoints; a scene cannot invent a dot to exempt an X.
    if scene['junctions']:
        raise Refused('pre-write scene cannot add junction dots absent from the compiled drawing')
    label_texts = Counter(text.get('markerId') for text in scene['texts'] if text.get('role') == 'net-label')
    if label_texts != Counter(label['id'] for label in scene['labels']):
        raise Refused('pre-write scene must include one text box for every compiled network marker')
    keys = ('minX', 'minY', 'maxX', 'maxY')
    paper = scene.get('paper', {})
    if (paper.get('usable') != [compiled['sheetBorder'][key] for key in keys]
            or paper.get('keepouts') != [[box[key] for key in keys] for box in (compiled.get('keepouts') or [])]):
        raise Refused('pre-write scene paper geometry differs from the compiled drawing')
    plan = load(root / 'plan.json')
    definitions = net_definitions(plan)
    nets = [{'id': name, 'name': name, 'scope': 'global' if entry['scope'] == 'global' else 'page',
             'pins': [[part['id'], pin['number']] for part in plan['components']
                      for pin in part['pins'] if pin['net'] == name]} for name, entry in definitions.items()]
    target = {'schemaVersion': 1, 'nets': nets,
              'components': [{'id': part['id'], 'ref': part['ref'], 'pins': [pin['number'] for pin in part['pins']]}
                             for part in plan['components']],
              'nc': [[part['id'], pin['number']] for part in plan['components'] for pin in part['pins'] if pin['net'] is None],
              **audit_projection(plan, nets)}
    check_candidate_geometry(layout, scene, {part['ref']: part for part in plan['components']}, target)
    observed_directions = {tuple(row['pin']): row.get('direction') for row in scene['pins']}
    refs = {part['ref']: part['id'] for part in plan['components']}
    cardinal = {0: 'right', 90: 'up', 180: 'left', 270: 'down'}
    if any(observed_directions[(refs[part['designator']], pin['number'])] != cardinal[pin['rotation'] % 360]
           for part in layout['placements'] for pin in part['pins']):
        raise Refused('pre-write pin directions differ from the compiled drawing')
    audit, readability = audit_tools()
    report['geometry'] = audit.audit_scene(target, scene)
    report['readability'] = readability.audit_readability(target, scene, strict=True)
    report['drawing_obstacles'] = drawing_obstacles(scene)
    report['passed'] = (report['geometry']['passed'] is True and report['readability']['passed'] is True
                        and not any(report['drawing_obstacles'].values()))
    return report
