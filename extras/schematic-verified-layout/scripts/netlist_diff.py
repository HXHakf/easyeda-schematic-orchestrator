#!/usr/bin/env python3
"""Diff a live EasyEDA netlist (.enet) against a frozen expected net table.

`easyeda sch netlist` prints a UTF-16 wrapped JSON envelope; the real netlist is
the file its ``result.artifactPath`` points at (UTF-8, no BOM issues: read with
utf-8-sig). Structure used here:

    components[<uid>].props.Designator        -> "U1"
    components[<uid>].pinInfoMap[<pin>].net   -> "GDR" ("" means unconnected)

Expected table (JSON, UTF-8):

    {"nets": {"GDR": ["Q1:1", "R1:1", "U1:2"], ...},
     "nc":    ["U1:1", "U1:4"],
     "allow_swap": ["C3"]}

``allow_swap`` lists reference designators whose pins may be permuted between
nets (non-polar parts such as ceramic capacitors): for those refs only the set of
nets the part participates in is compared, not the pin numbers.

Usage:

    python netlist_diff.py --enet page.enet --expect expected.json
                           [--out report.json] [--quiet]

Exit code: 0 = PASS, 1 = mismatch, 2 = unreadable input.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict


def read_json_file(path: str):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def load_enet(path: str) -> dict:
    data = read_json_file(path)
    if "components" not in data:
        raise ValueError(f"{path}: not a netlist (no 'components')")
    return data


def actual_maps(enet: dict) -> tuple[dict[str, list[str]], dict[str, str]]:
    """Return (net -> [REF:pin], pin -> net) from a live netlist."""
    nets: dict[str, list[str]] = defaultdict(list)
    pin_net: dict[str, str] = {}
    comps = enet.get("components") or {}
    items = comps.values() if isinstance(comps, dict) else comps
    for comp in items:
        ref = ((comp.get("props") or {}).get("Designator") or "").strip()
        pins = comp.get("pinInfoMap") or {}
        pin_items = pins.values() if isinstance(pins, dict) else pins
        for pin in pin_items:
            number = str(pin.get("number") or "").strip()
            net = (pin.get("net") or "").strip()
            if not ref or not number:
                continue
            key = f"{ref}:{number}"
            pin_net[key] = net
            if net:
                nets[net].append(key)
    return {k: sorted(v) for k, v in nets.items()}, pin_net


def split_ref(pin: str) -> str:
    return pin.rsplit(":", 1)[0]


def diff(expected: dict, nets: dict[str, list[str]], pin_net: dict[str, str]) -> dict:
    allow_swap = set(expected.get("allow_swap") or [])
    report: dict = {"ok": True, "nets": {}, "nc": {}, "unexpectedNets": []}

    exp_nets = expected.get("nets") or {}
    for net, pins in exp_nets.items():
        want = set(pins)
        got = set(nets.get(net, []))
        missing = sorted(want - got)
        extra = sorted(got - want)
        # Refs listed in allow_swap: compare at ref granularity only.
        if missing or extra:
            miss_sw = [p for p in missing if split_ref(p) in allow_swap]
            extra_sw = [p for p in extra if split_ref(p) in allow_swap]
            if miss_sw or extra_sw:
                miss_refs = {split_ref(p) for p in miss_sw}
                extra_refs = {split_ref(p) for p in extra_sw}
                if miss_refs == extra_refs:
                    missing = [p for p in missing if p not in miss_sw]
                    extra = [p for p in extra if p not in extra_sw]
        entry = {"expected": sorted(want), "actual": sorted(got),
                 "missing": missing, "extra": extra}
        if missing or extra:
            entry["status"] = "FAIL"
            report["ok"] = False
        else:
            entry["status"] = "PASS"
        report["nets"][net] = entry

    for net in nets:
        if net not in exp_nets:
            report["unexpectedNets"].append(net)
    if report["unexpectedNets"]:
        report["ok"] = False

    exp_nc = set(expected.get("nc") or [])
    got_nc = {pin for pin, net in pin_net.items() if not net}
    missing_nc = sorted(exp_nc - got_nc)
    unexpected_nc = sorted(got_nc - exp_nc)
    report["nc"] = {
        "expected": sorted(exp_nc),
        "actual": sorted(got_nc),
        "missingNc": missing_nc,
        "unexpectedNc": unexpected_nc,
    }
    if missing_nc:
        report["ok"] = False

    report["summary"] = "PASS" if report["ok"] else "FAIL"
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--enet", required=True)
    ap.add_argument("--expect", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    try:
        enet = load_enet(args.enet)
        expected = read_json_file(args.expect)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"[netlist_diff] cannot read input: {exc}", file=sys.stderr)
        return 2

    nets, pin_net = actual_maps(enet)
    report = diff(expected, nets, pin_net)
    report["source"] = {"enet": os.path.abspath(args.enet),
                        "expect": os.path.abspath(args.expect)}

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)

    if not args.quiet:
        print(f"[netlist_diff] {report['summary']}  nets={len(report['nets'])} "
              f"actualNets={len(nets)} nc={len(pin_net) - sum(1 for v in pin_net.values() if v)}")
        for net, entry in report["nets"].items():
            if entry["status"] != "PASS":
                print(f"  {net}: missing={entry['missing']} extra={entry['extra']}")
        if report["unexpectedNets"]:
            print(f"  unexpected nets: {report['unexpectedNets']}")
        if report["nc"]["missingNc"]:
            print(f"  expected NC but connected: {report['nc']['missingNc']}")
        if report["nc"]["unexpectedNc"]:
            print(f"  unconnected but not expected (review): {report['nc']['unexpectedNc']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
