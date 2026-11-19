import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from eso.core import Refused, dump, file_digest, load
from eso.drawing_preflight import STAGED_SVG, preflight
from eso.pin_strokes import native_pin_strokes, stroke_contacts
from eso.workflow import Workflow


class PinStrokeTests(unittest.TestCase):
    def test_visible_stem_blocks_vbus_but_allows_its_outward_connection(self):
        with tempfile.TemporaryDirectory() as t:
            svg = Path(t) / 'native.svg'
            svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"><g id="cc" c_partid="part_pin">'
                           '<path class="pin-bar" d="M 130 -555 h -10"/></g></svg>')
            components = [{'componentType': 'part', 'designator': 'J1', 'pins': [
                {'primitiveId': 'cc', 'pinNumber': 'A5', 'x': 130, 'y': 555}]}]
            stems = native_pin_strokes(svg, components)
            wires = [('vbus', (125, 490), (125, 570)), ('cc1', (130, 555), (140, 555)),
                     ('pass-through', (130, 545), (130, 565))]
            self.assertEqual([hit['wire'] for hit in stroke_contacts(stems, wires)], ['vbus', 'pass-through'])
            components[0]['pins'][0]['x'] = 135
            with self.assertRaisesRegex(Refused, 'anchor'):
                native_pin_strokes(svg, components)

    def test_collinear_overlap_includes_own_pin(self):
        strokes = [{'id': 'p', 'ref': 'J1', 'pin': '1', 'start': (10, 10), 'end': (0, 10)}]
        self.assertTrue(stroke_contacts(strokes, [('wire', (5, 10), (20, 10))]))
        self.assertFalse(stroke_contacts(strokes, [('wire', (10, 10), (20, 10))]))


class PreflightTests(unittest.TestCase):
    def test_measured_current_candidate_requires_strict_readability_before_apply(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            page = 'schematic'
            box = lambda x0, y0, x1, y1: dict(zip(('minX', 'minY', 'maxX', 'maxY'), (x0, y0, x1, y1)))
            parts = [
                ('a', 'J1', 0, 20, 0, box(0, -5, 10, 5), box(0, 10, 10, 15)),
                ('b', 'J2', 80, 60, 180, box(70, -5, 80, 5), box(70, 10, 80, 15)),
            ]
            plan = {'modules': [{'id': 'group', 'title': 'Link', 'core_ref': 'J1'}], 'components': [
                {'id': cid, 'ref': ref, 'module': 'group', 'pins': [{'number': '1', 'net': 'SIGNAL'}]}
                for cid, ref, *_ in parts]}
            dump(root / 'plan.json', plan)
            placed = [{'designator': ref, 'componentType': 'part', 'x': x, 'y': 0, 'rotation': rot, 'mirror': False,
                       'pins': [{'primitiveId': cid, 'pinNumber': '1', 'x': px, 'y': 0}]}
                      for cid, ref, x, px, rot, *_ in parts]
            dump(root / 'before.json', {'ok': True, 'result': {'components': placed}})
            svg = root / STAGED_SVG
            svg.write_text('<svg><g id="a" c_partid="part_pin"><path class="pin-bar" d="M 20 0 h -10"/></g>'
                           '<g id="b" c_partid="part_pin"><path class="pin-bar" d="M 60 0 h 10"/></g></svg>')
            border = [-25, -25, 105, 50]
            frame = [-20, -20, 100, 40]
            positions = [{'designator': ref, 'x': x, 'y': 0, 'rotation': rot, 'mirror': False,
                          'bbox': body, 'textBboxes': [text],
                          'pins': [{'number': '1', 'x': px, 'y': 0, 'rotation': rot}]}
                         for _, ref, x, px, rot, body, text in parts]
            lines = [{'points': [[20, 0], [60, 0]]}]
            compiled = {'sheetBorder': box(*border), 'keepouts': [],
                        'layout': {'placements': positions, 'wires': lines, 'flags': [],
                                   'frames': [{'id': 'group', 'rect': box(*frame)}]}}
            dump(root / 'upstream-plan.json', compiled)
            bundle = {'state': 'prepared', 'target': {'document_uuid': page, 'project_uuid': 'test'},
                      'artifacts': {STAGED_SVG: file_digest(svg)}}
            dump(root / 'bundle.json', bundle)
            dump(root / 'contract.json', {'design_evidence': {'policy_version': 1}})
            pin_rows = [{'pin': [cid, '1'], 'page': page, 'at': [px, 0],
                         'direction': 'right' if rot == 0 else 'left'}
                        for cid, _, _, px, rot, *_ in parts]
            scene = {'schemaVersion': 1, 'units': 'raw', 'grid': 5, 'evidence': 'measured', 'geometryScope': 'full',
                     'paper': {'usable': border, 'keepouts': []}, 'pins': pin_rows, 'labels': [], 'junctions': [],
                     'wires': [dict(lines[0], id='signal', page=page)],
                     'bodies': [{'id': cid, 'componentId': cid, 'page': page, 'bbox': list(body.values())}
                                for cid, _, _, _, _, body, _ in parts],
                     'texts': [{'id': cid+'-name', 'componentId': cid, 'page': page, 'bbox': list(text.values()),
                                'role': 'designator'} for cid, _, _, _, _, _, text in parts],
                     'textInventory': ['a-name', 'b-name', 'title'],
                     'moduleFrames': [{'drawingGroupId': 'group', 'titleTextId': 'title', 'page': page, 'bbox': frame}]}
            scene['texts'].append({'id': 'title', 'page': page, 'role': 'module-title', 'bbox': [0, 25, 25, 30]})
            self.assertTrue(preflight(root, bundle, scene)['passed'])
            scene['texts'][-1]['bbox'] = [30, -1, 50, 1]
            scene_path = root / 'scene.json'
            dump(scene_path, scene)
            adapter = Mock()
            workflow = Workflow(adapter, root)
            with patch.object(workflow, '_check_bundle'):
                with self.assertRaisesRegex(Refused, 'requires --drawing-scene'):
                    workflow.execute()
                with self.assertRaisesRegex(Refused, 'strict geometry or readability'):
                    workflow.execute(drawing_scene=scene_path)
            self.assertFalse((root / 'execution-started.lock').exists())
            self.assertEqual(adapter.mock_calls, [])
            self.assertFalse(load(root / 'drawing-preflight.json')['passed'])
