"""Failures reproduced during the 0.2 workflow review."""
import tempfile
import unittest
import copy
import contextlib
import io
import json
from pathlib import Path
from unittest.mock import patch
from eso import cli
from eso.core import Refused, load
from eso.requirements import evaluate
from eso.cli import delivery_manifest
import test_workflow as fixtures


class ReviewRegressions(unittest.TestCase):
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
