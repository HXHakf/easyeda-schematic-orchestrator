import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from eso.cli import main
from eso.core import confirm, digest, dump, load
import test_bridge as bridge_fixture
import test_workflow as workflow_fixture


def invoke(args):
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = main(list(map(str, args)))
    return rc, json.loads(out.getvalue() or err.getvalue())


class ContinuationTests(unittest.TestCase):
    def test_single_request_reaches_assistant_research_without_confirmation(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'requirements.json'
            rc, report = invoke(['intake', '--text', '做一块电池供电的 Wi-Fi 温湿度采集板',
                                 '--out', path, '--assess'])
            self.assertEqual(rc, 0)
            self.assertEqual(report['next_questions'], [])
            self.assertFalse(report['ready'])
            self.assertTrue(report['research_tasks'])
            self.assertEqual(report['next_action']['owner'], 'assistant')
            self.assertNotIn('next_action', load(path))
            _, report = invoke(['assess', path, '--for-execution'])
            self.assertEqual(report['next_questions'][0]['field'], 'target_project')
            self.assertEqual(report['next_action']['owner'], 'user')

    def test_placement_collects_and_status_reads_stage_only(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            c,p,b,s,labels,g,_ = bridge_fixture.fixture(root)
            adapter = bridge_fixture.FakeBridgeAdapter(c,p,b,s,labels,g)
            args = ['stage', '--collect', '--run-dir', root/'placed']
            for key, value in [('contract',c), ('approval',confirm(c,digest(c),'SYNTHETIC')),
                               ('plan',p), ('bindings',b)]:
                path = root/(key+'.json')
                dump(path, value)
                args.extend(['--'+key,path])
            with patch('eso.cli.EasyEDA', return_value=adapter):
                rc, result = invoke(args)
            self.assertEqual((rc,result['state']), (0,'layout-compiled'))
            rc, status = invoke(['status','--run-dir',root/'placed'])
            self.assertEqual(rc,0)
            self.assertIn('prepare',status['next_action']['task'])
            self.assertTrue((root/'placed/composition.json').is_file())

    def test_apply_verifies_once_and_differences_remain_incomplete(self):
        for mismatch in (False,True):
            with self.subTest(mismatch=mismatch), tempfile.TemporaryDirectory() as name:
                adapter = workflow_fixture.FakeAdapter()
                w, adapter.actual = workflow_fixture.OrchestrationTests().prepare(name, adapter)
                if mismatch:
                    adapter.actual['connections'].pop()
                args = ['execute','--run-dir',w.root,'--verify']
                with patch('eso.cli.EasyEDA', return_value=adapter):
                    rc, report = invoke(args)
                    self.assertEqual(rc,3 if mismatch else 0)
                    self.assertEqual(report['state'],'incomplete' if mismatch else 'verified')
                    before = len(adapter.calls)
                    self.assertEqual(invoke(args)[0],2)
                    self.assertEqual(before,len(adapter.calls))
                self.assertEqual(load(w.root/'verification.json')['attempt'],report['attempt'])
                self.assertIn(['sch','connectivity'], adapter.calls)

    def test_interrupted_apply_is_diagnosed_before_replay(self):
        with tempfile.TemporaryDirectory() as name:
            d = Path(name)
            dump(d/'bundle.json', {'state':'prepared', 'target':{'document_uuid':'p'}})
            (d/'execution-started.lock').touch()
            _, report = invoke(['status','--run-dir',d])
            self.assertIn('journal',report['next_action']['task'])
            self.assertEqual(report['target']['document_uuid'],'p')
