"""Opt-in evidence v2: private locators, reviewed envelopes and functional bounds."""

from copy import deepcopy
from pathlib import Path
from .core import Refused, digest, file_digest
from .knowledge_input import obj, CIRCUIT, LIBRARY
from .materials import MaterialStore, RECORD, SHA, TEXT, NUMBER, shape

EXTRA = (
    "supply_min_v",
    "ambient_min_c",
    "on_current_min_a",
    "on_current_max_a",
    "off_current_max_a",
    "thermal_context",
)
CIRCUIT_V2 = deepcopy(CIRCUIT)
CIRCUIT_V2["properties"]["parameters"]["properties"].update(
    {k: NUMBER for k in EXTRA if k != "thermal_context"}
)
CIRCUIT_V2["properties"]["parameters"]["properties"]["thermal_context"] = TEXT
CIRCUIT_V2["properties"]["parameters"]["required"] = list(EXTRA)
MATERIAL_V2 = obj(
    {"id": TEXT, "record_sha256": SHA, "record": RECORD, "library": LIBRARY},
    ("id", "record_sha256", "record"),
)
SCHEMA = obj(
    {
        "schema_version": {"const": 2},
        "materials": {"type": "array", "items": MATERIAL_V2, "minItems": 1},
        "circuits": {"type": "array", "items": CIRCUIT_V2, "minItems": 1},
    },
    ("schema_version", "materials", "circuits"),
)


def normalize(knowledge):
    """Private paths exist only in this ephemeral legacy calculation view."""
    shape(knowledge, SCHEMA)
    store = MaterialStore()
    materials = []
    for m in knowledge["materials"]:
        r = m["record"]
        path = store.check(m["record_sha256"], r)
        materials.append(
            {
                "id": m["id"],
                **{k: r[k] for k in ("model", "package", "kind", "pins")},
                "datasheet": {**r["datasheet"], "path": path},
                "facts": {k: f["value"] for k, f in r["facts"].items()},
                "fact_sections": {
                    k: f["section"] + "; " + f["test_conditions"]
                    for k, f in r["facts"].items()
                },
                "library": deepcopy(m.get("library", {})),
            }
        )
    circuits = deepcopy(knowledge["circuits"])
    for c in circuits:
        for key in EXTRA:
            c["parameters"].pop(key, None)
    return {"materials": materials, "circuits": circuits}


def dependency_evidence(knowledge):
    from .knowledge import catalog

    data = catalog()
    ids = {c["topic"] for c in knowledge["circuits"]}
    cards = sorted([t for t in data["topics"] if t["id"] in ids], key=lambda t: t["id"])
    rules = {rid: data["rules"][rid] for t in cards for rid in t["rule_ids"]}
    sources = {
        sid
        for t in cards
        for sid in [
            t["primary_source_id"],
            *t["related_core_source_ids"],
            *t["auxiliary_source_ids"],
            *[e["source_id"] for e in t["evidence"]],
        ]
    }
    sources.update(s for rule in rules.values() for s in rule["source_ids"])
    assets = {aid for t in cards for aid in t["asset_ids"]}
    used_assets = [
        a
        for a in data["upstream_candidates"] + data["community_discovery_candidates"]
        if a["id"] in assets
    ]
    sources.update(a["source_id"] for a in used_assets)
    deps = {
        "cards": cards,
        "rules": rules,
        "sources": sorted(
            [s for s in data["sources"] if s["id"] in sources], key=lambda s: s["id"]
        ),
        "assets": sorted(used_assets, key=lambda a: a["id"]),
    }
    # Actual validator bytes, not a manually maintained version label.
    validators = {
        name: file_digest(Path(__file__).parent / name)
        for name in (
            "knowledge.py",
            "knowledge_input.py",
            "knowledge_rules.py",
            "knowledge_v2.py",
            "materials.py",
            "knowledge_design.py",
        )
    }
    return {
        "evidence_version": 2,
        "dependencies_sha256": digest(deps),
        "validators_sha256": digest(validators),
        "cards": [
            {"id": t["id"], "version": t["version"], "sha256": digest(t)} for t in cards
        ],
        "materials_sha256": digest(knowledge),
        "documents": [
            {
                "material_id": m["id"],
                "sha256": m["record"]["datasheet"]["sha256"],
                "revision": m["record"]["datasheet"]["revision"],
            }
            for m in knowledge["materials"]
        ],
    }


def assess_v2(knowledge):
    from .knowledge import assess_knowledge
    from .knowledge_rules import COMMON, NPN, MOS, RESISTOR

    issues = []

    def issue(field, message):
        issues.append(
            {
                "field": "knowledge." + field,
                "question": message,
                "reason": "v2 functional/evidence check",
            }
        )

    try:
        legacy = normalize(knowledge)
    except (ValueError, OSError, KeyError, TypeError) as error:
        issue("materials", str(error))
        return {"ready": False, "questions": issues, "level": "incomplete"}
    report = assess_knowledge(legacy)
    issues.extend(report["questions"])
    if issues:
        return {**report, "ready": False, "questions": issues}
    records = {m["id"]: m["record"] for m in knowledge["materials"]}
    calculations = deepcopy(report["evidence"]["calculations"])
    for c in knowledge["circuits"]:
        cid = c["id"]
        p = c["parameters"]
        calc = calculations[cid]
        if not 0 < p["supply_min_v"] <= p["supply_voltage_v"]:
            issue(cid, "invalid supply range")
        if not -40 <= p["ambient_min_c"] <= p["ambient_max_c"]:
            issue(cid, "invalid ambient range")
        if not 0 < p["on_current_min_a"] <= p["on_current_max_a"]:
            issue(cid, "invalid required on-current range")
        if p["off_current_max_a"] < 0:
            issue(cid, "invalid off-current requirement")
        for role, mid in c["roles"].items():
            r = records[mid]
            coverage = r["coverage"]
            facts = r["facts"]
            mos = c["topic"] == "nmos-low-side-switch"
            needed = (
                (*COMMON, *(MOS if mos else NPN), "off_current_max_a")
                if role == "switch"
                else RESISTOR
            )
            for key in needed:
                f = facts.get(key)
                if not f or f["basis"] == "typical":
                    issue(cid + "." + role, "missing guaranteed/derived fact: " + key)
                    continue
                expected = (
                    "lower"
                    if key == "vbe_min_v"
                    else ("nominal" if key == "resistance_ohm" else "upper")
                )
                # A nominal resistor value is meaningful only together with its total tolerance.
                if f["bound"] != expected:
                    issue(cid + "." + role, "wrong guarantee direction: " + key)
            ranges = {
                "supply_v": [p["supply_min_v"], p["supply_voltage_v"]],
                "ambient_c": [p["ambient_min_c"], p["ambient_max_c"]],
                "control_v": [0, p["control_high_max_v"]],
                "load_current_a": [0, calc["load_current_max_a"]],
                "frequency_hz": [0, 0],
            }
            for key, (lo, hi) in ranges.items():
                a, b = coverage[key]
                if a > lo or b < hi:
                    issue(cid + "." + role, "reviewed coverage does not include " + key)
            if coverage["thermal_context"] != p["thermal_context"]:
                issue(cid + "." + role, "thermal_context requires new review")
        s = {k: f["value"] for k, f in records[c["roles"]["switch"]]["facts"].items()}
        load = {k: f["value"] for k, f in records[c["roles"]["load"]]["facts"].items()}
        resistance_max = load["resistance_ohm"] * (1 + load["tolerance_fraction"])
        if c["topic"] == "nmos-low-side-switch":
            on_min = p["supply_min_v"] / (resistance_max + s["rds_on_max_ohm"])
        else:
            on_min = max(0, (p["supply_min_v"] - s["vcesat_max_v"]) / resistance_max)
        off = s.get("off_current_max_a")
        if off is None or off < 0:
            issue(cid, "missing nonnegative off-current bound")
        elif off > p["off_current_max_a"]:
            issue(cid, "off-current exceeds functional requirement")
        if on_min < p["on_current_min_a"]:
            issue(cid, "safe but insufficient on-current")
        if calc["load_current_max_a"] > p["on_current_max_a"]:
            issue(cid, "on-current exceeds functional requirement")
        calc.update(
            on_current_min_a=on_min,
            on_current_max_a=calc["load_current_max_a"],
            off_current_max_a=off,
        )
    evidence = dependency_evidence(knowledge)
    evidence["calculations"] = calculations
    return {
        "ready": not issues,
        "questions": issues,
        "evidence": evidence,
        "semantic_review_required": True,
        "level": "offline-checked" if not issues else "incomplete",
    }


def evidence_status(contract):
    """Audit historical evidence without rewriting the historical result."""
    from .knowledge import check_contract_knowledge

    try:
        check_contract_knowledge(contract)
    except (ValueError, OSError) as error:
        return {
            "currently_usable": False,
            "issues": [str(error)],
            "historical_result_modified": False,
        }
    return {"currently_usable": True, "issues": [], "historical_result_modified": False}
