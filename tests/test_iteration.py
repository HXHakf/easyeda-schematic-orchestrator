"""Exercise durable optimization state and the live final-delivery boundary."""
import contextlib
import io
import json
import subprocess
import sys
import os
import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from eso import cli
from eso.core import Refused, digest, dump, file_digest, load, now
from eso.iteration import iteration_status, record_iteration
from test_drawing_review import drawing_run
from test_independent_drawing import independent_review


def artifact(path):
    return dict(path=str(path), sha256=file_digest(path))


class IterationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.workflow, self.reviews = drawing_run(self.root, modern=True, new_policy=True, search_measurements=True)
        reviews = load(self.reviews)
        visual = reviews['layers']['visual']
        report = load(visual['report'])
        independent = independent_review(self.root, {'binding': report['binding'], 'files': report['files']},
                                         outcome={k: report[k] for k in ('passed', 'coverage', 'drawing_review')})
        report.update(reviewer='visual-test', independent_review=independent)
        dump(visual['report'], report)
        visual['files'].update(report=artifact(visual['report']), invocation=independent['invocation'])
        dump(self.reviews, reviews)
        self.contract = digest(load(self.workflow.root / 'contract.json'))
        self.iteration = self.root / 'iteration'
        self.final = self.root / 'manifest.json'
        self.first = self.event('1', 'attempt', candidate_id='base', run_dir=str(self.workflow.root),
                                verification_sha256=file_digest(self.workflow.root / 'verification.json'), reviews=artifact(self.reviews))
        self.start = self.event('2', 'extra-start', candidate_id='alternative')

    def event(self, event_id, kind, **fields):
        return dict(id=event_id, kind=kind, contract_sha256=self.contract, summary=kind, elapsed_seconds=0.5, **fields)

    def extra_search(self):
        source = self.root / 'extra-input.json'
        from eso.bridge import layout_modules_for
        prepared = load(self.workflow.root / 'composition.json')
        plan = load(self.workflow.root / 'plan.json')
        dump(source, dict(schemaVersion=1, connectivity=prepared['connectivity'],
             measurements=prepared['modules'][0]['placements'],
             layoutModules=layout_modules_for(plan, prepared['connectivity']),
             **{key: prepared[key] for key in ('sheet', 'sheetBorder', 'keepouts')}, maxCandidates=1))
        receipt = self.root / 'extra-receipt.json'
        dump(receipt, dict(argv=['easyeda', 'sch', 'lib-layout', '--from', str(source), '--out', 'unused.json'],
                           exit_code=2, stderr='maxCandidates exhausted', input_sha256=file_digest(source),
                           started_at=now(), completed_at=now()))
        return self.event('3', 'extra-result', outcome='search-exhausted', candidate_id='alternative',
                          search_receipt=artifact(receipt), search_input=artifact(source))

    def deliver(self):
        with contextlib.redirect_stdout(io.StringIO()), patch('eso.cli.EasyEDA', return_value=self.workflow.adapter):
            code = cli.main(['deliver', '--run-dir', str(self.workflow.root), '--reviews', str(self.reviews),
                             '--iteration-dir', str(self.iteration), '--out', str(self.final)])
        return code, load(self.final)

    def test_delivery_requires_one_extra_round_and_current_readback(self):
        state = record_iteration(self.iteration, self.first)
        self.assertEqual(state['state'], 'qualified')
        code, manifest = self.deliver()
        self.assertEqual(code, 3)
        self.assertEqual(manifest['core_state'], 'verified')
        self.assertIn('complete one extra comparison', ' '.join(manifest['unverified']))
        record_iteration(self.iteration, self.start)
        self.assertEqual(iteration_status(self.iteration)['state'], 'extra-comparison')
        outcome = self.extra_search()
        state = record_iteration(self.iteration, outcome)
        self.assertEqual(record_iteration(self.iteration, outcome), state)
        self.assertEqual(state['state'], 'complete')
        code, manifest = self.deliver()
        self.assertEqual(code, 0, manifest['unverified'])
        self.assertTrue(manifest['iteration']['fresh_readback'])
        self.workflow.adapter.applied = False
        code, manifest = self.deliver()
        self.assertEqual(code, 3)
        self.assertIn('current schematic differs', ' '.join(manifest['unverified']))
        self.workflow.adapter.applied = True
        Path(outcome['search_receipt']['path']).write_text('{}')
        self.assertEqual(self.deliver()[0], 3)

    def test_concurrent_replay_keeps_one_extra_start_and_resume_survives(self):
        event_file = self.root / 'first-event.json'
        dump(event_file, self.first)
        source = Path(__file__).resolve().parents[1]
        command = [sys.executable, str(source / 'scripts/eso.py'), 'iteration-record',
                   '--iteration-dir', str(self.iteration), str(event_file)]
        with ThreadPoolExecutor(max_workers=2) as executor:
            pending = [executor.submit(subprocess.run, command, cwd=self.root, capture_output=True,
                                       text=True, timeout=15) for _ in range(2)]
            results = [future.result() for future in pending]
        for result in results:
            self.assertEqual(result.returncode, 0, result.stderr)
        results = [json.loads(result.stdout) for result in results]
        self.assertEqual(results[0], results[1])
        self.assertEqual(len(results[0]['events']), 1)
        record_iteration(self.iteration, self.start)
        with self.assertRaisesRegex(Refused, 'already used'):
            record_iteration(self.iteration, dict(self.start, candidate_id='different'))
        with self.assertRaisesRegex(Refused, 'invalid while extra'):
            record_iteration(self.iteration, dict(self.start, id='different'))
        with self.assertRaisesRegex(Refused, 'contract changed'):
            record_iteration(self.iteration, dict(self.start, contract_sha256='different'))
        record_iteration(self.iteration, self.event('blocked', 'blocked'))
        self.assertEqual(iteration_status(self.iteration)['state'], 'incomplete')
        self.assertEqual(record_iteration(self.iteration, self.event('resume', 'resume'))['state'], 'extra-comparison')
        self.assertEqual(record_iteration(self.iteration, self.extra_search())['state'], 'complete')
        self.assertEqual(len(iteration_status(self.iteration)['events']), 5)

    def test_search_must_bind_the_actual_protected_input(self):
        record_iteration(self.iteration, self.first)
        record_iteration(self.iteration, self.start)
        event = self.extra_search()
        original_source = load(event['search_input']['path'])
        original_receipt = load(event['search_receipt']['path'])
        for defect in ('target', 'net', 'library', 'native-part', 'pin', 'geometry', 'policy', 'input-hash', 'duplicate-from', 'stale-receipt', 'changed-input'):
            data, receipt = copy.deepcopy(original_source), copy.deepcopy(original_receipt)
            if defect == 'target': data['connectivity']['projectId'] = 'UNRELATED'
            if defect == 'net': data['connectivity']['nets'][0]['name'] = 'OTHER'
            if defect == 'library': data['connectivity']['components'][0]['device']['deviceUuid'] = 'f' * 32
            if defect == 'native-part': data['measurements'][0]['primitiveId'] = 'unknown'
            if defect == 'pin': data['measurements'][0]['pins'][0]['net'] = 'OTHER'
            if defect == 'geometry': data['measurements'][0]['bbox']['maxX'] += 100
            if defect == 'policy': data['layoutModules'][0]['netPolicies']['net-SIGNAL'] = 'module_port'
            dump(event['search_input']['path'], data)
            receipt['input_sha256'] = file_digest(event['search_input']['path'])
            if defect == 'input-hash': receipt.pop('input_sha256')
            if defect == 'duplicate-from': receipt['argv'] += ['--from', 'unrelated.json']
            if defect == 'stale-receipt': receipt['started_at'] = '2000-01-01T00:00:00+00:00'
            if defect == 'changed-input': receipt['input_changed'] = True
            dump(event['search_receipt']['path'], receipt)
            bad = dict(event, search_input=artifact(event['search_input']['path']), search_receipt=artifact(event['search_receipt']['path']))
            with self.subTest(defect=defect), self.assertRaises(Refused):
                record_iteration(self.iteration, bad)
            self.assertEqual(iteration_status(self.iteration)['state'], 'extra-comparison')
        dump(event['search_input']['path'], original_source)
        dump(event['search_receipt']['path'], original_receipt)
        self.assertEqual(record_iteration(self.iteration, event)['state'], 'complete')

    def test_relative_event_evidence_survives_cwd_change_and_exact_replay(self):
        record_iteration(self.iteration, self.first)
        record_iteration(self.iteration, self.start)
        event = self.extra_search()
        event['search_input']['path'] = Path(event['search_input']['path']).name
        event['search_receipt']['path'] = Path(event['search_receipt']['path']).name
        original_cwd = Path.cwd()
        try:
            os.chdir(self.root)
            accepted = record_iteration(self.iteration, event)
        finally:
            os.chdir(original_cwd)
        self.assertEqual(record_iteration(self.iteration, event), accepted)
        self.assertEqual(len(iteration_status(self.iteration)['events']), 3)
        code, manifest = self.deliver()
        self.assertEqual(code, 0, manifest['unverified'])
        state_path = self.iteration / 'iteration.json'
        state = load(state_path)
        state['events'][-1]['resolved_payload']['search_receipt']['path'] = 'wrong.json'
        dump(state_path, state)
        with self.assertRaisesRegex(Refused, 'resolution changed'):
            iteration_status(self.iteration)

    def test_comparison_nested_paths_retain_the_recorded_origin(self):
        from eso.iteration import _comparison_paths
        from eso.visual_review import review_comparison
        from test_independent_drawing import independent_review
        subject = {'author_context_id': 'author-test', 'candidates': {'base': {}}}
        outcome = {'evaluations': {'base': {'accepted': False, 'assessment': 'Synthetic failed candidate'}}}
        review = independent_review(self.root, subject, outcome=outcome)
        review.update(scope='independent-layout-comparison', **outcome)
        review['invocation']['path'] = Path(review['invocation']['path']).name
        self.assertEqual(review_comparison(review, subject, set(), reference_base=self.root), {})
        manifest = {'candidates': [{'id': 'base', 'run_dir': 'run', 'source': 'source.json', 'target': 'target.json',
                                   'scene': 'scene.json', 'geometry_report': 'geometry.json', 'readability_report': 'read.json',
                                   'preview': {'path': 'image.png'}}], 'visual_comparison': 'visual.json'}
        resolved = _comparison_paths(manifest, self.root)
        for key in ('run_dir', 'source', 'target', 'scene', 'geometry_report', 'readability_report'):
            self.assertEqual(Path(resolved['candidates'][0][key]).parent, self.root.resolve())
        self.assertEqual(resolved['candidates'][0]['preview']['path'], str((self.root / 'image.png').resolve()))
        self.assertEqual(manifest['candidates'][0]['source'], 'source.json')

    @unittest.skipUnless(os.environ.get('EASYEDA_BIN'), 'requires the actual offline search tool')
    def test_actual_related_search_exhaustion_completes_the_extra_round(self):
        from eso.adapter import EasyEDA
        record_iteration(self.iteration, self.first)
        record_iteration(self.iteration, self.start)
        event = self.extra_search()
        original = load(event['search_input']['path'])
        for unrelated in (True, False):
            data = copy.deepcopy(original)
            if unrelated:
                data['connectivity'].update(projectId='UNRELATED-PROJECT', documentId='UNRELATED-PAGE')
            dump(event['search_input']['path'], data)
            event['search_input'] = artifact(event['search_input']['path'])
            receipt = EasyEDA(os.environ['EASYEDA_BIN']).run(
                ['sch', 'lib-layout', '--from', event['search_input']['path'], '--out', str(self.root / 'unused.json')],
                json_output=False, check=False)
            self.assertNotEqual(receipt['exit_code'], 0)
            self.assertIn('search budget', receipt['stderr'])
            dump(event['search_receipt']['path'], receipt)
            event['search_receipt'] = artifact(event['search_receipt']['path'])
            if unrelated:
                with self.assertRaisesRegex(Refused, 'exact target'):
                    record_iteration(self.iteration, event)
                self.assertEqual(iteration_status(self.iteration)['state'], 'extra-comparison')
            else:
                self.assertEqual(record_iteration(self.iteration, event)['state'], 'complete')
        self.assertEqual(self.deliver()[0], 0)

    def test_search_receipt_hash_is_observed_at_tool_invocation(self):
        from eso.adapter import EasyEDA
        source = self.root / 'input.json'
        dump(source, {'maxCandidates': 1})
        original = file_digest(source)
        with patch('eso.adapter.shutil.which', return_value='/synthetic/easyeda'):
            adapter = EasyEDA('/synthetic/easyeda')
        completed = type('Result', (), {'returncode': 1, 'stdout': '', 'stderr': 'search budget exhausted'})()
        with patch('eso.adapter.subprocess.run', return_value=completed):
            receipt = adapter.run(['sch','lib-layout','--from',str(source)], json_output=False, check=False)
        self.assertEqual(receipt['input_sha256'], original)
        def mutate(*args, **kwargs):
            dump(source, {'maxCandidates': 2})
            return completed
        with patch('eso.adapter.subprocess.run', side_effect=mutate), self.assertRaisesRegex(Refused, 'input changed'):
            adapter.run(['sch','lib-layout','--from',str(source)], json_output=False, check=False)

    def test_failed_written_extra_requires_a_distinct_restoration_run(self):
        record_iteration(self.iteration, self.first)
        record_iteration(self.iteration, self.start)
        best = iteration_status(self.iteration)['best']
        failed = dict(best, id='alternative', run_dir=str(self.root / 'failed'), qualified=False)
        report = dict(selected='alternative')
        rows = {'alternative': dict(passed=True, inputs={'source_sha256': failed['source_sha256']})}
        with patch('eso.iteration._comparison', return_value=(report, rows)), patch('eso.iteration._observe_candidate', return_value=failed):
            extra = self.event('3', 'extra-result', candidate_id='alternative', outcome='evaluated', run_dir=failed['run_dir'])
            self.assertEqual(record_iteration(self.iteration, extra)['state'], 'restoring')
        self.assertEqual(self.deliver()[0], 3)
        with patch('eso.iteration._observe_candidate', return_value=best), self.assertRaisesRegex(Refused, 'new protected run'):
            record_iteration(self.iteration, self.event('4', 'restored', candidate_id='base'))
        self.assertEqual(iteration_status(self.iteration)['state'], 'restoring')
