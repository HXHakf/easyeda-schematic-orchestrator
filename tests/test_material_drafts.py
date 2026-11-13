import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from test_knowledge_v2 import v2_fixture
from eso.core import Refused
from eso.requirements import evaluate


class MaterialDraftTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        env = patch.dict(os.environ, {"ESO_MATERIAL_STORE": str(self.root / "cache")})
        env.start()
        self.addCleanup(env.stop)

    def draft(self):
        r = v2_fixture(self.root)
        record = copy.deepcopy(r["knowledge"]["materials"][0]["record"])
        record.pop("coverage")
        record.pop("coverage_review")
        record.update(
            status="draft",
            missing_evidence=["off-state envelope and thermal conditions"],
        )
        return r, record

    def test_source_draft_is_reusable_but_cannot_be_checked_for_execution(self):
        from eso.materials import MaterialStore

        r, record = self.draft()
        store = MaterialStore()
        sha = store.add(record, self.root / "switch.txt")
        self.assertEqual(store.get(sha)["status"], "draft")
        self.assertIn(
            sha, [row["record_sha256"] for row in store.list(model=record["model"])]
        )
        with self.assertRaises(Refused):
            store.check(sha, record)
        m = r["knowledge"]["materials"][0]
        m.update(record=record, record_sha256=sha)
        result = evaluate(r)
        self.assertFalse(result["ready"])
        self.assertIn("draft", str(result["knowledge"]["questions"]))

    def test_explicit_manifest_pin_cannot_promote_draft(self):
        from eso.materials import MaterialStore, import_manifest

        _, record = self.draft()
        store = MaterialStore()
        sha = store.add(record, self.root / "switch.txt")
        row = {key: record[key] for key in ("manufacturer", "model", "package")}
        row.update(
            id="switch", document=str(self.root / "switch.txt"), record_sha256=sha
        )
        output = import_manifest({"materials": [row], "circuits": []}, store)
        self.assertFalse(output["ready"])
        self.assertEqual(output["knowledge"]["materials"], [])
        self.assertIn(sha, str(output["issues"]))
        self.assertIn("draft", str(output["issues"]))

    def test_review_creates_new_version_and_preserves_draft(self):
        from eso.materials import MaterialStore

        r, record = self.draft()
        store = MaterialStore()
        draft_sha = store.add(record, self.root / "switch.txt")
        reviewed = r["knowledge"]["materials"][0]["record"]
        reviewed_sha = store.add(reviewed, self.root / "switch.txt")
        self.assertNotEqual(draft_sha, reviewed_sha)
        store.check(reviewed_sha, reviewed)
        self.assertEqual(store.get(draft_sha), record)

    def test_draft_requires_specific_missing_evidence_and_source_facts(self):
        from eso.materials import MaterialStore

        _, record = self.draft()
        record["missing_evidence"] = []
        with self.assertRaises(Refused):
            MaterialStore().add(record, self.root / "switch.txt")
        _, record = self.draft()
        record["facts"]["power_limit_w"].update(
            basis="derived",
            derivation={"inputs": ["voltage_limit_v"], "formula": "voltage_limit_v"},
        )
        with self.assertRaises(Refused):
            MaterialStore().add(record, self.root / "switch.txt")

    def test_malformed_unknown_status_and_identity_still_fail(self):
        from eso.materials import MaterialStore

        for change in ("status", "model"):
            _, record = self.draft()
            record[change] = "unexpected"
            with self.assertRaises(Refused):
                MaterialStore().add(record, self.root / "switch.txt")

    def test_source_draft_rejects_reversed_optional_test_scope(self):
        from eso.materials import MaterialStore

        r, record = self.draft()
        coverage = copy.deepcopy(r["knowledge"]["materials"][0]["record"]["coverage"])
        coverage["ambient_c"] = [85, 25]
        record["facts"]["power_limit_w"]["applicability"] = {
            "state": "on",
            "coverage": coverage,
        }
        with self.assertRaises(Refused):
            MaterialStore().add(record, self.root / "switch.txt")
