import unittest
from eso.core import Refused
from eso.knowledge_metrics import compare_runs


class MeasuredTasksTests(unittest.TestCase):
    def row(self, workflow, state="success"):
        return {
            "case_id": "same-input",
            "workflow": workflow,
            "scope": "offline",
            "status": state,
            "question_rounds": 1,
            "material_reviews": 2,
            "connection_errors": None,
            "elapsed_seconds": 12,
            "human_wait_seconds": 2,
            "tool_seconds": 3,
            "phase_seconds": {"intake": 4, "check": 3},
        }

    def test_failures_retained_and_wait_time_separated(self):
        rows = [self.row("legacy"), self.row("knowledge", "blocked")]
        out = compare_runs({"schema_version": 2, "records": rows})
        self.assertEqual(
            out["cases"][0]["workflows"]["knowledge"]["statuses"], {"blocked": 1}
        )
        self.assertEqual(
            out["cases"][0]["workflows"]["knowledge"]["mean_active_seconds"], 10
        )
        self.assertIsNone(
            out["cases"][0]["workflows"]["knowledge"]["connection_errors"]
        )
        self.assertFalse(out["cases"][0]["success_comparable"])

    def test_invalid_time_accounting_and_unknown_status_refused(self):
        for key, value in [
            ("human_wait_seconds", 13),
            ("tool_seconds", 13),
            ("status", "verified-ish"),
        ]:
            row = self.row("knowledge")
            row[key] = value
            with self.assertRaises(Refused):
                compare_runs({"schema_version": 2, "records": [row]})

    def test_empty_measurements_never_claim_speedup(self):
        self.assertEqual(
            compare_runs({"schema_version": 2, "records": []})["cases"], []
        )
