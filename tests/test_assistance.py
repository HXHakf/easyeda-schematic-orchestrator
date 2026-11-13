import json
import tempfile
import unittest
from pathlib import Path
from eso.core import Refused, dump, file_digest
from eso.assistance import catalog_review, text_restore_preview, compare_layouts


class AssistanceTests(unittest.TestCase):
    def test_catalog_keeps_same_supplier_part_candidates_and_exposes_missing_fields(self):
        devices = [{'uuid': 'a', 'libraryUuid': 'lib', 'name': 'generic',
                    'property': {'supplier': 'LCSC', 'supplierId': 'C1'}, 'association': {}},
                   {'uuid': 'b', 'libraryUuid': 'lib', 'name': 'full',
                    'property': {'supplier': 'LCSC', 'supplierId': 'C1', 'manufacturer': 'factory', 'manufacturerId': 'part'},
                    'association': {'symbolUuid': 'sym', 'footprintUuid': 'fp'}}]
        report = catalog_review({'R1': {'deviceUuid': 'b', 'libraryUuid': 'lib', 'expected_name': 'full'}}, devices)
        self.assertTrue(report['components']['R1']['identity_matches'])
        self.assertEqual(len(report['components']['R1']['same_supplier_part']), 2)
        self.assertTrue(report['devices'][0]['missing'])
        self.assertEqual(report['pin_review'], 'host-required')

    def test_text_preview_uses_current_attribute_id_and_measured_relative_offset(self):
        def snapshot(x, attr, y):
            return {'ok': True, 'context': {'projectUuid': 'p', 'documentUuid': 'd'}, 'result': {
                'components': [{'componentType': 'part', 'primitiveId': 'part', 'designator': 'R1',
                                'device': {'uuid': 'lib'}, 'x': x, 'y': 0, 'rotation': 0, 'mirror': False}],
                'pagePrimitives': {'attributes': [{'primitiveId': attr, 'ParentPrimitiveId': 'part',
                    'Key': 'Name', 'X': x, 'Y': y, 'Value': '={value}', 'ValueVisible': True}]}}}
        old, current = snapshot(10, 'old-id', 20), snapshot(30, 'new-id', 0)
        report = text_restore_preview(old, current)
        self.assertEqual(report['moves'][0]['primitiveId'], 'new-id')
        self.assertEqual(report['moves'][0]['y'], 20)
        self.assertEqual(report['moves'][0]['expected']['Value'], '={value}')
        current['result']['components'][0]['rotation'] = 90
        with self.assertRaises(Refused):
            text_restore_preview(old, current)

    def test_candidate_selection_rejects_failed_gate_and_is_deterministic(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dump(root / 'target.json', {'fixture': True})
            dump(root / 'scene.json', {'wires': [], 'moduleFrames': []})
            for name, passed in [('good', True), ('bad', False)]:
                dump(root / (name + '.json'), {'passed': passed, 'strict': True, 'inputSha256': {'target': file_digest(root/'target.json'), 'scene': file_digest(root/'scene.json')}})
            for name in ['good', 'bad']:
                report = json.loads((root/(name+'.json')).read_text())
                report.update(scope='declared-topology-pin-exit-and-obstacle-geometry', geometryScope='full')
                dump(root/(name+'-geometry.json'), report)
                report.update(scope='offline-readability')
                dump(root/(name+'.json'), report)
            rows = [{'id': name, 'target': str(root/'target.json'), 'scene': str(root/'scene.json'), 'geometry_report': str(root / (name + '-geometry.json')),
                     'readability_report': str(root / (name + '.json')),
                     'bends': 2, 'wire_length': 10, 'area': 20} for name in ['bad', 'good']]
            report = compare_layouts(rows, 2)
            self.assertEqual(report['selected'], 'good')
            self.assertFalse(report['execution_ready'])
            with self.assertRaises(Refused):
                compare_layouts(rows, 1)

    def test_readability_cannot_replace_full_geometry_report(self):
        from eso.core import file_digest
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dump(root/'target.json', {'components': []})
            dump(root/'scene.json', {'wires': []})
            dump(root/'readability.json', {'scope':'offline-readability','strict':True,'passed':True,
                 'inputSha256':{'target':file_digest(root/'target.json'),'scene':file_digest(root/'scene.json')}})
            candidate = {'id':'invalid', 'target':str(root/'target.json'),'scene':str(root/'scene.json'),
                         'geometry_report':str(root/'readability.json'),'readability_report':str(root/'readability.json')}
            with self.assertRaises(Refused):
                compare_layouts([candidate], 1)

    def test_text_restore_apply_rechecks_preview_and_runs_full_verify(self):
        import copy
        import test_workflow as fixtures
        from eso.assistance import apply_text_restore
        from eso.core import digest, load
        class Adapter(fixtures.FakeAdapter):
            current = None
            def snapshot(self, target):
                return self.current if self.current else fixtures.blank()
            def run(self, args, **kwargs):
                if args[:2] == ['sch','attribute-move']:
                    move = load(args[-1])
                    at = self.current['result']['pagePrimitives']['attributes'][0]
                    self.asserted_expected = move['expected'] == at
                    at.update(X=move['x'],Y=move['y'])
                    return {'ok':True,'result':{}}
                return super().run(args, **kwargs)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            a = Adapter()
            w, ir = fixtures.OrchestrationTests().prepare(directory,a)
            a.actual = ir
            w.execute()
            parts = [{'componentType':'part','primitiveId':c['id'],'designator':c['ref'],
                      'device':c['device'],'x':10,'y':0,'rotation':0,'mirror':False} for c in ir['components']]
            at = {'primitiveId':'current-text','ParentPrimitiveId':parts[0]['primitiveId'],
                  'Key':'Name','Value':'unchanged','ValueVisible':True,'X':10,'Y':0}
            a.current = {'ok':True,'context':fixtures.blank()['context'],'result':{
                'components':parts,'wires':[],'pagePrimitives':{'attributes':[at]}}}
            reference = copy.deepcopy(a.current)
            reference['result']['pagePrimitives']['attributes'][0]['Y']=20
            preview = text_restore_preview(reference,a.current)
            preview.update(reference=reference,contract_sha256=digest(load(w.root/'contract.json')))
            dump(root/'preview.json',preview)
            report = apply_text_restore(a,w.root,root/'preview.json',root/'restore')
            self.assertTrue(a.asserted_expected)
            self.assertEqual(report['state'],'verified')
            self.assertEqual(at['Y'],20)
            with self.assertRaises(Refused):
                apply_text_restore(a,w.root,root/'preview.json',root/'stale')
