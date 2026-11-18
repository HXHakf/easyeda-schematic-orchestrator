"""Drawing defects must fail through candidate and final-delivery entry points."""
import contextlib
import copy
import io
import tempfile
import unittest
from pathlib import Path

from eso import cli
from eso.assistance import compare_layouts
from eso.core import Refused, digest, dump, file_digest, load
from eso.evidence import evidence_index
import test_workflow as fixtures


def visual_checks():
    return {name: {'passed': True, 'objects': ['synthetic-module'],
                   'assessment': 'One synthetic module has a clear left to right reading path.'}
            for name in ('reading_order', 'peripheral_grouping', 'repeated_branches',
                         'orientations_and_labels', 'typography', 'page_balance')}


def bound_visual(workflow, report, root, svg_source='<svg/>'):
    svg, preview = root / 'image.svg', root / 'preview.png'
    svg.write_text(svg_source)
    preview.write_bytes(b'synthetic-preview')
    paths = {'snapshot': workflow.root / 'verification-history' / report['attempt'] / 'final-snapshot.json',
             'svg': svg, 'preview': preview}
    binding = {'attempt': report['attempt'], 'contract_sha256': digest(load(workflow.root / 'contract.json')),
               'snapshot_sha256': report['snapshot_sha256']}
    visual = root / 'visual.json'
    raw = {'passed': True, 'scope': 'host-visual-review', 'binding': binding, 'reviewer': 'synthetic tester',
           'files': {key: file_digest(path) for key, path in paths.items()},
           'coverage': {key: True for key in ('primary_paths', 'module_ownership', 'junctions',
                                           'external_text', 'internal_text', 'paper')},
           'drawing_review': {'checks': visual_checks(), 'open_findings': []}}
    dump(visual, raw)
    paths['report'] = visual
    reviews = dict(binding, layers={'visual': {'report': str(visual), 'files': {
        key: {'path': str(path), 'sha256': file_digest(path)} for key, path in paths.items()}}})
    return raw, reviews, visual


def drawing_run(root, *, crosses_frame=False, junction=None, text_bbox=None, modern=False):
    """A synthetic two-terminal drawing with real audit reports and a simulated EDA adapter."""
    import sys
    from eso.core import confirm
    from eso.requirements import contract_from
    from eso.planner import bind_plan
    from eso.workflow import Workflow
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'extras/schematic-verified-layout/scripts'))
    import circuit_audit
    import readability_audit
    contract = contract_from({'schema_version': 1, 'circuit_type': 'generic',
                              'function_description': 'Synthetic terminal connection',
                              'target_project': fixtures.TARGET['project_name']}, fixtures.TARGET)
    plan = {'schema_version': 1, 'contract_sha256': digest(contract), 'template': 'synthetic-terminals',
            'modules': [{'id': 'm', 'core_ref': 'J1', 'title': 'Terminal connection'}],
            'components': [{'id': cid, 'ref': ref, 'query': 'synthetic-terminal', 'role': 'connector',
                            'module': 'm', 'pins': [{'number': '1', 'name': '1', 'net': 'SIGNAL', 'nc_reason': None}]}
                           for cid, ref in [('a', 'J1'), ('b', 'J2')]],
            'evidence': [{'url': 'https://example.test/synthetic', 'section': 'Test fixture only'}], 'notes': []}
    if modern:
        plan['primary_paths'] = [{'id': 'main', 'title': 'Terminal connection', 'links': [
            {'from': {'componentId': 'a', 'pinNumber': '1'},
             'to': {'componentId': 'b', 'pinNumber': '1'}, 'net': 'SIGNAL'}]}]
        plan['modules'] = [{'id': mid, 'core_ref': ref, 'title': mid} for mid, ref in [('left', 'J1'), ('right', 'J2')]]
        for component, mid in zip(plan['components'], ['left', 'right']):
            component['module'] = mid
        plan['drawing_groups'] = [{'id': 'joined', 'title': 'Terminal connection', 'modules': ['left', 'right']}]
    bindings = {ref: {'libraryUuid': '1' * 32, 'deviceUuid': '2' * 32,
                     'verified_model': 'synthetic-terminal', 'evidence': 'synthetic only'} for ref in ('J1', 'J2')}
    ir = bind_plan(plan, bindings, fixtures.TARGET)
    snapshot = fixtures.blank()
    for part, x, bounds in zip(ir['components'], [10, 40], [[0, -3, 8, 3], [42, -3, 50, 3]]):
        snapshot['result']['components'].append({
            'componentType': 'part', 'primitiveId': part['id'], 'designator': part['ref'],
            'bbox': dict(zip(('minX', 'minY', 'maxX', 'maxY'), bounds)),
            'pins': [{'primitiveId': part['id'] + '-pin', 'pinNumber': '1', 'x': x, 'y': 0}]})
    snapshot['result']['wires'] = [{'x0': 10, 'y0': 0, 'x1': 40, 'y1': 0}]
    if modern:
        snapshot['result']['components'].append({'componentType': 'netport', 'primitiveId': 'marker', 'net': 'SIGNAL',
            'pins': [{'primitiveId': 'marker-pin', 'x': 25, 'y': 0}]})
    if junction:
        split = 30 if junction == 'moved' else 25
        snapshot['result']['wires'] = [{'x0': 10, 'y0': 0, 'x1': split, 'y1': 0},
                                     {'x0': split, 'y0': 0, 'x1': 40, 'y1': 0}]
    snapshot['result']['pagePrimitives']['texts'] = [{'primitiveId': 'title', 'Content': 'Terminal connection'}]
    if modern:
        snapshot['result']['pagePrimitives']['rectangles'] = [
            {'primitiveId': 'frame', 'TopLeftX': -10, 'TopLeftY': 20, 'Width': 70, 'Height': 40, 'Rotation': 0}]
    snapshot['result']['pagePrimitives']['attributes'] = [
        {'primitiveId': cid + '-text', 'ParentPrimitiveId': cid, 'Key': 'Designator'} for cid in ('a', 'b')]
    class Adapter(fixtures.FakeAdapter):
        applied = False
        def snapshot(self, target):
            return snapshot if self.applied else fixtures.blank()
        def run(self, args, **kwargs):
            value = super().run(args, **kwargs)
            if args[:2] == ['sch', 'apply'] and '--dry-run' not in args:
                self.applied = True
            return value
    adapter = Adapter(actual=ir)
    source = {'connectivity': ir}
    if modern:
        source['modules'] = [{'id': 'joined', 'title': 'Terminal connection', 'placements': [
            {'designator': c['ref'], 'pins': [{'number': '1', 'x': x, 'y': 0}]}
            for c, x in zip(plan['components'], [10, 40])],
            'wires': [{'points': [[10, 0], [40, 0]]}]}]
    dump(root / 'source.json', source)
    dump(root / 'before.json', fixtures.blank())
    workflow = Workflow(adapter, root / 'run')
    workflow.prepare(contract, confirm(contract, digest(contract), 'synthetic human'), plan,
                     root / 'source.json', root / 'before.json')
    workflow.execute()
    report = workflow.verify()
    fill = {'transparent': 'rgba(255, 0, 0, 0)', 'alpha': '#CC000000',
            'paint-server': 'url(#paint)', 'current-color': 'currentColor'}.get(junction, '#CC0000')
    dot = (f'<g class="shapeBox"><circle id="observed-dot" cx="25" cy="0" r="2.5" fill="{fill}"/></g>'
           if junction and junction != 'invented' else '')
    raw, reviews, visual_path = bound_visual(workflow, report, root,
        '<svg xmlns="http://www.w3.org/2000/svg"><text id="title" x="0" y="-15">Terminal connection</text>'
        '<text id="a-text" x="0" y="-7">J1</text><text id="b-text" x="42" y="-7">J2</text>' + dot + '</svg>')
    target = {'schemaVersion': 1, 'components': [{'id': cid, 'ref': ref, 'pins': ['1']} for cid, ref in [('a', 'J1'), ('b', 'J2')]],
              'nets': [{'id': 'signal', 'name': 'SIGNAL', 'scope': 'global', 'pins': [['a', '1'], ['b', '1']]}],
              'nc': [], 'modules': [{'id': 'm', 'members': ['a', 'b'], 'core': ['a'], 'direct': [[['a', '1'], ['b', '1']]]}]}
    bounds = text_bbox if text_bbox is not None else ([55, 10, 70, 15] if crosses_frame else [0, 10, 30, 15])
    scene = {'schemaVersion': 1, 'units': 'raw', 'grid': 5, 'evidence': 'synthetic-fixture', 'geometryScope': 'full',
             'pins': [{'pin': [cid, '1'], 'page': 'p', 'at': [x, 0], 'direction': direction}
                      for cid, x, direction in [('a', 10, 'right'), ('b', 40, 'left')]],
             'wires': [{'id': 'signal', 'page': 'p', 'points': [[10, 0], [40, 0]]}],
             'labels': [], 'junctions': [],
             'bodies': [{'id': cid + '-body', 'componentId': cid, 'page': 'p', 'bbox': box}
                        for cid, box in [('a', [0, -3, 8, 3]), ('b', [42, -3, 50, 3])]],
             'texts': [{'id': 'title', 'page': 'p', 'bbox': bounds},
                       {'id': 'a-text', 'page': 'p', 'componentId': 'a', 'role': 'designator', 'bbox': [-3, 5, 6, 9]},
                       {'id': 'b-text', 'page': 'p', 'componentId': 'b', 'role': 'designator', 'bbox': [42, 5, 51, 9]}],
             'textInventory': ['title', 'a-text', 'b-text'],
             'moduleFrames': [{'moduleId': 'm', 'page': 'p', 'bbox': [-10, -20, 60, 20], 'titleTextId': 'title'}]}
    if modern:
        target['nets'][0]['scope'] = 'page'
        from eso.main_paths import audit_projection
        target.update(audit_projection(plan, target['nets']))
        scene['labels'] = [{'id': 'marker', 'net': 'signal', 'page': 'p', 'at': [25, 0]}]
        scene['moduleFrames'] = [{'drawingGroupId': 'joined', 'primitiveId': 'frame', 'page': 'p',
                                  'bbox': [-10, -20, 60, 20], 'titleTextId': 'title'}]
    if junction:
        split = 30 if junction == 'moved' else 25
        scene['wires'] = [{'id': 'left', 'page': 'p', 'points': [[10, 0], [split, 0]]},
                          {'id': 'right', 'page': 'p', 'points': [[split, 0], [40, 0]]}]
        scene['junctions'] = [{'page': 'p', 'at': [30 if junction == 'moved' else 25, 0], 'svgId': 'observed-dot'}]
    dump(root / 'target.json', target)
    dump(root / 'scene.json', scene)
    hashes = {'target': file_digest(root / 'target.json'), 'scene': file_digest(root / 'scene.json')}
    geometry = circuit_audit.audit_scene(target, scene)
    if text_bbox is not None:
        # A retained passing report must never bypass current geometry rechecks.
        geometry.update(passed=True, findings=[])
    geometry.update(binding=raw['binding'], inputSha256=hashes)
    dump(root / 'geometry.json', geometry)
    readability = readability_audit.audit_readability(target, scene, strict=True)
    readability['inputSha256'] = hashes
    if crosses_frame or text_bbox is not None:
        # Simulate a previously accepted report with identical source hashes but no frame-edge rule.
        readability.update(passed=True, findings=[], checkerVersion='1.1.1')
    dump(root / 'readability.json', readability)
    dump(root / 'native-texts.json', {'svgSha256': file_digest(root / 'image.svg'),
                                    'texts': [{'id': text['id'], 'bbox': text['bbox']} for text in scene['texts']]})
    files = {'snapshot': workflow.root / 'verification-history' / report['attempt'] / 'final-snapshot.json',
             'target': root / 'target.json', 'scene': root / 'scene.json', 'report': root / 'geometry.json',
             'readability': root / 'readability.json', 'native_texts': root / 'native-texts.json', 'svg': root / 'image.svg'}
    reviews['layers']['geometry'] = {'report': str(root / 'geometry.json'), 'files': {
        key: {'path': str(path), 'sha256': file_digest(path)} for key, path in files.items()}}
    dump(root / 'reviews.json', reviews)
    return workflow, root / 'reviews.json'


class DrawingReviewTests(unittest.TestCase):
    def test_modern_delivery_rechecks_native_groups_and_target_permissions(self):
        for defect in (None, 'direct', 'core', 'group', 'native-frame', 'native-title', 'page', 'scope'):
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                workflow, path = drawing_run(root, modern=True, text_bbox=[20, -2, 30, 2] if defect == 'page' else None)
                reviews = load(path)
                layer = reviews['layers']['geometry']
                if defect:
                    target = load(root / 'target.json')
                    scene = load(root / 'scene.json')
                    if defect == 'direct': target['modules'][0]['direct'] = [[['a','1'],['b','1']]]
                    if defect == 'core': target['modules'][0]['core'] = ['b']
                    if defect == 'group': target['drawing_groups'][0]['title'] = 'fake'
                    if defect == 'native-frame': scene['moduleFrames'][0]['bbox'][0] = -15
                    if defect == 'native-title': scene['moduleFrames'][0]['titleTextId'] = 'a-text'
                    if defect == 'page': next(t for t in scene['texts'] if t['id'] == 'title')['page'] = 'invented'
                    if defect == 'scope': target['nets'][0]['scope'] = 'global'
                    dump(root / 'target.json', target)
                    dump(root / 'scene.json', scene)
                    for name in ('target', 'scene'):
                        layer['files'][name]['sha256'] = file_digest(layer['files'][name]['path'])
                    for name in ('report', 'readability'):
                        report = load(layer['files'][name]['path'])
                        report['inputSha256'] = {key: layer['files'][key]['sha256'] for key in ('target', 'scene')}
                        dump(layer['files'][name]['path'], report)
                        layer['files'][name]['sha256'] = file_digest(layer['files'][name]['path'])
                    dump(path, reviews)
                manifest = cli.delivery_manifest(workflow.root, root / 'manifest.json', path)
                self.assertEqual(manifest['state'], 'verified' if defect is None else 'incomplete', manifest)

    def test_final_delivery_rechecks_text_against_wires_bodies_and_other_text(self):
        for bounds in ([20, -2, 30, 2], [1, -2, 6, 2], [0, 6, 5, 8]):
            with self.subTest(bounds=bounds), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                workflow, reviews = drawing_run(root, text_bbox=bounds)
                manifest = cli.delivery_manifest(workflow.root, root / 'manifest.json', reviews)
                self.assertEqual(manifest['core_state'], 'verified')
                self.assertEqual(manifest['state'], 'incomplete')
                self.assertIn('body/text overlaps', str(manifest['unverified']))

    def test_current_text_geometry_rejects_old_passing_candidate_reports(self):
        base = {'wires': [{'id': 'wire', 'page': 'p', 'points': [[0, 0], [30, 0]]}],
                'bodies': [{'id': 'body', 'page': 'p', 'bbox': [40, 5, 50, 15]}],
                'texts': [{'id': 'value', 'page': 'p', 'bbox': [0, 20, 10, 25]}],
                'moduleFrames': []}
        variants = [
            ('clear', copy.deepcopy(base), True),
            ('value-reset-onto-wire', dict(base, texts=[{'id': 'value', 'page': 'p', 'bbox': [10, -2, 20, 2]}]), False),
            ('value-inside-body', dict(base, texts=[{'id': 'value', 'page': 'p', 'bbox': [42, 8, 48, 12]}]), False),
            ('net-name-over-value', dict(base, texts=base['texts'] + [{'id': 'net-name', 'page': 'p', 'bbox': [5, 22, 15, 27]}]), False),
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dump(root / 'target.json', {'synthetic': True})
            candidates = []
            for name, scene, expected_pass in variants:
                scene_path = root / (name + '-scene.json')
                dump(scene_path, scene)
                hashes = {'target': file_digest(root / 'target.json'), 'scene': file_digest(scene_path)}
                geometry = root / (name + '-geometry.json')
                readability = root / (name + '-readability.json')
                dump(geometry, {'scope': 'declared-topology-pin-exit-and-obstacle-geometry',
                                'geometryScope': 'full', 'passed': True, 'inputSha256': hashes})
                dump(readability, {'scope': 'offline-readability', 'strict': True,
                                   'passed': True, 'inputSha256': hashes})
                candidate = {'id': name, 'target': str(root / 'target.json'), 'scene': str(scene_path),
                             'geometry_report': str(geometry), 'readability_report': str(readability)}
                candidates.append(candidate)
                result = compare_layouts([candidate], 1)
                self.assertEqual(result['candidates'][0]['passed'], expected_pass, name)
            self.assertEqual(compare_layouts(candidates, len(candidates))['selected'], 'clear')

    def test_scene_junctions_require_matching_observed_svg_dots(self):
        for junction in ('invented', 'observed', 'moved', 'transparent', 'alpha', 'paint-server', 'current-color'):
            with self.subTest(junction=junction), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                workflow, reviews = drawing_run(root, junction=junction)
                manifest = cli.delivery_manifest(workflow.root, root / 'manifest.json', reviews)
                self.assertEqual(manifest['state'], 'verified' if junction == 'observed' else 'incomplete')

    def test_malformed_reviews_still_produce_incomplete_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow, path = drawing_run(root)
            good = load(path)
            for value in ([], dict(good, layers=[]), dict(good, layers={'visual': []}),
                          dict(good, layers={'visual': {'files': []}})):
                with self.subTest(value=value):
                    dump(path, value)
                    manifest = cli.delivery_manifest(workflow.root, root / 'manifest.json', path)
                    self.assertEqual(manifest['state'], 'incomplete')
                    self.assertTrue((root / 'manifest.json').is_file())

    def test_complete_drawing_passes_delivery_and_current_geometry_rejects_old_false_pass(self):
        for crossed in (False, True):
            with self.subTest(crossed=crossed), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                workflow, reviews = drawing_run(root, crosses_frame=crossed)
                manifest = cli.delivery_manifest(workflow.root, root / 'manifest.json', reviews)
                self.assertEqual(manifest['core_state'], 'verified')
                self.assertEqual(manifest['state'], 'incomplete' if crossed else 'verified', manifest['unverified'])

    def test_passing_old_reports_cannot_select_crossed_or_frame_overlapping_candidates(self):
        scenes = [
            {'wires': [{'id': 'h', 'page': 'p', 'points': [[0, 20], [40, 20]]},
                       {'id': 'v', 'page': 'p', 'points': [[20, 0], [20, 40]]}],
             'junctions': [], 'texts': [], 'moduleFrames': []},
            {'wires': [], 'texts': [{'id': 'note', 'page': 'p', 'bbox': [35, 10, 55, 20]}],
             'moduleFrames': [{'moduleId': 'm', 'page': 'p', 'bbox': [0, 0, 40, 40]}]}]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dump(root / 'target.json', {'synthetic': True})
            for scene in scenes:
                dump(root / 'scene.json', scene)
                binding = {'target': file_digest(root / 'target.json'), 'scene': file_digest(root / 'scene.json')}
                dump(root / 'geometry.json', {'scope': 'declared-topology-pin-exit-and-obstacle-geometry',
                     'geometryScope': 'full', 'passed': True, 'inputSha256': binding})
                dump(root / 'readability.json', {'scope': 'offline-readability', 'strict': True,
                     'passed': True, 'inputSha256': binding})
                candidate = {'id': 'bad', 'target': str(root / 'target.json'), 'scene': str(root / 'scene.json'),
                             'geometry_report': str(root / 'geometry.json'),
                             'readability_report': str(root / 'readability.json')}
                result = compare_layouts([candidate], 1)
                self.assertIsNone(result['selected'])
                self.assertFalse(result['candidates'][0]['passed'])

    def test_core_success_alone_cannot_complete_final_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = fixtures.FakeAdapter()
            workflow, ir = fixtures.OrchestrationTests().prepare(directory, adapter)
            adapter.actual = ir
            workflow.execute()
            workflow.verify()
            manifest = cli.delivery_manifest(workflow.root, Path(directory) / 'manifest.json')
            self.assertEqual(manifest['state'], 'incomplete')
            self.assertEqual(manifest['core_state'], 'verified')
            with contextlib.redirect_stdout(io.StringIO()):
                code = cli.main(['deliver', '--run-dir', str(workflow.root),
                                 '--out', str(Path(directory) / 'manifest-cli.json')])
            self.assertEqual(code, 3)

    def test_blanket_approval_and_open_visual_findings_cannot_complete_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adapter = fixtures.FakeAdapter()
            workflow, ir = fixtures.OrchestrationTests().prepare(directory, adapter)
            adapter.actual = ir
            workflow.execute()
            report = workflow.verify()
            good, reviews, path = bound_visual(workflow, report, root)
            self.assertEqual(evidence_index(workflow.root, reviews)['layers']['visual'], 'host-reviewed')
            for mode in ('legacy', 'missing_check', 'failed_check', 'blank_assessment', 'missing_objects', 'open_finding'):
                raw = copy.deepcopy(good)
                with self.subTest(mode=mode):
                    if mode == 'legacy':
                        del raw['drawing_review']
                    elif mode == 'missing_check':
                        del raw['drawing_review']['checks']['page_balance']
                    elif mode == 'failed_check':
                        raw['drawing_review']['checks']['page_balance']['passed'] = False
                    elif mode == 'blank_assessment':
                        raw['drawing_review']['checks']['page_balance']['assessment'] = ' '
                    elif mode == 'missing_objects':
                        raw['drawing_review']['checks']['page_balance']['objects'] = []
                    else:
                        raw['drawing_review']['open_findings'] = [{'object': 'R1', 'problem': 'text crosses frame'}]
                    dump(path, raw)
                    reviews['layers']['visual']['files']['report']['sha256'] = file_digest(path)
                    with self.assertRaises(Refused):
                        evidence_index(workflow.root, reviews)
