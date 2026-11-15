"""Common-component guidance is searchable before project parts are selected."""
import json
import copy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


class ComponentKnowledgeTests(unittest.TestCase):
    def cli(self, *args, env=None):
        return subprocess.run([sys.executable, '-m', 'eso.cli', *args],
                              capture_output=True, text=True, encoding='utf-8', env=env)

    def test_common_component_families_are_available_without_project_materials(self):
        names = {'电阻': 'resistor', '电容': 'capacitor', '电感': 'inductor', '磁珠': 'ferrite-bead',
                 '二极管': 'diode', '三极管': 'bjt', 'MOS': 'mosfet', '运放': 'op-amp',
                 '比较器': 'comparator', '稳压芯片': 'regulator', '逻辑门': 'logic',
                 '晶振': 'clock', '连接器': 'connector', '按键': 'switch',
                 '光耦': 'optocoupler', '继电器': 'relay', '蜂鸣器': 'buzzer'}
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / 'uncreated-material-cache'
            env = dict(os.environ, ESO_MATERIAL_STORE=str(cache))
            for query, cid in names.items():
                with self.subTest(query=query):
                    result = self.cli('kb-search', query, '--kind', 'component', env=env)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    rows = json.loads(result.stdout)['matches']
                    self.assertEqual([row['id'] for row in rows], ['component.' + cid])
                    self.assertEqual(rows[0]['kind'], 'component')
                    self.assertEqual(rows[0]['status'], 'reference-only')
                    self.assertFalse(rows[0]['automatic_plan_generation'])
                    self.assertTrue(rows[0]['sources'])
            self.assertFalse(cache.exists())

    def test_bjt_subtypes_share_one_card_and_circuit_search_stays_explicit(self):
        for query in ('三极管', 'NPN', 'PNP', 'BJT'):
            result = self.cli('kb-search', query)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual([row['id'] for row in json.loads(result.stdout)['matches']], ['component.bjt'])
        result = self.cli('kb-search', '三极管低侧驱动', '--kind', 'circuit')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([row['id'] for row in json.loads(result.stdout)['matches']], ['npn-low-side-switch'])

    def test_catalog_reports_component_and_circuit_coverage_separately(self):
        result = self.cli('kb-check')
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['topic_count'], 6)
        self.assertEqual(data.get('component_count'), 17)
        self.assertEqual(data['executable_topics'], ['npn-low-side-switch', 'nmos-low-side-switch'])

    def test_duplicate_sources_aliases_and_dangling_references_are_rejected(self):
        from eso.component_knowledge import catalog, check_component_catalog
        from eso.core import Refused
        original = catalog()
        for mode in ('id', 'alias', 'source-url', 'existing-source', 'primary', 'circuit', 'rule', 'capability', 'formula'):
            data = copy.deepcopy(original)
            with self.subTest(mode=mode):
                if mode == 'id':
                    data['components'].append(copy.deepcopy(data['components'][0]))
                elif mode == 'alias':
                    data['components'][0]['aliases'].append('ＮＰＮ')
                elif mode == 'source-url':
                    source = dict(data['sources'][0], id='duplicate', url=data['sources'][0]['url'] + '#section')
                    data['sources'].append(source)
                elif mode == 'existing-source':
                    from eso.knowledge import catalog as circuits
                    data['sources'].append(copy.deepcopy(circuits()['sources'][0]))
                elif mode == 'primary':
                    data['components'][0]['primary_source_id'] = 'nexperia-an11158'
                elif mode == 'circuit':
                    data['components'][0]['related_topics'].append('unknown-circuit')
                elif mode == 'rule':
                    data['shared_rule_ids'].append('unknown-rule')
                elif mode == 'capability':
                    data['components'][0]['status'] = 'executable-bounded'
                else:
                    data['components'][0]['calculations'][0].pop('conditions')
                with self.assertRaises(Refused):
                    check_component_catalog(data)

    def test_reference_card_cannot_be_used_as_an_executable_topic(self):
        from test_knowledge import fixture
        from eso.requirements import evaluate
        with tempfile.TemporaryDirectory() as directory:
            requirements = fixture(Path(directory))
            requirements['knowledge']['circuits'][0]['topic'] = 'component.bjt'
            self.assertFalse(evaluate(requirements)['ready'])

    def test_unrelated_reference_updates_preserve_pinned_circuit_confirmation(self):
        from test_knowledge import fixture
        from eso.component_knowledge import catalog, search
        from eso.core import confirm, check_confirmation, digest, dump
        from eso.knowledge import check_contract_knowledge
        from eso.knowledge_v2 import dependency_evidence
        from eso.requirements import contract_from
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract = contract_from(fixture(root))
            approval = confirm(contract, digest(contract), 'synthetic reviewer')
            v2_input = {'circuits': [{'topic': 'npn-low-side-switch'}], 'materials': []}
            v2_before = dependency_evidence(v2_input)
            cards = catalog()
            cards['components'][0]['version'] = '1.0.1'
            dump(root / 'components.json', cards)
            with patch('eso.component_knowledge.CATALOG', root / 'components.json'):
                self.assertEqual(search('电阻')['matches'][0]['version'], '1.0.1')
                check_contract_knowledge(contract)
                check_confirmation(contract, approval)
                self.assertEqual(dependency_evidence(v2_input), v2_before)

    def test_query_validation_and_unmatched_queries(self):
        from eso.component_knowledge import search
        from eso.core import Refused
        for kwargs in ({'query': None}, {'query': '电阻', 'kind': 'other'}, {'query': '电阻', 'parameters': []}):
            with self.subTest(kwargs=kwargs), self.assertRaises(Refused):
                search(**kwargs)
        self.assertEqual(search('')['matches'], [])
        self.assertEqual(search('unclassified-model-xyz')['matches'], [])
