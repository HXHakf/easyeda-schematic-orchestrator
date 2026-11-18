"""Exercise independent comparison and final delivery with real audit inputs."""
import contextlib
import copy
import json
import io
from pathlib import Path
import tempfile
import unittest

from eso import cli
from eso.assistance import compare_layouts
from eso.core import Refused, confirm, digest, dump, file_digest, load
from eso.evidence import design_qualification
from eso.main_paths import audit_tools
from eso.visual_review import comparison_subject
from test_drawing_review import drawing_run, visual_checks
from test_workflow import FakeAdapter


def independent_review(root, subject, *, context='independent-visual', outcome=None):
    invocation = dict(subject_sha256=digest(subject), reviewer_id='visual-test', context_id=context,
                      author_context_id='author-test', request='Synthetic input for independent visual review',
                      response=outcome if outcome is not None else {})
    path = root / 'visual-invocation.json'
    dump(path, invocation)
    return dict(subject_sha256=digest(subject), reviewer=dict(id='visual-test', context_id=context, independent=True),
                invocation=dict(path=str(path), sha256=file_digest(path)))


class IndependentDrawingTests(unittest.TestCase):
    def test_new_policy_requires_current_independent_visual_review_for_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow, reviews_path = drawing_run(root, modern=True, new_policy=True)
            output = root / 'delivery.json'
            self.assertEqual(cli.delivery_manifest(workflow.root, output, reviews_path)['state'], 'incomplete')
            reviews = load(reviews_path)
            layer = reviews['layers']['visual']
            raw = load(layer['report'])
            subject = {'binding': raw['binding'], 'files': raw['files']}
            native_preview = Path(layer['files']['preview']['path']).read_bytes()
            good_outcome = {key: copy.deepcopy(raw[key]) for key in ('passed', 'coverage', 'drawing_review')}
            for defect in ('same-author', 'preview-replaced', 'preview-rebound',
                           'open-finding', 'edited-outcome', None):
                with self.subTest(defect=defect):
                    outcome = (dict(good_outcome, drawing_review={'open_findings': [{'object': 'J1'}]})
                               if defect == 'edited-outcome' else good_outcome)
                    review = independent_review(root, subject,
                                                context='author-test' if defect == 'same-author' else 'independent-visual',
                                                outcome=outcome)
                    raw['independent_review'] = review
                    raw['reviewer'] = 'visual-test'
                    raw['drawing_review']['open_findings'] = [{'object': 'J1'}] if defect == 'open-finding' else []
                    preview = Path(layer['files']['preview']['path'])
                    preview.write_bytes(b'changed' if defect in ('preview-replaced', 'preview-rebound') else native_preview)
                    layer['files']['preview']['sha256'] = file_digest(preview)
                    raw['files'] = dict(subject['files'], preview=file_digest(preview))
                    if defect == 'preview-rebound':
                        review = independent_review(root, {'binding': raw['binding'], 'files': raw['files']},
                                                    outcome=good_outcome)
                        raw['independent_review'] = review
                    dump(layer['report'], raw)
                    layer['files']['report']['sha256'] = file_digest(layer['report'])
                    layer['files']['invocation'] = review['invocation']
                    dump(reviews_path, reviews)
                    manifest = cli.delivery_manifest(workflow.root, output, reviews_path)
                    self.assertEqual(manifest['core_state'], 'verified')
                    self.assertEqual(manifest['state'], 'incomplete')
                    if defect is None:
                        self.assertIn('new design delivery requires --iteration-dir', ' '.join(manifest['unverified']))
                    else:
                        self.assertNotIn('new design delivery requires --iteration-dir', ' '.join(manifest['unverified']))
            qualified = design_qualification(workflow.root, reviews)
            self.assertTrue(qualified['qualified'])
            self.assertEqual(qualified['evidence']['electrical'], 'independently-reviewed')
            self.assertEqual(qualified['evidence']['hardware_validation'], 'unverified')

    def test_native_export_failure_leaves_core_verified_and_visual_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow, reviews_path = drawing_run(root, modern=True, new_policy=True, native_export_error='png')
            report = load(workflow.root / 'verification.json')
            self.assertEqual(report['state'], 'verified')
            self.assertEqual(report['native_export'], {
                'passed': False, 'issues': ['png: synthetic image export failure']})
            manifest = cli.delivery_manifest(workflow.root, root / 'delivery.json', reviews_path)
            self.assertEqual(manifest['core_state'], 'verified')
            self.assertEqual(manifest['state'], 'incomplete')

    def test_independent_clarity_order_precedes_area_on_normal_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow, _ = drawing_run(root, modern=True)
            audit, readability = audit_tools()
            candidates = []
            for cid, shift in (('compact', 0), ('clear', 20)):
                target = load(root / 'target.json')
                scene = load(root / 'scene.json')
                target['drawing_groups'][0]['title'] += ' ' + cid
                # This candidate connects the two in-page pins directly.
                scene['labels'] = []
                if shift:
                    scene['pins'][1]['at'][0] += shift
                    scene['wires'][0]['points'][1][0] += shift
                    for row in (scene['bodies'][1], scene['texts'][2]):
                        for index in (0, 2):
                            row['bbox'][index] += shift
                    scene['moduleFrames'][0]['bbox'][2] += shift
                folder = root / cid
                folder.mkdir()
                protected_run, source = prepare_candidate(workflow, folder, target, scene,
                                                          select_page=bool(shift))
                input_data = dict(target=target, scene=scene)
                paths = {name: folder / (name + '.json') for name in input_data}
                paths['source'] = source
                for name, value in input_data.items():
                    dump(paths[name], value)
                hashes = {name: file_digest(paths[name]) for name in ('target', 'scene')}
                results = {'geometry_report': audit.audit_scene(target, scene),
                           'readability_report': readability.audit_readability(target, scene, strict=True)}
                for name, result in results.items():
                    self.assertTrue(result['passed'], result['findings'])
                    paths[name] = folder / (name + '.json')
                    dump(paths[name], dict(result, inputSha256=hashes))
                preview = folder / 'preview.svg'
                preview.write_text(f'<svg><text x="1" y="5">{cid}</text></svg>')
                candidates.append(dict(id=cid, run_dir=str(protected_run), **{key: str(path) for key, path in paths.items()},
                                       preview=dict(path=str(preview), pixels_per_raw=1, render_method='synthetic SVG',
                                                    source_sha256=file_digest(paths['source']), scene_sha256=file_digest(paths['scene']))))
            subject = comparison_subject(candidates, 'author-test')
            evaluations = {
                name: dict(accepted=True, clarity_rank=rank, assessment=f'{name} primary path traceability',
                           drawing_review=dict(checks=visual_checks(), open_findings=[]))
                for name, rank in [('compact', 2), ('clear', 1)]}
            review = independent_review(root, subject, outcome={'evaluations': evaluations})
            review.update(scope='independent-layout-comparison', evaluations=evaluations)
            path, manifest, result_path = (root / name for name in ('comparison.json', 'candidates.json', 'result.json'))
            dump(path, review)
            dump(manifest, dict(candidates=candidates, max_candidates=2, visual_comparison=str(path),
                                author_context_id='author-test', contract=str(workflow.root / 'contract.json'),
                                plan=str(workflow.root / 'plan.json')))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(['layout-compare', str(manifest), '--out', str(result_path)]), 0)
            compared = load(result_path)
            self.assertEqual(compared['selected'], 'clear')
            self.assertLess(compared['candidates'][0]['score'][-1], compared['candidates'][1]['score'][-1])
            self.assertFalse(compared['execution_ready'])
            with self.assertRaisesRegex(Refused, 'requires independent'):
                compare_layouts(candidates, 2)
            modified = copy.deepcopy(review)
            modified['evaluations']['compact']['clarity_rank'] = 1
            dump(path, modified)
            with self.assertRaisesRegex(Refused, 'outcome differs'):
                compare_layouts(candidates, 2, visual_comparison=path, author_context_id='author-test')
            dump(path, review)
            bad_source = [dict(candidates[0], source=candidates[1]['source']), candidates[1]]
            with self.assertRaisesRegex(Refused, 'source differs from the protected'):
                compare_layouts(bad_source, 2, visual_comparison=path, author_context_id='author-test')
            scene = load(candidates[0]['scene'])
            shifted = copy.deepcopy(scene)
            shifted['moduleFrames'][0]['bbox'][0] -= 5
            dump(candidates[0]['scene'], shifted)
            with self.assertRaisesRegex(Refused, 'geometry differs'):
                compare_layouts(candidates, 2, visual_comparison=path, author_context_id='author-test')
            dump(candidates[0]['scene'], scene)
            stripped = {k: v for k, v in load(candidates[0]['target']).items()
                        if k not in ('primary_paths', 'drawing_groups')}
            stray_target = root / 'stripped-target.json'
            dump(stray_target, stripped)
            with self.assertRaisesRegex(Refused, 'requires independent'):
                compare_layouts([dict(candidates[0], target=stray_target)], 1)
            Path(candidates[0]['preview']['path']).write_text('<svg><text>replaced</text></svg>')
            with self.assertRaisesRegex(Refused, 'another drawing or candidate set'):
                compare_layouts(candidates, 2, visual_comparison=path, author_context_id='author-test')


def prepare_candidate(workflow, folder, target, scene, *, select_page=False):
    """Use the actual preparation workflow with deterministic synthetic compiler geometry."""
    from eso.workflow import Workflow

    contract, plan, source = (load(workflow.root / name) for name in
                              ('contract.json', 'plan.json', 'composition.json'))
    title = target['drawing_groups'][0]['title']
    plan['drawing_groups'] = target['drawing_groups']
    source['connectivity']['modules'][0]['name'] = title
    module = source['modules'][0]
    module['title'] = title
    by_id = {row['id']: row['ref'] for row in plan['components']}
    position = {by_id[pin['pin'][0]]: pin['at'] for pin in scene['pins']}
    bodies = {by_id[b['componentId']]: b['bbox'] for b in scene['bodies']}
    bound_keys = ('minX', 'minY', 'maxX', 'maxY')
    for part in module['placements']:
        ref = part['designator']
        part['bbox'] = dict(zip(bound_keys, bodies[ref]))
        part['pins'][0].update(zip(('x', 'y'), position[ref]))
    module['wires'] = [{'points': row['points']} for row in scene['wires']]
    before = workflow.root / 'before.json'
    source_path = folder / 'composition.json'
    dump(source_path, source)
    selected_page = None
    if select_page:
        selected_page = folder / 'chosen-page.json'
        dump(selected_page, {'bounds': scene['moduleFrames'][0]['bbox']})

    class CandidateAdapter(FakeAdapter):
        def run(self, args, **kwargs):
            receipt = super().run(args, **kwargs)
            if args[:2] == ['sch', 'compose']:
                computed_path, queue_path = (Path(args[args.index(flag) + 1])
                                             for flag in ('--out', '--playbook'))
                computed, queue = load(computed_path), load(queue_path)
                frames = computed['layout']['frames']
                frames[0]['rect'] = dict(zip(bound_keys, scene['moduleFrames'][0]['bbox']))
                frame_step = next(row for row in queue['steps'] if row['id'] == 'apply-module-frames')
                frame_flags = json.loads(frame_step['flags']['data'])
                frame_flags['frames'] = frames
                frame_step['flags']['data'] = json.dumps(frame_flags)
                dump(computed_path, computed)
                dump(queue_path, queue)
            return receipt

    target_run = folder / 'prepared'
    Workflow(CandidateAdapter(), target_run).prepare(
        contract, confirm(contract, digest(contract), 'synthetic human'), plan, source_path, before,
        layout_page=selected_page)
    return target_run, selected_page or source_path
