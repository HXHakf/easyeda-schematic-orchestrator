"""Reviewable artifact state machine; execution always delegates to easyeda-agent."""
import copy
import json
import os
import uuid
from pathlib import Path
from .core import Refused, check_confirmation, digest, dump, file_digest, load, now, validate
from .adapter import result
from .requirements import evaluate
from .reconcile import reconcile


def validate_plan(plan, contract):
    validate(plan, "plan")
    from .planner import net_definitions
    net_definitions(plan)
    if plan["contract_sha256"] != digest(contract):
        raise Refused("plan refers to an old contract")
    if not evaluate(contract["requirements"])["ready"]:
        raise Refused("contract requirements are incomplete")
    ids, refs, modules = set(), set(), {m["id"] for m in plan["modules"]}
    if len(modules) != len(plan["modules"]):
        raise Refused("duplicate module IDs")
    for c in plan["components"]:
        if c["id"] in ids or c["ref"] in refs:
            raise Refused("duplicate component IDs/designators")
        ids.add(c["id"]); refs.add(c["ref"])
        if c["module"] not in modules:
            raise Refused("each component must have exactly one declared module")
        numbers = set()
        for p in c["pins"]:
            if p["number"] in numbers:
                raise Refused(f"{c['ref']}: duplicate physical pin")
            numbers.add(p["number"])
            if p["net"] is None and not p["nc_reason"]:
                raise Refused("NC requires a design reason, not a missing-data default")
            if p["net"] == "" or (p["net"] is not None and p["nc_reason"] is not None):
                raise Refused("each physical pin must have one net or explicit NC")
    by_ref = {c["ref"]: c for c in plan["components"]}
    for m in plan["modules"]:
        core = by_ref.get(m["core_ref"])
        if not core or core["module"] != m["id"]:
            raise Refused("module core must belong to its module")
    for c in plan["components"]:
        if "attach" in c:
            a = c["attach"]
            try:
                own = next(p for p in c["pins"] if p["number"] == a["pin"])
                other = next(p for p in by_ref[a["ref"]]["pins"] if p["number"] == a["pin_number"])
            except (KeyError, StopIteration) as e:
                raise Refused("attachment refers to absent physical pin") from e
            if not own["net"] or own["net"] != other["net"] or by_ref[a["ref"]]["module"] != c["module"]:
                raise Refused("attachment must be within a module and already on the same net")
    if not plan["evidence"]:
        raise Refused("a circuit plan requires component/electrical evidence")
    return plan


def check_source(plan, source, target):
    """The geometry stays upstream; verify its electrical intent before compilation."""
    ir = source.get("connectivity", {})
    if ir.get("schemaVersion") != "1.4" or ir.get("projectId") != target["project_uuid"] or ir.get("documentId") != target["document_uuid"]:
        raise Refused("composition needs canonical 1.4 connectivity with exact target identity")
    canonical = ir.get("components", [])
    from .reconcile import checked_inventory
    from .planner import net_definitions
    try:
        checked_inventory(ir)
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise Refused('invalid composition connectivity: ' + str(error)) from error
    definitions = net_definitions(plan)
    if {n['name']: (n.get('scope'), n.get('role')) for n in ir['nets']} != {
            name: (n['scope'], n['role']) for name, n in definitions.items()}:
        raise Refused('composition net role/scope inventory differs from reviewed plan')
    if len(canonical) != len(plan["components"]):
        raise Refused("composition component inventory differs from reviewed plan")
    by_id = {c["id"]: c for c in canonical}
    nets = {n["id"]: n["name"] for n in ir.get("nets", [])}
    edges = {}
    for e in ir.get("connections", []):
        key = (e["componentId"], e["pinNumber"])
        if key in edges or e["netId"] not in nets:
            raise Refused("ambiguous edge or unknown net")
        edges[key] = nets[e["netId"]]
    valid_keys = set()
    for c in plan["components"]:
        actual = by_id.get(c["id"], {})
        if actual.get("ref") != c["ref"]:
            raise Refused("composition stable identity/designator differs")
        pins = {p["number"]: p for p in actual.get("pins", [])}
        if len(pins) != len(actual.get("pins", [])) or set(pins) != {p["number"] for p in c["pins"]}:
            raise Refused("composition physical pin inventory differs")
        for p in c["pins"]:
            key = (c["id"], p["number"])
            valid_keys.add(key)
            if edges.get(key) != p["net"] or (pins[p["number"]].get("noConnected") is True) != (p["net"] is None):
                raise Refused(f"composition changed reviewed net/NC: {c['ref']}:{p['number']}")
    if set(edges) - valid_keys:
        raise Refused("extra composition connection")
    # Compare ownership explicitly, not by shared power net or screen proximity.
    expected_modules = {m["id"]: {
        "core": {c["id"] for c in plan["components"] if c["ref"] == m["core_ref"]},
        "peripheral": {c["id"] for c in plan["components"] if c["module"] == m["id"] and c["ref"] != m["core_ref"]}
    } for m in plan["modules"]}
    observed_modules = {m["id"]: {"core": set(m.get("coreComponents", [])),
                                   "peripheral": set(m.get("peripheralComponents", []))}
                        for m in ir.get("modules", [])}
    if expected_modules != observed_modules:
        raise Refused("composition module ownership differs from reviewed plan")
    return ir


def validate_queue(queue, target):
    """Reject broad escape hatches even if a queue file was substituted."""
    if queue.get("version") != 1 or not queue.get("steps"):
        raise Refused("upstream queue must be a nonempty version-1 playbook")
    meta = queue.get("meta", {})
    if meta.get("project") != target["project_uuid"] or meta.get("doc") != target["document_uuid"]:
        raise Refused("queue target differs from confirmed target")
    if queue.get("defaults", {}).get("continueOnError") or queue.get("defaults", {}).get("retry", 0):
        raise Refused("workflow requires stop on failure and no automatic write retries")
    allowed_actions = {"schematic.read", "schematic.components.list", "schematic.component.place",
                       "schematic.component.modify", "schematic.pin.set_no_connect", "schematic.save",
                       "schematic.wire.create", "schematic.netflag.create", "schematic.netport.create",
                       "schematic.rectangle.create", "schematic.text.create", "schematic.power.connect_pin",
                       "schematic.check", "schematic.bridgeCheck"}
    # Actual compose queues mostly use typed sch commands. Keep the surface narrow.
    allowed_runs = {"sch check", "sch drc", "sch bridge-check", "sch layout-lint", "sch clusters",
                    "sch gate", "sch frame-check", "sch frame-draw", "sch frame-apply",
                    "sch wire", "sch netflag", "sch netport", "sch no-connect", "sch save",
                    "sch titleblock", "sch read", "sch list", "sch group create",
                    "sch frame apply", "sch frame check", "sch clear"}
    for step in queue["steps"]:
        if step.get("retry", 0) or step.get("continueOnError"):
            raise Refused("queue contains retry/continue-on-error")
        if (bool(step.get("action")) + bool(step.get("run"))) != 1:
            raise Refused("each step needs one typed action or command")
        if step.get("action") and step["action"] not in allowed_actions:
            raise Refused(f"action outside MVP surface: {step['action']}")
        if step.get("run") and step["run"] not in allowed_runs:
            raise Refused(f"command outside MVP surface: {step['run']}")
        flags = step.get("flags", {})
        if any(k in flags for k in ("project", "doc", "window", "host", "ports", "force", "yes")):
            raise Refused("step cannot override the pinned target or approval scope")
        if step.get("run") == "sch clear" and not (
            flags.get("dry-run") is True and flags.get("expect-empty") is True
            or isinstance(flags.get("expect-page-primitives-b64"), str)):
            raise Refused("unprotected page clear is forbidden")
        # No destructive clear/reset, document/project switches, debug JS or PCB.
        if any(k in step for k in ("exec", "shell", "command")):
            raise Refused("arbitrary executable steps are forbidden")
    return queue


class Workflow:
    def __init__(self, adapter, run_dir):
        self.adapter = adapter
        self.root = Path(run_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def prepare(self, contract, approval, plan, composition_path, before_path, placement_run=None):
        if (self.root / "bundle.json").exists() or (self.root / "execution-started.lock").exists():
            raise Refused("prepare requires a fresh run directory; preserve prior execution and verification records")
        check_confirmation(contract, approval)
        validate_plan(plan, contract)
        target = contract.get("target")
        if not target:
            raise Refused("execution contract must bind exact live project/document UUIDs")
        if target["project_name"] != contract["requirements"]["target_project"]:
            raise Refused("contract target name mismatch")
        source = load(composition_path)
        ir = check_source(plan, source, target)
        before = load(before_path)
        ctx = before.get("context", {})
        if ctx.get("projectUuid") != target["project_uuid"] or ctx.get("documentUuid") != target["document_uuid"]:
            raise Refused("baseline target mismatch")
        # MVP never replaces/clears existing components or drawings.
        # Full design from a clean empty page is the supported execution path.
        data = result(before)
        if placement_run:
            from .bridge import observed_parts
            stage_root = Path(placement_run)
            stage = load(stage_root / "stage.json")
            bindings = load(stage_root / "bindings.json")
            if (stage.get("state") != "layout-compiled" or stage.get("target") != target
                    or stage.get("contract_sha256") != digest(contract) or stage.get("plan_sha256") != digest(plan)
                    or stage.get("bindings_sha256") != digest(bindings)
                    or stage.get("composition_sha256") != file_digest(composition_path)
                    or stage.get("measured_scene_sha256") != snapshot_fingerprint(before)):
                raise Refused("staging/layout proof does not match this contract, composition and snapshot")
            parts = observed_parts(plan, before, bindings, target)
            if {ref: p['primitiveId'] for ref, p in parts.items()} != stage['primitive_ids']:
                raise Refused("staged native instances changed")
            if data.get("wires") or any(p.get("net") or p.get("noConnected") is not False for c in parts.values() for p in c["pins"]):
                raise Refused("staged page must still be fully unwired")
            measurement_manifest = stage_root / "measurement-evidence.json"
            if stage.get("measurement_manifest_sha256") != file_digest(measurement_manifest):
                raise Refused("measurement manifest changed after layout")
            measurements = load(measurement_manifest)
            required_measurements = {"measured-snapshot.json", "designators.json", "measured-sheet.svg", "sheet-export.json", "sheet-geometry.json", "layout-input.json"}
            native_measurements = required_measurements | {"native-text-measurements.json", "native-text-coverage.json"}
            if set(measurements.get("files", {})) not in (required_measurements, native_measurements):
                raise Refused("measurement manifest is incomplete")
            for name, sha in measurements["files"].items():
                if file_digest(stage_root / name) != sha:
                    raise Refused("measurement evidence changed after layout: " + name)
                (self.root / ("measurement--" + name)).write_bytes((stage_root / name).read_bytes())
            (self.root / "measurement-evidence.json").write_bytes(measurement_manifest.read_bytes())
            dump(self.root / "staging-proof.json", stage)
        elif not is_blank(data):
            raise Refused("MVP compile requires a blank test page; staged/measured designs use upstream workflow explicitly")
        self.adapter.guard(target)
        fresh = self.adapter.snapshot(target)
        if snapshot_fingerprint(fresh) != snapshot_fingerprint(before):
            raise Refused("baseline changed; collect a new snapshot and recompile")
        # Copy exact inputs into this run so later edits to originals cannot alter it.
        for name, data in (("contract", contract), ("approval", approval), ("plan", plan), ("expected", ir)):
            dump(self.root / f"{name}.json", data)
        composition_copy, before_copy = self.root / "composition.json", self.root / "before.json"
        composition_copy.write_bytes(Path(composition_path).read_bytes())
        before_copy.write_bytes(Path(before_path).read_bytes())
        out, queue = self.root / "upstream-plan.json", self.root / "apply.json"
        command = ["sch", "compose", "--from", composition_copy, "--before", before_copy,
                   "--out", out, "--playbook", queue, "--replace"]
        if placement_run: command.append("--preserve-instances")
        self.adapter.run(command, json_output=False)
        validate_queue(load(queue), target)
        self.adapter.run(["sch", "apply", queue, "--dry-run"], json_output=False)
        artifacts = {name: file_digest(self.root / name) for name in
                     ("contract.json", "approval.json", "plan.json", "expected.json", "composition.json", "before.json", "apply.json", "upstream-plan.json")}
        if placement_run:
            for name in ("staging-proof.json", "measurement-evidence.json", *["measurement--" + n for n in required_measurements]):
                artifacts[name] = file_digest(self.root / name)
        bundle = {"schema_version": 1, "state": "prepared", "target": target, "artifacts": artifacts,
                  "baseline_fingerprint": snapshot_fingerprint(before), "created_at": now()}
        dump(self.root / "bundle.json", bundle)
        return bundle

    def execute(self):
        bundle = load(self.root / "bundle.json")
        self._check_bundle(bundle)
        if bundle["state"] != "prepared":
            raise Refused("bundle is not prepared; no automatic resume/retry")
        # Process lock persists on failure. A human must reconcile actual state.
        try:
            fd = os.open(self.root / "execution-started.lock", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as e:
            raise Refused("this run already started: inspect journal/readback; do not blindly replay") from e
        os.write(fd, now().encode()); os.close(fd)
        target = bundle["target"]
        try:
            self.adapter.guard(target)
            fresh = self.adapter.snapshot(target)
            if snapshot_fingerprint(fresh) != bundle["baseline_fingerprint"]:
                raise Refused("target changed after preparation")
            self.adapter.run(["sch", "apply", self.root / "apply.json", "--journal", self.root / "apply.journal.jsonl"], target=target, json_output=False, timeout=600)
            bundle["state"] = "applied-unverified"
            dump(self.root / "bundle.json", bundle)
        except Exception as e:
            bundle.update(state="incomplete", error=str(e))
            dump(self.root / "bundle.json", bundle)
            try: dump(self.root / "failure-readback.json", self.adapter.snapshot(target))
            except Exception as read_error: dump(self.root / "failure-readback-error.json", {"error": str(read_error)})
            raise
        return bundle

    def _check_bundle(self, bundle):
        for name, sha in bundle["artifacts"].items():
            if Path(name).name != name or file_digest(self.root / name) != sha:
                raise Refused("prepared artifact changed; recompile and review")
        contract = load(self.root / "contract.json")
        check_confirmation(contract, load(self.root / "approval.json"))
        if bundle["target"] != contract.get("target"):
            raise Refused("bundle target changed")
        validate_queue(load(self.root / "apply.json"), bundle["target"])

    def verify(self):
        bundle = load(self.root / "bundle.json")
        if bundle["state"] not in ("applied-unverified", "incomplete", "verified", "verifying"):
            raise Refused("a never-executed run cannot be certified")
        attempt = uuid.uuid4().hex
        bundle.update(state="verifying", verification_attempt=attempt)
        dump(self.root / "bundle.json", bundle)
        pending = {"schema_version": 1, "state": "verifying", "level": "not-verified",
                   "attempt": attempt, "checked_at": now(), "unverified": ["verification in progress"]}
        dump(self.root / "verification.json", pending)
        try:
            self._check_bundle(bundle)
            report = self._verify_once(bundle)
            files = ("save.json", "reload.json", "actual-connectivity.json", "drc-command.json",
                     "structure-command.json", "bridge-command.json", "geometry-command.json")
            report.update(attempt=attempt, bundle_artifacts_sha256=digest(bundle["artifacts"]),
                          evidence_files={name: file_digest(self.root / name) for name in files})
        except Exception as error:
            report = {"schema_version": 1, "state": "incomplete", "level": "live-incomplete",
                      "attempt": attempt, "error": str(error), "checked_at": now(),
                      "unverified": ["latest verification failed; older success is invalid"]}
            dump(self.root / "verification.json", report)
            dump(self.root / "verification-history" / f"{attempt}.json", report)
            bundle.update(state="incomplete", error=str(error))
            dump(self.root / "bundle.json", bundle)
            raise
        dump(self.root / "verification.json", report)
        dump(self.root / "verification-history" / f"{attempt}.json", report)
        bundle["state"] = report["state"]
        bundle.pop("error", None)
        dump(self.root / "bundle.json", bundle)
        return report

    def _verify_once(self, bundle):
        target, evidence = bundle["target"], {}
        self.adapter.guard(target)
        saved = self.adapter.run(["sch", "save"], target=target)
        dump(self.root / "save.json", saved)
        if result(saved).get("saved") is not True:
            raise Refused("save did not return boolean saved:true")
        reload_receipt = self.adapter.run(["doc", "reload", target["document_uuid"], "--json"], target=target)
        dump(self.root / "reload.json", reload_receipt)
        if (reload_receipt.get("status") != "passed" or reload_receipt.get("saved") is not True
                or reload_receipt.get("closed") is not True or reload_receipt.get("reloaded") != target["document_uuid"]):
            raise Refused("real document reload not confirmed; inspect receipt")
        self.adapter.guard(target)
        actual = self.adapter.run(["sch", "connectivity"], target=target)
        dump(self.root / "actual-connectivity.json", actual)
        evidence["reconciliation"] = reconcile(load(self.root / "expected.json"), actual)
        # Use receipts even on nonzero exits; never treat missing fields as zero findings.
        for key, args in (("drc", ["sch", "drc", "--strict", "--json"]),
                          ("structure", ["sch", "check", "--strict", "--json"]),
                          ("bridge", ["sch", "bridge-check", "--json"]),
                          ("geometry", ["sch", "layout-lint", "--strict", "--json"])):
            raw = self.adapter.run(args, target=target, check=False, json_output=False)
            dump(self.root / f"{key}-command.json", raw)
            try:
                document = json.loads(raw["stdout"])
                # layout-lint uses a bare report; the other three use typed envelopes.
                parsed = document if key == "geometry" and "result" not in document else result(document)
                passed = parsed.get("ok" if key == "geometry" else "passed") is True
            except (ValueError, Refused, KeyError):
                parsed, passed = {}, False
            evidence[key] = {"passed": raw["exit_code"] == 0 and passed, "result": parsed,
                             "exit_code": raw["exit_code"]}
        passed = all(e.get("passed") is True for e in evidence.values())
        report = {"schema_version": 1, "state": "verified" if passed else "incomplete",
                  "level": "live-verified" if passed else "live-incomplete", "checks": evidence,
                  "unverified": ["hardware operation", "firmware", "PCB", "full UI ERC warning list", "visual export review"],
                  "checked_at": now()}
        return report


def snapshot_fingerprint(snapshot):
    """Ignore transport IDs/timestamps, retain every observed object and circuit datum."""
    data = copy.deepcopy(result(snapshot))
    return digest({"context": {k: snapshot.get("context", {}).get(k) for k in
                               ("projectUuid", "documentUuid", "documentType")}, "result": data})


def is_blank(data):
    if not isinstance(data.get('components'), list) or data.get('wires') != []:
        return False
    page = data.get("pagePrimitives")
    classes = {'components', 'attributes', 'arcs', 'buses', 'circles', 'objects', 'polygons', 'rectangles', 'texts', 'wires'}
    if (not isinstance(page, dict) or set(page) not in (classes, classes | {'pins'})
            or any(not isinstance(rows, list) for rows in page.values())):
        return False
    components = data['components']
    if any(not isinstance(c, dict) or c.get('componentType') != 'sheet' or not c.get('primitiveId') for c in components):
        return False
    sheets = {c['primitiveId'] for c in components}
    native = page['components']
    if (len(sheets) != len(components) or len(native) != len(components)
            or any(not isinstance(c, dict) or c.get('componentType') != 'sheet' for c in native)
            or {c.get('primitiveId') for c in native} != sheets):
        return False
    for kind in ("arcs", "buses", "circles", "objects", "polygons", "rectangles", "texts", "wires"):
        if kind not in page or page[kind] != []:
            return False
    if "pins" in page and page["pins"] != []:
        return False
    # Native sheet attributes are preserved. Any other object ownership is unknown.
    return all(isinstance(a, dict) and a.get("ParentPrimitiveId") in sheets for a in page['attributes'])
