import argparse
import json
import os
import sys
from pathlib import Path
from . import __version__
from .adapter import EasyEDA
from .core import Refused, confirm, digest, dump, load
from .planner import make_plan
from .requirements import contract_from, contract_summary, evaluate, merge_answers, parse_seed
from .workflow import Workflow, validate_plan, VERIFICATION_CHECKS, VERIFICATION_FILES


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
    p.add_argument("--material-store", help="private material cache directory; also ESO_MATERIAL_STORE")
    sub = p.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor", help="read-only readiness; never installs, upgrades or writes EDA")
    doctor.add_argument("--live", action="store_true"); doctor.add_argument("--target")
    doctor.add_argument("--run-dir"); doctor.add_argument("--out")
    add = sub.add_parser("kb-material-add", help="register immutable source draft or reviewed facts and document locator")
    add.add_argument("record"); add.add_argument("--document", required=True)
    listing = sub.add_parser("kb-material-list")
    for key in ("manufacturer", "model", "package"): listing.add_argument("--"+key)
    withdraw = sub.add_parser("kb-material-withdraw")
    withdraw.add_argument("sha256"); withdraw.add_argument("--reason", required=True)
    imp = sub.add_parser("kb-import", help="resolve manifest to a draft; never supplies library review or approval")
    imp.add_argument("manifest"); imp.add_argument("--out",required=True)
    status = sub.add_parser("kb-evidence-status", help="audit pinned historical evidence without modifying it")
    status.add_argument("contract")
    rules = sub.add_parser("rules", help="offline JLC-centered stage checklist; reuses existing validators")
    rules.add_argument("--stage",choices=("design","layout","verify","all"),default="design")
    rules.add_argument("--query"); rules.add_argument("--check",action="store_true"); rules.add_argument("--out")
    kbsearch = sub.add_parser("kb-search", help="search local canonical circuit topics and aliases")
    kbsearch.add_argument("query")
    kbsearch.add_argument("--parameters", help="JSON parameter object for missing-input hints")
    metrics = sub.add_parser("kb-metrics", help="compare measured legacy/knowledge runs for the same case and scope")
    metrics.add_argument("records"); metrics.add_argument("--out")
    sub.add_parser("kb-check", help="check catalog source/alias/rule uniqueness")
    kbbind = sub.add_parser("kb-bindings", help="export reviewed material library bindings; no EDA calls")
    kbbind.add_argument("contract"); kbbind.add_argument("--out", required=True)
    catalog_review = sub.add_parser('catalog-review', help='compare retained catalog records with selected bindings')
    for field in ('bindings', 'devices', 'out'):
        catalog_review.add_argument('--' + field, required=True)
    compare = sub.add_parser('layout-compare', help='rank a bounded set of audited layout candidates')
    compare.add_argument('manifest'); compare.add_argument('--out', required=True)
    for command in ('text-restore-preview', 'text-restore-apply', 'evidence-index'):
        parser = sub.add_parser(command)
        parser.add_argument('--run-dir', required=True)
        parser.add_argument('--out', required=True)
        if command == 'text-restore-preview':
            parser.add_argument('--reference', required=True)
        elif command == 'text-restore-apply':
            parser.add_argument('--preview', required=True)
        else:
            parser.add_argument('--reviews')
            parser.add_argument('--attempt', help='audit one retained attempt independently of current inputs')
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
    prepare.add_argument("--layout-page", help="selected upstream layout-sheet-plan page; compiled without repacking")
    stage = sub.add_parser("stage", help="place reviewed library parts only in an approved blank test page")
    for field in ("contract", "approval", "plan", "bindings", "run-dir"):
        stage.add_argument("--" + field, required=True)
    collect = sub.add_parser("collect", help="read measured parts/designators/sheet, then call upstream lib-layout")
    collect.add_argument("--run-dir", required=True)
    collect.add_argument("--max-candidates", type=int, default=20000, help="bounded upstream layout search, 1..1000000")
    recover = sub.add_parser("recover-stage", help="reconcile journal ownership and place only missing reviewed parts")
    recover.add_argument("--run-dir", required=True, help="original interrupted staging directory")
    recover.add_argument("--out", required=True, help="fresh recovery staging directory")
    for action in ("execute", "verify", "status", "deliver"):
        a = sub.add_parser(action)
        a.add_argument("--run-dir", required=True)
        if action == "deliver":
            a.add_argument("--out", required=True)
            a.add_argument("--reviews", help="bound geometry and object-specific visual review files")
    args = p.parse_args(argv)
    if args.material_store: os.environ["ESO_MATERIAL_STORE"] = args.material_store
    try:
        output = None
        if args.command == "doctor":
            from .doctor import doctor
            output = doctor(args.easyeda,live=args.live,target=load(args.target) if args.target else None,
                            run_dir=args.run_dir,window=args.window)
            if args.out: dump(args.out,output)
        elif args.command == "kb-evidence-status":
            from .knowledge_v2 import evidence_status
            output = evidence_status(load(args.contract))
        elif args.command in ("kb-material-add","kb-material-list","kb-material-withdraw","kb-import"):
            from .materials import MaterialStore, import_manifest
            store = MaterialStore()
            if args.command == "kb-material-add":
                record = load(args.record)
                output = {"record_sha256":store.add(record,args.document), "status":record.get("status","reviewed")}
            elif args.command == "kb-material-list": output = {"materials":store.list(manufacturer=args.manufacturer,model=args.model,package=args.package)}
            elif args.command == "kb-material-withdraw": output = store.withdraw(args.sha256,args.reason)
            else:
                output = import_manifest(load(args.manifest),store); dump(args.out,output)
        elif args.command == "rules":
            from .schematic_rules import checklist, rule_catalog, check_rules
            output = check_rules(rule_catalog()) if args.check else checklist(args.stage,args.query)
            if args.out: dump(args.out,output)
        elif args.command == "kb-search":
            from .knowledge import search
            output = search(args.query, load(args.parameters) if args.parameters else None)
        elif args.command == "kb-metrics":
            from .knowledge_metrics import compare_runs
            output = compare_runs(load(args.records))
            if args.out: dump(args.out, output)
        elif args.command == "kb-check":
            from .knowledge import catalog, check_catalog
            output = check_catalog(catalog())
        elif args.command == "kb-bindings":
            from .knowledge_design import make_bindings
            output = make_bindings(load(args.contract)); dump(args.out, output)
        elif args.command == "intake": output = parse_seed(args.text); dump(args.out, output)
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
        elif args.command == 'catalog-review':
            from .assistance import catalog_review
            output = catalog_review(load(args.bindings), load(args.devices))
            dump(args.out, output)
        elif args.command == 'layout-compare':
            from .assistance import compare_layouts
            manifest = load(args.manifest)
            output = compare_layouts(manifest['candidates'], manifest['max_candidates'])
            dump(args.out, output)
        elif args.command == 'evidence-index':
            from .evidence import evidence_index
            output = evidence_index(args.run_dir, load(args.reviews) if args.reviews else None, attempt=args.attempt)
            dump(args.out, output)
        elif args.command == 'text-restore-preview':
            from .assistance import text_restore_preview
            root = Path(args.run_dir)
            contract = load(root / 'contract.json')
            adapter = EasyEDA(args.easyeda, args.evidence, window=args.window)
            from .locking import target_lock
            with target_lock(contract['target']):
                output = text_restore_preview(load(args.reference), adapter.snapshot(contract['target']))
                output['contract_sha256'] = digest(contract)
                output['reference'] = load(args.reference)
            dump(args.out, output)
        elif args.command == 'text-restore-apply':
            from .assistance import apply_text_restore
            output = apply_text_restore(EasyEDA(args.easyeda, args.evidence, window=args.window), args.run_dir, args.preview, args.out)
        elif args.command == "status": output = load(Path(args.run_dir) / "bundle.json")
        elif args.command == "deliver": output = delivery_manifest(args.run_dir, args.out, args.reviews)
        elif args.command == "recover-stage":
            from .recovery import recover_stage
            output = recover_stage(EasyEDA(args.easyeda, args.evidence, window=args.window), args.run_dir, args.out)
        elif args.command in ("stage", "collect"):
            from .bridge import DesignBridge
            bridge = DesignBridge(EasyEDA(args.easyeda, args.evidence, window=args.window), args.run_dir)
            if args.command == "stage":
                output = bridge.stage(load(args.contract), load(args.approval), load(args.plan), load(args.bindings))
            else: output = bridge.collect(max_candidates=args.max_candidates)
        else:
            workflow = Workflow(EasyEDA(args.easyeda, args.evidence, window=args.window), args.run_dir)
            if args.command == "prepare":
                output = workflow.prepare(load(args.contract), load(args.approval), load(args.plan), args.composition, args.before, args.placement_run, layout_page=args.layout_page)
            elif args.command == "execute": output = workflow.execute()
            elif args.command == "verify": output = workflow.verify()
        print(json.dumps(output, ensure_ascii=False, indent=2))
        if args.command in ("verify", "deliver") and output.get("state") != "verified":
            return 3
        return 0
    except (Refused, OSError, ValueError, KeyError) as e:
        print(json.dumps({"status": "refused", "error": str(e)}, ensure_ascii=False), file=sys.stderr)
        return 2


def delivery_manifest(run_dir, output_path, reviews_path=None):
    from .core import file_digest
    root = Path(run_dir).resolve()
    if not root.is_dir(): raise Refused("run directory missing")
    verification = root / "verification.json"
    report = load(verification) if verification.exists() else {}
    eligible, issues = delivery_eligibility(root, report)
    core_state = 'verified' if eligible else 'incomplete'
    drawing = None
    if reviews_path is None:
        issues.append('final delivery requires --reviews with geometry and object-specific visual assessments')
        eligible = False
    else:
        from .evidence import evidence_index
        try:
            drawing = evidence_index(root, load(reviews_path))
            if drawing['eda_evidence_complete'] is not True:
                issues.append('drawing evidence is incomplete')
                eligible = False
        except (OSError, ValueError, KeyError, TypeError) as error:
            issues.append('drawing evidence unavailable: ' + str(error))
            eligible = False
    manifest = {"schema_version": 1, "state": "verified" if eligible else "incomplete",
                "core_state": core_state, "drawing": drawing,
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
        from .knowledge import check_contract_knowledge
        check_contract_knowledge(load(root / "contract.json"))
        if bundle.get("state") != "verified" or report.get("state") != "verified":
            issues.append("latest verification is incomplete or in progress")
        if not report.get("attempt") or report["attempt"] != bundle.get("verification_attempt"):
            issues.append("verification belongs to a different attempt")
        if report.get("bundle_artifacts_sha256") != digest(bundle["artifacts"]):
            issues.append("verification belongs to different prepared inputs")
        checks, evidence_files = report.get("checks"), report.get("evidence_files")
        if (not isinstance(checks, dict) or not VERIFICATION_CHECKS.issubset(checks)
                or not all(isinstance(check, dict) and check.get("passed") is True for check in checks.values())
                or not isinstance(evidence_files, dict) or not set(VERIFICATION_FILES).issubset(evidence_files)):
            issues.append("verification has no complete passing check evidence")
        for name, sha in {**bundle["artifacts"], **report.get("evidence_files", {})}.items():
            if Path(name).name != name or file_digest(root / name) != sha:
                issues.append("prepared input or verification evidence changed: " + name)
        if report.get('evidence_format') == 2:
            from .evidence import check_attempt
            check_attempt(root, report)
    except (OSError, ValueError, KeyError, TypeError) as error:
        issues.append("verification evidence unavailable: " + str(error))
    return not issues, issues


if __name__ == "__main__":
    sys.exit(main())
