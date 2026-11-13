"""Operating-state regressions use synthetic guarantees and isolated private files."""

import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from test_knowledge_v2 import v2_fixture
from eso.core import Refused
from eso.requirements import evaluate


class FactApplicabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        env = patch.dict(os.environ, {"ESO_MATERIAL_STORE": str(self.root / "cache")})
        env.start()
        self.addCleanup(env.stop)

    def scoped(self, state="on", control=(2.5, 3.3)):
        r = v2_fixture(self.root, "nmos-low-side-switch")
        material = r["knowledge"]["materials"][0]
        coverage = copy.deepcopy(material["record"]["coverage"])
        coverage["control_v"] = list(control)
        material["record"]["facts"]["rds_on_max_ohm"]["applicability"] = {
            "state": state,
            "coverage": coverage,
        }
        from eso.materials import MaterialStore

        material["record_sha256"] = MaterialStore().add(
            material["record"], self.root / "switch.txt"
        )
        return r

    def test_on_resistance_uses_on_gate_voltage_instead_of_off_voltage(self):
        r = self.scoped()
        report = evaluate(r)
        self.assertTrue(report["ready"], report)

    def test_uncovered_on_gate_voltage_and_wrong_state_fail(self):
        for state, control in [("on", (3.1, 3.3)), ("off", (0, 3.3))]:
            with self.subTest(state=state, control=control):
                report = evaluate(self.scoped(state, control))
                self.assertFalse(report["ready"])
                self.assertIn("applicability", str(report))

    def test_off_leakage_scope_must_cover_declared_off_input_bound(self):
        from eso.materials import MaterialStore

        r = self.scoped()
        material = r["knowledge"]["materials"][0]
        coverage = copy.deepcopy(material["record"]["coverage"])
        coverage["control_v"] = [0, 0]
        material["record"]["facts"]["off_current_max_a"]["applicability"] = {
            "state": "off",
            "coverage": coverage,
        }
        material["record_sha256"] = MaterialStore().add(
            material["record"], self.root / "switch.txt"
        )
        self.assertFalse(evaluate(r)["ready"])

    def test_derived_fact_cannot_expand_source_state_or_temperature(self):
        from eso.materials import MaterialStore

        r = v2_fixture(self.root)
        material = r["knowledge"]["materials"][0]
        record = material["record"]
        original = copy.deepcopy(record["facts"]["power_limit_w"])
        coverage = copy.deepcopy(record["coverage"])
        coverage["ambient_c"] = [25, 25]
        original["applicability"] = {"state": "on", "coverage": coverage}
        record["facts"]["raw_power_w"] = original
        record["facts"]["power_limit_w"].update(
            basis="derived",
            value=0.125,
            derivation={"formula": "raw_power_w * 0.5", "inputs": ["raw_power_w"]},
        )
        with self.assertRaises(Refused):
            MaterialStore().add(record, self.root / "switch.txt")

    def test_all_missing_documents_are_reported_together(self):
        r = v2_fixture(self.root)
        (self.root / "switch.txt").unlink()
        (self.root / "load.txt").unlink()
        report = evaluate(r)
        self.assertFalse(report["ready"])
        text = str(report["knowledge"]["questions"])
        self.assertIn("switch", text)
        self.assertIn("load", text)

    def test_controller_fact_with_narrow_temperature_cannot_hide_in_wide_record(self):
        from test_knowledge_composition import combined_fixture
        from eso.materials import MaterialStore

        r = combined_fixture(self.root)
        controller = r["composition"]["controller"]
        coverage = copy.deepcopy(controller["record"]["coverage"])
        coverage["ambient_c"] = [25, 25]
        controller["record"]["facts"]["gpio_voh_min_v"]["applicability"] = {
            "state": "on",
            "coverage": coverage,
        }
        controller["record_sha256"] = MaterialStore().add(
            controller["record"], self.root / "mcu.txt"
        )
        self.assertFalse(evaluate(r)["ready"])
