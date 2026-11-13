"""Offline, deduplicated schematic guidance. Existing validators remain authoritative."""

from pathlib import Path
from .core import Refused, digest, load
from .knowledge import catalog, normalized

RULES = Path(__file__).parent / "data" / "schematic_rules.json"
STAGES = ("design", "layout", "verify", "all")


def check_rules(data, sources=None):
    sources = sources if sources is not None else catalog()
    by_source = {s["id"]: s for s in sources["sources"]}
    primary = by_source.get(data.get("primary_source_id"))
    if not primary or primary["ecosystem"] != "jlc" or primary["role"] != "core":
        raise Refused("schematic guidance requires a JLC core primary source")
    if (
        data.get("schema_version") != 1
        or not data.get("version")
        or not isinstance(data.get("rules"), list)
    ):
        raise Refused("invalid schematic rules catalog")
    ids = set()
    names = {}
    for rule in data["rules"]:
        rid = rule.get("id")
        if not isinstance(rid, str) or not rid or rid in ids:
            raise Refused("duplicate or invalid schematic rule ID")
        ids.add(rid)
        if (
            not rule.get("title")
            or not rule.get("summary")
            or rule.get("basis") not in ("jlc", "auxiliary", "project")
        ):
            raise Refused("incomplete schematic rule " + rid)
        stages = rule.get("stages")
        if (
            not isinstance(stages, list)
            or not stages
            or len(set(stages)) != len(stages)
            or not set(stages) <= set(STAGES) - {"all"}
        ):
            raise Refused("invalid schematic rule stages " + rid)
        aliases = rule.get("aliases")
        if not isinstance(aliases, list) or any(
            not isinstance(name, str) for name in aliases
        ):
            raise Refused("schematic aliases must be a list of strings " + rid)
        for name in [rid, rule["title"], *aliases]:
            if not isinstance(name, str) or not normalized(name):
                raise Refused("invalid schematic rule alias")
            key = normalized(name)
            if key in names and names[key] != rid:
                raise Refused("duplicate schematic rule alias " + name)
            names[key] = rid
        refs = rule.get("source_refs")
        if not isinstance(refs, list) or (rule["basis"] != "project" and not refs):
            raise Refused("schematic rule lacks source references " + rid)
        seen = set()
        for ref in refs:
            sid = ref.get("source_id")
            section = ref.get("section")
            if (
                sid not in by_source
                or not isinstance(section, str)
                or not section.strip()
                or (sid, section) in seen
            ):
                raise Refused("duplicate or dangling schematic source " + rid)
            seen.add((sid, section))
        if rule["basis"] == "jlc" and data["primary_source_id"] not in {
            s for s, _ in seen
        }:
            raise Refused("JLC rule must locate its primary evidence " + rid)
        reuse = rule.get("reuse_refs")
        if (
            not isinstance(reuse, list)
            or not reuse
            or any(not isinstance(r, str) or not r.strip() for r in reuse)
            or len(set(reuse)) != len(reuse)
        ):
            raise Refused("schematic rule must reuse existing guidance/checks " + rid)
        verification = rule.get("verification", {})
        if not all(
            isinstance(verification.get(k), list) for k in ("machine", "review")
        ) or not verification.get("policy"):
            raise Refused(
                "schematic rule must distinguish machine and review coverage " + rid
            )
    return {
        "valid": True,
        "rule_count": len(ids),
        "version": data["version"],
        "primary_source_id": primary["id"],
    }


def rule_catalog():
    data = load(RULES)
    check_rules(data)
    return data


def checklist(stage="design", query=None):
    if (
        not isinstance(stage, str)
        or stage not in STAGES
        or (query is not None and not isinstance(query, str))
    ):
        raise Refused("invalid guidance stage or query")
    source_data = catalog()
    data = load(RULES)
    check_rules(data, source_data)
    rules = [r for r in data["rules"] if stage == "all" or stage in r["stages"]]
    if query is not None:
        q = normalized(query)
        if not q:
            rules = []
        else:
            exact = [
                r
                for r in rules
                if q in [normalized(n) for n in [r["id"], r["title"], *r["aliases"]]]
            ]
            rules = exact or [
                r
                for r in rules
                if any(q in normalized(n) for n in [r["id"], r["title"], *r["aliases"]])
            ]
    ids = {ref["source_id"] for rule in rules for ref in rule["source_refs"]} | {
        data["primary_source_id"]
    }
    sources = [
        {k: s[k] for k in ("id", "title", "url", "role", "ecosystem")}
        for s in source_data["sources"]
        if s["id"] in ids
    ]
    return {
        "state": "guidance-only",
        "verified": False,
        "stage": stage,
        "version": data["version"],
        "rules_sha256": digest(data),
        "primary_source": {"id": data["primary_source_id"]},
        "rules": [
            {
                key: rule[key]
                for key in (
                    "id",
                    "title",
                    "basis",
                    "summary",
                    "source_refs",
                    "reuse_refs",
                    "verification",
                )
            }
            for rule in rules
        ],
        "sources": sources,
        "evidence_policy": "Checklist selection is not a pass report. Record actual machine artifacts and visual review separately.",
    }
