"""Optional real upstream CLI integration. Only offline commands; never project writes."""
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


@unittest.skipUnless(os.environ.get("EASYEDA_BIN"), "set EASYEDA_BIN to test actual upstream offline compiler")
class UpstreamCLITests(unittest.TestCase):
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
            # Release v1.16.0 expects ten classes. Newer main also includes pins;
            # never drop that class from a real snapshot just to pass the old CLI.
            page={k:[] for k in ("arcs","attributes","buses","circles","objects","polygons","rectangles","texts","wires")};page["components"]=[component]
            before={"ok":True,"context":{"projectUuid":target["project_uuid"],"documentUuid":target["document_uuid"]},"result":{"components":[component],"count":1,"wires":[],"wiresAvailable":True,"pinNetsAvailable":True,"pagePrimitives":page,"connectivitySummary":{"scope":"activePage","wires":0,"buses":0,"netflags":0,"netports":0,"netlabels":0,"shortSymbols":0}}}
            dump(root/"composition.json",source);dump(root/"before.json",before)
            adapter.run(["sch","compose","--from",root/"composition.json","--before",root/"before.json","--out",root/"computed.json","--playbook",root/"queue.json","--replace"],json_output=False)
            q=load(root/"queue.json");validate_queue(q,target)
            self.assertTrue(any(s.get("action")=="schematic.power.connect_pin" for s in q["steps"]))
            self.assertTrue(any(s["id"]=="verify-physical-pins-before-wiring" for s in q["steps"]))
            self.assertEqual(adapter.run(["sch","apply",root/"queue.json","--dry-run"],json_output=False)["exit_code"],0)
            if "v1.16.0" in adapter.run(["--version"],json_output=False)["stdout"]:
                before["result"]["pagePrimitives"]["pins"]=[]
                dump(root/"eleven-classes.json",before)
                with self.assertRaisesRegex(Refused,"incomplete class inventory"):
                    adapter.run(["sch","compose","--from",root/"composition.json","--before",root/"eleven-classes.json",
                                 "--out",root/"unsupported-plan.json","--playbook",root/"unsupported-queue.json","--replace"],json_output=False)


if __name__=="__main__":unittest.main()
