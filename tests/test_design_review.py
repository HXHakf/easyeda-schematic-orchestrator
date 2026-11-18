"""Synthetic offline fixtures exercise the actual public CLI and write boundaries."""
import contextlib
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from eso.cli import main
from eso.core import Refused, check_confirmation, confirm, digest, dump, file_digest, load
from eso.design_review import (REVIEW_CHECKS, bom_status, compare_bom_costs, electrical_intent,
                               pin_design_evidence, requirements_digest, review_subject, passive_inventory,
                               validate_design_evidence)
from eso.requirements import contract_from, evaluate
from eso.planner import bind_plan
from eso.workflow import Workflow, validate_plan
from test_workflow import TARGET, FakeAdapter, blank, design as legacy_design


class DesignReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def cli(self, *args, success=True):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main([str(a) for a in args])
        self.assertEqual(code, 0 if success else 2, err.getvalue() + out.getvalue())
        return json.loads(out.getvalue() if success else err.getvalue())

    def fixture(self):
        _, plan, _ = legacy_design()
        r = {'schema_version': 1, 'circuit_type': 'generic', 'function_description': 'synthetic controller',
             'target_project': TARGET['project_name'], 'design_review': {'policy_version': 1, 'status': 'analyzed'}}
        source = self.root / 'synthetic-source.txt'
        source.write_text('Synthetic technical and price facts for offline tests only.')
        design = {'policy_version': 1, 'requirements_sha256': requirements_digest(r),
                  'function': 'controller → pressure sensor → external header',
                  'operating_conditions': 'Synthetic regulated 3.3V test conditions', 'author_context_id': 'author-test',
                  'applicability': {key: {'applicable': True, 'reason': 'synthetic reviewed use',
                                        'decision': 'synthetic explicit condition'} for key in ('power', 'load', 'interfaces', 'assembly')},
                  'assembly': {'method': 'hand-solder', 'passive_package': '0805'},
                  'sources': [{'id': 'source', 'path': str(source), 'sha256': file_digest(source),
                               'url': 'https://example.invalid/synthetic', 'section': 'pins 1; application 2; prices 3'}],
                  'defaults': [], 'electrical_intent': electrical_intent(plan),
                  'selections': [], 'bom': [], 'required_parts': [], 'build_quantity': 10,
                  'calculations': 'Synthetic 3.3V operating point checked against source section 2'}
        dummies = passive_inventory(design['electrical_intent'])
        design['assembly']['passive_component_ids'] = sorted(dummies)
        for c in plan['components']:
            package = '0805' if c['id'] in dummies else 'SYNTHETIC-' + c['ref']
            design['selections'].append({'role': c['role'], 'model': c['query'], 'package': package,
                                         'component_ids': [c['id']], 'source_ids': ['source'],
                                         'pinout_section': '1', 'application_section': '2',
                                         'capabilities': ['pressure'], 'required_capabilities': ['pressure']})
            design['bom'].append({'model': c['query'], 'package': package, 'quantity': 1,
                                  'component_ids': [c['id']], 'source_ids': ['source'],
                                  'quote': {'unit_price': 0.1, 'purchase_quantity': 10, 'tier_quantity': 10,
                                            'currency': 'CNY', 'queried_at': '2026-11-17T12:00:00+08:00', 'source_id': 'source'}})
            design['required_parts'].append({'role': c['role'], 'component_ids': [c['id']], 'source_ids': ['source']})
        return r, design

    def publish(self, r, design, review_patch=None):
        design_path = self.root / 'design-review.json'
        dump(design_path, design)
        invocation = {'reviewer_id': 'reviewer-test', 'context_id': 'reviewer-context',
                      'author_context_id': design['author_context_id'], 'subject_sha256': review_subject(design),
                      'request': 'Synthetic independent call fixture with original source and subject',
                      'response': 'Synthetic itemized review response fixture'}
        invocation_path = self.root / 'invocation.json'; dump(invocation_path, invocation)
        review = {'policy_version': 1, 'reviewer': {'id': 'reviewer-test', 'context_id': 'reviewer-context', 'independent': True},
                  'subject_sha256': review_subject(design),
                  'invocation': {'path': str(invocation_path), 'sha256': file_digest(invocation_path)},
                  'checks': [{'id': key, 'status': 'closed', 'conclusion': 'Synthetic explicit checked result', 'source_ids': ['source']}
                             for key in REVIEW_CHECKS], 'issues': []}
        review.update(review_patch or {})
        review_path = self.root / 'electrical-review.json'; dump(review_path, review)
        r['design_review'].update(design={'path': str(design_path), 'sha256': file_digest(design_path)},
                                  review={'path': str(review_path), 'sha256': file_digest(review_path)})
        dump(self.root / 'requirements.json', r)
        return r

    def contract_cli(self, r):
        dump(self.root / 'requirements.json', r)
        dump(self.root / 'target.json', TARGET)
        self.cli('contract', self.root / 'requirements.json', '--target', self.root / 'target.json', '--out', self.root / 'contract.json')
        return load(self.root / 'contract.json')

    def test_new_intake_requires_review_beyond_function_text(self):
        self.cli('intake', '--text', '我要做温湿度采集器', '--out', self.root / 'intake.json')
        r = load(self.root / 'intake.json')
        self.assertEqual(r['function_description'], '我要做温湿度采集器')
        report = self.cli('assess', self.root / 'intake.json')
        self.assertFalse(report['ready']); self.assertEqual(report['review_mode'], 'new')
        self.assertLessEqual(len(report['next_questions']), 3)
        self.assertNotIn('circuit_type', {q['field'] for q in report['questions']})
        self.assertTrue(report['research_tasks']); self.assertTrue(report['selection_tasks'])
        self.cli('contract', self.root / 'intake.json', '--out', self.root / 'bad.json', success=False)

    def test_design_evidence_binds_intent_without_digest_cycle(self):
        r, d = self.fixture(); c = self.contract_cli(self.publish(r, d))
        self.cli('confirm', self.root / 'contract.json', '--sha256', digest(c), '--actor', 'synthetic test actor', '--out', self.root / 'approval.json')
        self.cli('plan', self.root / 'contract.json', '--out', self.root / 'plan.json')
        self.cli('validate-plan', self.root / 'plan.json', '--contract', self.root / 'contract.json')
        p = load(self.root / 'plan.json')
        self.assertNotIn('contract_sha256', d['electrical_intent'])
        p['components'][0].update(x=100, y=200)
        p['components'][0]['pins'][0].update(x=1, y=2)
        p['modules'][0]['title'] = 'Renamed frame'
        p['drawing_groups'] = [{'id': 'all', 'modules': [m['id'] for m in p['modules']]}]
        validate_design_evidence(c, p)
        check_confirmation(c, load(self.root / 'approval.json'))
        for change in ('query', 'pins', 'module'):
            changed = deepcopy(p)
            if change == 'pins': changed['components'][0]['pins'][0]['number'] = '999'
            else: changed['components'][0][change] = 'changed'
            with self.subTest(change=change), self.assertRaises(Refused): validate_design_evidence(c, changed)
        self.assertIn('完整 BOM', (self.root / 'contract.md').read_text())
        self.assertIn('装配', (self.root / 'contract.md').read_text())

    def test_missing_peripheral_and_package_mismatch_are_refused(self):
        for failure in ('bom', 'peripheral', 'package', 'humidity'):
            r, d = self.fixture()
            if failure == 'bom': d['bom'].pop()
            if failure == 'peripheral': d['required_parts'][0]['component_ids'] = ['missing-capacitor']
            if failure == 'package': d['bom'][0]['package'] = 'OTHER PACKAGE SAME MODEL'
            if failure == 'humidity': d['selections'][0]['required_capabilities'].append('humidity')
            self.publish(r, d)
            with self.subTest(failure=failure):
                self.assertFalse(self.cli('assess', self.root / 'requirements.json')['ready'])
                self.cli('contract', self.root / 'requirements.json', '--out', self.root / 'bad.json', success=False)

    def test_unknown_quotes_are_visible_and_only_binding_cost_blocks(self):
        r, d = self.fixture(); d['bom'][0]['quote'] = None
        c = self.contract_cli(self.publish(r, d))
        status = self.cli('design-evidence-check', self.root / 'contract.json')['cost']
        self.assertFalse(status['complete']); self.assertTrue(status['known_subtotal']); self.assertTrue(status['missing_quotes'])
        for field, value in [('binding_budget', {'amount': 100, 'currency': 'CNY'}), ('complete_cost_comparison', True)]:
            changed = deepcopy(d); changed[field] = value
            self.publish(r, changed)
            self.assertFalse(self.cli('assess', self.root / 'requirements.json')['ready'])

    def test_quote_tiers_and_candidate_purchase_conditions(self):
        r, d = self.fixture()
        self.assertEqual(len(compare_bom_costs([d, deepcopy(d)])), 2)
        changed = deepcopy(d); changed['build_quantity'] = 100
        with self.assertRaisesRegex(Refused, 'quantities differ'): compare_bom_costs([d, changed])
        for key, value in [('purchase_quantity', 1000), ('tier_quantity', 1000)]:
            changed = deepcopy(d); changed['bom'][0]['quote'][key] = value
            self.publish(r, changed)
            self.assertFalse(self.cli('assess', self.root / 'requirements.json')['ready'])

    def test_empty_reviews_boolean_pass_and_same_context_cannot_approve(self):
        for patch_ in ({'checks': []}, {'checks': [], 'passed': True},
                       {'reviewer': {'id': 'author', 'context_id': 'author-test', 'independent': True}},
                       {'issues': ['unclosed issue']}, {'subject_sha256': '0' * 64}):
            r, d = self.fixture(); self.publish(r, d, patch_)
            with self.subTest(patch=patch_):
                self.assertFalse(self.cli('assess', self.root / 'requirements.json')['ready'])

    def test_forged_invocation_record_cannot_approve(self):
        r, d = self.fixture(); self.publish(r, d)
        path = self.root / 'invocation.json'; dump(path, {'passed': True})
        review_path = self.root / 'electrical-review.json'; review = load(review_path)
        review['invocation']['sha256'] = file_digest(path); dump(review_path, review)
        r['design_review']['review']['sha256'] = file_digest(review_path)
        dump(self.root / 'requirements.json', r)
        self.assertFalse(self.cli('assess', self.root / 'requirements.json')['ready'])

    def test_changed_conditions_revoke_defaults_and_review(self):
        r, d = self.fixture(); r['input_voltage_v'] = 3.3; d['requirements_sha256'] = requirements_digest(r)
        d['defaults'] = [{'field': 'passive_package', 'value': '0805', 'reason': 'hand solder', 'depends_on': {'input_voltage_v': 3.3}, 'source_ids': ['source']}]
        c = self.contract_cli(self.publish(r, d))
        self.assertEqual(c['defaults'][0]['value'], '0805')
        r['input_voltage_v'] = 5
        self.assertFalse(evaluate(r)['ready'])
        d['requirements_sha256'] = requirements_digest(r); self.publish(r, d)
        self.assertFalse(evaluate(r)['ready'])

    def test_policy_mixing_or_orphan_marker_and_legacy_hash_preserved(self):
        c, p, ir = legacy_design(); original = json.dumps(c).encode()
        self.assertEqual(validate_design_evidence(c)['review_mode'], 'legacy')
        self.assertEqual(json.dumps(c).encode(), original)
        for changed in (dict(c, design_evidence={'policy_version': 1}),
                        dict(c, requirements={**c['requirements'], 'design_review': {'policy_version': 1}})):
            with self.assertRaises(Refused): validate_design_evidence(changed)
        self.assertFalse(evaluate({'schema_version': 1, 'function_description': 'x', 'design_review': {'policy_version': 1}})['ready'])

    def test_relocation_content_change_and_pinned_history(self):
        r, d = self.fixture(); c = self.contract_cli(self.publish(r, d))
        approval = confirm(c, digest(c), 'synthetic test actor')
        cache = self.root / 'relocated'; cache.mkdir()
        source = Path(d['sources'][0]['path']); source.rename(cache / 'new-name.txt')
        self.cli('design-evidence-check', self.root / 'contract.json', '--search-path', cache)
        r['design_review']['search_paths'] = [str(cache)]
        c = self.contract_cli(r); approval = confirm(c, digest(c), 'synthetic test actor')
        pinned = self.root / 'run'; artifacts = pin_design_evidence(c, pinned)
        self.assertEqual(len(artifacts), 4)
        (cache / 'new-name.txt').write_text('changed original content')
        with self.assertRaises(Refused): validate_design_evidence(c)
        check_confirmation(c, approval, evidence_root=pinned)
        first = next(iter(artifacts)); (pinned / first).write_text('mutated pinned content')
        with self.assertRaises(Refused): check_confirmation(c, approval, evidence_root=pinned)

    def test_prepare_cli_pins_artifacts_before_adapter_calls(self):
        r, d = self.fixture(); c = self.contract_cli(self.publish(r, d))
        self.cli('plan', self.root / 'contract.json', '--out', self.root / 'plan.json')
        self.cli('confirm', self.root / 'contract.json', '--sha256', digest(c), '--actor', 'synthetic test actor', '--out', self.root / 'approval.json')
        p = load(self.root / 'plan.json')
        bindings = {x['ref']: {'libraryUuid': '1'*32, 'deviceUuid': '2'*32, 'verified_model': x['query'], 'evidence': 'synthetic'} for x in p['components']}
        ir = bind_plan(p, bindings, TARGET)
        dump(self.root / 'composition.json', {'connectivity': ir}); dump(self.root / 'before.json', blank())
        adapter = FakeAdapter()
        args = ['prepare']
        for field in ('contract', 'approval', 'plan', 'composition', 'before'):
            args += ['--' + field, self.root / (field + '.json')]
        args += ['--run-dir', self.root / 'run']
        original_guard = adapter.guard
        def guard(target):
            self.assertEqual(len(list((self.root / 'run').glob('design-evidence--*'))), 4)
            original_guard(target)
        adapter.guard = guard
        with patch('eso.cli.EasyEDA', return_value=adapter): bundle = self.cli(*args)
        names = [n for n in bundle['artifacts'] if n.startswith('design-evidence--')]
        self.assertEqual(len(names), 4)
        for record in (r['design_review']['design'], r['design_review']['review'], *d['sources']):
            Path(record['path']).unlink()
        (self.root / 'invocation.json').unlink()
        Workflow(adapter, self.root / 'run')._check_bundle(bundle)
        (self.root / 'run' / names[0]).write_text('changed')
        with self.assertRaises(Refused): Workflow(adapter, self.root / 'run')._check_bundle(bundle)

    def test_external_parts_and_assembly_are_checked(self):
        r, d = self.fixture()
        d['required_parts'].append({'role': 'external cable', 'component_ids': [],
                                    'external_parts': [{'model': 'TEST-CABLE', 'package': 'ASSEMBLY'}], 'source_ids': ['source']})
        self.publish(r, d); self.assertFalse(evaluate(r)['ready'])
        d['bom'].append({'model': 'TEST-CABLE', 'package': 'ASSEMBLY', 'quantity': 1,
                         'component_ids': [], 'external': True, 'source_ids': ['source'], 'quote': None})
        self.publish(r, d); self.assertTrue(evaluate(r)['ready'])
        passive = next(s for s in d['selections'] if s['component_ids'][0] in d['assembly']['passive_component_ids'])
        bid = next(b for b in d['bom'] if b['component_ids'] == passive['component_ids'])
        passive['package'] = bid['package'] = '0603'
        self.publish(r, d); self.assertFalse(evaluate(r)['ready'])
        passive['package'] = bid['package'] = d['assembly']['passive_package']
        self.publish(r, d); self.assertTrue(evaluate(r)['ready'])

    def test_stage_cli_pins_before_guard_and_refuses_changed_sources(self):
        r, d = self.fixture(); c = self.contract_cli(self.publish(r, d))
        self.cli('plan', self.root / 'contract.json', '--out', self.root / 'plan.json')
        self.cli('confirm', self.root / 'contract.json', '--sha256', digest(c), '--actor', 'synthetic actor', '--out', self.root / 'approval.json')
        plan = load(self.root / 'plan.json')
        bindings = {x['ref']: {'libraryUuid': '1'*32, 'deviceUuid': '2'*32, 'verified_model': x['query'], 'evidence': 'synthetic'} for x in plan['components']}
        dump(self.root / 'bindings.json', bindings)
        args = ['stage']
        for field in ('contract', 'approval', 'plan', 'bindings'):
            args += ['--' + field, self.root / (field + '.json')]
        args += ['--run-dir', self.root / 'stage']
        adapter = FakeAdapter()
        def guard(target):
            self.assertEqual(len(list((self.root / 'stage').glob('design-evidence--*'))), 4)
            raise Refused('synthetic guard stops before EDA')
        adapter.guard = guard
        with patch('eso.cli.EasyEDA', return_value=adapter): self.cli(*args, success=False)
        self.assertEqual(adapter.calls, [])
        Path(d['sources'][0]['path']).write_text('changed source')
        adapter.guard = lambda target: self.fail('must refuse changed source before guard')
        args[-1] = self.root / 'new-stage'
        with patch('eso.cli.EasyEDA', return_value=adapter): self.cli(*args, success=False)

    def test_invalid_physical_inventory_fails_before_confirmation(self):
        for field in ('duplicate-pin', 'nc', 'module'):
            r, d = self.fixture()
            component = d['electrical_intent']['components'][0]
            if field == 'duplicate-pin': component['pins'].append(deepcopy(component['pins'][0]))
            if field == 'nc': component['pins'][0].update(net=None, nc_reason=None)
            if field == 'module': component['module'] = 'missing-module'
            self.publish(r, d)
            with self.subTest(field=field): self.assertFalse(evaluate(r)['ready'])

    def test_existing_profile_defaults_remain_visible_candidates(self):
        self.cli('intake', '--text', 'STM32F103C8T6 外部稳压3.3V 最小系统', '--out', self.root / 'intake.json')
        report = self.cli('assess', self.root / 'intake.json')
        self.assertFalse(report['ready'])
        candidates = {d['field']: d['value'] for d in report['default_candidates']}
        self.assertEqual(candidates['passive_package'], '0603')
        self.assertNotIn('passive_package', report['values'])

    def test_new_policy_keeps_bounded_knowledge_plan_and_calculations(self):
        from test_knowledge import fixture as knowledge_fixture
        from eso.planner import make_plan
        r, d = self.fixture()
        r = knowledge_fixture(self.root)
        expected = make_plan(contract_from(r))
        r['design_review'] = {'policy_version': 1}
        d['requirements_sha256'] = requirements_digest(r)
        d['electrical_intent'] = electrical_intent(expected)
        d['selections'], d['bom'], d['required_parts'] = [], [], []
        for component in expected['components']:
            d['selections'].append({'role': component['role'], 'model': component['query'], 'package': 'TEST',
                                    'component_ids': [component['id']], 'source_ids': ['source'],
                                    'pinout_section': '1', 'application_section': '2',
                                    'required_capabilities': [], 'capabilities': []})
            d['bom'].append({'model': component['query'], 'package': 'TEST', 'quantity': 1,
                             'component_ids': [component['id']], 'source_ids': ['source'], 'quote': None})
            d['required_parts'].append({'role': component['role'], 'component_ids': [component['id']], 'source_ids': ['source']})
        d['assembly']['passive_component_ids'] = sorted(passive_inventory(d['electrical_intent']))
        d['assembly']['passive_package'] = 'TEST'
        self.publish(r, d)
        dump(self.root / 'requirements.json', r)
        self.cli('contract', self.root / 'requirements.json', '--out', self.root / 'contract.json')
        self.cli('plan', self.root / 'contract.json', '--out', self.root / 'plan.json')
        c = load(self.root / 'contract.json')
        generated = load(self.root / 'plan.json')
        self.assertEqual(electrical_intent(generated), electrical_intent(make_plan(c)))
        self.assertEqual(generated['knowledge_evidence'], make_plan(c)['knowledge_evidence'])
        r['knowledge']['circuits'][0]['parameters']['supply_voltage_v'] = 100
        d['requirements_sha256'] = requirements_digest(r)
        self.publish(r, d)
        self.cli('contract', self.root / 'requirements.json', '--out', self.root / 'bad.json', success=False)

    def test_malformed_nested_records_return_structured_refusal(self):
        for field in ('applicability', 'reviewer', 'quote-time'):
            r, d = self.fixture()
            review_patch = None
            if field == 'applicability': d['applicability'] = []
            if field == 'reviewer': review_patch = {'reviewer': []}
            if field == 'quote-time': d['bom'][0]['quote']['queried_at'] = 12
            self.publish(r, d, review_patch)
            with self.subTest(field=field):
                self.cli('contract', self.root / 'requirements.json', '--out', self.root / 'bad.json', success=False)

    def mixed_fixture(self, requirements):
        from eso.planner import make_plan
        r, design = self.fixture()
        r = deepcopy(requirements)
        r['target_project'] = TARGET['project_name']
        expected = make_plan(contract_from(r))
        r['design_review'] = {'policy_version': 1}
        design['requirements_sha256'] = requirements_digest(r)
        design['electrical_intent'] = electrical_intent(expected)
        design['selections'], design['bom'], design['required_parts'] = [], [], []
        package = r.get('passive_package', 'TEST')
        design['assembly'].update(passive_package=package,
                                  passive_component_ids=sorted(passive_inventory(design['electrical_intent'])))
        for c in expected['components']:
            design['selections'].append({'role': c['role'], 'model': c['query'], 'package': 'TEST',
                                          'component_ids': [c['id']], 'source_ids': ['source'],
                                          'pinout_section': '1', 'application_section': '2',
                                          'required_capabilities': [], 'capabilities': []})
            design['bom'].append({'model': c['query'], 'package': 'TEST', 'quantity': 1,
                                  'component_ids': [c['id']], 'source_ids': ['source'], 'quote': None})
            design['required_parts'].append({'role': c['role'], 'component_ids': [c['id']], 'source_ids': ['source']})
        if package != 'TEST':
            design['assembly']['package_exceptions'] = [
                {'component_id': cid, 'package': 'TEST', 'reason': 'synthetic package source exception', 'source_ids': ['source']}
                for cid in design['assembly']['passive_component_ids']]
        return r, design

    def test_reviewed_knowledge_and_composition_paths_keep_exact_evidence(self):
        from test_knowledge import fixture as knowledge_fixture
        from test_knowledge_composition import combined_fixture
        from eso.design_review import plan_from_design
        from eso.knowledge_design import check_knowledge_plan
        from eso.planner import make_plan
        from eso.workflow import validate_plan
        for kind in ('knowledge', 'composition'):
            with self.subTest(kind=kind):
                original = knowledge_fixture(self.root) if kind == 'knowledge' else combined_fixture(self.root)
                legacy = contract_from(original)
                legacy_plan = make_plan(legacy)
                changed_legacy = deepcopy(legacy_plan)
                changed_legacy['modules'][0]['title'] = 'changed'
                with self.assertRaises(Refused): check_knowledge_plan(changed_legacy, legacy)
                r, design = self.mixed_fixture(original)
                pins = [(c['id'], p['number'], p['net']) for c in design['electrical_intent']['components']
                        for p in c['pins'] if p['net'] is not None]
                a, b = next((a,b) for i,a in enumerate(pins) for b in pins[i+1:] if a[2] == b[2])
                design['electrical_intent']['primary_paths'] = [{'id': 'reviewed', 'title': 'Reviewed link', 'links': [
                    {'from': {'componentId': a[0], 'pinNumber': a[1]},
                     'to': {'componentId': b[0], 'pinNumber': b[1]}, 'net': a[2]}]}]
                contract = self.contract_cli(self.publish(r, design))
                plan = plan_from_design(contract)
                self.assertEqual(plan['primary_paths'], design['electrical_intent']['primary_paths'])
                self.assertEqual(plan['knowledge_evidence'], make_plan(contract)['knowledge_evidence'])
                plan['modules'][0]['title'] = 'Reviewed drawing title'
                plan['drawing_groups'] = [{'id': 'joined', 'title': 'Joined', 'modules': [m['id'] for m in plan['modules']]}]
                validate_plan(plan, contract)
                for defect in ('path', 'net', 'evidence'):
                    changed = deepcopy(plan)
                    if defect == 'path': changed['primary_paths'][0]['links'][0]['net'] = 'OTHER'
                    if defect == 'net': changed['components'][0]['pins'][0]['net'] = 'OTHER'
                    if defect == 'evidence': changed['knowledge_evidence'] = {}
                    with self.subTest(defect=defect), self.assertRaises(Refused): validate_plan(changed, contract)

    def test_mixed_policy_pins_all_knowledge_datasheets_and_revalidates_history(self):
        from test_knowledge import fixture as knowledge_fixture
        r, d = self.mixed_fixture(knowledge_fixture(self.root))
        c = self.contract_cli(self.publish(r, d)); original = json.dumps(c).encode()
        self.cli('confirm', self.root / 'contract.json', '--sha256', digest(c), '--actor', 'synthetic actor', '--out', self.root / 'approval.json')
        approval = load(self.root / 'approval.json')
        run = self.root / 'mixed-history'
        pinned = pin_design_evidence(c, run)
        self.assertEqual(len(pinned), 8)
        for m in r['knowledge']['materials']:
            path = Path(m['datasheet']['path'])
            self.assertIn('design-evidence--' + m['datasheet']['sha256'], pinned)
            path.unlink()
        check_confirmation(c, approval, evidence_root=run)
        self.cli('design-evidence-check', self.root / 'contract.json', '--search-path', run)
        self.assertEqual(json.dumps(c).encode(), original)
        with self.assertRaises(Refused): check_confirmation(c, approval)
        legacy = deepcopy(c); legacy['requirements'].pop('design_review'); legacy.pop('design_evidence')
        with self.assertRaises(Refused): check_confirmation(legacy, approval, evidence_root=run)
        bad = next(m['datasheet']['sha256'] for m in r['knowledge']['materials'])
        (run / ('design-evidence--' + bad)).write_text('altered pinned datasheet')
        with self.assertRaises(Refused): check_confirmation(c, approval, evidence_root=run)

    def test_new_knowledge_and_composition_intent_must_match_before_confirmation(self):
        from test_knowledge import fixture as knowledge_fixture
        from test_knowledge_composition import combined_fixture
        with patch.dict('os.environ', {'ESO_MATERIAL_STORE': str(self.root / 'store')}):
            for kind in ('knowledge', 'composition'):
                source = knowledge_fixture(self.root) if kind == 'knowledge' else combined_fixture(self.root)
                r, d = self.mixed_fixture(source)
                c = self.contract_cli(self.publish(r, d))
                component = next(c for c in d['electrical_intent']['components'] if c['id'].endswith('-bias'))
                next(p for p in component['pins'] if p['net'] != 'GND')['net'] = 'GND'
                # Keep explicit net declarations complete to isolate topology reconciliation.
                used = {p['net'] for c in d['electrical_intent']['components'] for p in c['pins'] if p['net']}
                d['electrical_intent']['nets'] = [n for n in d['electrical_intent']['nets'] if n['name'] in used]
                self.publish(r, d)
                with self.subTest(kind=kind):
                    assessed = self.cli('assess', self.root / 'requirements.json')
                    self.assertFalse(assessed['ready'])
                    self.assertIn('bounded knowledge/composition topology', str(assessed['research_tasks']))
                    self.cli('contract', self.root / 'requirements.json', '--out', self.root / 'bad.json', success=False)
                    # Even a manually constructed contract with refreshed evidence cannot bypass confirm.
                    c['requirements'] = deepcopy(r)
                    c['design_evidence'].update(design=r['design_review']['design'], review=r['design_review']['review'],
                                                 electrical_intent_sha256=digest(d['electrical_intent']))
                    dump(self.root / 'crafted.json', c)
                    refused = self.cli('confirm', self.root / 'crafted.json', '--sha256', digest(c), '--actor', 'synthetic actor', '--out', self.root / 'bad-approval.json', success=False)
                    self.assertIn('bounded knowledge/composition topology', refused['error'])

    def test_v2_composition_pinned_documents_keep_withdrawal_checks(self):
        from test_knowledge_composition import combined_fixture
        from eso.materials import MaterialStore
        from eso.planner import make_plan
        with patch.dict('os.environ', {'ESO_MATERIAL_STORE': str(self.root / 'store')}):
            r, d = self.mixed_fixture(combined_fixture(self.root))
            c = self.contract_cli(self.publish(r, d)); original = json.dumps(c).encode()
            approval = confirm(c, digest(c), 'synthetic actor')
            plan = make_plan(c)
            run = self.root / 'composition-history'
            pinned = pin_design_evidence(c, run)
            self.assertEqual(len(pinned), 9)
            store = MaterialStore()
            records = [*r['knowledge']['materials'], r['composition']['controller']]
            for material in records:
                sha = material['record']['datasheet']['sha256']
                path = Path(store.document(sha))
                path.write_text('later cache contents replaced')
            check_confirmation(c, approval, evidence_root=run)
            validate_plan(plan, c, evidence_root=run)
            self.cli('design-evidence-check', self.root / 'contract.json', '--search-path', run)
            self.assertEqual(json.dumps(c).encode(), original)
            store.withdraw(records[0]['record_sha256'], 'synthetic withdrawal after pinning')
            with self.assertRaisesRegex(Refused, 'withdrawn'): check_confirmation(c, approval, evidence_root=run)

    def test_passive_inventory_omission_partial_coverage_and_reviewed_exceptions(self):
        for failure in ('omitted', 'partial', 'empty', 'wrong-package'):
            r, d = self.fixture()
            r['passive_package'] = '0805'; d['requirements_sha256'] = requirements_digest(r)
            if failure == 'omitted': d['assembly'].pop('passive_component_ids')
            if failure == 'partial': d['assembly']['passive_component_ids'].pop()
            if failure == 'empty': d['assembly']['passive_component_ids'] = []
            if failure == 'wrong-package':
                cid = d['assembly']['passive_component_ids'][0]
                next(s for s in d['selections'] if cid in s['component_ids'])['package'] = '0603'
                next(b for b in d['bom'] if cid in b['component_ids'])['package'] = '0603'
            self.publish(r, d)
            with self.subTest(failure=failure):
                self.assertFalse(self.cli('assess', self.root / 'requirements.json')['ready'])
                self.cli('contract', self.root / 'requirements.json', '--out', self.root / 'bad.json', success=False)
        r, d = self.fixture(); cid = d['assembly']['passive_component_ids'][0]
        next(s for s in d['selections'] if cid in s['component_ids'])['package'] = '0603'
        next(b for b in d['bom'] if cid in b['component_ids'])['package'] = '0603'
        d['assembly']['package_exceptions'] = [{'component_id': cid, 'package': '0603',
                                                'reason': 'synthetic validated module supplied part', 'source_ids': ['source']}]
        c = self.contract_cli(self.publish(r, d))
        self.assertTrue(any(s['package'].startswith('SYNTHETIC-U') for s in d['selections']))
        self.cli('confirm', self.root / 'contract.json', '--sha256', digest(c), '--actor', 'synthetic actor', '--out', self.root / 'approval.json')
        d['assembly']['package_exceptions'][0]['reason'] = ''
        self.publish(r, d); self.assertFalse(evaluate(r)['ready'])
