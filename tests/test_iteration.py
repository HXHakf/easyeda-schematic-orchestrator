"""Exercise durable optimization state and the live final-delivery boundary."""
import contextlib
import io
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from eso import cli
from eso.core import Refused, digest, dump, file_digest, load
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
        self.workflow, self.reviews = drawing_run(self.root, modern=True, new_policy=True)
        reviews = load(self.reviews)
        visual = reviews['layers']['visual']
        report = load(visual['report'])
        independent = independent_review(self.root, {'binding': report['binding'], 'files': report['files']})
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
        dump(source, {'maxCandidates': 20000})
        receipt = self.root / 'extra-receipt.json'
        dump(receipt, dict(argv=['easyeda', 'sch', 'lib-layout', '--from', str(source), '--out', 'unused.json'],
                           exit_code=2, stderr='maxCandidates exhausted'))
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
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda event: record_iteration(self.iteration, event), [self.first, self.first]))
        self.assertEqual(results[0], results[1])
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
