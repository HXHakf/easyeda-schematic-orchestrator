"""Bounded composition of reference MCU templates and reviewed static switches."""

from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from .core import Refused, digest, file_digest
from .knowledge_input import obj
from .materials import MaterialStore, RECORD, SHA, TEXT, NUMBER, shape

CONNECTION = obj(
    {
        k: TEXT
        for k in (
            "circuit_id",
            "gpio_pin",
            "supply_net",
            "ground_net",
            "reset_high_z_review",
        )
    },
    ("circuit_id", "gpio_pin", "supply_net", "ground_net", "reset_high_z_review"),
)
SUPPLY = obj(
    {"net": TEXT, "min_v": NUMBER, "max_v": NUMBER, "ground": TEXT, "evidence": TEXT},
    ("net", "min_v", "max_v", "ground", "evidence"),
)
SCHEMA = obj(
    {
        "mode": {"const": "stable-powered-static"},
        "controller": obj(
            {"record_sha256": SHA, "record": RECORD}, ("record_sha256", "record")
        ),
        "base_bindings": {"type": "object", "additionalProperties": {"type": "object"}},
        "connections": {"type": "array", "items": CONNECTION, "minItems": 1},
        "supplies": {"type": "array", "items": SUPPLY},
        "port_budget_ma": obj({"A": NUMBER, "B": NUMBER}, ("A", "B")),
        "total_budget_ma": NUMBER,
        "budget_review": TEXT,
    },
    (
        "mode",
        "controller",
        "base_bindings",
        "connections",
        "supplies",
        "port_budget_ma",
        "total_budget_ma",
        "budget_review",
    ),
)
ALLOWED = (
    {f"PA{i}" for i in range(8)} | {"PB0", "PB1"} | {f"PB{i}" for i in range(5, 12)}
)


def base_plan(requirements):
    from .requirements import contract_from
    from .planner import make_plan

    r = deepcopy(requirements)
    r.pop("knowledge", None)
    r.pop("composition", None)
    return make_plan(contract_from(r))


def checked_composition(requirements, knowledge_report):
    from .planner import bind_plan, MCU_PINS

    comp = requirements["composition"]
    shape(comp, SCHEMA)
    if (
        requirements.get("circuit_type") not in ("mcu-minimum", "mcu-sensor")
        or requirements["knowledge"].get("schema_version") != 2
    ):
        raise Refused("composition requires reference MCU profile and knowledge v2")
    if not knowledge_report["ready"]:
        raise Refused("knowledge circuits incomplete")
    base = base_plan(requirements)
    bindings = comp["base_bindings"]
    if set(bindings) != {c["ref"] for c in base["components"]}:
        raise Refused("base_bindings must cover exact reference plan")
    # Existing identity/pin/net checks remain the baseline, no synthetic binding substitution.
    bind_plan(
        base,
        bindings,
        {"project_uuid": "offline-validation", "document_uuid": "offline-validation"},
    )
    controller = comp["controller"]
    r = controller["record"]
    MaterialStore().check(controller["record_sha256"], r)
    if (
        r["kind"] != "mcu"
        or r["model"] != requirements["mcu"]
        or r["package"] != "LQFP48"
    ):
        raise Refused(
            "controller identity must match the STM32F103C8T6 LQFP48 reference"
        )
    expected = {name + "_" + str(i): str(i) for i, name in enumerate(MCU_PINS, 1)}
    if (
        r["pins"] != expected
        or bindings["U1"].get("pins") != expected
        or bindings["U1"].get("verified_package") != "LQFP48"
    ):
        raise Refused("controller/library physical pin evidence differs from reference")
    required = (
        "gpio_voh_min_v",
        "gpio_voh_max_v",
        "gpio_vol_max_v",
        "gpio_source_limit_ma",
        "gpio_leakage_max_ua",
        "port_a_source_limit_ma",
        "port_b_source_limit_ma",
        "total_source_limit_ma",
    )
    for key in required:
        f = r["facts"].get(key)
        direction = "lower" if key == "gpio_voh_min_v" else "upper"
        if (
            not f
            or f["basis"] == "typical"
            or f["bound"] != direction
            or f["value"] < 0
        ):
            raise Refused("missing bounded controller fact: " + key)
    f = {k: r["facts"][k]["value"] for k in required}
    if not 0 <= f["gpio_vol_max_v"] < f["gpio_voh_min_v"] <= f["gpio_voh_max_v"] <= 3.3:
        raise Refused("controller logic bounds invalid")
    if f["gpio_source_limit_ma"] <= 0:
        raise Refused("controller source current must be positive")
    cov = r["coverage"]
    if not cov["supply_v"][0] <= 3.3 <= cov["supply_v"][1] or cov["frequency_hz"] != [
        0,
        0,
    ]:
        raise Refused(
            "controller coverage must include regulated 3.3V static operation"
        )
    if not cov["control_v"][0] <= 0 <= f["gpio_voh_max_v"] <= cov["control_v"][1]:
        raise Refused("controller control-voltage coverage mismatch")
    for bank in ("A", "B"):
        if (
            not 0
            <= comp["port_budget_ma"][bank]
            <= f["port_" + bank.lower() + "_source_limit_ma"]
        ):
            raise Refused("allocated port budget exceeds reviewed limit")
    if not 0 <= comp["total_budget_ma"] <= f["total_source_limit_ma"]:
        raise Refused("invalid total current budget")
    supplies = {"+3V3": {"min_v": 3.3, "max_v": 3.3, "ground": "GND"}}
    base_nets = {n["name"] for n in base["nets"]}
    internal_nets = {
        c["id"] + suffix
        for c in requirements["knowledge"]["circuits"]
        for suffix in ("_DRIVE", "_LOAD")
    }
    for supply in comp["supplies"]:
        if (
            supply["net"] in internal_nets
            or supply["net"] in base_nets
            or supply["net"] in supplies
            or not 0 < supply["min_v"] <= supply["max_v"] <= 60
            or supply["ground"] != "GND"
        ):
            raise Refused(
                "external supply collides or has unsupported voltage/reference ground"
            )
        supplies[supply["net"]] = supply
    circuits = {c["id"]: c for c in requirements["knowledge"]["circuits"]}
    if len(comp["connections"]) != len(circuits) or {
        x["circuit_id"] for x in comp["connections"]
    } != set(circuits):
        raise Refused("connections must map every circuit exactly once")
    occupied = {
        p["number"] for p in base["components"][0]["pins"] if p["net"] is not None
    }
    by_pin = {}
    by_net = {}
    loads = defaultdict(float)
    banks = defaultdict(float)
    maps = {}
    used_supplies = set()
    for link in comp["connections"]:
        c = circuits[link["circuit_id"]]
        p = c["parameters"]
        pin = link["gpio_pin"]
        if pin not in expected.values():
            raise Refused("controller pin missing")
        name = MCU_PINS[int(pin) - 1]
        if pin in occupied or name not in ALLOWED:
            raise Refused("GPIO occupied or outside bounded supported GPIO set: " + pin)
        control = c["ports"]["control"]
        if control in base_nets or control in supplies:
            raise Refused("control net collides with reference/supply")
        if (pin in by_pin and by_pin[pin] != control) or (
            control in by_net and by_net[control] != pin
        ):
            raise Refused(
                "one GPIO maps to one control net; cannot short distinct GPIOs"
            )
        by_pin[pin] = control
        by_net[control] = pin
        supply = supplies.get(link["supply_net"])
        used_supplies.add(link["supply_net"])
        if not supply or link["ground_net"] != "GND" or c["ports"]["ground"] != "GND":
            raise Refused("explicit common ground/supply mapping required")
        if (
            p["supply_min_v"] != supply["min_v"]
            or p["supply_voltage_v"] != supply["max_v"]
        ):
            raise Refused("module supply range differs from explicit supply")
        if (
            p["control_high_min_v"] > f["gpio_voh_min_v"]
            or p["control_high_max_v"] < f["gpio_voh_max_v"]
            or p["control_low_max_v"] < f["gpio_vol_max_v"]
            or p["gpio_leakage_max_ua"] < f["gpio_leakage_max_ua"]
            or p["gpio_source_limit_ma"] > f["gpio_source_limit_ma"]
        ):
            raise Refused(
                "module assumes better controller drive/leakage than reviewed facts"
            )
        if (
            not cov["ambient_c"][0]
            <= p["ambient_min_c"]
            <= p["ambient_max_c"]
            <= cov["ambient_c"][1]
            or cov["thermal_context"] != p["thermal_context"]
        ):
            raise Refused("controller temperature/thermal coverage mismatch")
        current = (
            knowledge_report["evidence"]["calculations"][c["id"]]["gpio_current_max_a"]
            * 1000
        )
        loads[pin] += current
        banks[name[1]] += current
        maps[c["id"]] = {
            c["ports"]["supply"]: link["supply_net"],
            c["ports"]["ground"]: "GND",
        }
    if set(supplies) - used_supplies - {"+3V3"}:
        raise Refused("unused external supply declaration")
    if any(x > f["gpio_source_limit_ma"] for x in loads.values()):
        raise Refused("shared GPIO budget exceeded")
    if (
        any(x > comp["port_budget_ma"][bank] for bank, x in banks.items())
        or sum(loads.values()) > comp["total_budget_ma"]
    ):
        raise Refused("aggregate port/chip current budget exceeded")
    if (
        sum(loads.values()) / 1000 > cov["load_current_a"][1]
        or cov["load_current_a"][0] > 0
    ):
        raise Refused("controller current envelope does not cover aggregate load")
    return (
        base,
        maps,
        by_pin,
        {
            "gpio_current_ma": dict(loads),
            "port_current_ma": dict(banks),
            "total_current_ma": sum(loads.values()),
        },
    )


def assess_composition(requirements, knowledge_report):
    try:
        _, _, _, budget = checked_composition(requirements, knowledge_report)
        root = Path(__file__).parent
        validators = {
            n: file_digest(root / n)
            for n in ("knowledge_composition.py", "planner.py", "requirements.py")
        }
        return {
            "ready": True,
            "questions": [],
            "evidence": {
                "composition_sha256": digest(requirements["composition"]),
                "composition_validators_sha256": digest(validators),
                "controller_budget": budget,
            },
        }
    except (ValueError, KeyError, TypeError, OSError, IndexError) as error:
        return {
            "ready": False,
            "questions": [
                {
                    "field": "composition",
                    "question": str(error),
                    "reason": "composition check",
                }
            ],
        }


def make_composed_plan(contract):
    from .knowledge import assess_knowledge, check_contract_knowledge
    from .knowledge_design import make_knowledge_plan

    check_contract_knowledge(contract)
    r = contract["requirements"]
    comp = r["composition"]
    report = assess_knowledge(r["knowledge"])
    base, maps, by_pin, _ = checked_composition(r, report)
    switch = make_knowledge_plan(contract)
    bindings = deepcopy(comp["base_bindings"])
    materials = {m["id"]: m for m in r["knowledge"]["materials"]}
    circuits = {c["id"]: c for c in r["knowledge"]["circuits"]}
    refs = {c["ref"] for c in base["components"]}
    remap = {}
    for c in switch["components"]:
        old = c["ref"]
        prefix = old[0]
        i = 1
        while prefix + str(i) in refs:
            i += 1
        c["ref"] = prefix + str(i)
        refs.add(c["ref"])
        remap[old] = c["ref"]
        bindings[c["ref"]] = deepcopy(
            materials[circuits[c["module"]]["roles"][c["role"]]]["library"]
        )
        for pin in c["pins"]:
            pin["net"] = maps[c["module"]].get(pin["net"], pin["net"])
        c["module"] = "kb_" + c["module"]
        c["id"] = "kb_" + c["id"]
    for module in switch["modules"]:
        module["core_ref"] = remap[module["core_ref"]]
        module["id"] = "kb_" + module["id"]
    for pin in base["components"][0]["pins"]:
        if pin["number"] in by_pin:
            pin.update(net=by_pin[pin["number"]], nc_reason=None)
    nets = {n["name"]: deepcopy(n) for n in base["nets"]}
    port_rename = {}
    for mapping in maps.values():
        for old, new in mapping.items():
            if old in port_rename and port_rename[old] != new:
                raise Refused("ambiguous module supply aliases")
            port_rename[old] = new
    for net in switch["nets"]:
        n = deepcopy(net)
        n["name"] = port_rename.get(n["name"], n["name"])
        if n["name"] in nets and nets[n["name"]] != n:
            raise Refused("composed network definitions conflict")
        nets[n["name"]] = n
    components = base["components"] + switch["components"]
    if len({c["id"] for c in components}) != len(components):
        raise Refused("composed component ID collision")
    evidence = {
        **contract["knowledge_evidence"],
        "bindings_sha256": digest(bindings),
        "library_identities": {
            c["id"]: {k: bindings[c["ref"]][k] for k in ("libraryUuid", "deviceUuid")}
            for c in components
        },
    }
    plan = {
        **base,
        "contract_sha256": digest(contract),
        "template": "stm32-static-switches-v2",
        "components": components,
        "modules": base["modules"] + switch["modules"],
        "nets": sorted(nets.values(), key=lambda n: n["name"]),
        "evidence": base["evidence"]
        + switch["evidence"]
        + [
            {
                "url": "urn:sha256:"
                + comp["controller"]["record"]["datasheet"]["sha256"],
                "section": comp["controller"]["record"]["datasheet"]["pinout_section"],
            }
        ],
        "knowledge_evidence": evidence,
        "notes": base["notes"]
        + switch["notes"]
        + [
            "只覆盖已声明稳定供电、静态输出和经审阅高阻状态；不验证任意电源时序或固件。"
        ],
    }
    return plan, bindings
