import copy
import json
import subprocess
import sys
import unittest
from eso.core import Refused


class SchematicRulesTests(unittest.TestCase):
    def test_phase_checklist_reuses_rules_and_jlc_primary(self):
        p = subprocess.run(
            [sys.executable, "-m", "eso.cli", "rules", "--stage", "layout"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        data = json.loads(p.stdout)
        self.assertEqual(data["primary_source"]["id"], "jlc-schematic-guidance")
        self.assertFalse(data["verified"])
        self.assertNotIn("final-verification", [r["id"] for r in data["rules"]])
        self.assertEqual(len({r["id"] for r in data["rules"]}), len(data["rules"]))
        self.assertTrue(all(r["reuse_refs"] for r in data["rules"]))

    def test_aliases_return_one_canonical_requirement(self):
        from eso.schematic_rules import checklist

        for name in ("图签", "标题栏", "document-identity"):
            data = checklist(stage="all", query=name)
            self.assertEqual([r["id"] for r in data["rules"]], ["document-identity"])

    def test_duplicate_alias_or_rule_and_dangling_source_rejected(self):
        from eso.schematic_rules import rule_catalog, check_rules

        original = rule_catalog()
        for kind in ("id", "alias", "source"):
            data = copy.deepcopy(original)
            if kind == "id":
                data["rules"].append(copy.deepcopy(data["rules"][0]))
            elif kind == "alias":
                data["rules"][1]["aliases"].append(data["rules"][0]["aliases"][0])
            else:
                data["rules"][0]["source_refs"][0]["source_id"] = "missing-source"
            with self.assertRaises(Refused):
                check_rules(data)

    def test_external_source_cannot_replace_primary_and_broken_reuse_fails(self):
        from eso.schematic_rules import rule_catalog, check_rules

        data = rule_catalog()
        data["primary_source_id"] = "autodesk-schematic-tips"
        with self.assertRaises(Refused):
            check_rules(data)
        data = rule_catalog()
        data["rules"][0]["reuse_refs"] = []
        with self.assertRaises(Refused):
            check_rules(data)

    def test_registry_and_checklist_are_packaged_without_network_or_live_calls(self):
        from eso.schematic_rules import checklist
        from unittest.mock import patch

        with patch("eso.adapter.EasyEDA", side_effect=AssertionError("live access")):
            result = checklist(stage="verify")
        self.assertEqual(result["state"], "guidance-only")
        self.assertIn("final-verification", [r["id"] for r in result["rules"]])
        self.assertTrue(
            all(s["role"] in ("core", "auxiliary") for s in result["sources"])
        )

    def test_invalid_stage_and_query_types_refuse(self):
        from eso.schematic_rules import checklist

        for args in ({"stage": "other"}, {"query": 1}):
            with self.assertRaises(Refused):
                checklist(**args)

    def test_missing_and_malformed_aliases_are_rejected_before_query(self):
        from eso.schematic_rules import rule_catalog, check_rules

        for aliases in (None, "图签", [1]):
            data = rule_catalog()
            if aliases is None:
                data["rules"][0].pop("aliases")
            else:
                data["rules"][0]["aliases"] = aliases
            with self.assertRaises(Refused):
                check_rules(data)
