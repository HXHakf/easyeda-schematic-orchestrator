"""Measured workflow outcomes, including blocked runs and distinct time categories."""

from collections import Counter, defaultdict
from statistics import mean
from .materials import NUMBER, TEXT, obj, shape
from .core import Refused

NONNEG = {"type": ["number", "null"], "minimum": 0, "maximum": 1e12}
COUNT = {"type": ["integer", "null"], "minimum": 0, "maximum": 1000000000}
ROW = obj(
    {
        "case_id": TEXT,
        "workflow": {"enum": ["legacy", "knowledge"]},
        "scope": {"enum": ["offline", "live"]},
        "status": {"enum": ["success", "blocked", "failed", "interrupted"]},
        **{
            k: NONNEG for k in ("elapsed_seconds", "human_wait_seconds", "tool_seconds")
        },
        **{
            k: COUNT
            for k in ("question_rounds", "material_reviews", "connection_errors")
        },
        "phase_seconds": {"type": "object", "additionalProperties": NONNEG},
    },
    (
        "case_id",
        "workflow",
        "scope",
        "status",
        "elapsed_seconds",
        "human_wait_seconds",
        "tool_seconds",
        "question_rounds",
        "material_reviews",
        "connection_errors",
        "phase_seconds",
    ),
)


def compare_measured(document):
    shape(
        document,
        obj(
            {
                "schema_version": {"const": 2},
                "records": {"type": "array", "items": ROW},
            },
            ("schema_version", "records"),
        ),
    )
    groups = defaultdict(lambda: defaultdict(list))
    for row in document["records"]:
        elapsed, wait, tool = (
            row[k] for k in ("elapsed_seconds", "human_wait_seconds", "tool_seconds")
        )
        phases = list(row["phase_seconds"].values())
        if elapsed is not None:
            if any(v is not None and v > elapsed for v in (wait, tool)) or (
                wait is not None and tool is not None and wait + tool > elapsed
            ):
                raise Refused("wait/tool time exceeds wall time")
            if all(v is not None for v in phases) and sum(phases) > elapsed:
                raise Refused("non-overlapping phase durations exceed wall time")
        groups[(row["case_id"], row["scope"])][row["workflow"]].append(row)
    cases = []

    def avg(values):
        return mean(values) if values and all(v is not None for v in values) else None

    for (case, scope), workflows in sorted(groups.items()):
        summaries = {}
        for name, rows in workflows.items():
            phases = {k for r in rows for k in r["phase_seconds"]}
            summaries[name] = {
                "samples": len(rows),
                "statuses": dict(Counter(r["status"] for r in rows)),
                **{
                    k: avg([r[k] for r in rows])
                    for k in (
                        "elapsed_seconds",
                        "human_wait_seconds",
                        "tool_seconds",
                        "question_rounds",
                        "material_reviews",
                        "connection_errors",
                    )
                },
                "mean_active_seconds": avg(
                    [
                        r["elapsed_seconds"] - r["human_wait_seconds"]
                        if r["elapsed_seconds"] is not None
                        and r["human_wait_seconds"] is not None
                        else None
                        for r in rows
                    ]
                ),
                "phase_seconds": {
                    k: avg([r["phase_seconds"].get(k) for r in rows])
                    for k in sorted(phases)
                },
            }
        comparable = set(workflows) == {"legacy", "knowledge"} and all(
            r["status"] == "success" for rows in workflows.values() for r in rows
        )
        cases.append(
            {
                "case_id": case,
                "scope": scope,
                "workflows": summaries,
                "success_comparable": comparable,
            }
        )
    return {
        "schema_version": 2,
        "cases": cases,
        "note": "Blocked/failed runs retained; no inferred zeros or speedup claims. Phase times are non-overlapping wall-clock segments; wait/tool are separate wall-clock categories.",
    }
