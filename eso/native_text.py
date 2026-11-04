"""Read-only native SVG text evidence. Never normalize unknown raw visibility."""
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

from .adapter import result
from .core import Refused, file_digest, load

TRANSFORM = [1, 0, 0, -1, 0, 0]


def render(svg):
    """Reuse the installed layout skill's isolated renderer, not the user browser."""
    skill = Path(os.environ.get('ESO_LAYOUT_SKILL', Path.home()/'.codex/skills/schematic-verified-layout'))
    helper = skill/'scripts/measure_svg_text.py'
    if not helper.is_file():
        raise Refused('native text renderer missing: install schematic-verified-layout or set ESO_LAYOUT_SKILL')
    with tempfile.TemporaryDirectory(prefix='eso-text-') as directory:
        out = Path(directory)/'texts.json'
        proc = subprocess.run([sys.executable, str(helper), str(svg), '--transform',
                               *map(str, TRANSFORM), '--out', str(out)], capture_output=True,
                              encoding='utf-8', timeout=60,
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if proc.returncode or not out.is_file():
            raise Refused('native text renderer failed: ' + (proc.stderr or proc.stdout)[-2000:])
        measured = load(out)
    measured['helperSha256'] = file_digest(helper)
    return measured


def needs_render(snapshot):
    for a in result(snapshot)['pagePrimitives']['attributes']:
        visible = [a.get(k) for k in ('KeyVisible', 'ValueVisible')]
        if any(type(v) is not bool for v in visible) or (any(visible) and not isinstance(a.get('bbox'), dict)):
            return True
    return False


def validate(snapshot, svg, measured):
    """Validate hash, exact pin anchors, native IDs and complete text ownership."""
    from .bridge import bbox, number
    if (not isinstance(measured, dict) or measured.get('source') != 'native-svg-browser-getBBox'
            or measured.get('svgSha256') != file_digest(svg) or measured.get('svgToEdaTransform') != TRANSFORM):
        raise Refused('native text evidence source/hash/coordinate transform mismatch')
    try:
        root = ET.fromstring(Path(svg).read_bytes())
    except (ET.ParseError, OSError) as error:
        raise Refused('invalid retained native SVG') from error
    if any(token in Path(svg).read_text(encoding='utf-8-sig').upper() for token in ('<!DOCTYPE', '<!ENTITY')):
        raise Refused('DTD/entity SVG is unsupported')
    parents = {child: parent for parent in root.iter() for child in parent}
    expected_text_ids = set()
    for element in root.iter():
        tag = element.tag.split('}')[-1]
        if tag == 'style' and re.search(r'\b(display|visibility|opacity)\s*:', element.text or '', re.I):
            raise Refused('stylesheet visibility requires explicit native text inventory support')
        if tag != 'text' or not ''.join(element.itertext()).strip(): continue
        hidden, node = False, element
        while node is not None:
            style = dict(pair.strip().split(':',1) for pair in node.get('style','').split(';') if ':' in pair)
            def property_value(key):
                return style.get(key, node.get(key, '')).strip().lower()
            hidden |= (node.tag.split('}')[-1] == 'defs' or property_value('display') == 'none'
                       or property_value('visibility') in ('hidden','collapse'))
            opacity = property_value('opacity')
            if opacity:
                try: hidden |= float(opacity) == 0
                except ValueError as error: raise Refused('unsupported native opacity expression') from error
            node = parents.get(node)
        if not hidden:
            if not element.get('id'): raise Refused('visible native text has no stable identity')
            expected_text_ids.add(element.get('id'))
    ids = {}
    for e in root.iter():
        if e.get('id'):
            if e.get('id') in ids: raise Refused('duplicate native SVG ID')
            ids[e.get('id')] = e
    data = result(snapshot)
    parts = [c for c in data['components'] if c.get('componentType') == 'part']
    pins = [pin for c in parts for pin in c['pins']]
    anchors = []
    for p in pins:
        e = ids.get(p['primitiveId'])
        if e is None or e.get('c_partid') != 'part_pin': raise Refused('native SVG pin anchor missing')
        circles = [child for child in e if child.tag.split('}')[-1] == 'circle' and child.get('cx') is not None]
        if len(circles) != 1: raise Refused('ambiguous native SVG pin anchor')
        node = circles[0]
        while node is not None:
            if node.get('transform') or re.search(r'(?:^|;)\s*(?:transform|translate|rotate|scale)\s*:', node.get('style', ''), re.I):
                raise Refused('transformed SVG pin anchors require explicit calibration')
            node = parents.get(node)
        try: x, y = float(circles[0].get('cx')), -float(circles[0].get('cy'))
        except (TypeError, ValueError) as error: raise Refused('invalid SVG pin coordinates') from error
        number(x, 'SVG pin.x'); number(y, 'SVG pin.y')
        if abs(x-number(p.get('x'), 'pin.x')) > 1e-6 or abs(y-number(p.get('y'), 'pin.y')) > 1e-6:
            raise Refused('native SVG-to-raw calibration differs from physical pins')
        anchors.append([x,y])
    if len(set(map(tuple, anchors))) < 2: raise Refused('insufficient independent native pin anchors')
    attrs = {a['primitiveId']: a for a in data['pagePrimitives']['attributes']}
    texts = measured.get('texts')
    if not isinstance(texts, list): raise Refused('native visible text inventory missing')
    measured_ids, boxes, fixed = set(), {}, []
    sheet_ids = {c['primitiveId'] for c in data['components'] if c.get('componentType') == 'sheet'}
    native_sheets = [e for e in root.iter() if e.get('c_partid') == 'sheet']
    for t in texts:
        if not isinstance(t, dict): raise Refused('invalid native text record')
        tid = t.get('id')
        if not tid or tid in measured_ids or tid not in ids: raise Refused('ambiguous or missing native text identity')
        measured_ids.add(tid)
        values = t.get('bbox')
        if not isinstance(values, list) or len(values) != 4: raise Refused('invalid native text bbox')
        box = bbox(dict(zip(('minX','minY','maxX','maxY'), values)), 'native text')
        if tid in attrs:
            boxes.setdefault(attrs[tid]['ParentPrimitiveId'], []).append(box)
        else:
            # Only native sheet-owned fixed printed text may lack an attribute ID.
            node = ids[tid]
            while node is not None and node.get('id') not in sheet_ids and not (
                    len(sheet_ids) == 1 and len(native_sheets) == 1 and node is native_sheets[0]):
                node = parents.get(node)
            if node is None: raise Refused('unowned rendered native text: ' + tid)
            fixed.append(tid)
    for a in attrs.values():
        # A nonempty known-visible attribute missing from the export is not hidden.
        if a['primitiveId'] not in measured_ids and (a.get('KeyVisible') is True or
                (a.get('ValueVisible') is True and a.get('Value') not in ('', None))):
            raise Refused('known visible attribute missing from native SVG: ' + a['primitiveId'])
    if measured_ids != expected_text_ids:
        raise Refused('native rendered text inventory is incomplete or inconsistent with SVG')
    return boxes, {'source': measured['source'], 'svgSha256': measured['svgSha256'],
                   'calibratedPinCount': len(anchors), 'fixedSheetTextIds': fixed,
                   'nullableVisibilityPreserved': [a['primitiveId'] for a in attrs.values() if
                       any(type(a.get(k)) is not bool for k in ('KeyVisible','ValueVisible'))],
                   'notRenderedAttributeIds': sorted(set(attrs)-measured_ids),
                   'unverified': measured.get('unverified', []),
                   'rawSnapshotModified': False}
