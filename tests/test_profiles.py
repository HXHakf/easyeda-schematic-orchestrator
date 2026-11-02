import unittest
from eso.core import Refused, check_confirmation, confirm, digest
from eso.requirements import contract_from, contract_summary, evaluate, merge_answers, parse_seed
from eso.planner import make_plan


class ProfileTests(unittest.TestCase):
    def test_ldo_does_not_ask_for_mcu_sensor_or_i2c(self):
        r = evaluate({'schema_version': 1, 'circuit_type': 'ldo-power', 'input_voltage_v': 5,
                      'output_voltage_v': 3.3, 'power_source': 'usb'})
        fields = {q['field'] for q in r['questions']}
        self.assertEqual(fields, {'output_current_ma', 'regulator_model'})
        self.assertNotIn('bus', r['values']); self.assertNotIn('clock', r['values'])

    def test_minimum_system_has_no_sensor_question(self):
        r = evaluate({'schema_version': 1, 'circuit_type': 'mcu-minimum', 'mcu': 'STM32F103C8T6',
                      'input_voltage_v': 3.3, 'power_source': 'external-regulated'})
        self.assertTrue(r['ready']); self.assertNotIn('sensor', r['values'])
        plan = make_plan(contract_from(r['values']))
        self.assertNotIn('U2', {c['ref'] for c in plan['components']})
        self.assertFalse(any(p['net'] == 'I2C_SCL' for c in plan['components'] for p in c['pins']))

    def test_next_questions_are_bounded_without_discarding_blockers(self):
        r = evaluate({'schema_version': 1, 'circuit_type': 'mcu-sensor'})
        self.assertGreater(len(r['questions']), 3)
        self.assertEqual(len(r['next_questions']), 3)
        self.assertEqual([q['field'] for q in r['next_questions']][:2], ['input_voltage_v', 'power_source'])

    def test_help_me_choose_becomes_agent_task(self):
        r = {'schema_version': 1, 'circuit_type': 'ldo-power', 'input_voltage_v': 5,
             'output_voltage_v': 3.3, 'output_current_ma': 100, 'power_source': 'usb'}
        r = merge_answers(r, {'regulator_model': '帮我选'})
        report = evaluate(r)
        self.assertNotIn('regulator_model', {q['field'] for q in report['questions']})
        self.assertEqual(report['selection_tasks'][0]['field'], 'regulator_model')
        self.assertFalse(report['ready'])

    def test_explicit_reaffirmed_default_is_preserved(self):
        r = evaluate({'schema_version': 1, 'mcu': 'STM32F103C8T6', 'sensor': 'BMP280',
                      'sensor_form': 'bare-chip', 'input_voltage_v': 3.3, 'power_source': 'external-regulated'})['values']
        r = merge_answers(r, {'i2c_pullups': 'new', 'pullup_ohm': 4700, 'sensor_form': 'module'})
        self.assertTrue(evaluate(r)['ready'])

    def test_human_summary_keeps_internal_hash_out_of_prose(self):
        contract = contract_from({'schema_version': 1, 'circuit_type': 'ldo-power', 'input_voltage_v': 5,
                                 'output_voltage_v': 3.3, 'output_current_ma': 100,
                                 'power_source': 'usb', 'regulator_model': 'SYNTHETIC TEST ONLY'})
        prose = contract_summary(contract)
        self.assertIn('最大输出电流', prose); self.assertIn('离线草稿', prose)
        self.assertNotIn(digest(contract), prose)

    def test_profile_change_invalidates_old_confirmation(self):
        r = {'schema_version': 1, 'mcu': 'STM32F103C8T6', 'input_voltage_v': 3.3, 'power_source': 'external-regulated'}
        c = contract_from(r); a = confirm(c, digest(c), 'SYNTHETIC HUMAN')
        c['requirements']['circuit_type'] = 'mcu-sensor'
        with self.assertRaises(Refused): check_confirmation(c, a)

    def test_ldo_voltage_order_is_checked(self):
        r = evaluate({'schema_version': 1, 'circuit_type': 'ldo-power', 'input_voltage_v': 3.3,
                      'output_voltage_v': 5, 'output_current_ma': 100, 'power_source': 'usb',
                      'regulator_model': 'SYNTHETIC TEST ONLY'})
        self.assertFalse(r['ready']); self.assertTrue(r['errors'])

    def test_profile_switch_clears_previous_specific_answers(self):
        r = {'schema_version': 1, 'circuit_type': 'mcu-sensor', 'mcu': 'STM32F103C8T6',
             'sensor': 'BMP280', 'sensor_form': 'bare-chip', 'input_voltage_v': 3.3,
             'power_source': 'external-regulated', 'bus': 'i2c'}
        r = evaluate(r)['values']
        revised = merge_answers(r, {'circuit_type': 'ldo-power', 'input_voltage_v': 5, 'output_voltage_v': 3.3,
                                    'output_current_ma': 100, 'regulator_model': 'SYNTHETIC TEST ONLY'})
        report = evaluate(revised)
        self.assertTrue(report['ready'])
        self.assertNotIn('mcu', report['values']); self.assertNotIn('bus', report['values'])

    def test_negated_external_crystal_does_not_create_frequency_question(self):
        seed = parse_seed('STM32F103C8T6，外部稳压3.3V，无需外部晶振')
        self.assertEqual(seed['clock'], 'internal')
        self.assertNotIn('crystal_hz', {q['field'] for q in evaluate(seed)['questions']})

    def test_known_function_does_not_mean_unsupported_template_is_implemented(self):
        c = contract_from({'schema_version': 1, 'circuit_type': 'ldo-power', 'input_voltage_v': 5,
                           'output_voltage_v': 3.3, 'output_current_ma': 100, 'power_source': 'usb',
                           'regulator_model': 'SYNTHETIC TEST ONLY'})
        with self.assertRaisesRegex(Refused, 'unsupported'): make_plan(c)
