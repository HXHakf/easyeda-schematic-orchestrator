import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
try:
    import circuit_audit as audit
    import readability_audit as read
except ModuleNotFoundError:
    audit = read = None


def target_of(pin_components, nets, modules=None):
    if modules is None:
        modules = [{'id': 'm', 'members': [cid for cid, _ in pin_components], 'core': [pin_components[0][0]],
                    'direct': [pins for _, _, pins in nets]}]
    else:
        owned = {module['id']: set(module['members']) for module in modules}
        modules = [{'id': module['id'], 'members': list(module['members']),
                    'core': list(module.get('core') or [module['members'][0]]),
                    'direct': [pins for _, _, pins in nets
                               if len({tuple(p) for p in pins}) >= 2 and all(p[0] in owned[module['id']] for p in pins)]}
                   for module in modules]
    return {
        'schemaVersion': 1,
        'components': [{'id': cid, 'ref': ref, 'pins': ['1']} for cid, ref in pin_components],
        'nets': [{'id': nid, 'name': name, 'scope': 'page', 'pins': pins} for nid, name, pins in nets],
        'nc': [],
        'modules': modules,
    }


def scene_of(pins, wires, labels=(), paper=None, grid=5):
    scene = {'schemaVersion': 1, 'units': 'raw', 'grid': grid, 'evidence': 'synthetic-fixture',
             'geometryScope': 'topology-only',
             'pins': [{'pin': item[0], 'page': item[3] if len(item) > 3 else 'p',
                       'at': item[1], 'direction': item[2]} for item in pins],
             'wires': [{'id': item[0], 'page': item[2] if len(item) > 2 else 'p',
                        'points': item[1]} for item in wires],
             'labels': [{'id': item[0], 'page': item[3] if len(item) > 3 else 'p',
                         'at': item[1], 'net': item[2]} for item in labels],
             'junctions': [], 'bodies': [], 'texts': [], 'textInventory': []}
    if paper:
        scene['paper'] = paper
    return scene


def straight_pair(gap):
    """Two unrelated horizontal two-terminal nets, `gap` raw apart."""
    target = target_of([('a', 'A1'), ('b', 'B1'), ('c', 'C1'), ('d', 'D1')],
                       [('n1', 'N1', [['a', '1'], ['b', '1']]),
                        ('n2', 'N2', [['c', '1'], ['d', '1']])])
    scene = scene_of([(['a', '1'], [0, 0], 'right'), (['b', '1'], [40, 0], 'left'),
                      (['c', '1'], [0, gap], 'right'), (['d', '1'], [40, gap], 'left')],
                     [('w1', [[0, 0], [40, 0]]), ('w2', [[0, gap], [40, gap]])])
    return target, scene


def bent_net(bends):
    route = {1: [[0, 0], [0, 20], [60, 20]],
             2: [[0, 0], [30, 0], [30, 20], [60, 20]],
             3: [[0, 0], [0, 20], [40, 20], [40, 40], [60, 40]]}[bends]
    target = target_of([('a', 'A1'), ('b', 'B1')], [('n', 'N', [['a', '1'], ['b', '1']])])
    scene = scene_of([(['a', '1'], [0, 0], 'right'), (['b', '1'], route[-1], 'left')],
                     [('w', route)])
    return target, scene


def two_modules(frame_gap=20.0, with_frames=True, titles=True):
    """Two self-contained modules stacked vertically; frame_gap is the clear space between frames."""
    top, bottom = 40.0, 50.0 + frame_gap
    target = target_of([('a', 'A1'), ('b', 'B1'), ('c', 'C1'), ('d', 'D1')],
                       [('n1', 'N1', [['a', '1'], ['b', '1']]),
                        ('n2', 'N2', [['c', '1'], ['d', '1']])],
                       modules=[{'id': 'one', 'members': ['a', 'b']},
                                {'id': 'two', 'members': ['c', 'd']}])
    scene = scene_of([(['a', '1'], [0, top], 'right'), (['b', '1'], [40, top], 'left'),
                      (['c', '1'], [0, bottom], 'right'), (['d', '1'], [40, bottom], 'left')],
                     [('w1', [[0, top], [40, top]]), ('w2', [[0, bottom], [40, bottom]])])
    if with_frames:
        scene['moduleFrames'] = [
            {'moduleId': 'one', 'page': 'p', 'bbox': [-12, top - 5, 52, top + 5],
             'titleTextId': 't1' if titles else None},
            {'moduleId': 'two', 'page': 'p', 'bbox': [-12, bottom - 5, 52, bottom + 5],
             'titleTextId': 't2' if titles else None}]
    if titles:
        scene['texts'] = [{'id': 't1', 'componentId': 'a', 'role': 'module-title', 'page': 'p',
                           'bbox': [-10, top + 1, 20, top + 4]},
                          {'id': 't2', 'componentId': 'c', 'role': 'module-title', 'page': 'p',
                           'bbox': [-10, bottom + 1, 20, bottom + 4]}]
        scene['textInventory'] = ['t1', 't2']
    return target, scene


class ReadabilityTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(read, 'readability_audit must be importable')

    def codes(self, result, severity=None):
        return {f['rule'] for f in result['findings'] if severity is None or f['severity'] == severity}

    def test_clean_two_net_page_passes_and_measures_spacing(self):
        target, scene = straight_pair(10)
        self.assertTrue(audit.audit_scene(target, scene)['passed'])
        result = read.audit_readability(target, scene)
        self.assertTrue(result['passed'], result['findings'])
        self.assertEqual(result['metrics']['crossings'], 0)
        self.assertEqual(result['metrics']['minParallelGapGrid'], 2.0)
        self.assertEqual(result['metrics']['bendsHistogram'], {0: 2})

    def test_electrically_legal_crossing_still_fails_readability(self):
        target = target_of([('a', 'A1'), ('b', 'B1'), ('c', 'C1'), ('d', 'D1')],
                           [('n1', 'N1', [['a', '1'], ['b', '1']]),
                            ('n2', 'N2', [['c', '1'], ['d', '1']])])
        scene = scene_of([(['a', '1'], [0, 10], 'right'), (['b', '1'], [40, 10], 'left'),
                          (['c', '1'], [20, -10], 'up'), (['d', '1'], [20, 30], 'down')],
                         [('w1', [[0, 10], [40, 10]]), ('w2', [[20, -10], [20, 30]])])
        self.assertTrue(audit.audit_scene(target, scene)['passed'],
                        'interior X must stay electrically legal')
        result = read.audit_readability(target, scene)
        self.assertFalse(result['passed'])
        self.assertIn('foreign-net-crossing', self.codes(result, 'hard'))
        self.assertEqual(result['metrics']['crossings'], 1)

    def test_three_bends_fail_two_bends_pass(self):
        target, scene = bent_net(2)
        self.assertTrue(read.audit_readability(target, scene)['passed'])
        self.assertEqual(read.audit_readability(target, scene)['metrics']['maxBends'], 2)
        target, scene = bent_net(3)
        self.assertFalse(read.audit_readability(target, scene)['passed'])
        self.assertIn('two-terminal-bends', self.codes(read.audit_readability(target, scene), 'hard'))

    def test_max_bends_threshold_is_explicit(self):
        target, scene = bent_net(3)
        self.assertTrue(read.audit_readability(target, scene, max_bends=3)['passed'])
        self.assertEqual(read.audit_readability(target, scene, max_bends=1)['thresholds']['maxBends'], 1)

    def test_labels_cannot_substitute_for_a_wire(self):
        target = target_of([('a', 'A1'), ('b', 'B1')], [('n', 'N', [['a', '1'], ['b', '1']])])
        scene = scene_of([(['a', '1'], [0, 0], 'right'), (['b', '1'], [80, 0], 'left')],
                         [('w1', [[0, 0], [20, 0]]), ('w2', [[80, 0], [100, 0]])],
                         labels=[('l1', [20, 0], 'n'), ('l2', [80, 0], 'n')])
        result = read.audit_readability(target, scene)
        self.assertIn('label-bridged-net', self.codes(result, 'advisory'))
        self.assertTrue(result['passed'], 'advisory rules do not fail the default gate')
        self.assertFalse(read.audit_readability(target, scene, strict=True)['passed'])

    def test_power_net_may_use_multiple_symbol_trees(self):
        target = target_of([('a', 'A1'), ('b', 'B1')], [('g', 'GND', [['a', '1'], ['b', '1']])])
        scene = scene_of([(['a', '1'], [0, 0], 'down'), (['b', '1'], [80, 0], 'down')],
                         [('w1', [[0, 0], [0, -20]]), ('w2', [[80, 0], [80, -20]])],
                         labels=[('l1', [0, -20], 'g'), ('l2', [80, -20], 'g')])
        self.assertTrue(read.audit_readability(target, scene, strict=True)['passed'])

    def test_unsplit_net_is_reported_hard(self):
        target = target_of([('a', 'A1'), ('b', 'B1')], [('n', 'N', [['a', '1'], ['b', '1']])])
        scene = scene_of([(['a', '1'], [0, 0], 'right'), (['b', '1'], [80, 0], 'left')],
                         [('w1', [[0, 0], [20, 0]]), ('w2', [[80, 0], [100, 0]])])
        self.assertIn('net-not-physically-connected', self.codes(read.audit_readability(target, scene), 'hard'))

    def test_parallel_spacing_below_two_grid_is_advisory(self):
        target, scene = straight_pair(5)
        result = read.audit_readability(target, scene)
        self.assertIn('parallel-spacing', self.codes(result, 'advisory'))
        self.assertTrue(result['passed'])
        self.assertFalse(read.audit_readability(target, scene, strict=True)['passed'])

    def test_paper_fit_uses_measured_usable_area(self):
        target, scene = straight_pair(10)
        scene['paper'] = {'name': 'A4', 'usable': [10, 10, 100, 100]}
        result = read.audit_readability(target, scene)
        self.assertIn('out-of-usable', self.codes(result, 'hard'))
        self.assertEqual(result['metrics']['paperName'], 'A4')
        scene['paper'] = {'name': 'A4', 'usable': [-50, -50, 150, 150]}
        self.assertTrue(read.audit_readability(target, scene)['passed'])

    def test_pin_inventory_mismatch_is_invalid_not_silent(self):
        target, scene = straight_pair(10)
        scene['pins'].pop()
        self.assertIn('invalid-input', self.codes(read.audit_readability(target, scene), 'hard'))

    def test_modular_layout_passes_with_frames_gap_and_titles(self):
        target, scene = two_modules()
        self.assertTrue(audit.audit_scene(target, scene)['passed'], audit.audit_scene(target, scene)['findings'])
        result = read.audit_readability(target, scene, strict=True)
        self.assertTrue(result['passed'], result['findings'])
        self.assertEqual(result['metrics']['modules'], 2)
        self.assertEqual(result['metrics']['moduleFrames'], 2)
        self.assertEqual(result['metrics']['minModuleGapGrid'], 4.0)
        self.assertEqual(result['metrics']['modulePhysicalBridges'], 0)

    def test_module_boundary_cannot_be_a_continuous_wire(self):
        target = target_of([('a', 'A1'), ('b', 'B1')], [('n', 'N', [['a', '1'], ['b', '1']])],
                           modules=[{'id': 'one', 'members': ['a']}, {'id': 'two', 'members': ['b']}])
        scene = scene_of([(['a', '1'], [0, 0], 'right'), (['b', '1'], [40, 0], 'left')],
                         [('w', [[0, 0], [40, 0]])])
        result = read.audit_readability(target, scene)
        self.assertIn('module-physical-crossing', self.codes(result, 'hard'))
        self.assertEqual(result['metrics']['modulePhysicalBridges'], 1)

    def test_module_boundary_may_be_labels_instead(self):
        target = target_of([('a', 'A1'), ('b', 'B1')], [('n', 'N', [['a', '1'], ['b', '1']])],
                           modules=[{'id': 'one', 'members': ['a']}, {'id': 'two', 'members': ['b']}])
        scene = scene_of([(['a', '1'], [0, 0], 'right'), (['b', '1'], [60, 0], 'left')],
                         [('w1', [[0, 0], [20, 0]]), ('w2', [[60, 0], [40, 0]])],
                         labels=[('l1', [20, 0], 'n'), ('l2', [40, 0], 'n')])
        result = read.audit_readability(target, scene)
        self.assertNotIn('module-physical-crossing', self.codes(result))
        self.assertTrue(result['passed'], result['findings'])

    def test_overlapping_module_frames_are_hard(self):
        target, scene = two_modules()
        scene['moduleFrames'][1]['bbox'] = [-12, 43, 52, 60]
        self.assertIn('module-overlap', self.codes(read.audit_readability(target, scene), 'hard'))

    def test_module_content_must_stay_inside_its_frame(self):
        target, scene = two_modules()
        scene['moduleFrames'][0]['bbox'] = [-12, 35, 30, 45]
        result = read.audit_readability(target, scene)
        self.assertIn('module-out-of-frame', self.codes(result, 'hard'))
        self.assertTrue(any(item.startswith('one:') for f in result['findings']
                            if f['rule'] == 'module-out-of-frame' for item in f['objects']))

    def test_every_wired_module_needs_a_declared_frame(self):
        target, scene = two_modules(with_frames=False)
        scene['moduleFrames'] = [{'moduleId': 'one', 'page': 'p', 'bbox': [-12, 35, 52, 45]}]
        self.assertIn('module-frame-missing', self.codes(read.audit_readability(target, scene), 'hard'))

    def test_tight_module_gutter_is_advisory(self):
        target, scene = two_modules(frame_gap=10.0)
        result = read.audit_readability(target, scene)
        self.assertIn('module-gutter', self.codes(result, 'advisory'))
        self.assertTrue(result['passed'], result['findings'])
        self.assertFalse(read.audit_readability(target, scene, strict=True)['passed'])

    def test_module_may_not_span_pages(self):
        target = target_of([('a', 'A1'), ('b', 'B1')], [('n', 'N', [['a', '1'], ['b', '1']])],
                           modules=[{'id': 'one', 'members': ['a', 'b']}])
        scene = scene_of([(['a', '1'], [0, 0], 'right', 'p'), (['b', '1'], [0, 0], 'right', 'q')],
                         [('w1', [[0, 0], [20, 0]], 'p'), ('w2', [[0, 0], [20, 0]], 'q')])
        self.assertIn('module-split-pages', self.codes(read.audit_readability(target, scene), 'hard'))

    def test_unknown_or_duplicate_module_frame_is_invalid_input(self):
        target, scene = two_modules()
        scene['moduleFrames'][0]['moduleId'] = 'ghost'
        self.assertIn('invalid-input', self.codes(read.audit_readability(target, scene), 'hard'))
        target, scene = two_modules()
        scene['moduleFrames'].append(dict(scene['moduleFrames'][0]))
        self.assertIn('invalid-input', self.codes(read.audit_readability(target, scene), 'hard'))

    def test_cli_exit_codes_and_new_file_only(self):
        target, scene = straight_pair(10)
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            target_path, scene_path = tmp / 'target.json', tmp / 'scene.json'
            target_path.write_text(json.dumps(target), encoding='utf-8')
            scene_path.write_text(json.dumps(scene), encoding='utf-8')
            out = tmp / 'readability-001.json'
            command = [sys.executable, str(SCRIPTS / 'readability_audit.py'), '--target', str(target_path),
                       '--scene', str(scene_path), '--out', str(out)]
            good = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(good.returncode, 0, good.stderr)
            report = json.loads(out.read_text(encoding='utf-8'))
            self.assertTrue(report['passed'])
            self.assertEqual(report['scope'], 'offline-readability')
            self.assertIn('inputSha256', report)
            self.assertEqual(len(report['inputSha256']), 2)
            repeat = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(repeat.returncode, 0, 'evidence files must never be overwritten')
            bad_scene = dict(scene)
            bad_scene['wires'] = scene['wires'] + [{'id': 'cross', 'page': 'p', 'points': [[20, -20], [20, 20]]}]
            scene_path.write_text(json.dumps(bad_scene), encoding='utf-8')
            bad = subprocess.run([sys.executable, str(SCRIPTS / 'readability_audit.py'), '--target',
                                  str(target_path), '--scene', str(scene_path), '--out', str(tmp / 'bad.json')],
                                 capture_output=True, text=True)
            self.assertEqual(bad.returncode, 2, bad.stderr)


if __name__ == '__main__':
    unittest.main()
