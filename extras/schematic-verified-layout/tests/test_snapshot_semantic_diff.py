import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
try:
    import snapshot_semantic_diff as diff
except ModuleNotFoundError:
    diff = None


def snapshot():
    classes = {key: [] for key in ('components', 'wires', 'buses', 'arcs', 'circles', 'rectangles', 'polygons', 'texts', 'attributes', 'objects')}
    classes['components'] = [{'primitiveId': 'part-a', 'x': 10, 'y': 20}]
    classes['attributes'] = [{'primitiveId': 'attr-1', 'ParentPrimitiveId': 'part-a', 'Key': 'Name', 'Value': '={Manufacturer Part}', 'ValueVisible': None, 'X': 10, 'Y': 20}]
    return {'ok': True, 'context': {'projectUuid': 'project-a', 'documentUuid': 'page-a'}, 'result': {'pagePrimitives': classes}}


class SnapshotDiffTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(diff, 'attribute semantic diff is not implemented')
        self.before = snapshot()
        self.after = copy.deepcopy(self.before)

    def test_attribute_id_churn_is_reported_without_semantic_change(self):
        self.after['result']['pagePrimitives']['attributes'][0]['primitiveId'] = 'rebuilt-attribute'
        r = diff.compare(self.before, self.after)
        self.assertTrue(r['semanticEqual'])
        self.assertFalse(r['rawEqual'])
        self.assertTrue(r['attributeIdsChanged'])

    def test_coordinate_change_remains_visible_after_id_churn(self):
        a = self.after['result']['pagePrimitives']['attributes'][0]
        a.update(primitiveId='rebuilt', X=30)
        r = diff.compare(self.before, self.after)
        self.assertFalse(r['semanticEqual'])
        self.assertEqual(r['changedClasses'], ['attributes'])
        self.assertEqual(r['differences']['attributes']['added'][0]['X'], 30)

    def test_null_visibility_is_distinct_from_false(self):
        self.after['result']['pagePrimitives']['attributes'][0]['ValueVisible'] = False
        self.assertFalse(diff.compare(self.before, self.after)['semanticEqual'])

    def test_missing_visibility_is_distinct_from_null(self):
        del self.after['result']['pagePrimitives']['attributes'][0]['ValueVisible']
        self.assertFalse(diff.compare(self.before, self.after)['semanticEqual'])

    def test_formula_is_not_replaced_by_rendered_model(self):
        self.after['result']['pagePrimitives']['attributes'][0]['Value'] = 'REAL-PART'
        self.assertFalse(diff.compare(self.before, self.after)['semanticEqual'])

    def test_parent_rebinding_is_a_real_change(self):
        self.after['result']['pagePrimitives']['attributes'][0]['ParentPrimitiveId'] = 'part-b'
        self.assertFalse(diff.compare(self.before, self.after)['semanticEqual'])

    def test_duplicate_multiplicity_is_preserved(self):
        a = copy.deepcopy(self.after['result']['pagePrimitives']['attributes'][0])
        a['primitiveId'] = 'attr-2'
        self.after['result']['pagePrimitives']['attributes'].append(a)
        self.assertFalse(diff.compare(self.before, self.after)['semanticEqual'])

    def test_non_attribute_identity_is_not_ignored(self):
        self.after['result']['pagePrimitives']['components'][0]['primitiveId'] = 'replacement-part'
        self.assertEqual(diff.compare(self.before, self.after)['changedClasses'], ['components'])

    def test_unknown_attribute_fields_are_preserved(self):
        self.after['result']['pagePrimitives']['attributes'][0]['NewNativeField'] = 1
        self.assertFalse(diff.compare(self.before, self.after)['semanticEqual'])

    def test_numeric_type_changes_are_not_coerced(self):
        self.after['result']['pagePrimitives']['attributes'][0]['X'] = True
        self.assertFalse(diff.compare(self.before, self.after)['semanticEqual'])

    def test_target_drift_and_incomplete_inventory_are_refused(self):
        self.after['context']['documentUuid'] = 'other-page'
        with self.assertRaises(ValueError):
            diff.compare(self.before, self.after)
        self.after = copy.deepcopy(self.before)
        del self.after['result']['pagePrimitives']['wires']
        with self.assertRaises(ValueError):
            diff.compare(self.before, self.after)

    def test_changed_schema_is_not_silently_discarded(self):
        self.after['result']['pagePrimitives']['newNativeClass'] = []
        with self.assertRaises(ValueError):
            diff.compare(self.before, self.after)

    def test_array_order_is_irrelevant_but_source_is_untouched(self):
        a = copy.deepcopy(self.after['result']['pagePrimitives']['attributes'][0])
        a.update(primitiveId='second', Key='Designator', Value='J1')
        self.before['result']['pagePrimitives']['attributes'].append(a)
        self.after = copy.deepcopy(self.before)
        self.after['result']['pagePrimitives']['attributes'].reverse()
        frozen = copy.deepcopy(self.after)
        self.assertTrue(diff.compare(self.before, self.after)['semanticEqual'])
        self.assertEqual(self.after, frozen)

    def test_cli_records_difference_and_refuses_overwriting_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            self.after['result']['pagePrimitives']['attributes'][0]['X'] = 30
            for name, value in [('before', self.before), ('after', self.after)]:
                (work / (name + '.json')).write_text(json.dumps(value), encoding='utf-8')
            command = [sys.executable, diff.__file__, '--before', str(work/'before.json'), '--after', str(work/'after.json'), '--out', str(work/'diff.json')]
            p = subprocess.run(command, capture_output=True)
            self.assertEqual(p.returncode, 1)
            raw = (work/'diff.json').read_bytes()
            self.assertFalse(json.loads(raw)['semanticEqual'])
            self.assertEqual(subprocess.run(command, capture_output=True).returncode, 2)
            self.assertEqual((work/'diff.json').read_bytes(), raw)

    def test_cli_refuses_duplicate_json_keys_in_raw_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            raw = json.dumps(self.before).replace('"X": 10', '"X": 999, "X": 10')
            self.assertNotEqual(raw, json.dumps(self.before))
            (work/'before.json').write_text(raw, encoding='utf-8')
            (work/'after.json').write_text(json.dumps(self.after), encoding='utf-8')
            command = [sys.executable, diff.__file__, '--before', str(work/'before.json'), '--after', str(work/'after.json'), '--out', str(work/'diff.json')]
            result = subprocess.run(command, capture_output=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn(b'duplicate JSON key', result.stderr)
            self.assertFalse((work/'diff.json').exists())


if __name__ == '__main__':
    unittest.main()
