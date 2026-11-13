"""Failures reproduced during the 0.2 workflow review."""
import tempfile
import unittest
import copy
import contextlib
import io
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch
from eso import cli
from eso.core import Refused, load
from eso.requirements import evaluate
from eso.cli import delivery_manifest
import test_workflow as fixtures


class ReviewRegressions(unittest.TestCase):
    def test_delivery_requires_every_core_check_and_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = fixtures.FakeAdapter()
            workflow, ir = fixtures.OrchestrationTests().prepare(directory, adapter)
            adapter.actual = ir
            workflow.execute()
            original = workflow.verify()
            for field in ('checks', 'evidence_files'):
                for name in original[field]:
                    with self.subTest(field=field, missing=name):
                        report = copy.deepcopy(original)
                        del report[field][name]
                        eligible, issues = cli.delivery_eligibility(workflow.root, report)
                        self.assertFalse(eligible, (field, name))
                        self.assertTrue(issues)

    def test_separate_adapters_preserve_each_timeout_receipt(self):
        from eso.adapter import EasyEDA

        with tempfile.TemporaryDirectory() as directory, patch(
            'eso.adapter.now', return_value='2026-11-13T00:00:00+00:00'
        ):
            for output in (b'first partial write', b'second partial write'):
                adapter = EasyEDA(sys.executable, evidence=directory)
                with patch('eso.adapter.subprocess.run', side_effect=subprocess.TimeoutExpired(
                    'synthetic', 1, output=output
                )), self.assertRaises(Refused):
                    adapter.run(['sch', 'save'])
            receipts = [load(path) for path in Path(directory).glob('*-timeout.json')]
            self.assertEqual(len(receipts), 2)
            self.assertEqual({r['stdout'] for r in receipts},
                             {'first partial write', 'second partial write'})

    def test_form_change_invalidates_inferred_pullups(self):
        previous = evaluate(fixtures.requirements())["values"]
        previous["sensor_form"] = "module"
        current = evaluate(previous)
        self.assertFalse(current["ready"])
        self.assertNotIn("i2c_pullups", current["values"])
        self.assertNotIn("pullup_ohm", current["values"])
        self.assertIn("i2c_pullups", {q["field"] for q in current["questions"]})

    def test_supply_change_invalidates_inferred_logic_voltage(self):
        previous = evaluate(fixtures.requirements())["values"]
        previous["input_voltage_v"] = 5
        current = evaluate(previous)
        self.assertNotIn("logic_voltage_v", current["values"])
        self.assertIn("logic_voltage_v", {q["field"] for q in current["questions"]})

    def test_explicit_pullups_survive_a_form_change(self):
        r = fixtures.requirements(); r.update(i2c_pullups="existing", pullup_ohm=2200)
        previous = evaluate(r)["values"]; previous["sensor_form"] = "module"
        current = evaluate(previous)
        self.assertEqual(current["values"]["pullup_ohm"], 2200)
        self.assertEqual(current["values"]["i2c_pullups"], "existing")

    def test_failed_reverify_invalidates_success_and_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = fixtures.FakeAdapter()
            workflow, ir = fixtures.OrchestrationTests().prepare(directory, adapter)
            adapter.actual = ir
            workflow.execute(); workflow.verify()
            adapter.saved = False
            with self.assertRaises(Refused): workflow.verify()
            self.assertEqual(load(workflow.root / "bundle.json")["state"], "incomplete")
            self.assertEqual(load(workflow.root / "verification.json")["state"], "incomplete")
            self.assertEqual(delivery_manifest(workflow.root, Path(directory) / "manifest.json")["state"], "incomplete")

    def test_modified_evidence_cannot_be_delivered_as_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = fixtures.FakeAdapter()
            workflow, ir = fixtures.OrchestrationTests().prepare(directory, adapter)
            adapter.actual = ir
            workflow.execute(); workflow.verify()
            (workflow.root / "actual-connectivity.json").write_text('{}\n')
            self.assertEqual(delivery_manifest(workflow.root, Path(directory) / "manifest.json")["state"], "incomplete")

    def test_cli_failed_checks_and_incomplete_delivery_are_nonzero(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter=fixtures.FakeAdapter()
            workflow,ir=fixtures.OrchestrationTests().prepare(directory,adapter)
            adapter.actual=copy.deepcopy(ir);adapter.actual['connections'].pop();workflow.execute()
            capture=io.StringIO()
            with patch.object(cli,'EasyEDA',return_value=adapter),contextlib.redirect_stdout(capture):
                code=cli.main(['verify','--run-dir',str(workflow.root)])
            self.assertEqual(code,3);self.assertEqual(json.loads(capture.getvalue())['state'],'incomplete')
            with contextlib.redirect_stdout(io.StringIO()):
                code=cli.main(['deliver','--run-dir',str(workflow.root),'--out',str(Path(directory)/'manifest.json')])
            self.assertEqual(code,3)


if __name__ == "__main__": unittest.main()
