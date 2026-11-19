"""Audit the visible pin strokes separately from electrical connection points."""
import re
import xml.etree.ElementTree as ET

from .core import Refused
from .drawing_checks import EPS, coordinates, on

_NUMBER = r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?'
_STEM = re.compile(rf'M\s+({_NUMBER})\s+({_NUMBER})\s+([hv])\s+({_NUMBER})')
DIRECTIONS = {'left': (-1, 0), 'right': (1, 0), 'up': (0, 1), 'down': (0, -1)}


def native_pin_strokes(svg, components):
    """Read line endpoints from a native SVG with the raw Y-up calibration."""
    text = svg.read_text(encoding='utf-8-sig')
    if re.search(r'<!DOCTYPE|<!ENTITY', text, re.I):
        raise Refused('native pin stroke SVG cannot contain DTD/entity declarations')
    try:
        root = ET.fromstring(text)
    except ET.ParseError as error:
        raise Refused('native pin stroke SVG is unreadable') from error
    parents = {child: parent for parent in root.iter() for child in parent}
    nodes = {}
    for node in root.iter():
        if node.get('c_partid') == 'part_pin':
            ident = node.get('id')
            if not ident or ident in nodes:
                raise Refused('native pin stroke identity is ambiguous')
            nodes[ident] = node
    strokes = []
    for part in components:
        kind = part.get('componentType')
        if kind not in ('part', 'netport', 'netflag', 'netlabel'):
            continue
        pins = part.get('pins')
        ref = part.get('designator') or part.get('net') or part.get('primitiveId')
        if not pins:
            raise Refused(f'{ref}: native pin inventory is unavailable')
        for pin in pins:
            ident = pin['primitiveId']
            node = nodes.pop(ident, None)
            bars = [bar for bar in node if 'pin-bar' in bar.get('class', '').split()] if node is not None else []
            if len(bars) != 1 or not (match := _STEM.fullmatch(bars[0].get('d', ''))):
                raise Refused(f'{ref}.{pin.get("pinNumber")}: native pin stroke missing or unsupported')
            cursor = bars[0]
            while cursor is not None:
                if cursor.get('transform') or re.search(r'(?:^|;)\s*(transform|translate|rotate|scale)\s*:', cursor.get('style', ''), re.I):
                    raise Refused('transformed native pin stroke requires explicit calibration')
                cursor = parents.get(cursor)
            x, y, axis, length = match.groups()
            x, y, length = coordinates((float(x), -float(y), float(length)), 3)
            a = (x, y)
            observed = coordinates((pin['x'], pin['y']), 2)
            if any(abs(p - q) > EPS for p, q in zip(a, observed)):
                raise Refused(f'{ref}.{pin.get("pinNumber")}: native pin stroke anchor differs from readback')
            b = (x + length, y) if axis == 'h' else (x, y - length)
            strokes.append({'id': ident, 'ref': ref, 'kind': kind, 'pin': str(pin['pinNumber']),
                            'start': a, 'end': b})
    if nodes:
        raise Refused('native SVG contains pin strokes absent from measured inventory')
    return strokes


def stroke_contacts(strokes, wires):
    """Allow endpoint-to-endpoint attachment and report all other contacts."""
    def same(a, b):
        return all(abs(x - y) <= EPS for x, y in zip(a, b))

    findings = []
    for stroke in strokes:
        a, b = stroke['start'], stroke['end']
        if same(a, b):
            continue
        for ident, c, d in wires:
            horizontal = abs(a[1] - b[1]) <= EPS
            coord = 0 if horizontal else 1
            other = 1 - coord
            if abs(c[other] - d[other]) <= EPS:
                if abs(a[other] - c[other]) > EPS:
                    continue
                lo = max(min(a[coord], b[coord]), min(c[coord], d[coord]))
                hi = min(max(a[coord], b[coord]), max(c[coord], d[coord]))
                contact = (lo, a[other]) if horizontal else (a[other], lo)
                if lo - hi > EPS:
                    continue
                if hi - lo > EPS:
                    mode = 'overlap'
                elif same(contact, a) and (same(a, c) or same(a, d)):
                    continue
                else:
                    mode = 'contact'
            else:
                contact = (c[0], a[1]) if horizontal else (a[0], c[1])
                if not on(contact, a, b) or not on(contact, c, d):
                    continue
                if same(contact, a) and (same(a, c) or same(a, d)):
                    continue
                mode = 'crossing'
            findings.append({'wire': ident, 'symbol': stroke['ref'], 'pin': stroke['pin'],
                             'pin_id': stroke['id'], 'at': list(contact), 'type': mode})
    return findings


def projected_strokes(strokes, placed, compiled):
    """Translate measured strokes only when each pose and pin anchor stay bound."""
    parts = {part['designator']: part for part in placed if part.get('componentType') == 'part'}
    pins = {(part['designator'], str(pin['number'])): (part, pin) for part in compiled for pin in part['pins']}
    actual = []
    for stroke in strokes:
        ref, pin = stroke['ref'], stroke['pin']
        if stroke['kind'] != 'part':
            continue
        old, (part, new_pin) = parts[ref], pins.pop((ref, pin))
        if (old.get('rotation'), old.get('mirror')) != (part.get('rotation'), part.get('mirror')):
            raise Refused(f'{ref}: changed orientation requires a new native pin measurement')
        dx, dy = part['x'] - old['x'], part['y'] - old['y']
        start = (stroke['start'][0] + dx, stroke['start'][1] + dy)
        if any(abs(p - q) > EPS for p, q in zip(start, (new_pin['x'], new_pin['y']))):
            raise Refused(f'{ref}.{pin}: compiled stroke anchor differs from translated native pin')
        actual.append(dict(stroke, start=start, end=(stroke['end'][0] + dx, stroke['end'][1] + dy)))
    if pins:
        raise Refused('native stroke measurement does not cover compiled physical pins')
    return actual


def compiled_lines(layout):
    wires = [(f'wire-{i}', a, b) for i, wire in enumerate(layout['wires'])
             for a, b in zip(wire['points'], wire['points'][1:])]
    markers = []
    for index, flag in enumerate(layout['flags']):
        dx, dy = DIRECTIONS[flag['direction']]
        x, y, offset = flag['pinX'], flag['pinY'], flag['offset']
        anchor = (x + offset * dx, y + offset * dy)
        # Standard native symbol profiles, in raw units from the electrical anchor.
        length = {'power': 5, 'ground': 10, 'net_port_bi': 10,
                  'net_port_in': 10, 'net_port_out': 10}.get(flag['kind'])
        if length is None:
            raise Refused('unsupported marker stroke profile: ' + flag['kind'])
        ident = f'marker-{index}'
        wires.append((ident, (x, y), anchor))
        markers.append({'id': ident, 'ref': flag['net'], 'pin': '1', 'start': anchor,
                        'end': (anchor[0] + length * dx, anchor[1] + length * dy)})
    return wires, markers
