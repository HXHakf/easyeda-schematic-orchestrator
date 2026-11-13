"""Synthetic extracts only. No fixture is a real part/library or live EDA receipt."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from eso.core import Refused, digest, file_digest, confirm, check_confirmation
from eso.requirements import evaluate, contract_from, contract_summary
from eso.planner import make_plan, bind_plan
from eso.workflow import validate_plan, check_source


def fixture(root, topic='npn-low-side-switch'):
    """Deliberately invented models and UUIDs, only for offline tests."""
    mos = topic == 'nmos-low-side-switch'
    def material(mid, kind, pins, facts):
        path = root / (mid + '.txt')
        path.write_text('SYNTHETIC TEST DOCUMENT: ' + mid, encoding='utf-8')
        model = 'SYNTHETIC-' + mid
        return {'id': mid, 'model': model, 'package': 'TEST', 'kind': kind,
                'datasheet': {'path': str(path.resolve()), 'sha256': file_digest(path),
                              'model': model, 'package': 'TEST', 'revision': 'fixture-1',
                              'pinout_section': 'synthetic pin table', 'reviewed_by': 'test fixture'},
                'pins': pins, 'facts': facts,
                'fact_sections': {key: 'synthetic test limits' for key in facts},
                'library': {'libraryUuid': '1' * 32, 'deviceUuid': '2' * 32,
                            'verified_model': model, 'verified_package': 'TEST', 'expected_name': model,
                            'pins': pins.copy(), 'evidence': 'synthetic only'}}
    common = {'voltage_limit_v': 30, 'current_limit_a': .5, 'power_limit_w': .25,
              'rated_ambient_c': 85, 'off_input_max_v': .3, 'input_leakage_max_a': 1e-7}
    switch = dict(common, **({'rds_on_max_ohm': .08, 'rds_test_vgs_v': 2.5,
                              'gate_voltage_limit_v': 12} if mos else
                            {'vbe_min_v': .6, 'vbe_max_v': .9, 'vcesat_max_v': .3,
                             'sat_test_ic_a': .1, 'sat_test_ib_a': .01}))
    def resistor(ohm, power):
        return {'resistance_ohm': ohm, 'tolerance_fraction': .01,
                'power_limit_w': power, 'voltage_limit_v': 50, 'rated_ambient_c': 85}
    materials = [material('switch', 'nmos' if mos else 'npn',
                          {'G': '1', 'S': '2', 'D': '3'} if mos else {'B': '1', 'E': '2', 'C': '3'}, switch),
                 material('series', 'resistor', {'1': '1', '2': '2'}, resistor(470 if mos else 1000, .125)),
                 material('bias', 'resistor', {'1': '1', '2': '2'}, resistor(100000, .125)),
                 material('load', 'resistor', {'1': '1', '2': '2'}, resistor(1000, .25))]
    circuit = {'id': 'channel1', 'topic': topic, 'variant': 'resistive-dc',
               'ports': {'supply': 'V12', 'ground': 'GND', 'control': 'CTRL1'},
               'roles': {role: role for role in ('switch', 'series', 'bias', 'load')},
               'parameters': {'supply_voltage_v': 12, 'control_high_min_v': 3,
                              'control_high_max_v': 3.3, 'control_low_max_v': .1,
                              'gpio_source_limit_ma': 8, 'ambient_max_c': 50,
                              'frequency_hz': 0, 'default_state': 'off', 'gpio_leakage_max_ua': .1}}
    return {'schema_version': 1, 'circuit_type': 'generic', 'function_description': 'synthetic switch test',
            'knowledge': {'materials': materials, 'circuits': [circuit]}}


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def cli(self, *args):
        return subprocess.run([sys.executable, '-m', 'eso.cli', *args], capture_output=True, text=True)

    def test_alias_search_returns_one_canonical_topic(self):
        outputs = []
        for query in ('NPN 低边开关', '三极管低侧驱动', 'npn-low-side-switch'):
            result = self.cli('kb-search', query)
            self.assertEqual(result.returncode, 0, result.stderr)
            outputs.append([row['id'] for row in json.loads(result.stdout)['matches']])
        self.assertEqual(outputs, [['npn-low-side-switch']] * 3)

    def test_catalog_has_six_unique_topics_and_guidance_cards_do_not_auto_generate(self):
        result = self.cli('kb-check')
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['topic_count'], 6)
        r = fixture(self.root, 'gpio-led-indicator')
        self.assertFalse(evaluate(r)['ready'])

    def test_static_switches_generate_expected_physical_connectivity_and_bindings(self):
        from eso.knowledge_design import make_bindings
        for topic in ('npn-low-side-switch', 'nmos-low-side-switch'):
            with self.subTest(topic=topic):
                r = fixture(self.root, topic)
                report = evaluate(r)
                self.assertTrue(report['ready'], report)
                c = contract_from(r)
                p = make_plan(c)
                validate_plan(p, c)
                self.assertEqual(len(p['components']), 4)
                switch = p['components'][0]
                self.assertEqual({v['number']: v['net'] for v in switch['pins']},
                                 {'1': 'channel1_DRIVE', '2': 'GND', '3': 'channel1_LOAD'})
                self.assertEqual(p['components'][-1]['pins'][0]['net'], 'V12')
                bindings = make_bindings(c)
                target = {'project_uuid': 'synthetic', 'document_uuid': 'synthetic'}
                check_source(p, {'connectivity': bind_plan(p, bindings, target)}, target)
                self.assertIn('知识', contract_summary(c))
                self.assertNotIn(str(self.root), json.dumps(p))

    def test_missing_material_identity_and_pin_evidence_are_aggregated(self):
        r = fixture(self.root)
        r['knowledge']['materials'][0]['datasheet']['path'] = str(self.root / 'absent')
        r['knowledge']['materials'][1]['datasheet']['model'] = 'WRONG'
        r['knowledge']['materials'][2]['library']['verified_package'] = 'WRONG'
        r['knowledge']['materials'][3]['datasheet']['pinout_section'] = ''
        report = evaluate(r)
        self.assertFalse(report['ready'])
        issues = str(report['questions']) + str(report['errors'])
        for word in ('path', 'model', 'package', 'pinout_section'):
            self.assertIn(word, issues)

    def test_npn_drive_and_gpio_overcurrent_rejected(self):
        for resistance in (100000, 10):
            r = fixture(self.root)
            r['knowledge']['materials'][1]['facts']['resistance_ohm'] = resistance
            self.assertFalse(evaluate(r)['ready'])

    def test_mos_requires_guaranteed_rds_at_actual_gate_voltage(self):
        r = fixture(self.root, 'nmos-low-side-switch')
        facts = r['knowledge']['materials'][0]['facts']
        facts.pop('rds_on_max_ohm')
        facts['vgs_threshold_v'] = 1
        self.assertFalse(evaluate(r)['ready'])
        r = fixture(self.root, 'nmos-low-side-switch')
        r['knowledge']['materials'][0]['facts']['rds_test_vgs_v'] = 4.5
        self.assertFalse(evaluate(r)['ready'])

    def test_parameters_power_and_unsupported_variants_block(self):
        for change in ({'frequency_hz': 1000}, {'ambient_max_c': 100},
                       {'control_high_max_v': 1}, {'supply_voltage_v': float('nan')}):
            r = fixture(self.root)
            r['knowledge']['circuits'][0]['parameters'].update(change)
            self.assertFalse(evaluate(r)['ready'])
        r = fixture(self.root)
        r['knowledge']['materials'][3]['facts']['power_limit_w'] = .01
        self.assertFalse(evaluate(r)['ready'])
        r = fixture(self.root)
        r['knowledge']['circuits'][0]['variant'] = 'inductive'
        self.assertFalse(evaluate(r)['ready'])

    def test_two_channels_keep_independent_components_and_merge_only_ports(self):
        r = fixture(self.root)
        second = copy.deepcopy(r['knowledge']['circuits'][0])
        second.update(id='channel2')
        second['ports']['control'] = 'CTRL2'
        r['knowledge']['circuits'].append(second)
        p = make_plan(contract_from(r))
        self.assertEqual(len(p['components']), 8)
        self.assertEqual(len([n for n in p['nets'] if n['name'] == 'V12']), 1)
        self.assertEqual(len([n for n in p['nets'] if n['name'] == 'GND']), 1)
        self.assertIn('channel2_DRIVE', {n['name'] for n in p['nets']})
        second['parameters']['supply_voltage_v'] = 5
        self.assertFalse(evaluate(r)['ready'])

    def test_shared_control_budget_and_net_collisions_block(self):
        r = fixture(self.root)
        second = copy.deepcopy(r['knowledge']['circuits'][0]); second['id'] = 'channel2'
        r['knowledge']['circuits'].append(second)
        for c in r['knowledge']['circuits']: c['parameters']['gpio_source_limit_ma'] = 4
        self.assertFalse(evaluate(r)['ready'])
        r = fixture(self.root)
        r['knowledge']['circuits'][0]['ports']['control'] = 'GND'
        self.assertFalse(evaluate(r)['ready'])

    def test_changed_datasheet_invalidates_confirmation_and_plan(self):
        r = fixture(self.root)
        c = contract_from(r); p = make_plan(c)
        a = confirm(c, digest(c), 'synthetic human')
        Path(r['knowledge']['materials'][0]['datasheet']['path']).write_text('changed')
        with self.assertRaises(Refused): check_confirmation(c, a)
        with self.assertRaises(Refused): validate_plan(p, c)

    def test_changed_parameters_pinmap_or_missing_provenance_rejected(self):
        c = contract_from(fixture(self.root)); p = make_plan(c)
        a = confirm(c, digest(c), 'synthetic human')
        changed = copy.deepcopy(c)
        changed['requirements']['knowledge']['circuits'][0]['parameters']['supply_voltage_v'] = 11
        with self.assertRaises(Refused): check_confirmation(changed, a)
        for key in ('knowledge_evidence',):
            corrupt = copy.deepcopy(p); corrupt.pop(key)
            with self.assertRaises(Refused): validate_plan(corrupt, c)
        p['components'][0]['pins'][0]['net'] = 'GND'
        with self.assertRaises(Refused): validate_plan(p, c)

    def test_new_model_and_values_share_card(self):
        r = fixture(self.root)
        m = r['knowledge']['materials'][0]
        m['model'] = m['datasheet']['model'] = m['library']['verified_model'] = 'ANOTHER-SYNTHETIC'
        r['knowledge']['materials'][1]['facts']['resistance_ohm'] = 1200
        p = make_plan(contract_from(r))
        self.assertEqual(p['components'][0]['query'], 'ANOTHER-SYNTHETIC')
        self.assertEqual(p['knowledge_evidence']['cards'][0]['id'], 'npn-low-side-switch')

    def test_library_binding_changes_cannot_bypass_review(self):
        from eso.knowledge_design import make_bindings
        c = contract_from(fixture(self.root)); p = make_plan(c); b = make_bindings(c)
        b['Q1']['deviceUuid'] = '3' * 32
        with self.assertRaises(Refused): bind_plan(p, b, {'project_uuid':'t','document_uuid':'t'})
        b = make_bindings(c)
        target = {'project_uuid':'t','document_uuid':'t'}
        ir = bind_plan(p, b, target)
        ir['components'][0]['device']['deviceUuid'] = '3' * 32
        with self.assertRaises(Refused): check_source(p, {'connectivity':ir}, target)

    def test_missing_library_name_and_floating_input_leakage_block(self):
        r = fixture(self.root)
        r['knowledge']['materials'][0]['library'].pop('expected_name', None)
        self.assertFalse(evaluate(r)['ready'])
        r = fixture(self.root, 'nmos-low-side-switch')
        r['knowledge']['circuits'][0]['parameters']['gpio_leakage_max_ua'] = 100
        self.assertFalse(evaluate(r)['ready'])

    def test_catalog_edit_invalidates_contract(self):
        from unittest.mock import patch
        from eso.knowledge import CATALOG
        c = contract_from(fixture(self.root))
        a = confirm(c,digest(c),'synthetic human')
        path=self.root/'catalog.json'
        data=json.loads(CATALOG.read_text()); data['topics'][0]['version']='changed'
        path.write_text(json.dumps(data))
        with patch('eso.knowledge.CATALOG',path), self.assertRaises(Refused): check_confirmation(c,a)

    def test_unknown_input_fields_and_malformed_topic_are_reported(self):
        for change in ({'topic':[]}, {'parameters':{'load_type':'inductive'}}, {'unreviewed_extra':True}):
            r=fixture(self.root); r['knowledge']['circuits'][0].update(change)
            self.assertFalse(evaluate(r)['ready'])

    def test_same_rail_with_different_ground_references_is_not_merged(self):
        r=fixture(self.root)
        second=copy.deepcopy(r['knowledge']['circuits'][0]); second['id']='channel2'
        second['ports'].update(ground='ISOLATED_GND',control='CTRL2')
        r['knowledge']['circuits'].append(second)
        self.assertFalse(evaluate(r)['ready'])

    def test_knowledge_cannot_silently_replace_mcu_design(self):
        r = fixture(self.root)
        r.update(circuit_type='mcu-minimum',mcu='STM32F103C8T6',input_voltage_v=3.3,power_source='external-regulated')
        self.assertFalse(evaluate(r)['ready'])

    def test_benchmark_compares_only_matching_cases_and_validation_scope(self):
        source=self.root/'metrics.json'
        rows=[{'case_id':'same-case','workflow':'legacy','scope':'offline','elapsed_seconds':100,
               'question_rounds':4,'connection_errors':2},
              {'case_id':'same-case','workflow':'knowledge','scope':'offline','elapsed_seconds':60,
               'question_rounds':1,'connection_errors':0},
              {'case_id':'different-case','workflow':'knowledge','scope':'live','elapsed_seconds':10,
               'question_rounds':0,'connection_errors':None}]
        source.write_text(json.dumps(rows))
        proc=self.cli('kb-metrics',str(source))
        self.assertEqual(proc.returncode,0,proc.stderr)
        result=json.loads(proc.stdout)
        self.assertEqual(len(result['comparisons']),1)
        self.assertEqual(result['comparisons'][0]['delta']['elapsed_seconds'],-40)
        self.assertEqual(result['comparisons'][0]['delta']['connection_errors'],-2)
        self.assertEqual(len(result['unpaired']),1)
        rows[0]['elapsed_seconds']=-1; source.write_text(json.dumps(rows))
        self.assertEqual(self.cli('kb-metrics',str(source)).returncode,2)

    def test_offline_demo_exports_both_drafts_without_approval(self):
        out=self.root/'demo'
        proc=subprocess.run([sys.executable,'scripts/demo_knowledge.py','--out',str(out)],capture_output=True,text=True)
        self.assertEqual(proc.returncode,0,proc.stderr)
        for name in ('npn','nmos'):
            self.assertTrue((out/name/'plan.json').is_file())
            self.assertTrue((out/name/'bindings.json').is_file())
            self.assertFalse((out/name/'approval.json').exists())
        status=json.loads((out/'status.json').read_text())
        self.assertEqual(status['EDA_calls'],0)
        self.assertEqual(status['state'],'awaiting-human-confirmation')
        self.assertIsNone(status['connection_errors'])

    def test_catalog_deduplication_covers_assets_aliases_and_url_fragments(self):
        from eso.knowledge import catalog, check_catalog
        for mutation in ('alias','asset','url','dangling-asset'):
            data=catalog()
            if mutation=='alias': data['topics'][1]['aliases'].append(data['topics'][0]['aliases'][0])
            if mutation=='asset': data['upstream_candidates'].append(copy.deepcopy(data['upstream_candidates'][0]))
            if mutation=='url':
                row=copy.deepcopy(data['sources'][0]); row['id']='different'; row['url']+='#section'
                data['sources'].append(row)
            if mutation=='dangling-asset': data['topics'][0]['asset_ids'].append('absent')
            with self.subTest(mutation=mutation), self.assertRaises(Refused): check_catalog(data)

    def test_selected_packages_do_not_get_an_unrelated_default(self):
        self.assertNotIn('passive_package',evaluate(fixture(self.root))['values'])

    def test_active_low_includes_switch_leakage(self):
        r=fixture(self.root,'nmos-low-side-switch')
        r['knowledge']['materials'][1]['facts']['resistance_ohm']=10000
        r['knowledge']['materials'][0]['facts']['input_leakage_max_a']=2e-6
        r['knowledge']['circuits'][0]['parameters']['control_low_max_v']=.32
        self.assertFalse(evaluate(r)['ready'])

    def test_shared_floating_control_checks_coupled_leakage(self):
        r=fixture(self.root,'nmos-low-side-switch')
        second=copy.deepcopy(r['knowledge']['circuits'][0]);second['id']='channel2'
        switch2=copy.deepcopy(r['knowledge']['materials'][0]);switch2['id']='switch2'
        switch2['facts'].update(input_leakage_max_a=1e-7,off_input_max_v=.1)
        r['knowledge']['materials'][0]['facts']['input_leakage_max_a']=2.8e-6
        second['roles']['switch']='switch2'
        r['knowledge']['materials'].append(switch2);r['knowledge']['circuits'].append(second)
        for c in r['knowledge']['circuits']: c['parameters']['gpio_source_limit_ma']=16
        self.assertFalse(evaluate(r)['ready'])

    def test_extreme_arithmetic_and_search_parameter_type(self):
        for topic in ('npn-low-side-switch','nmos-low-side-switch'):
            r=fixture(self.root,topic)
            r['knowledge']['materials'][3]['facts']['resistance_ohm']=1e-308
            report=evaluate(r)
            self.assertFalse(report['ready'])
            json.dumps(report,allow_nan=False)
        path=self.root/'bad.json';path.write_text('1')
        proc=self.cli('kb-search','NPN','--parameters',str(path))
        self.assertEqual(proc.returncode,2)
        self.assertNotIn('Traceback',proc.stderr)
