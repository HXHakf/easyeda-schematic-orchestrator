import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from eso.adapter import EasyEDA, result
from eso.core import Refused, check_confirmation, confirm, digest, dump, load
from eso.requirements import contract_from, evaluate, parse_seed
from eso.planner import make_plan, bind_plan
from eso.reconcile import reconcile
from eso.workflow import Workflow, check_source, is_blank, snapshot_fingerprint, validate_plan, validate_queue


TARGET = {"project_name": "ESO-TEST-FIXTURE", "project_uuid": "fixture-project", "document_uuid": "fixture-page"}


def requirements():
    return {"schema_version": 1, "mcu": "STM32F103C8T6", "sensor": "BMP280", "input_voltage_v": 3.3,
            "sensor_form": "bare-chip",
            "power_source": "external-regulated", "target_project": TARGET["project_name"]}


def design():
    c = contract_from(requirements(), TARGET)
    p = make_plan(c)
    bindings = {x["ref"]: {"libraryUuid": "1" * 32, "deviceUuid": "2" * 32,
                          "verified_model": x["query"], "evidence": "synthetic-test-only"} for x in p["components"]}
    ir = bind_plan(p, bindings, TARGET)
    return c, p, ir


def blank():
    page = {k: [] for k in ("arcs", "buses", "circles", "objects", "pins", "polygons", "rectangles", "texts", "wires", "attributes")}
    return {"ok": True, "context": {"projectUuid": TARGET["project_uuid"], "documentUuid": TARGET["document_uuid"], "documentType": "schematic"},
            "result": {"components": [], "wires": [], "pagePrimitives": page}}


def queue():
    return {"version": 1, "requireFullExecution": True, "meta": {"project": TARGET["project_uuid"], "doc": TARGET["document_uuid"]},
            "defaults": {"retry": 0, "continueOnError": False}, "steps": [{"id": "save", "action": "schematic.save"}]}


class RulesTests(unittest.TestCase):
    def test_missing_fields_are_questions_not_silent_guesses(self):
        report = evaluate({"schema_version": 1})
        self.assertFalse(report["ready"])
        self.assertEqual({q["field"] for q in report["questions"]}, {"circuit_type"})

    def test_target_is_only_required_for_execution(self):
        r = requirements(); del r["target_project"]
        self.assertTrue(evaluate(r)["ready"])
        self.assertFalse(evaluate(r, require_target=True)["ready"])

    def test_module_does_not_receive_unexamined_extra_pullups(self):
        r = requirements(); r["sensor_form"] = "module"
        report = evaluate(r)
        self.assertNotIn("i2c_pullups", report["values"])
        self.assertIn("i2c_pullups", {q["field"] for q in report["questions"]})

    def test_defaults_survive_multiple_question_rounds(self):
        first = evaluate(requirements())
        second = evaluate(first["values"])
        self.assertEqual(first["defaults"], second["defaults"])
        second["values"]["pullup_ohm"] = 2200
        self.assertFalse(any(d["field"] == "pullup_ohm" for d in evaluate(second["values"])["defaults"]))

    def test_conditional_questions(self):
        r = requirements(); r.update(i2c_pullups="existing", clock="external-crystal", bus_speed_hz=400000)
        q = {x["field"] for x in evaluate(r)["questions"]}
        self.assertEqual(q, {"pullup_ohm", "crystal_hz", "bus_capacitance_pf"})

    def test_defaults_are_recorded_and_explicit_values_win(self):
        r = requirements(); r["pullup_ohm"] = 2200
        report = evaluate(r)
        self.assertTrue(report["ready"])
        self.assertEqual(report["values"]["pullup_ohm"], 2200)
        self.assertFalse(any(d["field"] == "pullup_ohm" for d in report["defaults"]))

    def test_supply_conflict_and_i2c_rise_time(self):
        r = requirements(); r.update(logic_voltage_v=5, pullup_ohm=10000, bus_capacitance_pf=400)
        self.assertEqual(len(evaluate(r)["errors"]), 2)

    def test_wrong_types_unknown_fields_and_unresolved_constraints(self):
        for patch_ in ({"input_voltage_v": True}, {"bus_speed_hz": 0}, {"supplier_password": "x"}, {"pullup_ohm": None}):
            with self.subTest(patch_=patch_), self.assertRaises(Refused): evaluate(dict(requirements(), **patch_))
        r = requirements(); r["unresolved"] = [{"field": "humidity", "question": "需要测湿度吗？", "reason": "传感器功能冲突"}]
        self.assertFalse(evaluate(r)["ready"])

    def test_multi_voltage_seed_does_not_collapse_rails(self):
        seed = parse_seed("STM32F103C8T6 BMP280 输入5V 逻辑3.3V I2C")
        self.assertNotIn("input_voltage_v", seed)
        self.assertEqual(seed["mcu"], "STM32F103C8T6")

    def test_unsupported_reference_is_explicit(self):
        c = contract_from(dict(requirements(), sensor="BME280"))
        with self.assertRaisesRegex(Refused, "unsupported"): make_plan(c)


class ContractAndPlanTests(unittest.TestCase):
    def test_confirmation_is_bound_to_exact_contract(self):
        c, _, _ = design()
        a = confirm(c, digest(c), "test human")
        check_confirmation(c, a)
        c["requirements"]["pullup_ohm"] = 2200
        with self.assertRaises(Refused): check_confirmation(c, a)

    def test_wrong_confirmation_hash_or_missing_human(self):
        c, _, _ = design()
        for sha, actor in (("0" * 64, "human"), (digest(c), " ")):
            with self.assertRaises(Refused): confirm(c, sha, actor)

    def test_full_pin_inventory_and_sensor_mode(self):
        c, p, _ = design(); validate_plan(p, c)
        by_ref = {x["ref"]: x for x in p["components"]}
        self.assertEqual(len(by_ref["U1"]["pins"]), 48)
        pins = {x["number"]: x["net"] for x in by_ref["U2"]["pins"]}
        self.assertEqual(pins["5"], "GND"); self.assertEqual(pins["7"], "GND"); self.assertEqual(pins["2"], "+3V3")

    def test_duplicate_pin_and_missing_nc_reason_rejected(self):
        c, p, _ = design()
        p["components"][0]["pins"].append(copy.deepcopy(p["components"][0]["pins"][0]))
        with self.assertRaises(Refused): validate_plan(p, c)
        _, p, _ = design()
        next(x for x in p["components"][0]["pins"] if x["net"] is None)["nc_reason"] = None
        with self.assertRaises(Refused): validate_plan(p, c)

    def test_source_electrical_drift_or_module_drift_refused(self):
        c, p, ir = design(); check_source(p, {"connectivity": ir}, TARGET)
        ir["connections"][0]["netId"] = "net-GND"
        with self.assertRaises(Refused): check_source(p, {"connectivity": ir}, TARGET)
        _, _, ir = design(); ir["modules"][0]["peripheralComponents"] = []
        with self.assertRaises(Refused): check_source(p, {"connectivity": ir}, TARGET)

    def test_instance_uuid_not_accepted_as_library_device_uuid(self):
        _, p, _ = design()
        with self.assertRaises(Refused): bind_plan(p, {}, TARGET)

    def test_reconciliation_does_not_accept_missing_or_ambiguous_nets(self):
        _, _, ir = design()
        self.assertTrue(reconcile(ir, copy.deepcopy(ir))["passed"])
        actual = copy.deepcopy(ir); actual["connections"].pop()
        self.assertFalse(reconcile(ir, actual)["passed"])
        actual = copy.deepcopy(ir); actual["connections"].append(actual["connections"][0])
        self.assertFalse(reconcile(ir, actual)["passed"])


class SafetyTests(unittest.TestCase):
    def test_queue_escape_hatches_and_retry_refused(self):
        validate_queue(queue(), TARGET)
        for step in ({"action": "debug.exec_js"}, {"run": "sch clear", "flags": {}},
                     {"action": "schematic.save", "retry": 1}, {"run": "sch save", "flags": {"project": "important"}}):
            q = queue(); q["steps"] = [step]
            with self.subTest(step=step), self.assertRaises(Refused): validate_queue(q, TARGET)

    def test_blank_detection_requires_full_inventory(self):
        b = blank(); self.assertTrue(is_blank(b["result"]))
        b["result"]["pagePrimitives"]["texts"] = [{"text": "important"}]
        self.assertFalse(is_blank(b["result"]))
        del b["result"]["pagePrimitives"]["texts"]
        self.assertFalse(is_blank(b["result"]))

    def test_transport_id_changes_do_not_hide_object_changes(self):
        a, b = blank(), blank(); b["id"] = "new-response"
        self.assertEqual(snapshot_fingerprint(a), snapshot_fingerprint(b))
        b["result"]["wires"] = [{"id": "unexpected"}]
        self.assertNotEqual(snapshot_fingerprint(a), snapshot_fingerprint(b))

    def test_duplicate_json_keys_refused(self):
        with tempfile.TemporaryDirectory() as t:
            f = Path(t) / "bad.json"; f.write_text('{"a":1,"a":2}')
            with self.assertRaises(Refused): load(f)

    def test_adapter_has_no_shell_or_automatic_daemon_start(self):
        with patch("eso.adapter.shutil.which", return_value="/usr/bin/fake-easyeda"), patch("eso.adapter.subprocess.run") as run:
            run.return_value.returncode = 0; run.return_value.stdout = '{"ok":true,"result":{}}'; run.return_value.stderr = ""
            adapter = EasyEDA("fake-easyeda"); adapter.run(["lib", "search", "$(do-not-execute)"])
            self.assertIsInstance(run.call_args.args[0], list)
            self.assertNotIn("shell", run.call_args.kwargs)
            self.assertEqual(run.call_args.kwargs["env"]["EASYEDA_NO_AUTOSTART"], "1")


class FakeAdapter:
    def __init__(self, fail=False, saved=True, actual=None): self.calls=[]; self.fail=fail; self.saved=saved; self.actual=actual
    def guard(self, target): self.calls.append("guard")
    def snapshot(self, target): self.calls.append("snapshot"); return blank()
    def run(self, args, **kwargs):
        self.calls.append(list(map(str,args)))
        if args[:2] == ["sch", "compose"]:
            dump(args[args.index("--playbook")+1], queue())
            dump(args[args.index("--out")+1], {"synthetic": True})
        elif args[:2] == ["sch", "apply"] and "--dry-run" not in args and self.fail:
            raise Refused("synthetic timeout; may have landed")
        elif args[:2] == ["sch", "save"]: return {"ok":True,"result":{"saved":self.saved}}
        elif args[:2] == ["doc", "reload"]: return {"status":"passed","saved":True,"closed":True,"reloaded":TARGET["document_uuid"]}
        elif args[:2] == ["sch", "connectivity"]: return self.actual
        elif args[0] == "sch" and kwargs.get("check") is False:
            raw = {"ok":True} if args[1] == "layout-lint" else {"ok":True,"result":{"passed":True}}
            return {"exit_code":0,"stdout":json.dumps(raw),"stderr":""}
        return {"exit_code": 0, "stdout": "synthetic dry run", "stderr": ""}


class OrchestrationTests(unittest.TestCase):
    def prepare(self, t, adapter):
        c,p,ir = design(); a = confirm(c,digest(c),"synthetic human")
        source, before = Path(t)/"source.json",Path(t)/"baseline.json"
        dump(source,{"connectivity":ir}); dump(before,blank())
        w=Workflow(adapter,Path(t)/"run"); w.prepare(c,a,p,source,before)
        return w,ir

    def test_timeout_captures_readback_and_blocks_replay(self):
        with tempfile.TemporaryDirectory() as t:
            adapter=FakeAdapter(fail=True); w,_=self.prepare(t,adapter)
            with self.assertRaises(Refused): w.execute()
            self.assertEqual(load(w.root/"bundle.json")["state"],"incomplete")
            self.assertTrue((w.root/"failure-readback.json").exists())
            count=len(adapter.calls)
            with self.assertRaises(Refused): w.execute()
            self.assertEqual(count,len(adapter.calls))

    def test_changed_artifact_is_refused_before_write(self):
        with tempfile.TemporaryDirectory() as t:
            adapter=FakeAdapter();w,_=self.prepare(t,adapter)
            dump(w.root/"apply.json",{"steps":[]})
            count=len(adapter.calls)
            with self.assertRaises(Refused):w.execute()
            self.assertEqual(count,len(adapter.calls))

    def test_save_false_or_string_true_does_not_pass(self):
        for saved in (False,"true",None):
            with tempfile.TemporaryDirectory() as t:
                a=FakeAdapter(saved=saved);w,ir=self.prepare(t,a);a.actual=ir;w.execute()
                with self.assertRaises(Refused):w.verify()
                self.assertEqual(load(w.root/"verification.json")["state"], "incomplete")

    def test_simulated_complete_pipeline_requires_fresh_readback(self):
        with tempfile.TemporaryDirectory() as t:
            a=FakeAdapter();w,ir=self.prepare(t,a);a.actual=ir;w.execute();report=w.verify()
            self.assertEqual(report["state"],"verified")
            self.assertTrue(report["checks"]["reconciliation"]["passed"])
            # This test is synthetic even though the function's runtime label is live.
            self.assertIn(["sch","connectivity"],a.calls)

    def test_changed_net_fails_final_verification(self):
        with tempfile.TemporaryDirectory() as t:
            a=FakeAdapter();w,ir=self.prepare(t,a);a.actual=copy.deepcopy(ir);a.actual["connections"].pop();w.execute()
            self.assertEqual(w.verify()["state"],"incomplete")


if __name__ == "__main__": unittest.main()
