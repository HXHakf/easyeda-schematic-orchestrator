import argparse
import json
import sys
from pathlib import Path
from . import __version__
from .adapter import EasyEDA
from .core import Refused, confirm, digest, dump, load
from .planner import make_plan
from .requirements import contract_from, contract_summary, evaluate, merge_answers, parse_seed
from .workflow import Workflow, validate_plan


def main(argv=None):
    # Machine-readable JSON and Chinese summaries use UTF-8 on every platform.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="EasyEDA Schematic Orchestrator — requirements first")
    p.add_argument("--version", action="version", version="EasyEDA Schematic Orchestrator " + __version__)
    p.add_argument("--easyeda", default="easyeda", help="upstream executable (never auto-installs)")
    p.add_argument("--evidence", help="private raw CLI receipts directory")
    p.add_argument("--window", help="exact live connector window ID; still verifies project and document")
    sub = p.add_subparsers(dest="command", required=True)
    intake = sub.add_parser("intake", help="conservative seed extraction; agent semantic review still required")
    intake.add_argument("--text", required=True); intake.add_argument("--out", required=True)
    assess = sub.add_parser("assess", help="dynamic questions/defaults and electrical conflicts")
    assess.add_argument("requirements"); assess.add_argument("--out")
    assess.add_argument("--for-execution", action="store_true", help="also require the authorized test project name")
    update = sub.add_parser("update", help="merge explicit answers; '帮我选' creates an agent selection task")
    update.add_argument("requirements"); update.add_argument("--answers", required=True); update.add_argument("--out", required=True)
    c = sub.add_parser("contract", help="generate a reviewable Design Contract; does not confirm it")
    c.add_argument("requirements"); c.add_argument("--target", help="exact project/document target JSON")
    c.add_argument("--out", required=True)
    c.add_argument("--summary-out", help="Chinese review summary; defaults to the contract path with .md suffix")
    a = sub.add_parser("confirm", help="record a human's already-given confirmation of the exact hash")
    a.add_argument("contract"); a.add_argument("--sha256", required=True)
    a.add_argument("--actor", required=True); a.add_argument("--out", required=True)
    d = sub.add_parser("demo-plan", aliases=["plan"], help="STM32 minimum/BMP280 reference plan; no EDA calls")
    d.add_argument("contract"); d.add_argument("--out", required=True)
    v = sub.add_parser("validate-plan", help="validate an agent-authored structured plan")
    v.add_argument("plan"); v.add_argument("--contract", required=True)
    discover = sub.add_parser("discover", help="read-only CLI/catalog/health capability investigation")
    discover.add_argument("--out", required=True)
    diagnostic = sub.add_parser('diagnose', help='read-only partial-run diagnosis; never replay or remove locks')
    diagnostic.add_argument('--run-dir', required=True)
    diagnostic.add_argument('--snapshot', help='explicit offline components.list snapshot')
    diagnostic.add_argument('--connectivity', help='explicit offline canonical connectivity')
    diagnostic.add_argument('--live', action='store_true', help='read fresh live snapshot/connectivity; no writes')
    prepare = sub.add_parser("prepare", help="compile an upstream composition and dry-run; no project writes")
    for field in ("contract", "approval", "plan", "composition", "before", "run-dir"):
        prepare.add_argument("--" + field, required=True)
    prepare.add_argument("--placement-run", help="owned staging/measurement proof; preserves native instances")
    stage = sub.add_parser("stage", help="place reviewed library parts only in an approved blank test page")
    for field in ("contract", "approval", "plan", "bindings", "run-dir"):
        stage.add_argument("--" + field, required=True)
    collect = sub.add_parser("collect", help="read measured parts/designators/sheet, then call upstream lib-layout")
    collect.add_argument("--run-dir", required=True)
    for action in ("execute", "verify", "status", "deliver"):
        a = sub.add_parser(action)
        a.add_argument("--run-dir", required=True)
        if action == "deliver": a.add_argument("--out", required=True)
    args = p.parse_args(argv)
    try:
        output = None
        if args.command == "intake": output = parse_seed(args.text); dump(args.out, output)
        elif args.command == "assess":
            output = evaluate(load(args.requirements), require_target=args.for_execution)
            if args.out: dump(args.out, output)
        elif args.command == "update":
            output = evaluate(merge_answers(load(args.requirements), load(args.answers)))
            dump(args.out, output["values"])
        elif args.command == "contract":
            output = contract_from(load(args.requirements), load(args.target) if args.target else None)
            summary = Path(args.summary_out) if args.summary_out else Path(args.out).with_suffix(".md")
            if summary.resolve() == Path(args.out).resolve():
                raise Refused("contract summary must not overwrite the machine contract")
            dump(args.out, output)
            summary.parent.mkdir(parents=True, exist_ok=True)
            summary.write_text(contract_summary(output), encoding="utf-8")
            output = {"contract": str(Path(args.out).resolve()), "summary": str(summary.resolve()),
                      "sha256": digest(output), "state": "awaiting-human-confirmation"}
        elif args.command == "confirm":
            output = confirm(load(args.contract), args.sha256, args.actor); dump(args.out, output)
        elif args.command in ("demo-plan", "plan"):
            output = make_plan(load(args.contract)); dump(args.out, output)
        elif args.command == "validate-plan":
            validate_plan(load(args.plan), load(args.contract)); output = {"valid": True, "level": "offline-verified"}
        elif args.command == "discover":
            output = EasyEDA(args.easyeda, args.evidence, window=args.window).discover(); dump(args.out, output)
        elif args.command == 'diagnose':
            from .diagnostics import diagnose
            if args.live and (args.snapshot or args.connectivity):
                raise Refused('choose --live or offline input files, not both')
            output = diagnose(args.run_dir, snapshot=load(args.snapshot) if args.snapshot else None,
                              connectivity=load(args.connectivity) if args.connectivity else None,
                              adapter=EasyEDA(args.easyeda, args.evidence, window=args.window) if args.live else None)
        elif args.command == "status": output = load(Path(args.run_dir) / "bundle.json")
        elif args.command == "deliver": output = delivery_manifest(args.run_dir, args.out)
        elif args.command in ("stage", "collect"):
            from .bridge import DesignBridge
            bridge = DesignBridge(EasyEDA(args.easyeda, args.evidence, window=args.window), args.run_dir)
            if args.command == "stage":
                output = bridge.stage(load(args.contract), load(args.approval), load(args.plan), load(args.bindings))
            else: output = bridge.collect()
        else:
            workflow = Workflow(EasyEDA(args.easyeda, args.evidence, window=args.window), args.run_dir)
            if args.command == "prepare":
                output = workflow.prepare(load(args.contract), load(args.approval), load(args.plan), args.composition, args.before, args.placement_run)
            elif args.command == "execute": output = workflow.execute()
            elif args.command == "verify": output = workflow.verify()
        print(json.dumps(output, ensure_ascii=False, indent=2))
        if args.command in ("verify", "deliver") and output.get("state") != "verified":
            return 3
        return 0
    except (Refused, OSError, ValueError, KeyError) as e:
        print(json.dumps({"status": "refused", "error": str(e)}, ensure_ascii=False), file=sys.stderr)
        return 2


def delivery_manifest(run_dir, output_path):
    from .core import file_digest
    root = Path(run_dir).resolve()
    if not root.is_dir(): raise Refused("run directory missing")
    verification = root / "verification.json"
    report = load(verification) if verification.exists() else {}
    eligible, issues = delivery_eligibility(root, report)
    manifest = {"schema_version": 1, "state": "verified" if eligible else "incomplete",
                "level": report.get("level", "not-live-verified") if eligible else "not-verified",
                "files": [{"path": str(f.relative_to(root)), "sha256": file_digest(f)}
                          for f in sorted(root.rglob("*")) if f.is_file() and f.resolve() != Path(output_path).resolve()],
                "unverified": report.get("unverified", ["EDA execution and final acceptance"]) + issues,
                "publication": "private evidence may contain project IDs/local paths; redact before GitHub publication"}
    dump(output_path, manifest)
    return manifest


def delivery_eligibility(root, report):
    from .core import file_digest
    issues = []
    try:
        bundle = load(root / "bundle.json")
        if bundle.get("state") != "verified" or report.get("state") != "verified":
            issues.append("latest verification is incomplete or in progress")
        if not report.get("attempt") or report["attempt"] != bundle.get("verification_attempt"):
            issues.append("verification belongs to a different attempt")
        if report.get("bundle_artifacts_sha256") != digest(bundle["artifacts"]):
            issues.append("verification belongs to different prepared inputs")
        if not report.get("evidence_files") or not report.get("checks") or not all(
                check.get("passed") is True for check in report["checks"].values()):
            issues.append("verification has no complete passing check evidence")
        for name, sha in {**bundle["artifacts"], **report.get("evidence_files", {})}.items():
            if Path(name).name != name or file_digest(root / name) != sha:
                issues.append("prepared input or verification evidence changed: " + name)
    except (OSError, ValueError, KeyError, TypeError) as error:
        issues.append("verification evidence unavailable: " + str(error))
    return not issues, issues


if __name__ == "__main__":
    sys.exit(main())
