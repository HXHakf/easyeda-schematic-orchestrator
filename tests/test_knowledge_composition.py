import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from test_knowledge_v2 import v2_fixture
from eso.core import Refused, digest, file_digest, confirm, check_confirmation
from eso.requirements import contract_from, evaluate
from eso.planner import make_plan, bind_plan, MCU_PINS
from eso.workflow import validate_plan, check_source


def combined_fixture(root, sensor=False):
    from eso.materials import MaterialStore, fact_unit

    r = v2_fixture(root)
    r.update(
        circuit_type="mcu-sensor" if sensor else "mcu-minimum",
        mcu="STM32F103C8T6",
        input_voltage_v=3.3,
        logic_voltage_v=3.3,
        power_source="external-regulated",
        clock="internal",
        debug="swd",
        passive_package="0603",
    )
    if sensor:
        r.update(
            sensor="BMP280",
            sensor_form="bare-chip",
            bus="i2c",
            bus_speed_hz=100000,
            i2c_pullups="new",
            pullup_ohm=4700,
            bus_capacitance_pf=100,
        )
    base = copy.deepcopy(r)
    base.pop("knowledge")
    baseplan = make_plan(contract_from(base))
    bindings = {
        c["ref"]: {
            "libraryUuid": "3" * 32,
            "deviceUuid": "4" * 32,
            "verified_model": c["query"],
            "verified_package": "TEST",
            "expected_name": c["query"],
            "pins": {p["name"] + "_" + p["number"]: p["number"] for p in c["pins"]},
            "evidence": "synthetic library",
        }
        for c in baseplan["components"]
    }
    path = root / "mcu.txt"
    path.write_text("SYNTHETIC MCU evidence only")
    facts = {
        "gpio_voh_min_v": 3,
        "gpio_voh_max_v": 3.3,
        "gpio_vol_max_v": 0.1,
        "gpio_source_limit_ma": 8,
        "gpio_leakage_max_ua": 0.1,
        "port_a_source_limit_ma": 20,
        "port_b_source_limit_ma": 20,
        "total_source_limit_ma": 30,
    }
    rec = {
        "schema_version": 2,
        "manufacturer": "SYNTHETIC",
        "model": r["mcu"],
        "package": "LQFP48",
        "kind": "mcu",
        "datasheet": {
            "sha256": file_digest(path),
            "model": r["mcu"],
            "package": "LQFP48",
            "revision": "synthetic-1",
            "pinout_section": "synthetic pins",
            "reviewed_by": "test fixture",
        },
        "pins": {name + "_" + str(i): str(i) for i, name in enumerate(MCU_PINS, 1)},
        "coverage": {
            "ambient_c": [0, 85],
            "supply_v": [3.3, 3.3],
            "control_v": [0, 3.3],
            "load_current_a": [0, 0.05],
            "frequency_hz": [0, 0],
            "thermal_context": "synthetic-board",
        },
        "coverage_review": "synthetic GPIO envelope",
        "facts": {
            key: {
                "value": value,
                "unit": fact_unit(key),
                "basis": "guaranteed",
                "bound": "lower" if key == "gpio_voh_min_v" else "upper",
                "section": "synthetic GPIO",
                "test_conditions": "synthetic stable 3.3V",
            }
            for key, value in facts.items()
        },
    }
    sha = MaterialStore().add(rec, path)
    bindings["U1"]["verified_package"] = "LQFP48"
    bindings["U1"]["pins"] = rec["pins"]
    r["composition"] = {
        "mode": "stable-powered-static",
        "controller": {"record_sha256": sha, "record": rec},
        "base_bindings": bindings,
        "connections": [
            {
                "circuit_id": "channel1",
                "gpio_pin": "10",
                "supply_net": "V12",
                "ground_net": "GND",
                "reset_high_z_review": "synthetic reset state table",
            }
        ],
        "supplies": [
            {
                "net": "V12",
                "min_v": 11,
                "max_v": 12,
                "ground": "GND",
                "evidence": "synthetic regulated supply",
            }
        ],
        "port_budget_ma": {"A": 20, "B": 20},
        "total_budget_ma": 30,
        "budget_review": "synthetic remaining budget after base loads",
    }
    return r


class CompositionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        env = patch.dict(os.environ, {"ESO_MATERIAL_STORE": str(self.root / "cache")})
        env.start()
        self.addCleanup(env.stop)

    def test_reference_and_sensor_combine_with_exact_physical_gpio(self):
        from eso.knowledge_design import make_bindings

        for sensor in (False, True):
            r = combined_fixture(self.root, sensor)
            c = contract_from(r)
            plan = make_plan(c)
            validate_plan(plan, c)
            mcu = next(x for x in plan["components"] if x["ref"] == "U1")
            pin = next(p for p in mcu["pins"] if p["number"] == "10")
            self.assertEqual(pin["net"], "CTRL1")
            self.assertIsNone(pin["nc_reason"])
            self.assertEqual(
                len([x for x in plan["components"] if x["role"] == "switch"]), 1
            )
            self.assertEqual(
                len({x["ref"] for x in plan["components"]}), len(plan["components"])
            )
            bindings = make_bindings(c)
            target = {"project_uuid": "synthetic", "document_uuid": "synthetic"}
            check_source(
                plan, {"connectivity": bind_plan(plan, bindings, target)}, target
            )
            self.assertNotIn(str(self.root), str(c))

    def test_occupied_gpio_reserved_debug_and_budget_are_rejected(self):
        for pin in ("34", "42", "39"):
            r = combined_fixture(self.root, True)
            r["composition"]["connections"][0]["gpio_pin"] = pin
            self.assertFalse(evaluate(r)["ready"])
        r = combined_fixture(self.root)
        r["composition"]["total_budget_ma"] = 0.1
        self.assertFalse(evaluate(r)["ready"])

    def test_two_branches_preserve_parts_and_sum_shared_gpio_load(self):
        r = combined_fixture(self.root)
        second = copy.deepcopy(r["knowledge"]["circuits"][0])
        second["id"] = "channel2"
        r["knowledge"]["circuits"].append(second)
        second_binding = copy.deepcopy(r["composition"]["connections"][0])
        second_binding["circuit_id"] = "channel2"
        r["composition"]["connections"].append(second_binding)
        plan = make_plan(contract_from(r))
        self.assertEqual(
            len([c for c in plan["components"] if c["role"] == "switch"]), 2
        )
        r["composition"]["port_budget_ma"]["A"] = 3
        self.assertFalse(evaluate(r)["ready"])

    def test_supply_mismatch_and_missing_binding_block(self):
        r = combined_fixture(self.root)
        r["composition"]["supplies"][0]["max_v"] = 13
        self.assertFalse(evaluate(r)["ready"])
        r = combined_fixture(self.root)
        del r["composition"]["base_bindings"]["U1"]
        self.assertFalse(evaluate(r)["ready"])

    def test_controller_withdrawal_and_base_topology_tampering_block(self):
        from eso.materials import MaterialStore

        r = combined_fixture(self.root)
        c = contract_from(r)
        a = confirm(c, digest(c), "synthetic test")
        plan = make_plan(c)
        plan["components"][0]["pins"][0]["net"] = "GND"
        with self.assertRaises(Refused):
            validate_plan(plan, c)
        MaterialStore().withdraw(
            r["composition"]["controller"]["record_sha256"], "wrong controller evidence"
        )
        with self.assertRaises(Refused):
            check_confirmation(c, a)

    def test_controller_control_envelope_and_internal_supply_alias_block(self):
        from eso.materials import MaterialStore

        r = combined_fixture(self.root)
        controller = r["composition"]["controller"]
        controller["record"]["coverage"]["control_v"] = [0, 0]
        controller["record_sha256"] = MaterialStore().add(
            controller["record"], self.root / "mcu.txt"
        )
        self.assertFalse(evaluate(r)["ready"])
        r = combined_fixture(self.root)
        r["composition"]["supplies"][0]["net"] = "channel1_LOAD"
        r["composition"]["connections"][0]["supply_net"] = "channel1_LOAD"
        self.assertFalse(evaluate(r)["ready"])
