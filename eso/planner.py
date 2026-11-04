"""A bounded, data-sheet based reference design, not an arbitrary-circuit generator."""
from .core import Refused, digest, validate

ST = "https://www.st.com/resource/en/datasheet/stm32f103c8.pdf"
BOSCH = "https://www.bosch-sensortec.com/media/boschsensortec/downloads/datasheets/bst-bmp280-ds001.pdf"
MCU_PINS = "VBAT PC13 PC14 PC15 PD0 PD1 NRST VSSA VDDA PA0 PA1 PA2 PA3 PA4 PA5 PA6 PA7 PB0 PB1 PB2 PB10 PB11 VSS VDD PB12 PB13 PB14 PB15 PA8 PA9 PA10 PA11 PA12 PA13 VSS VDD PA14 PA15 PB3 PB4 PB5 PB6 PB7 BOOT0 PB8 PB9 VSS VDD".split()


def make_plan(contract):
    validate(contract, "contract")
    r = contract["requirements"]
    profile = r.get("circuit_type", "mcu-sensor")
    if profile not in ("mcu-minimum", "mcu-sensor"):
        raise Refused("reference planner unsupported for this circuit type; author a source-backed plan and validate-plan")
    sensor_mode = profile == "mcu-sensor"
    supported = {"mcu": "STM32F103C8T6", "input_voltage_v": 3.3,
                 "logic_voltage_v": 3.3, "power_source": "external-regulated",
                 "clock": "internal", "debug": "swd", "passive_package": "0603"}
    if sensor_mode:
        supported.update(sensor="BMP280", sensor_form="bare-chip", bus="i2c", bus_speed_hz=100000)
    for key, value in supported.items():
        if r.get(key) != value:
            raise Refused(f"planner unsupported: {key}={r.get(key)!r}; supported reference value {value!r}")
    if sensor_mode and r.get("i2c_pullups") != "new":
        raise Refused("reference planner supports new on-board pullups only; existing pullups need a new topology")
    components = []
    def part(ref, query, names, nets, module, role, attach=None):
        pins = []
        for number, name in enumerate(names, 1):
            net = nets.get(str(number))
            pins.append({"number": str(number), "name": name, "net": net,
                         "nc_reason": "unused in this reference design; configure unused GPIO in firmware" if net is None else None})
        c = {"id": "eso-" + ref.lower(), "ref": ref, "query": query, "role": role,
             "module": module, "pins": pins}
        if attach:
            c["attach"] = {"pin": "1", "ref": attach[0], "pin_number": attach[1]}
        components.append(c)
    power = {"1": "+3V3", "8": "GND", "9": "+3V3", "23": "GND", "24": "+3V3",
             "35": "GND", "36": "+3V3", "47": "GND", "48": "+3V3"}
    mcu_nets = dict(power, **{"7": "NRST", "20": "BOOT1", "34": "SWDIO", "37": "SWCLK",
                             "42": "I2C_SCL", "43": "I2C_SDA", "44": "BOOT0"})
    if not sensor_mode:
        mcu_nets.pop("42"); mcu_nets.pop("43")
    part("U1", r["mcu"], MCU_PINS, mcu_nets, "mcu", "MCU")
    if sensor_mode:
        part("U2", "BMP280", ["GND", "CSB", "SDI", "SCK", "SDO", "VDDIO", "GND", "VDD"],
             {"1": "GND", "2": "+3V3", "3": "I2C_SDA", "4": "I2C_SCL", "5": "GND", "6": "+3V3", "7": "GND", "8": "+3V3"}, "sensor", "pressure sensor; I2C address 0x76")
    # Header pin order is part of the design contract, not a claim about any debugger standard.
    part("J1", "PinHeader 1x02 2.54", ["1", "2"], {"1": "+3V3", "2": "GND"}, "power", "regulated power input")
    part("J2", "PinHeader 1x05 2.54", [str(i) for i in range(1, 6)],
         {"1": "+3V3", "2": "GND", "3": "SWDIO", "4": "SWCLK", "5": "NRST"}, "debug", "custom SWD header; VTref only")
    for ref, net, pin in ((("R1", "I2C_SCL", "4"), ("R2", "I2C_SDA", "3")) if sensor_mode else ()):
        part(ref, f"{r['pullup_ohm']}ohm 0603 1%", ["1", "2"], {"1": net, "2": "+3V3"}, "sensor", "I2C pullup", ("U2", pin))
    for ref, net, rail, pin in (("R3", "NRST", "+3V3", "7"), ("R4", "BOOT0", "GND", "44"), ("R5", "BOOT1", "GND", "20")):
        part(ref, "10kohm 0603 1%", ["1", "2"], {"1": net, "2": rail}, "mcu", "reset/boot bias", ("U1", pin))
    for i, pin in enumerate(("24", "36", "48", "9"), 1):
        part(f"C{i}", "100nF 0603 X7R 16V", ["1", "2"], {"1": "+3V3", "2": "GND"}, "mcu", "supply decoupling", ("U1", pin))
    part("C5", "4.7uF 0603 X5R 10V", ["1", "2"], {"1": "+3V3", "2": "GND"}, "power", "input bulk", ("J1", "1"))
    part("C6", "100nF 0603 X7R 16V", ["1", "2"], {"1": "NRST", "2": "GND"}, "mcu", "reset filter", ("U1", "7"))
    for i, pin in (((7, "8"), (8, "6")) if sensor_mode else ()):
        part(f"C{i}", "100nF 0603 X7R 16V", ["1", "2"], {"1": "+3V3", "2": "GND"}, "sensor", "sensor decoupling", ("U2", pin))
    return {"schema_version": 1, "contract_sha256": digest(contract), "template": "stm32f103-bmp280-v1" if sensor_mode else "stm32f103-minimum-v1",
            "nets": [{'name': name, 'role': 'power' if name == '+3V3' else 'ground' if name == 'GND' else 'signal',
                      'scope': 'global' if name in ('+3V3', 'GND') else 'local',
                      **({'voltage_v': 3.3 if name == '+3V3' else 0} if name in ('+3V3', 'GND') else {})}
                     for name in sorted({pin['net'] for component in components for pin in component['pins'] if pin['net'] is not None})],
            "components": components, "modules": [{"id": m, "core_ref": c, "title": t} for m, c, t in
                (("power", "J1", "3V3 INPUT"), ("mcu", "U1", "STM32 MCU"), ("sensor", "U2", "BMP280 I2C"), ("debug", "J2", "SWD DEBUG")) if sensor_mode or m != "sensor"],
            "evidence": [{"url": ST, "section": "LQFP48 pinout, power supply scheme, HSI/SWD"},
                         *([{"url": BOSCH, "section": "5.2 and 6.1-6.3"}] if sensor_mode else [])],
            "notes": ["HSI 8MHz; no USB or crystal", "SWD connector: 1 VTref, 2 GND, 3 SWDIO, 4 SWCLK, 5 NRST",
                      *(["Address 0x76; CSB high; SDO low"] if sensor_mode else []), "All physical pins accounted for; library pinout still requires live verification"]}


def net_definitions(plan):
    """Explicit definitions cover every used net; old plans retain legacy semantics."""
    names = {p['net'] for c in plan['components'] for p in c['pins'] if p['net'] is not None}
    if 'nets' not in plan:
        return {name: {'name': name, 'scope': 'global' if name in ('+3V3', 'GND') else 'local',
                       'role': 'power' if name == '+3V3' else 'ground' if name == 'GND' else 'signal'} for name in names}
    definitions = {n['name']: n for n in plan['nets']}
    if len(definitions) != len(plan['nets']) or set(definitions) != names:
        raise Refused('explicit net definitions must cover each used net exactly once')
    for net in definitions.values():
        if net['role'] == 'ground' and net['voltage_v'] != 0:
            raise Refused('ground voltage must be zero relative to its domain reference')
    return definitions


def bind_plan(plan, bindings, target):
    validate(plan, "plan")
    definitions = net_definitions(plan)
    refs = {c["ref"] for c in plan["components"]}
    if set(bindings) != refs:
        raise Refused("bindings must cover exactly the planned component references")
    components, nets, connections = [], {}, []
    for index, c in enumerate(plan["components"]):
        b = bindings[c["ref"]]
        import re
        if not all(isinstance(b.get(k), str) and re.fullmatch(r"[a-fA-F0-9]{32}", b[k]) for k in ("libraryUuid", "deviceUuid")):
            raise Refused(f"{c['ref']}: exact 32-character library/device UUID required")
        if not b.get("evidence") or b.get("verified_model") != c["query"]:
            raise Refused(f"{c['ref']}: reviewed model/selection evidence missing")
        pins = []
        for p in c["pins"]:
            pin = {"number": p["number"], "name": p["name"]}
            if p["net"] is None:
                pin["noConnected"] = True
            else:
                net_id = "net-" + p["net"]
                definition = definitions[p['net']]
                nets[net_id] = {"id": net_id, **{k: definition[k] for k in ('name', 'scope', 'role')}}
                connections.append({"componentId": c["id"], "pinNumber": p["number"], "netId": net_id, "kind": "netlist"})
            pins.append(pin)
        components.append({"id": c["id"], "ref": c["ref"], "role": c["role"],
                           "device": {"libraryUuid": b["libraryUuid"], "deviceUuid": b["deviceUuid"], "name": c["query"]},
                           "pins": pins, "placement": {"x": 150 + index % 5 * 200, "y": 650 - index // 5 * 160, "rotation": 0, "mirror": False}})
    modules = [{"id": m["id"], "name": m["title"],
                "coreComponents": [c["id"] for c in plan["components"] if c["ref"] == m["core_ref"]],
                "peripheralComponents": [c["id"] for c in plan["components"] if c["module"] == m["id"] and c["ref"] != m["core_ref"]]} for m in plan["modules"]]
    return {"schemaVersion": "1.4", "projectId": target["project_uuid"], "documentId": target["document_uuid"],
            "components": components, "nets": list(nets.values()), "connections": connections, "modules": modules}
