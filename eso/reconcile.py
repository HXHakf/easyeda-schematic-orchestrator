"""Compare exact physical pin intent against canonical upstream 1.4 readback."""


def reconcile(expected, actual):
    differences, unknown = [], []
    if expected.get("projectId") != actual.get("projectId") or expected.get("documentId") != actual.get("documentId"):
        differences.append("wrong project/document identity")
    ec = {c["id"]: c for c in expected.get("components", [])}
    ac = {c["id"]: c for c in actual.get("components", [])}
    if len(ac) != len(actual.get("components", [])):
        differences.append("duplicate actual component IDs")
    for c in sorted(ec.keys() - ac.keys()): differences.append(f"missing component {c}")
    for c in sorted(ac.keys() - ec.keys()): differences.append(f"unexpected component {c}")
    en = {n["id"]: n.get("name") for n in expected.get("nets", [])}
    an = {n["id"]: n.get("name") for n in actual.get("nets", [])}
    ee = {(e["componentId"], e["pinNumber"]): en.get(e["netId"]) for e in expected.get("connections", [])}
    ae = {(e["componentId"], e["pinNumber"]): an.get(e["netId"]) for e in actual.get("connections", [])}
    if len(ae) != len(actual.get("connections", [])):
        differences.append("duplicate/ambiguous actual pin connections")
    for cid in sorted(ec.keys() & ac.keys()):
        e, a = ec[cid], ac[cid]
        if e["ref"] != a.get("ref"):
            differences.append(f"{cid}: designator changed")
        for key in ("libraryUuid", "deviceUuid"):
            if not a.get("device", {}).get(key): unknown.append(f"{cid}: missing {key}")
            elif e["device"][key] != a["device"][key]: differences.append(f"{cid}: {key} changed")
        ep, ap = {p["number"]: p for p in e["pins"]}, {p["number"]: p for p in a.get("pins", [])}
        if set(ep) != set(ap) or len(ap) != len(a.get("pins", [])):
            differences.append(f"{cid}: physical pin inventory differs")
        for num in ep.keys() & ap.keys():
            key = (cid, num)
            desired_nc = ep[num].get("noConnected") is True
            actual_nc = ap[num].get("noConnected") is True
            if desired_nc != actual_nc: differences.append(f"{cid}:{num}: NC differs")
            if ee.get(key) != ae.get(key): differences.append(f"{cid}:{num}: net {ae.get(key)!r} != {ee.get(key)!r}")
            if not actual_nc and key not in ae:
                unknown.append(f"{cid}:{num}: no confirmed net or NC")
    for issue in actual.get("issues", []):
        if issue.get("severity", "").lower() in ("error", "fatal"):
            differences.append(f"upstream IR issue: {issue}")
    return {"passed": not differences and not unknown, "differences": differences, "unverified": unknown,
            "coverage": "component identity, physical-pin inventory, NC and net names; geometry/ownership checked separately upstream"}
