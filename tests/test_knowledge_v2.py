"""All device evidence here is synthetic; no fixture constitutes live approval."""

import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_knowledge import fixture
from eso.core import Refused, digest, confirm, check_confirmation, load
from eso.requirements import contract_from, evaluate, contract_summary
from eso.planner import make_plan
from eso.workflow import validate_plan


def v2_fixture(root, topic="npn-low-side-switch"):
    from eso.materials import MaterialStore, fact_unit

    old = fixture(root, topic)
    store = MaterialStore()
    k = {"schema_version": 2, "materials": [], "circuits": old["knowledge"]["circuits"]}
    for m in old["knowledge"]["materials"]:
        ds = dict(m["datasheet"])
        path = ds.pop("path")
        facts = dict(m["facts"])
        if m["kind"] != "resistor":
            facts["off_current_max_a"] = 1e-6
        record = {
            "schema_version": 2,
            "manufacturer": "SYNTHETIC",
            "model": m["model"],
            "package": m["package"],
            "kind": m["kind"],
            "datasheet": ds,
            "pins": m["pins"],
            "coverage": {
                "ambient_c": [0, 85],
                "supply_v": [3, 12],
                "control_v": [0, 3.3],
                "load_current_a": [0, 0.02],
                "frequency_hz": [0, 0],
                "thermal_context": "synthetic-board",
            },
            "coverage_review": "SYNTHETIC full envelope reviewed",
            "facts": {
                key: {
                    "value": value,
                    "unit": fact_unit(key),
                    "basis": "guaranteed",
                    "section": "synthetic table",
                    "test_conditions": "synthetic envelope",
                    "bound": "lower"
                    if key == "vbe_min_v"
                    else "nominal"
                    if key == "resistance_ohm"
                    else "upper",
                }
                for key, value in facts.items()
            },
        }
        sha = store.add(record, path)
        k["materials"].append(
            {
                "id": m["id"],
                "record_sha256": sha,
                "record": record,
                "library": m["library"],
            }
        )
    p = k["circuits"][0]["parameters"]
    p.update(
        supply_min_v=11,
        ambient_min_c=20,
        on_current_min_a=0.01,
        on_current_max_a=0.013,
        off_current_max_a=1e-5,
        thermal_context="synthetic-board",
    )
    old["knowledge"] = k
    return old


class KnowledgeV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        env = patch.dict(os.environ, {"ESO_MATERIAL_STORE": str(self.root / "cache")})
        env.start()
        self.addCleanup(env.stop)

    def test_both_switches_functional_plan_and_private_path_separation(self):
        for topic in ("npn-low-side-switch", "nmos-low-side-switch"):
            r = v2_fixture(self.root, topic)
            c = contract_from(r)
            p = make_plan(c)
            validate_plan(p, c)
            self.assertNotIn(str(self.root), str(c))
            self.assertIn("知识", contract_summary(c))
            values = c["knowledge_evidence"]["calculations"]["channel1"]
            self.assertGreaterEqual(values["on_current_min_a"], 0.01)
            self.assertLessEqual(values["off_current_max_a"], 1e-5)

    def test_safe_but_insufficient_load_current_fails(self):
        r = v2_fixture(self.root)
        r["knowledge"]["circuits"][0]["parameters"]["on_current_min_a"] = 0.012
        self.assertFalse(evaluate(r)["ready"])

    def test_uncertain_temperature_and_typical_values_fail(self):
        from eso.materials import MaterialStore

        r = v2_fixture(self.root)
        r["knowledge"]["circuits"][0]["parameters"]["ambient_min_c"] = -20
        self.assertFalse(evaluate(r)["ready"])
        r = v2_fixture(self.root)
        record = r["knowledge"]["materials"][0]["record"]
        record["facts"]["vcesat_max_v"]["basis"] = "typical"
        sha = MaterialStore().add(record, self.root / "switch.txt")
        r["knowledge"]["materials"][0]["record_sha256"] = sha
        self.assertFalse(evaluate(r)["ready"])

    def test_move_document_does_not_change_confirmation_but_replacement_blocks(self):
        from eso.materials import MaterialStore

        r = v2_fixture(self.root)
        c = contract_from(r)
        a = confirm(c, digest(c), "synthetic test")
        m = r["knowledge"]["materials"][0]
        moved = self.root / "moved.txt"
        (self.root / "switch.txt").rename(moved)
        MaterialStore().add(m["record"], moved)
        check_confirmation(c, a)
        self.assertEqual(digest(contract_from(r)), digest(c))
        moved.write_text("different content")
        with self.assertRaises(Refused):
            check_confirmation(c, a)

    def test_irrelevant_catalog_edit_preserves_but_used_rule_invalidates(self):
        from eso.knowledge import catalog

        r = v2_fixture(self.root)
        c = contract_from(r)
        a = confirm(c, digest(c), "synthetic test")
        data = catalog()
        unused = next(t for t in data["topics"] if t["id"] == "adc-voltage-divider")
        unused["version"] = "unrelated"
        with patch("eso.knowledge.catalog", return_value=data):
            check_confirmation(c, a)
        data["rules"]["npn-drive"]["summary"] += " changed"
        with patch("eso.knowledge.catalog", return_value=data):
            with self.assertRaises(Refused):
                check_confirmation(c, a)

    def test_withdrawal_blocks_frozen_record_and_history_reports_issue(self):
        from eso.materials import MaterialStore
        from eso.knowledge_v2 import evidence_status

        r = v2_fixture(self.root)
        c = contract_from(r)
        a = confirm(c, digest(c), "synthetic test")
        sha = r["knowledge"]["materials"][0]["record_sha256"]
        MaterialStore().withdraw(sha, "wrong pin evidence")
        with self.assertRaises(Refused):
            check_confirmation(c, a)
        status = evidence_status(c)
        self.assertFalse(status["currently_usable"])
        self.assertIn("withdrawn", str(status))
        self.assertEqual(
            c["requirements"]["knowledge"]["materials"][0]["record_sha256"], sha
        )

    def test_new_cache_version_does_not_replace_pinned_record(self):
        from eso.materials import MaterialStore

        r = v2_fixture(self.root)
        c = contract_from(r)
        a = confirm(c, digest(c), "synthetic test")
        record = copy.deepcopy(r["knowledge"]["materials"][0]["record"])
        record["coverage_review"] += " second revision"
        MaterialStore().add(record, self.root / "switch.txt")
        check_confirmation(c, a)

    def test_import_ambiguous_identity_and_missing_library_are_drafts(self):
        from eso.materials import import_manifest

        r = v2_fixture(self.root)
        m = r["knowledge"]["materials"][0]
        row = {key: m["record"][key] for key in ("manufacturer", "model", "package")}
        row.update(id="switch", document=str(self.root / "switch.txt"))
        result = import_manifest(
            {"materials": [row], "circuits": r["knowledge"]["circuits"]}
        )
        self.assertEqual(len(result["knowledge"]["materials"]), 1)
        self.assertNotIn("library", result["knowledge"]["materials"][0])
        self.assertFalse(result["ready"])
        self.assertIn("library", str(result["issues"]))
        row["manufacturer"] = "another manufacturer"
        result = import_manifest({"materials": [row], "circuits": []})
        self.assertEqual(result["knowledge"]["materials"], [])

    def test_derived_results_require_valid_inputs_and_no_engineering_cache(self):
        from eso.materials import MaterialStore

        r = v2_fixture(self.root)
        record = copy.deepcopy(r["knowledge"]["materials"][0]["record"])
        f = record["facts"]["power_limit_w"]
        f["basis"] = "derived"
        f["derivation"] = {"formula": "missing * 0.5", "inputs": ["missing"]}
        with self.assertRaises(Refused):
            MaterialStore().add(record, self.root / "switch.txt")
        record = copy.deepcopy(r["knowledge"]["materials"][0]["record"])
        record["calculations"] = {"switch_power": 0.01}
        with self.assertRaises(Refused):
            MaterialStore().add(record, self.root / "switch.txt")

    def test_condition_change_invalidates_full_contract_approval(self):
        r = v2_fixture(self.root)
        c = contract_from(r)
        a = confirm(c, digest(c), "synthetic test")
        r["knowledge"]["circuits"][0]["parameters"]["supply_min_v"] = 10.9
        c2 = contract_from(r)
        with self.assertRaises(Refused):
            check_confirmation(c2, a)

    def test_derivation_cannot_promote_lower_bound_to_upper_guarantee(self):
        from eso.materials import MaterialStore

        r = v2_fixture(self.root)
        record = copy.deepcopy(r["knowledge"]["materials"][0]["record"])
        value = record["facts"]["vbe_min_v"]["value"]
        fact = record["facts"]["vbe_max_v"]
        fact.update(
            basis="derived",
            value=value,
            derivation={"formula": "vbe_min_v", "inputs": ["vbe_min_v"]},
        )
        with self.assertRaises(Refused):
            MaterialStore().add(record, self.root / "switch.txt")

    def test_derivation_preserves_scaled_upper_bound(self):
        from eso.materials import MaterialStore

        r = v2_fixture(self.root)
        record = copy.deepcopy(r["knowledge"]["materials"][0]["record"])
        original = copy.deepcopy(record["facts"]["power_limit_w"])
        record["facts"]["raw_power_w"] = original
        record["facts"]["power_limit_w"].update(
            basis="derived",
            value=original["value"] * 0.5,
            derivation={"formula": "raw_power_w * 0.5", "inputs": ["raw_power_w"]},
        )
        self.assertTrue(MaterialStore().add(record, self.root / "switch.txt"))

    def test_v2_reaches_existing_pipeline_in_synthetic_transport(self):
        from test_knowledge_pipeline import design, SwitchTransport, TARGET
        from eso.bridge import DesignBridge
        from eso.workflow import Workflow
        from eso.planner import bind_plan
        from eso.cli import delivery_manifest

        with patch("test_knowledge_pipeline.inputs", v2_fixture):
            c, p, b, s, l, g = design(self.root, "npn-low-side-switch")
        adapter = SwitchTransport(c, p, b, s, l, g)
        approval = confirm(c, digest(c), "SYNTHETIC TEST ONLY")
        stage = DesignBridge(adapter, self.root / "stage")
        self.assertEqual(stage.stage(c, approval, p, b)["state"], "placed")
        self.assertEqual(stage.collect()["state"], "layout-compiled")
        flow = Workflow(adapter, self.root / "run")
        flow.prepare(
            c,
            approval,
            p,
            stage.root / "composition.json",
            stage.root / "measured-snapshot.json",
            stage.root,
        )
        adapter.actual = bind_plan(p, b, TARGET)
        flow.execute()
        self.assertEqual(flow.verify()["state"], "verified")
        manifest = delivery_manifest(flow.root, self.root / "delivery.json")
        self.assertEqual(manifest["core_state"], "verified")
        self.assertEqual(manifest["state"], "incomplete")
        from eso.materials import MaterialStore

        MaterialStore().withdraw(
            c["requirements"]["knowledge"]["materials"][0]["record_sha256"],
            "synthetic revocation",
        )
        self.assertEqual(
            delivery_manifest(flow.root, self.root / "delivery.json")["state"],
            "incomplete",
        )

    def test_one_sided_resistance_cannot_supply_two_sided_load_guarantees(self):
        from eso.materials import MaterialStore

        r = v2_fixture(self.root)
        material = next(m for m in r["knowledge"]["materials"] if m["id"] == "load")
        material["record"]["facts"]["resistance_ohm"]["bound"] = "lower"
        material["record_sha256"] = MaterialStore().add(
            material["record"], self.root / "load.txt"
        )
        self.assertFalse(evaluate(r)["ready"])
