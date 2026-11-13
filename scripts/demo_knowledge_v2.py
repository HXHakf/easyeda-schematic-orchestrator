#!/usr/bin/env python3
"""Synthetic v2 material reuse, functional checks and MCU composition. No EDA calls."""

import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from eso.core import dump, load, file_digest, Refused
from eso.materials import MaterialStore
from eso.requirements import contract_from, contract_summary
from eso.planner import make_plan
from eso.knowledge_design import make_bindings
from eso.workflow import validate_plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    out = Path(args.out).resolve()
    if out.exists():
        raise Refused("demo output already exists; choose a new private directory")
    out.mkdir(parents=True)
    old = os.environ.get("ESO_MATERIAL_STORE")
    os.environ["ESO_MATERIAL_STORE"] = str(out / "material-cache")
    try:
        examples = ROOT / "examples/knowledge-v2"
        docs = {file_digest(p): p for p in (examples / "documents").glob("*.txt")}
        store = MaterialStore()
        cases = []
        for name in ("npn", "nmos", "stm32-switches"):
            r = load(examples / (name + ".json"))
            materials = [m["record"] for m in r["knowledge"]["materials"]]
            if "composition" in r:
                materials.append(r["composition"]["controller"]["record"])
            for record in materials:
                store.add(record, docs[record["datasheet"]["sha256"]])
            contract = contract_from(r)
            plan = make_plan(contract)
            validate_plan(plan, contract)
            folder = out / name
            for f, value in [
                ("requirements", r),
                ("contract", contract),
                ("plan", plan),
                ("bindings", make_bindings(contract)),
            ]:
                dump(folder / (f + ".json"), value)
            (folder / "contract.md").write_text(
                contract_summary(contract), encoding="utf-8"
            )
            cases.append(
                {
                    "case": name,
                    "state": "synthetic-offline-verified",
                    "components": len(plan["components"]),
                }
            )
        dump(
            out / "report.json",
            {"cases": cases, "live_verified": False, "approval_created": False},
        )
        print("Synthetic offline v2 demo passed: " + str(out))
    finally:
        if old is None:
            os.environ.pop("ESO_MATERIAL_STORE", None)
        else:
            os.environ["ESO_MATERIAL_STORE"] = old


if __name__ == "__main__":
    main()
