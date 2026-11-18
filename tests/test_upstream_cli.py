"""Optional real upstream CLI integration. Only offline commands; never project writes."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from eso.adapter import EasyEDA
from eso.core import Refused, dump, load
from eso.planner import bind_plan, make_plan
from eso.requirements import contract_from
from eso.workflow import validate_queue


TARGET = {"project_name": "ESO-TEST-OFFLINE", "project_uuid": "synthetic-project",
          "document_uuid": "synthetic-page"}
INSTANCE_FIELDS = ("uniqueId", "name", "subPartName", "addIntoBom", "addIntoPcb", "manufacturer",
                   "manufacturerId", "supplier", "supplierId", "otherProperty", "component",
                   "symbol", "footprint")


def joint_drawing_fixture():
    """Synthetic measured geometry, with two logical modules in one drawing frame."""
    logical_modules = [
        {"id": "logic-a", "core": "core-a", "peripheral": "peripheral-a"},
        {"id": "logic-b", "core": "core-b", "peripheral": "peripheral-b"},
    ]
    rows = [
        ("core-a", "U1", 100, 100, ("IN", "MAIN")),
        ("peripheral-a", "R1", 100, 200, ("IN", "")),
        ("core-b", "U2", 300, 100, ("MAIN", "OUT")),
        ("peripheral-b", "R2", 300, 200, ("", "OUT")),
    ]
    components, placements, native_parts, connections = [], [], [], []
    for component_id, ref, x, y, nets in rows:
        pins = [{"number": str(index), "name": str(index), "noConnected": not net}
                for index, net in enumerate(nets, 1)]
        components.append({
            "id": component_id, "ref": ref, "uniqueId": "unique-" + ref,
            "device": {"libraryUuid": "1" * 32, "deviceUuid": "2" * 32,
                       "name": "SYNTHETIC ONLY"}, "pins": pins,
        })
        measured_pins = [dict(number=pin["number"], name=pin["name"], net=net,
                              x=x + offset, y=y)
                         for pin, net, offset in zip(pins, nets, (-30, 30))]
        bbox = {"minX": x - 10, "minY": y - 10, "maxX": x + 10, "maxY": y + 10}
        placements.append({"primitiveId": "native-" + ref, "designator": ref,
                           "value": "SYNTHETIC", "x": x, "y": y, "rotation": 0,
                           "mirror": False, "bbox": bbox, "pins": measured_pins})
        native_parts.append({
            "componentType": "part", "primitiveId": "native-" + ref,
            "designator": ref, "uniqueId": "unique-" + ref, "name": "SYNTHETIC",
            "subPartName": "fixture.1", "addIntoBom": False, "addIntoPcb": True,
            "manufacturer": "", "manufacturerId": "", "supplier": None, "supplierId": "",
            "component": {"uuid": "native-component-" + ref},
            "symbol": {"uuid": "native-symbol-" + ref},
            "footprint": {"uuid": "native-footprint-" + ref},
            "device": {"libraryUuid": "1" * 32, "uuid": "2" * 32},
            "otherProperty": {"EasyEDA Agent Component ID": component_id,
                              "Synthetic assembly note": "retain exactly", "empty": "",
                              "enabled": False, "zero": 0, "literal": "${literal}"},
            "x": x, "y": y, "rotation": 0, "mirror": False, "bbox": bbox,
            "pinsAvailable": True, "netlistAvailable": True,
            "pins": [{"primitiveId": "native-" + ref + "-pin-" + pin["number"],
                      "pinNumber": pin["number"], "pinName": pin["name"],
                      "otherProperty": {"Synthetic pin note": "retain", "empty": "", "zero": 0},
                      "x": pin["x"], "y": pin["y"], "rotation": 0,
                      "net": pin["net"], "noConnected": not pin["net"]}
                     for pin in measured_pins],
        })
        connections.extend({"componentId": component_id, "pinNumber": pin["number"],
                            "netId": pin["net"], "kind": "netlist"}
                           for pin in measured_pins if pin["net"])
    group = {"id": "joint-group", "name": "SYNTHETIC JOINT GROUP",
             "coreComponents": [module["core"] for module in logical_modules],
             "peripheralComponents": [module["peripheral"] for module in logical_modules]}
    wires = [
        {"net": "IN", "points": [[70, 100], [50, 100], [50, 200], [70, 200]]},
        {"net": "MAIN", "points": [[130, 100], [270, 100]]},
        {"net": "OUT", "points": [[330, 100], [350, 100], [350, 200], [330, 200]]},
    ]
    sheet = {"minX": 0, "minY": 0, "maxX": 1170, "maxY": 825}
    source = {
        "schemaVersion": 1,
        "connectivity": {"schemaVersion": "1.4", "projectId": TARGET["project_uuid"],
                         "documentId": TARGET["document_uuid"], "components": components,
                         "nets": [{"id": net, "name": net} for net in ("IN", "MAIN", "OUT")],
                         "connections": connections, "modules": [group]},
        "sheet": sheet, "keepouts": [],
        "modules": [{"id": group["id"], "title": group["name"], "placements": placements,
                     "wires": wires, "flags": [
                         {"net": "IN", "kind": "net_port_bi", "pinX": 50, "pinY": 150,
                          "direction": "left", "offset": 20},
                         {"net": "MAIN", "kind": "net_port_bi", "pinX": 200, "pinY": 100,
                          "direction": "down", "offset": 20},
                         {"net": "OUT", "kind": "net_port_bi", "pinX": 350, "pinY": 150,
                          "direction": "right", "offset": 20},
                     ]}],
    }
    native_sheet = {"componentType": "sheet", "primitiveId": "fixture-sheet", "bbox": sheet}
    native_wires = []
    for wire in wires:
        for start, end in zip(wire["points"], wire["points"][1:]):
            native_wires.append({"primitiveId": "old-wire-" + str(len(native_wires)),
                                 "net": wire["net"], "x0": start[0], "y0": start[1],
                                 "x1": end[0], "y1": end[1]})
    page = {key: [] for key in ("arcs", "attributes", "buses", "circles", "objects",
                                "pins", "polygons", "rectangles", "texts", "wires")}
    page["components"] = [native_sheet, *native_parts]
    page["pins"] = [{"primitiveId": pin["primitiveId"], "componentId": part["primitiveId"]}
                    for part in native_parts for pin in part["pins"]]
    page["wires"] = [{"primitiveId": wire["primitiveId"], "Net": wire["net"],
                      "Line": [wire["x0"], wire["y0"], wire["x1"], wire["y1"]],
                      "Color": "#008000", "LineWidth": 1, "LineType": 0}
                     for wire in native_wires]
    page["attributes"] = [
        {"primitiveId": "attribute-" + part["designator"], "ParentPrimitiveId": part["primitiveId"],
         "Key": "Value", "Value": "SYNTHETIC", "KeyVisible": False, "ValueVisible": True,
         "FontSize": 10, "X": part["x"], "Y": part["y"] + 20, "Rotation": 0,
         "Color": "#000000", "FontName": "default", "Bold": False, "Italic": False,
         "UnderLine": False, "AlignMode": 0, "FillColor": "none"}
        for part in native_parts
    ]
    page["rectangles"] = [{"primitiveId": "old-frame", "TopLeftX": 20, "TopLeftY": 250,
                            "Width": 380, "Height": 200, "CornerRadius": 0, "Rotation": 0,
                            "Color": "#AA00AA", "FillColor": "none", "LineWidth": 1,
                            "LineType": 1, "FillStyle": "none"}]
    page["texts"] = [{"primitiveId": "old-title", "X": 30, "Y": 240, "Content": "OLD FRAME",
                      "Rotation": 0, "TextColor": "#AA00AA", "FontName": "default",
                      "FontSize": 10, "Bold": False, "Italic": False, "UnderLine": False,
                      "AlignMode": 0}]
    before = {"ok": True, "context": {"projectUuid": TARGET["project_uuid"],
                                       "documentUuid": TARGET["document_uuid"], "documentType": "schematic"},
              "result": {"components": page["components"], "count": len(page["components"]),
                         "wires": native_wires, "wiresAvailable": True, "pinNetsAvailable": True,
                         "pagePrimitives": page,
                         "connectivitySummary": {"scope": "activePage", "wires": len(native_wires),
                                                 "buses": 0, "netflags": 0, "netports": 0,
                                                 "netlabels": 0, "shortSymbols": 0}}}
    primary_path = {"id": "main", "title": "SYNTHETIC MAIN",
                    "links": [{"from": {"componentId": "core-a", "pinNumber": "2"},
                               "to": {"componentId": "core-b", "pinNumber": "1"}, "net": "MAIN"}]}
    return logical_modules, primary_path, source, before


@unittest.skipUnless(os.environ.get("EASYEDA_BIN"), "set EASYEDA_BIN to test actual upstream offline compiler")
class UpstreamCLITests(unittest.TestCase):
    def compile_fixture(self, root, source, before, *, preserve_instances=False):
        dump(root / "source.json", source)
        dump(root / "before.json", before)
        adapter = EasyEDA(os.environ["EASYEDA_BIN"], root / "receipts")
        command = ["sch", "compose", "--from", root / "source.json", "--before", root / "before.json",
                   "--out", root / "computed.json", "--playbook", root / "queue.json", "--replace"]
        if preserve_instances:
            command.append("--preserve-instances")
        adapter.run(command, json_output=False)
        computed, queue = load(root / "computed.json"), load(root / "queue.json")
        self.assertEqual(load(root / "source.json"), source)
        self.assertEqual(load(root / "before.json"), before)
        if preserve_instances:
            # Task 4 must add a narrowly protected drawing-reset adapter before this
            # queue can enter Workflow. The real upstream offline compiler supports it.
            with self.assertRaisesRegex(Refused, "action outside MVP surface: schematic.primitives.delete"):
                validate_queue(queue, TARGET)
        else:
            validate_queue(queue, TARGET)
        self.assertEqual(adapter.run(["sch", "apply", root / "queue.json", "--dry-run"],
                                     json_output=False)["exit_code"], 0)
        return computed, queue

    def assert_joint_intent(self, source, computed, queue, logical_modules, primary_path):
        original = source["connectivity"]
        compiled = computed["connectivity"]
        self.assertEqual({component["id"] for component in compiled["components"]},
                         {module[key] for module in logical_modules for key in ("core", "peripheral")})
        self.assertEqual(compiled["connections"], original["connections"])
        self.assertEqual(compiled["nets"], original["nets"])
        self.assertEqual(compiled["modules"], original["modules"])
        original_pins = {(component["id"], pin["number"]): (pin["name"], pin.get("noConnected", False))
                         for component in original["components"] for pin in component["pins"]}
        compiled_pins = {(component["id"], pin["number"]): (pin["name"], pin.get("noConnected", False))
                         for component in compiled["components"] for pin in component["pins"]}
        self.assertEqual(compiled_pins, original_pins)
        self.assertEqual(sum(nc for _, nc in compiled_pins.values()), 2)
        self.assertEqual({component["id"]: component["device"] for component in compiled["components"]},
                         {component["id"]: component["device"] for component in original["components"]})
        self.assertEqual(len(computed["layout"]["frames"]), 1)
        self.assertEqual(computed["layout"]["frames"][0]["id"], "joint-group")
        steps = {step["id"]: step for step in queue["steps"]}
        frames = json.loads(steps["apply-module-frames"]["flags"]["data"])["frames"]
        self.assertEqual(frames, computed["layout"]["frames"])
        self.assertTrue(queue["requireFullExecution"])
        final = steps["verify-all-pins-nets-nc"]["expectSchematic"]
        self.assertEqual(final["ownership"]["modules"], original["modules"])
        nets = {(edge["componentId"], edge["pinNumber"]): edge["netId"] for edge in original["connections"]}
        for component in original["components"]:
            for pin in component["pins"]:
                protected_pin = final["parts"][component["ref"]]["pins"][pin["number"]]
                self.assertEqual(protected_pin["net"], nets.get((component["id"], pin["number"]), ""))
                self.assertEqual(protected_pin["noConnected"], pin["noConnected"])
        for link in primary_path["links"]:
            points = []
            source_points = []
            for endpoint in ("from", "to"):
                component = next(component for component in compiled["components"]
                                 if component["id"] == link[endpoint]["componentId"])
                pin = next(pin for pin in component["pins"]
                           if pin["number"] == link[endpoint]["pinNumber"])
                points.append([pin["x"], pin["y"]])
                placement = next(placement for placement in source["modules"][0]["placements"]
                                 if placement["designator"] == component["ref"])
                measured = next(pin for pin in placement["pins"] if pin["number"] == link[endpoint]["pinNumber"])
                source_points.append([measured["x"], measured["y"]])
                self.assertEqual(final["parts"][component["ref"]]["pins"][pin["number"]]["net"], link["net"])
            self.assertIn({"net": link["net"], "points": source_points}, source["modules"][0]["wires"])
            wire = {"net": link["net"], "points": points}
            self.assertIn(wire, computed["layout"]["wires"])
            self.assertIn(wire, final["drawing"]["wires"])
            self.assertTrue(any(step.get("action") == "schematic.wire.create"
                                and step["payload"]["points"] == points for step in queue["steps"]))
        wiring_index = next(index for index, step in enumerate(queue["steps"])
                            if step.get("action") == "schematic.wire.create")
        self.assertLess(next(index for index, step in enumerate(queue["steps"])
                             if step["id"] == "verify-physical-pins-before-wiring"), wiring_index)

    def test_joint_drawing_group_preserves_primary_wire(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logical_modules, primary_path, source, before = joint_drawing_fixture()
            # Compile onto an empty synthetic page without claiming a live observation.
            page = before["result"]["pagePrimitives"]
            page["components"] = page["components"][:1]
            page["pins"] = []
            page["wires"] = []
            page["attributes"] = []
            page["rectangles"] = []
            page["texts"] = []
            before["result"].update(components=page["components"], count=1, wires=[])
            before["result"]["connectivitySummary"]["wires"] = 0
            computed, queue = self.compile_fixture(root, source, before)
            self.assert_joint_intent(source, computed, queue, logical_modules, primary_path)

    def test_owned_wired_revision_compiles_with_preserved_instances(self):
        with tempfile.TemporaryDirectory() as directory:
            logical_modules, primary_path, source, before = joint_drawing_fixture()
            computed, queue = self.compile_fixture(Path(directory), source, before, preserve_instances=True)
            self.assert_joint_intent(source, computed, queue, logical_modules, primary_path)
            steps = {step["id"]: step for step in queue["steps"]}
            page = before["result"]["pagePrimitives"]
            source_guard = steps["verify-source-before-reset"]["expectSchematic"]
            self.assertEqual(source_guard["sourceScene"]["pagePrimitives"], page)
            reset = steps["reset-drawing-preserving-instances"]
            deleted_ids = sorted(row["primitiveId"] for kind in ("wires", "rectangles", "texts")
                                 for row in page[kind])
            self.assertEqual(reset["action"], "schematic.primitives.delete")
            self.assertEqual(reset["payload"], {"primitiveIds": deleted_ids})
            self.assertEqual(reset["assert"], {"$.total": "==9", "$.requested": "==9"})
            remaining = copy.deepcopy(page)
            for kind in ("wires", "rectangles", "texts"):
                remaining[kind] = []
            after_clear = steps["verify-preserved-parts-after-clear"]["expectSchematic"]
            self.assertEqual(after_clear["pagePrimitives"], remaining)
            self.assertEqual(after_clear["drawing"], {"wires": None, "flags": None})
            parts = [part for part in before["result"]["components"] if part["componentType"] == "part"]
            moves = [step for step in queue["steps"] if step["id"].startswith("move-preserved-")]
            self.assertEqual(len(moves), len(parts))
            self.assertEqual({step["payload"]["primitiveId"] for step in moves},
                             {part["primitiveId"] for part in parts})
            for step in moves:
                self.assertTrue(step["payload"]["preserveInstance"])
                self.assertEqual(set(step["payload"]["patch"]), {"x", "y", "rotation", "mirror"})
                self.assertEqual(step["assert"], {"$.instancePreserved": "==true"})
            for phase in ("verify-source-before-reset", "verify-preserved-parts-after-clear",
                          "verify-physical-pins-before-wiring", "verify-all-pins-nets-nc",
                          "verify-saved-instance-preservation"):
                guard = steps[phase]["expectSchematic"]
                for part in parts:
                    protected = guard["parts"][part["designator"]]
                    self.assertEqual(protected["primitiveId"], part["primitiveId"])
                    self.assertEqual(protected["instance"], {key: part[key] for key in INSTANCE_FIELDS})
                    for pin in part["pins"]:
                        self.assertEqual(protected["pins"][pin["pinNumber"]]["persistent"],
                                         {key: pin[key] for key in ("primitiveId", "pinNumber", "otherProperty")})
                expected_attributes = {row["primitiveId"]: {key: value for key, value in row.items()
                                                            if key not in ("X", "Y", "Rotation")}
                                       for row in page["attributes"]}
                self.assertEqual(guard["ownedAttributes"]["attributes"], expected_attributes)
            self.assertFalse(any(step.get("run") == "sch clear" or step.get("action") in
                                 ("schematic.page.clear", "schematic.component.place") for step in queue["steps"]))

    def assert_compile_rejected(self, root, source, before, message):
        dump(root / "source.json", source)
        dump(root / "before.json", before)
        adapter = EasyEDA(os.environ["EASYEDA_BIN"], root / "receipts")
        receipt = adapter.run(
            ["sch", "compose", "--from", root / "source.json", "--before", root / "before.json",
             "--out", root / "computed.json", "--playbook", root / "queue.json", "--replace",
             "--preserve-instances"], json_output=False, check=False)
        self.assertEqual(receipt["exit_code"], 1)
        self.assertIn(message, receipt["stderr"])
        self.assertFalse((root / "queue.json").exists())
        self.assertFalse((root / "computed.json").exists())
        self.assertEqual(load(root / "source.json"), source)
        self.assertEqual(load(root / "before.json"), before)

    def test_joint_drawing_group_rejects_invalid_membership_and_pin_intent(self):
        defects = {
            "missing-member": "module joint-group member set differs from IR",
            "duplicate-owner": "module joint-group has unknown/duplicate member core-a",
            "wrong-net": "U1.2 pin geometry/net differs from IR",
            "wrong-nc": "U1.2 must have exactly one net or explicit NC or unconnected intent",
        }
        for defect, message in defects.items():
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as directory:
                _, _, source, before = joint_drawing_fixture()
                if defect == "missing-member":
                    source["connectivity"]["modules"][0]["peripheralComponents"].pop()
                if defect == "duplicate-owner":
                    source["connectivity"]["modules"][0]["peripheralComponents"].append("core-a")
                if defect == "wrong-net":
                    source["modules"][0]["placements"][0]["pins"][1]["net"] = "OUT"
                if defect == "wrong-nc":
                    source["connectivity"]["components"][0]["pins"][1]["noConnected"] = True
                self.assert_compile_rejected(Path(directory), source, before, message)

    def test_owned_wired_revision_rejects_drift_and_unknown_inventory(self):
        defects = {
            "before-net": "U1.2 source name/net/NC differs from target",
            "before-nc": "R1.2 source name/net/NC differs from target",
            "missing-instance-field": "U1: instance field manufacturer unavailable",
            "unknown-graphic": "drawing-only reset does not support circles",
        }
        for defect, message in defects.items():
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as directory:
                _, _, source, before = joint_drawing_fixture()
                if defect == "before-net":
                    before["result"]["components"][1]["pins"][1]["net"] = "OUT"
                if defect == "before-nc":
                    before["result"]["components"][2]["pins"][1]["noConnected"] = False
                if defect == "missing-instance-field":
                    del before["result"]["components"][1]["manufacturer"]
                if defect == "unknown-graphic":
                    before["result"]["pagePrimitives"]["circles"] = [
                        {"primitiveId": "unknown-circle", "CenterX": 500, "CenterY": 500, "Radius": 5,
                         "Color": "#000000", "FillColor": "none", "LineWidth": 1, "LineType": 0,
                         "FillStyle": "none"}]
                self.assert_compile_rejected(Path(directory), source, before, message)

    def test_native_svg_nullable_source_runs_real_renderer_and_layout(self):
        from test_native_text import NativeTextTests
        from eso.native_text import render
        from eso.bridge import build_layout_input
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            c,p,b,s,l,g,svg,_=NativeTextTests().setup_source(root)
            measured=render(svg)
            source=build_layout_input(c,p,b,s,l,g,svg,text_measurements=measured)
            self.assertGreaterEqual(len(source['measurements'][0]['textBboxes']),2)
            dump(root/'source.json',source)
            EasyEDA(os.environ['EASYEDA_BIN'],root/'receipts').run(
                ['sch','lib-layout','--from',root/'source.json','--out',root/'composition.json'],json_output=False)
            self.assertEqual(len(load(root/'composition.json')['modules'][0]['placements']),1)

    def test_snapshot_compatibility_probe_is_real_and_offline(self):
        import test_bridge
        from eso.compatibility import probe_snapshot
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            c,p,b,s,l,g,svg = test_bridge.fixture(root)
            fake = test_bridge.FakeBridgeAdapter(c,p,b,s,l,g)
            before = fake.snapshot(test_bridge.TARGET)
            # This is a synthetic empty-page fixture, not a repaired live snapshot.
            before['result']['pagePrimitives']['pins'] = []
            before['result']['connectivitySummary'] = {'scope':'activePage', 'wires':0, 'buses':0,
                                                       'netflags':0, 'netports':0, 'netlabels':0, 'shortSymbols':0}
            adapter = EasyEDA(os.environ['EASYEDA_BIN'], root/'receipts')
            report = probe_snapshot(adapter, before, test_bridge.TARGET, root/'probe')
            self.assertTrue(report['passed'])
            self.assertEqual(load(root/'probe'/'before.json'), before)
            self.assertEqual(report['level'], 'offline-compatibility-only')

    def test_incomplete_or_invalid_native_inventory_is_rejected(self):
        import test_bridge
        from eso.compatibility import probe_snapshot
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            c,p,b,s,l,g,svg = test_bridge.fixture(root)
            before = test_bridge.FakeBridgeAdapter(c,p,b,s,l,g).snapshot(test_bridge.TARGET)
            before['result']['pagePrimitives']['pins'] = []
            before['result']['connectivitySummary'] = {'scope':'activePage', 'wires':0, 'buses':0,
                                                       'netflags':0, 'netports':0, 'netlabels':0, 'shortSymbols':0}
            for defect in ('missing-pins', 'unknown-class', 'unavailable-pins', 'orphan-pin'):
                damaged = copy.deepcopy(before)
                page = damaged['result']['pagePrimitives']
                if defect == 'missing-pins': del page['pins']
                if defect == 'unknown-class': page['future'] = []
                if defect == 'unavailable-pins': page['pins'] = None
                if defect == 'orphan-pin': page['pins'] = [{'primitiveId':'orphan', 'componentId':'absent'}]
                original = copy.deepcopy(damaged)
                adapter = EasyEDA(os.environ['EASYEDA_BIN'], root/defect/'receipts')
                with self.subTest(defect=defect), self.assertRaises(Refused):
                    probe_snapshot(adapter, damaged, test_bridge.TARGET, root/defect/'probe')
                self.assertEqual(damaged, original)
                self.assertEqual(load(root/defect/'probe'/'before.json'), original)
                self.assertFalse(load(root/defect/'probe'/'report.json')['passed'])

    def test_measured_bridge_input_runs_the_real_layout_kernel(self):
        import test_bridge
        from eso.bridge import build_layout_input
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract, plan, bindings, snapshot, labels, sheet, svg = test_bridge.fixture(root)
            source = build_layout_input(contract, plan, bindings, snapshot, labels, sheet, svg)
            dump(root / 'layout-input.json', source)
            adapter = EasyEDA(os.environ['EASYEDA_BIN'], root / 'receipts')
            adapter.run(['sch', 'lib-layout', '--from', root / 'layout-input.json', '--out', root / 'composition.json'], json_output=False)
            composition = load(root / 'composition.json')
            self.assertEqual(len(composition['modules']), 1)
            self.assertEqual(len(composition['modules'][0]['placements']), 1)
            self.assertEqual(composition['connectivity']['connections'], source['connectivity']['connections'])

    def test_reference_materialize_and_dry_run_are_real_upstream(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adapter = EasyEDA(os.environ["EASYEDA_BIN"], root/"receipts")
            r = {"schema_version":1,"mcu":"STM32F103C8T6","sensor":"BMP280","sensor_form":"bare-chip",
                 "input_voltage_v":3.3,"power_source":"external-regulated","target_project":"ESO-TEST-OFFLINE"}
            plan = make_plan(contract_from(r))
            bindings = {c["ref"]:{"libraryUuid":"1"*32,"deviceUuid":"2"*32,"verified_model":c["query"],"evidence":"SYNTHETIC-DO-NOT-EXECUTE"} for c in plan["components"]}
            target = {"project_name":"ESO-TEST-OFFLINE","project_uuid":"synthetic-project","document_uuid":"synthetic-page"}
            ir = bind_plan(plan,bindings,target)
            dump(root/"input.json",ir)
            adapter.run(["sch","materialize",root/"input.json","--out",root/"queue.json"],json_output=False)
            validate_queue(load(root/"queue.json"),target)
            receipt = adapter.run(["sch","apply",root/"queue.json","--dry-run"],json_output=False)
            self.assertEqual(receipt["exit_code"],0)
            self.assertGreater(len(load(root/"queue.json")["steps"]),len(plan["components"]))
            # materialize is placement-only. A successful dry-run is not circuit completion.
            self.assertFalse(any(s.get("action")=="schematic.wire.create" for s in load(root/"queue.json")["steps"]))

    def test_compose_generates_guarded_wiring_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);adapter=EasyEDA(os.environ["EASYEDA_BIN"],root/"receipts")
            target={"project_name":"ESO-TEST-OFFLINE","project_uuid":"synthetic-project","document_uuid":"synthetic-page"}
            ir={"schemaVersion":"1.4","projectId":target["project_uuid"],"documentId":target["document_uuid"],
                "components":[{"id":"fixture-resistor","ref":"R1","device":{"libraryUuid":"1"*32,"deviceUuid":"2"*32,"name":"SYNTHETIC ONLY"},"pins":[{"number":"1","name":"1"},{"number":"2","name":"2"}]}],
                "nets":[{"id":"fixture-a","name":"A"},{"id":"fixture-b","name":"B"}],
                "connections":[{"componentId":"fixture-resistor","pinNumber":"1","netId":"fixture-a","kind":"netlist"},{"componentId":"fixture-resistor","pinNumber":"2","netId":"fixture-b","kind":"netlist"}],
                "modules":[{"id":"fixture-module","name":"TEST ONLY","coreComponents":["fixture-resistor"]}]}
            sheet={"minX":0,"minY":0,"maxX":1170,"maxY":825}
            source={"schemaVersion":1,"connectivity":ir,"sheet":sheet,"keepouts":[],"modules":[{"id":"fixture-module","title":"TEST ONLY",
                "placements":[{"primitiveId":"synthetic-old-id","designator":"R1","value":"SYNTHETIC","x":100,"y":100,"rotation":0,"mirror":False,"bbox":{"minX":90,"minY":90,"maxX":110,"maxY":110},"pins":[{"number":"1","name":"1","net":"A","x":70,"y":100},{"number":"2","name":"2","net":"B","x":130,"y":100}]}],
                "wires":[],"flags":[],"terminals":[{"designator":"R1","pin":"1","direction":"left","kind":"net_port_bi"},{"designator":"R1","pin":"2","direction":"right","kind":"net_port_bi"}]}]}
            component={"componentType":"sheet","primitiveId":"fixture-sheet","bbox":sheet}
            # Current upstream requires all eleven explicit class inventories.
            page={k:[] for k in ("arcs","attributes","buses","circles","objects","pins","polygons","rectangles","texts","wires")};page["components"]=[component]
            before={"ok":True,"context":{"projectUuid":target["project_uuid"],"documentUuid":target["document_uuid"]},"result":{"components":[component],"count":1,"wires":[],"wiresAvailable":True,"pinNetsAvailable":True,"pagePrimitives":page,"connectivitySummary":{"scope":"activePage","wires":0,"buses":0,"netflags":0,"netports":0,"netlabels":0,"shortSymbols":0}}}
            dump(root/"composition.json",source);dump(root/"before.json",before)
            adapter.run(["sch","compose","--from",root/"composition.json","--before",root/"before.json","--out",root/"computed.json","--playbook",root/"queue.json","--replace"],json_output=False)
            q=load(root/"queue.json");validate_queue(q,target)
            self.assertTrue(any(s.get("action")=="schematic.power.connect_pin" for s in q["steps"]))
            self.assertTrue(any(s["id"]=="verify-physical-pins-before-wiring" for s in q["steps"]))
            self.assertEqual(adapter.run(["sch","apply",root/"queue.json","--dry-run"],json_output=False)["exit_code"],0)


if __name__=="__main__":unittest.main()
