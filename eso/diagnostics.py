"""Read-only partial-run diagnosis. No lock removal or replay generation."""
import json
from pathlib import Path
from .adapter import result
from .core import Refused, load
from .reconcile import reconcile


def diagnose(run_dir, *, snapshot=None, connectivity=None, adapter=None):
    root = Path(run_dir).resolve()
    if not root.is_dir(): raise Refused('run directory missing')
    report = {'state': 'diagnostic-only', 'level': 'offline-observations', 'resume_allowed': False,
              'issues': [], 'unverified': [], 'journals': {},
              'locks': [name for name in ('placement-started.lock', 'execution-started.lock') if (root/name).exists()],
              'next_steps': ['保留锁与原始证据；核对新鲜回读后生成新的受保护计划，不重放旧队列。']}
    contract, plan = load(root/'contract.json'), load(root/'plan.json')
    target = contract.get('target')
    if not target: raise Refused('diagnosis requires exact contract target')
    report['target'] = target
    for state_file in ('stage.json', 'bundle.json'):
        if (root/state_file).exists():
            state = load(root/state_file)
            report[state_file] = state.get('state')
            if state.get('target') != target: report['issues'].append(state_file + ': target differs from contract')
    for journal in sorted(root.glob('*.journal.jsonl')):
        info = {'valid_records': 0, 'errors': [], 'last_record': None}
        for number, line in enumerate(journal.read_bytes().splitlines(), 1):
            if not line.strip(): continue
            try:
                row = json.loads(line.decode('utf-8'))
                if not isinstance(row, dict): raise ValueError('object required')
                info['valid_records'] += 1
                info['last_record'] = row
            except ValueError as error:
                info['errors'].append({'line': number, 'error': str(error)})
        report['journals'][journal.name] = info
        if info['errors']: report['issues'].append(journal.name + ': incomplete or invalid journal')
    if adapter is not None:
        if snapshot is not None or connectivity is not None:
            raise Refused('choose live reads or supplied offline observations, not both')
        snapshot = adapter.snapshot(target)
        connectivity = adapter.run(['sch', 'connectivity'], target=target)
        report['level'] = 'live-read-only-diagnosis'
    if snapshot is None:
        report['unverified'].append('no fresh snapshot supplied; journal is not proof of actual placement')
    else:
        context = snapshot.get('context', {})
        if context.get('projectUuid') != target['project_uuid'] or context.get('documentUuid') != target['document_uuid']:
            report['issues'].append('snapshot target differs from contract')
        else:
            data = result(snapshot)
            parts = data.get('components')
            if not isinstance(parts, list): raise Refused('diagnostic snapshot component inventory unavailable')
            parts = [p for p in parts if p.get('componentType') != 'sheet']
            expected = {c['ref']: c for c in plan['components']}
            observed = {p.get('designator'): p for p in parts}
            if len(observed) != len(parts) or None in observed:
                report['issues'].append('missing or duplicate observed designators')
            report['components'] = {'missing': sorted(set(expected) - set(observed)),
                                    'unexpected': sorted(str(r) for r in set(observed) - set(expected)), 'present': sorted(set(expected) & set(observed)),
                                    'identity_conflicts': []}
            for ref in set(expected) & set(observed):
                stable = observed[ref].get('otherProperty', {}).get('EasyEDA Agent Component ID')
                if stable != expected[ref]['id']: report['components']['identity_conflicts'].append(ref)
            if any(report['components'][k] for k in ('missing', 'unexpected', 'identity_conflicts')):
                report['issues'].append('observed component inventory differs from plan')
    if connectivity is not None:
        if (root/'expected.json').exists():
            report['reconciliation'] = reconcile(load(root/'expected.json'), connectivity)
            if not report['reconciliation']['passed']: report['issues'].append('pin/net/NC reconciliation failed')
        else: report['unverified'].append('expected connectivity missing')
    else: report['unverified'].append('no fresh canonical connectivity supplied')
    report['unverified'].append('diagnosis does not certify save/reload, geometry or electrical rule checks')
    return report
