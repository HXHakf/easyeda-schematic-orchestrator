import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from eso.core import Refused, load, file_digest
import test_workflow as fixtures
from test_workflow import TARGET


class AttemptTests(unittest.TestCase):
    def test_success_and_failure_keep_independent_original_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            a = fixtures.FakeAdapter()
            w, ir = fixtures.OrchestrationTests().prepare(directory, a)
            a.actual = ir
            w.execute()
            first = w.verify()
            archive = w.root / 'verification-history' / first['attempt']
            original = {p.name: p.read_bytes() for p in archive.iterdir() if p.is_file()}
            self.assertTrue(original)
            a.saved = False
            with self.assertRaises(Refused):
                w.verify()
            self.assertEqual(original, {p.name: p.read_bytes() for p in archive.iterdir() if p.is_file()})
            current = load(w.root / 'verification.json')
            failed = w.root / 'verification-history' / current['attempt']
            self.assertFalse(load(failed / 'save.json')['result']['saved'])
            self.assertFalse((failed / 'reload.json').exists())
            for name, sha in first['attempt_files'].items():
                self.assertEqual(file_digest(archive / name), sha)

    def test_target_lock_rejects_contention_and_releases_on_exception(self):
        from eso.locking import target_lock
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {'ESO_LOCK_DIR': directory}):
            with self.assertRaises(RuntimeError):
                with target_lock(TARGET):
                    with self.assertRaises(Refused):
                        with target_lock(TARGET):
                            self.fail('second writer entered')
                    with target_lock(dict(TARGET, document_uuid='other')):
                        pass
                    raise RuntimeError('crash')
            with target_lock(TARGET):
                pass

    def test_archived_input_tampering_blocks_delivery(self):
        from eso.cli import delivery_eligibility
        with tempfile.TemporaryDirectory() as directory:
            a = fixtures.FakeAdapter()
            w, ir = fixtures.OrchestrationTests().prepare(directory, a)
            a.actual = ir
            w.execute()
            report = w.verify()
            (w.root / 'verification-history' / report['attempt'] / 'contract.json').write_text('{}')
            self.assertFalse(delivery_eligibility(w.root, report)[0])

    def test_target_lock_blocks_other_process_and_process_exit_releases(self):
        import os
        import subprocess
        import sys
        from eso.locking import target_lock
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'ESO_LOCK_DIR': directory}):
            script = "from eso.locking import target_lock; from test_workflow import TARGET; from eso.core import Refused\ntry:\n with target_lock(TARGET): pass\nexcept Refused:\n raise SystemExit(7)\n"
            env = dict(os.environ, PYTHONPATH=os.pathsep.join(['.','tests']))
            with target_lock(TARGET):
                self.assertEqual(subprocess.run([sys.executable, '-c', script], env=env).returncode, 7)
            self.assertEqual(subprocess.run([sys.executable, '-c', script], env=env).returncode, 0)

    def test_final_snapshot_is_required_and_bound_to_report(self):
        import copy
        from eso.cli import delivery_eligibility
        from eso.core import dump
        with tempfile.TemporaryDirectory() as directory:
            a = fixtures.FakeAdapter()
            w, ir = fixtures.OrchestrationTests().prepare(directory, a)
            a.actual = ir
            w.execute()
            original = w.verify()
            for mode in ('missing', 'wrong-hash'):
                report = copy.deepcopy(original)
                if mode == 'missing':
                    del report['attempt_files']['final-snapshot.json']
                else:
                    report['snapshot_sha256'] = '0'*64
                dump(w.root/'verification-history'/report['attempt']/'verification.json', report)
                self.assertFalse(delivery_eligibility(w.root, report)[0])

    def test_old_unrelated_reviews_cannot_complete_current_index(self):
        from eso.evidence import evidence_index
        from eso.core import dump, digest
        with tempfile.TemporaryDirectory() as directory:
            a = fixtures.FakeAdapter()
            w, ir = fixtures.OrchestrationTests().prepare(directory, a)
            a.actual = ir
            w.execute()
            report = w.verify()
            old = Path(directory)/'old.json'
            dump(old, {'passed': True, 'scope': 'wrong-scope', 'inputSha256': {'old':'old-hash'}})
            entry = {'report': str(old), 'files': {'report': {'path':str(old),'sha256':file_digest(old)}}}
            reviews = {'attempt':report['attempt'], 'contract_sha256':digest(load(w.root/'contract.json')),
                       'snapshot_sha256':report['snapshot_sha256'], 'layers':{'geometry':entry,'visual':entry}}
            with self.assertRaises(Refused):
                evidence_index(w.root, reviews)

    def test_interrupted_attempt_retains_pending_report_and_can_be_reverified(self):
        with tempfile.TemporaryDirectory() as directory:
            a = fixtures.FakeAdapter()
            w, ir = fixtures.OrchestrationTests().prepare(directory,a)
            a.actual = ir
            w.execute()
            with patch.object(w,'_verify_once',side_effect=SystemExit(1)), self.assertRaises(SystemExit):
                w.verify()
            old = load(w.root/'verification.json')
            folder = w.root/'verification-history'/old['attempt']
            self.assertEqual(load(folder/'verification.json')['state'],'verifying')
            self.assertEqual(w.verify()['state'],'verified')
            self.assertEqual(load(folder/'verification.json'),old)

    def test_index_accepts_bound_visual_report_and_rejects_replaced_file(self):
        from eso.evidence import evidence_index
        from eso.core import digest, dump
        with tempfile.TemporaryDirectory() as directory:
            a = fixtures.FakeAdapter()
            w, ir = fixtures.OrchestrationTests().prepare(directory,a)
            a.actual = ir
            w.execute()
            report=w.verify()
            root=Path(directory)
            svg, preview = root/'image.svg',root/'preview.png'
            svg.write_text('<svg/>');preview.write_bytes(b'synthetic-preview')
            snapshot=w.root/'verification-history'/report['attempt']/'final-snapshot.json'
            paths={'snapshot':snapshot,'svg':svg,'preview':preview}
            binding={'attempt':report['attempt'],'contract_sha256':digest(load(w.root/'contract.json')),
                     'snapshot_sha256':report['snapshot_sha256']}
            visual=root/'visual.json'
            dump(visual,{'passed':True,'scope':'host-visual-review','binding':binding,'reviewer':'synthetic tester',
                 'files':{k:file_digest(v) for k,v in paths.items()},
                 'coverage':{k:True for k in ['primary_paths','module_ownership','junctions','external_text','internal_text','paper']}})
            paths['report']=visual
            reviews=dict(binding,layers={'visual':{'report':str(visual),'files':{k:{'path':str(v),'sha256':file_digest(v)} for k,v in paths.items()}}})
            index=evidence_index(w.root,reviews)
            self.assertEqual(index['layers']['visual'],'host-reviewed')
            self.assertFalse(index['eda_evidence_complete'])
            svg.write_text('<svg>changed</svg>')
            with self.assertRaises(Refused):evidence_index(w.root,reviews)

    def test_historical_index_uses_its_own_inputs_after_current_failure(self):
        from eso.evidence import evidence_index
        with tempfile.TemporaryDirectory() as directory:
            a = fixtures.FakeAdapter()
            w, ir = fixtures.OrchestrationTests().prepare(directory,a)
            a.actual = ir
            w.execute()
            old = w.verify()
            a.saved=False
            with self.assertRaises(Refused):w.verify()
            (w.root/'contract.json').write_text('{}')
            index=evidence_index(w.root, attempt=old['attempt'])
            self.assertEqual(index['layers']['core'],'historical-verified')
            self.assertFalse(index['eda_evidence_complete'])

    def test_page_change_during_verification_invalidates_attempt(self):
        import copy
        with tempfile.TemporaryDirectory() as directory:
            a = fixtures.FakeAdapter()
            w, ir = fixtures.OrchestrationTests().prepare(directory,a)
            a.actual = ir
            w.execute()
            initial=fixtures.blank()
            changed=copy.deepcopy(initial)
            changed['result']['unexpected-edit']=True
            with patch.object(a,'snapshot',side_effect=[initial,changed]), self.assertRaises(Refused):
                w.verify()
            self.assertEqual(load(w.root/'verification.json')['state'],'incomplete')
