import copy
import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
try:
    import circuit_audit as audit
except ModuleNotFoundError:
    audit = None


def fixture():
    target = {
        'schemaVersion': 1,
        'components': [
            {'id': 'a', 'ref': 'A1', 'pins': ['1']},
            {'id': 'b', 'ref': 'B1', 'pins': ['1']},
            {'id': 'c', 'ref': 'C1', 'pins': ['1']},
            {'id': 'd', 'ref': 'D1', 'pins': ['1']},
        ],
        'nets': [
            {'id': 'n1', 'name': 'N1', 'scope': 'global', 'pins': [['a', '1'], ['b', '1']]},
            {'id': 'n2', 'name': 'N2', 'scope': 'global', 'pins': [['c', '1'], ['d', '1']]},
        ],
        'nc': [],
        'modules': [{'id': 'm', 'members': ['a', 'b', 'c', 'd'], 'core': ['a'],
                     'direct': [[['a', '1'], ['b', '1']]]}],
    }
    scene = {
        'schemaVersion': 1, 'units': 'raw', 'grid': 5,
        'evidence': 'synthetic-fixture',
        'geometryScope': 'topology-only',
        'pins': [
            {'pin': ['a', '1'], 'page': 'p', 'at': [0, 10], 'direction': 'right'},
            {'pin': ['b', '1'], 'page': 'p', 'at': [40, 10], 'direction': 'left'},
            {'pin': ['c', '1'], 'page': 'p', 'at': [20, -10], 'direction': 'up'},
            {'pin': ['d', '1'], 'page': 'p', 'at': [20, 30], 'direction': 'down'},
        ],
        'wires': [
            {'id': 'w1', 'page': 'p', 'points': [[0, 10], [40, 10]]},
            {'id': 'w2', 'page': 'p', 'points': [[20, -10], [20, 30]]},
        ],
        'labels': [], 'junctions': [], 'bodies': [],
        'textInventory': ['label'],
        'texts': [{'id': 'label', 'page': 'p', 'bbox': [0, 40, 15, 50]}],
    }
    return target, scene


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(audit, 'circuit_audit must implement independent checks')
        self.target, self.scene = fixture()

    def codes(self, result):
        return {x['rule'] for x in result['findings']}

    def test_clean_x_crossing_does_not_short(self):
        self.assertTrue(audit.audit_scene(self.target, self.scene)['passed'])

    def test_explicit_junction_on_x_is_a_short(self):
        self.scene['junctions'] = [{'page': 'p', 'at': [20, 10]}]
        self.assertIn('short', self.codes(audit.audit_scene(self.target, self.scene)))

    def test_t_contact_is_a_short(self):
        self.scene['wires'][1]['points'] = [[20, 10], [20, 30]]
        self.scene['pins'][2]['at'] = [20, 10]
        self.assertIn('short', self.codes(audit.audit_scene(self.target, self.scene)))

    def test_redundant_collinear_waypoint_is_not_a_junction(self):
        self.scene['wires'][0]['points'] = [[0, 10], [20, 10], [40, 10]]
        self.assertTrue(audit.audit_scene(self.target, self.scene)['passed'])

    def test_deleted_wire_is_open(self):
        self.scene['wires'].pop(0)
        self.assertIn('open-net', self.codes(audit.audit_scene(self.target, self.scene)))

    def test_same_name_labels_cannot_replace_required_direct_wire(self):
        self.scene['wires'][0]['points'] = [[0, 10], [10, 10]]
        self.scene['wires'].append({'id': 'w3', 'page': 'p', 'points': [[30, 10], [40, 10]]})
        self.scene['labels'] = [
            {'id': 'l1', 'page': 'p', 'at': [10, 10], 'net': 'n1'},
            {'id': 'l2', 'page': 'p', 'at': [30, 10], 'net': 'n1'},
        ]
        result = audit.audit_scene(self.target, self.scene)
        self.assertNotIn('open-net', self.codes(result))
        self.assertIn('direct-required', self.codes(result))

    def test_page_scoped_labels_do_not_join_across_pages(self):
        self.target['nets'][0]['scope'] = 'page'
        self.scene['wires'][0]['points'] = [[0, 10], [10, 10]]
        self.scene['wires'].append({'id': 'w3', 'page': 'q', 'points': [[30, 10], [40, 10]]})
        self.scene['pins'][1]['page'] = 'q'
        self.scene['labels'] = [
            {'id': 'l1', 'page': 'p', 'at': [10, 10], 'net': 'n1'},
            {'id': 'l2', 'page': 'q', 'at': [30, 10], 'net': 'n1'},
        ]
        self.assertIn('open-net', self.codes(audit.audit_scene(self.target, self.scene)))

    def test_wire_touching_text_boundary_fails(self):
        self.scene['texts'][0]['bbox'] = [5, 10, 15, 20]
        self.assertIn('wire-text-overlap', self.codes(audit.audit_scene(self.target, self.scene)))

    def test_different_net_ids_with_same_page_name_are_rejected(self):
        for net in self.target['nets']:
            net['scope'] = 'page'
            net['name'] = 'SAME'
        self.assertIn('net-name-alias', self.codes(audit.audit_scene(self.target, self.scene)))

    def test_body_wire_exemption_cannot_hide_crossing(self):
        self.scene['bodies'] = [{'id':'bad','page':'p','bbox':[5,5,15,15],'allowWireIds':['w1']}]
        result = audit.audit_scene(self.target, self.scene)
        self.assertIn('invalid-wire-exemption', self.codes(result))
        self.assertIn('wire-body-overlap', self.codes(result))

    def test_net_label_can_overlap_its_verified_carrier_wire(self):
        self.scene['labels'] = [{'id':'mark','page':'p','at':[10,10],'net':'n1'}]
        self.scene['texts'][0].update(bbox=[5,8,15,12], role='net-label', markerId='mark', allowWireIds=['w1'])
        self.assertTrue(audit.audit_scene(self.target, self.scene)['passed'])

    def test_net_label_cannot_exempt_an_unrelated_wire(self):
        self.scene['labels'] = [{'id':'mark','page':'p','at':[10,10],'net':'n1'}]
        self.scene['texts'][0].update(bbox=[5,8,15,12], role='net-label', markerId='mark', allowWireIds=['w2'])
        self.assertIn('invalid-wire-exemption', self.codes(audit.audit_scene(self.target, self.scene)))

    def test_full_measured_geometry_requires_body_and_text_inventory(self):
        self.scene['geometryScope'] = 'full'
        self.scene['evidence'] = 'measured'
        self.scene['texts'] = []
        self.scene['textInventory'] = []
        result = audit.audit_scene(self.target, self.scene)
        self.assertIn('body-coverage', self.codes(result))
        self.assertIn('text-coverage', self.codes(result))

    def test_topology_only_cannot_be_marked_measured(self):
        self.scene['evidence'] = 'measured'
        self.assertFalse(audit.audit_scene(self.target, self.scene)['passed'])

    def test_missing_visible_text_measurement_fails(self):
        self.scene['texts'] = []
        self.assertIn('text-coverage', self.codes(audit.audit_scene(self.target, self.scene)))

    def test_unknown_pin_is_not_automatically_nc(self):
        self.target['components'][0]['pins'].append('2')
        self.assertIn('pin-state', self.codes(audit.validate_target(self.target)))

    def test_unwired_single_pin_net_is_floating(self):
        self.target['nets'][0]['pins'] = [['a', '1']]
        self.target['nets'].append({'id':'n3', 'name':'N3', 'scope':'global', 'pins':[['b','1']]})
        self.target['modules'][0]['direct'] = []
        self.scene['wires'].pop(0)
        self.assertIn('floating-pin', self.codes(audit.audit_scene(self.target, self.scene)))

    def test_extra_unterminated_branch_is_detected(self):
        self.scene['wires'].append({'id':'stray','page':'p','points':[[10,10],[10,25]]})
        self.assertIn('dangling-wire', self.codes(audit.audit_scene(self.target, self.scene)))

    def test_isolated_closed_wire_loop_is_detected(self):
        self.scene['wires'].append({'id':'loop','page':'p','points':[[100,100],[120,100],[120,120],[100,120],[100,100]]})
        self.assertIn('orphan-wire-island', self.codes(audit.audit_scene(self.target, self.scene)))

    def test_duplicate_module_membership_fails(self):
        self.target['modules'].append({'id': 'extra', 'members': ['a'], 'core': ['a'], 'direct': []})
        self.assertIn('ownership', self.codes(audit.validate_target(self.target)))

    def test_wrong_pin_exit_fails(self):
        self.scene['pins'][0]['direction'] = 'left'
        self.assertIn('pin-exit', self.codes(audit.audit_scene(self.target, self.scene)))

    def test_global_translation_preserves_checks(self):
        for pin in self.scene['pins']:
            pin['at'] = [v + 100 for v in pin['at']]
        for wire in self.scene['wires']:
            wire['points'] = [[v + 100 for v in p] for p in wire['points']]
        for text in self.scene['texts']:
            text['bbox'] = [v + 100 for v in text['bbox']]
        self.assertTrue(audit.audit_scene(self.target, self.scene)['passed'])

    def test_readback_requires_true_nc_boolean_and_exact_pin_inventory(self):
        components = []
        for c in self.target['components']:
            net = 'N1' if c['id'] in ('a', 'b') else 'N2'
            components.append({'componentType': 'part', 'designator': c['ref'],
                               'pinsAvailable': True, 'netlistAvailable': True,
                               'pins': [{'pinNumber': '1', 'net': net, 'noConnected': False}]})
        snap = {'ok': True, 'result': {'components': components}}
        self.assertTrue(audit.compare_readback(self.target, snap)['passed'])
        alias_target = copy.deepcopy(self.target)
        for net in alias_target['nets']:
            net.update(scope='page', name='N1')
        self.assertIn('net-name-alias', self.codes(audit.compare_readback(alias_target, snap)))
        snap['result']['components'][0]['pins'][0]['net'] = 'N2'
        self.assertIn('pin-net', self.codes(audit.compare_readback(self.target, snap)))
        snap['result']['components'][0]['pins'][0]['noConnected'] = 'false'
        self.assertIn('pin-state-unknown', self.codes(audit.compare_readback(self.target, snap)))


if __name__ == '__main__':
    unittest.main()
