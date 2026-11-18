"""Exercise independent comparison and final delivery with real audit inputs."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest

from eso import cli
from eso.assistance import compare_layouts
from eso.core import Refused, digest, dump, file_digest, load
from eso.evidence import design_qualification
from eso.main_paths import audit_tools
from eso.visual_review import comparison_subject
from test_drawing_review import drawing_run, visual_checks


def independent_review(root, subject, *, context='independent-visual'):
    invocation = dict(subject_sha256=digest(subject), reviewer_id='visual-test', context_id=context,
                      author_context_id='author-test', request='Synthetic input for independent visual review',
                      response='Synthetic visual assessment fixture')
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
            for defect in ('same-author', 'preview-replaced', 'open-finding', None):
                with self.subTest(defect=defect):
                    review = independent_review(root, subject, context='author-test' if defect == 'same-author' else 'independent-visual')
                    raw['independent_review'] = review
                    raw['reviewer'] = 'visual-test'
                    raw['drawing_review']['open_findings'] = [{'object': 'J1'}] if defect == 'open-finding' else []
                    preview = Path(layer['files']['preview']['path'])
                    preview.write_bytes(b'changed' if defect == 'preview-replaced' else b'synthetic-preview')
                    layer['files']['preview']['sha256'] = file_digest(preview)
                    raw['files'] = dict(subject['files'], preview=file_digest(preview))
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

    def test_independent_clarity_order_precedes_area_on_normal_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            drawing_run(root, modern=True)
            audit, readability = audit_tools()
            candidates = []
            for cid, shift in (('compact', 0), ('clear', 20)):
                target = load(root / 'target.json')
                scene = load(root / 'scene.json')
                target['drawing_groups'][0]['title'] += ' ' + cid
                if shift:
                    scene['pins'][1]['at'][0] += shift
                    scene['wires'][0]['points'][1][0] += shift
                    for row in (scene['bodies'][1], scene['texts'][2]):
                        for index in (0, 2):
                            row['bbox'][index] += shift
                    scene['moduleFrames'][0]['bbox'][2] += shift
                folder = root / cid
                folder.mkdir()
                input_data = dict(target=target, scene=scene, source={'placements': scene['pins']})
                paths = {name: folder / (name + '.json') for name in input_data}
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
                preview.write_text(f'<svg><title>{cid}</title></svg>')
                candidates.append(dict(id=cid, **{key: str(path) for key, path in paths.items()},
                                       preview=dict(path=str(preview), pixels_per_raw=1, render_method='synthetic SVG',
                                                    source_sha256=file_digest(paths['source']), scene_sha256=file_digest(paths['scene']))))
            subject = comparison_subject(candidates, 'author-test')
            review = independent_review(root, subject)
            review.update(scope='independent-layout-comparison', evaluations={
                name: dict(accepted=True, clarity_rank=rank, assessment=f'{name} primary path traceability',
                           drawing_review=dict(checks=visual_checks(), open_findings=[]))
                for name, rank in [('compact', 2), ('clear', 1)]})
            path, manifest, result_path = (root / name for name in ('comparison.json', 'candidates.json', 'result.json'))
            dump(path, review)
            dump(manifest, dict(candidates=candidates, max_candidates=2, visual_comparison=str(path), author_context_id='author-test'))
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(['layout-compare', str(manifest), '--out', str(result_path)]), 0)
            compared = load(result_path)
            self.assertEqual(compared['selected'], 'clear')
            self.assertLess(compared['candidates'][0]['score'][-1], compared['candidates'][1]['score'][-1])
            self.assertFalse(compared['execution_ready'])
            with self.assertRaisesRegex(Refused, 'requires independent'):
                compare_layouts(candidates, 2)
            Path(candidates[0]['preview']['path']).write_text('<svg><title>replaced</title></svg>')
            with self.assertRaisesRegex(Refused, 'another drawing or candidate set'):
                compare_layouts(candidates, 2, visual_comparison=path, author_context_id='author-test')
