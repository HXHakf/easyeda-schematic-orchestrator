"""Reconcile interrupted placement and materialize only missing owned parts."""
import copy
import json
import os
from pathlib import Path

from .adapter import result
from .bridge import observed_parts
from .core import Refused, check_confirmation, digest, dump, file_digest, load, now
from .planner import bind_plan
from .workflow import is_blank, snapshot_fingerprint, validate_plan, validate_queue


def checked_recovery(root, state):
    path = root / 'recovery.json'
    if not path.exists() and 'recovery_sha256' not in state:
        return None
    recovery = load(path)
    if state.get('recovery_sha256') != digest(recovery):
        raise Refused('recovery provenance changed')
    history = recovery.get('source_artifacts')
    if not isinstance(history, dict) or not history:
        raise Refused('recovery source evidence missing')
    for name, sha in history.items():
        if not isinstance(name, str) or Path(name).name != name:
            raise Refused('invalid recovery source evidence name')
        copied = root / ('source--' + name)
        if file_digest(copied) != sha:
            raise Refused('recovery source evidence changed: ' + name)
        if name.endswith('recovery.json'):
            parent_history = load(copied).get('source_artifacts')
            if not isinstance(parent_history, dict) or not parent_history:
                raise Refused('historical recovery evidence missing')
            prefix = name[:-len('recovery.json')] + 'source--'
            for ancestor, ancestor_sha in parent_history.items():
                if not isinstance(ancestor, str) or history.get(prefix + ancestor) != ancestor_sha:
                    raise Refused('incomplete recovery evidence ancestry')
    return recovery


def journal_ownership(root, target, references, state):
    owned = {}
    recovery = checked_recovery(root, state)
    if recovery is not None:
        known = recovery.get('known_primitive_ids')
        if not isinstance(known, dict):
            raise Refused('recovery ownership inventory missing')
        owned.update(known)
    journal = root / 'place.journal.jsonl'
    rows = []
    if journal.exists():
        try:
            rows = [json.loads(line) for line in journal.read_text(encoding='utf-8').splitlines() if line.strip()]
        except (ValueError, OSError) as error:
            raise Refused('placement journal is incomplete or unreadable') from error
        if not rows or any(not isinstance(row, dict) for row in rows):
            raise Refused('placement journal requires complete object records')
        # Upstream journals may omit doc. Their byte-bound queue always pins it.
        validate_queue(load(root / 'place.json'), target)
        header = rows[0]
        if (header.get('playbookSha256') != file_digest(root / 'place.json')
                or header.get('project') != target['project_uuid']
                or header.get('doc', target['document_uuid']) != target['document_uuid']):
            raise Refused('placement journal does not match its queue and target')
    for row in rows[1:]:
        captures = row.get('captured', {})
        if not isinstance(captures, dict):
            raise Refused('invalid placement captures')
        for ref, primitive_id in captures.items():
            if (ref not in references or not isinstance(primitive_id, str) or not primitive_id
                    or ref in owned and owned[ref] != primitive_id):
                raise Refused('ambiguous or unexpected placement capture')
            owned[ref] = primitive_id
    if (any(ref not in references or not isinstance(pid, str) or not pid for ref, pid in owned.items())
            or len(set(owned.values())) != len(owned)):
        raise Refused('invalid placement ownership inventory')
    return owned


def recover_stage(adapter, source_dir, output_dir):
    from .locking import target_lock
    target = load(Path(source_dir) / 'contract.json')['target']
    with target_lock(target):
        if load(Path(source_dir) / 'contract.json')['target'] != target:
            raise Refused('target changed while acquiring lock')
        return _recover_stage_locked(adapter, source_dir, output_dir)


def _recover_stage_locked(adapter, source_dir, output_dir):
    source, output = Path(source_dir).resolve(), Path(output_dir).resolve()
    if output.exists() or source in output.parents or output in source.parents:
        raise Refused('recovery requires a fresh separate output directory')
    names = ('contract.json', 'approval.json', 'plan.json', 'bindings.json', 'stage.json',
             'expected.json', 'before-placement.json', 'place.json', 'place.journal.jsonl', 'recovery.json')
    source_hashes = {name: file_digest(source / name) for name in names if (source / name).is_file()}
    contract, approval, plan, bindings, state = (
        load(source / (name + '.json')) for name in ('contract', 'approval', 'plan', 'bindings', 'stage'))
    check_confirmation(contract, approval)
    validate_plan(plan, contract)
    target = contract['target']
    if state.get('target') != target:
        raise Refused('staging target differs from confirmed contract')
    for name, value in (('contract', contract), ('plan', plan), ('bindings', bindings)):
        if state.get(name + '_sha256') != digest(value):
            raise Refused('staging input changed: ' + name)
    expected = bind_plan(plan, bindings, target)
    if digest(load(source / 'expected.json')) != digest(expected):
        raise Refused('original placement intent changed')
    original = load(source / 'before-placement.json')
    if not is_blank(result(original)):
        raise Refused('original placement must have started on a blank test page')
    original_context = original.get('context', {})
    if (original_context.get('projectUuid') != target['project_uuid']
            or original_context.get('documentUuid') != target['document_uuid']):
        raise Refused('original blank page target differs')
    references = {part['ref'] for part in plan['components']}
    owned = journal_ownership(source, target, references, state)
    parent = checked_recovery(source, state)
    if parent is not None:
        source_hashes.update({'source--' + name: sha for name, sha in parent['source_artifacts'].items()})
    snapshot = adapter.snapshot(target)
    inventory = result(snapshot).get('components')
    if not isinstance(inventory, list):
        raise Refused('current component inventory unavailable')
    actual = [part for part in inventory if part.get('componentType') == 'part']
    present = {part.get('designator'): part for part in actual}
    if len(present) != len(actual) or set(present) - references:
        raise Refused('unexpected or duplicate placed component references')
    present_plan = {**plan, 'components': [part for part in plan['components'] if part['ref'] in present]}
    parts = observed_parts(present_plan, snapshot, bindings, target)
    if any(owned.get(ref) != part['primitiveId'] for ref, part in parts.items()):
        raise Refused('placed instance lacks matching original journal ownership')
    missing = references - set(present)
    output.mkdir(parents=True)
    for name, sha in source_hashes.items():
        copied = output / ('source--' + name)
        copied.write_bytes((source / name).read_bytes())
        if file_digest(copied) != sha:
            raise Refused('source evidence changed while copying: ' + name)
    for name in ('contract.json', 'approval.json', 'plan.json', 'bindings.json', 'expected.json', 'before-placement.json'):
        (output / name).write_bytes((source / name).read_bytes())
    dump(output / 'before-recovery.json', snapshot)
    recovery = {'source_artifacts': source_hashes, 'known_primitive_ids': {
        ref: part['primitiveId'] for ref, part in parts.items()}, 'missing_refs': sorted(missing),
        'created_at': now(), 'scope': 'original confirmed placement; fresh queue for absent parts only'}
    dump(output / 'recovery.json', recovery)
    recovered = {key: state[key] for key in ('target', 'contract_sha256', 'plan_sha256', 'bindings_sha256')}
    recovered.update(state='placing', recovery_sha256=digest(recovery))
    dump(output / 'stage.json', recovered)
    placement_started = False
    try:
        if missing:
            libraries = {}
            for ref in sorted(missing):
                binding = bindings[ref]
                identity = (binding['libraryUuid'], binding['deviceUuid'])
                if identity not in libraries:
                    libraries[identity] = adapter.run(
                        ['lib', 'device', 'get', '--library', identity[0], '--uuid', identity[1]], target=target)
                raw = libraries[identity]
                device = result(raw).get('device', {})
                if (device.get('uuid') != binding['deviceUuid']
                        or device.get('libraryUuid') != binding['libraryUuid']
                        or device.get('name') != binding['expected_name']):
                    raise Refused(ref + ': current library identity differs from selection')
                dump(output / 'library' / (ref + '.json'), raw)
            remaining = copy.deepcopy(expected)
            remaining['components'] = [part for part in remaining['components'] if part['ref'] in missing]
            remaining_ids = {part['id'] for part in remaining['components']}
            remaining['connections'] = [edge for edge in remaining['connections'] if edge['componentId'] in remaining_ids]
            net_ids = {edge['netId'] for edge in remaining['connections']}
            remaining['nets'] = [net for net in remaining['nets'] if net['id'] in net_ids]
            remaining.pop('modules', None)
            dump(output / 'remaining.json', remaining)
            adapter.run(['sch', 'materialize', output / 'remaining.json', '--out', output / 'place.json'], json_output=False)
            validate_queue(load(output / 'place.json'), target)
            adapter.run(['sch', 'apply', output / 'place.json', '--dry-run'], json_output=False)
        if any(file_digest(source / name) != sha for name, sha in source_hashes.items()):
            raise Refused('original evidence changed during recovery preparation')
        fresh = adapter.snapshot(target)
        if snapshot_fingerprint(fresh) != snapshot_fingerprint(snapshot):
            raise Refused('page changed during recovery preparation')
        fd = os.open(output / 'placement-started.lock', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        if missing:
            placement_started = True
            adapter.run(['sch', 'apply', output / 'place.json', '--journal', output / 'place.journal.jsonl'],
                        target=target, json_output=False, timeout=600)
        after = adapter.snapshot(target)
        dump(output / 'after-placement.json', after)
        completed = observed_parts(plan, after, bindings, target)
        final_ownership = journal_ownership(output, target, references, recovered)
        if {ref: part['primitiveId'] for ref, part in completed.items()} != final_ownership:
            raise Refused('final placed instances differ from captured ownership')
        saved = adapter.run(['sch', 'save'], target=target)
        dump(output / 'placement-save.json', saved)
        if result(saved).get('saved') is not True:
            raise Refused('recovery save did not return boolean saved:true')
        recovered.update(state='placed', primitive_ids={ref: part['primitiveId'] for ref, part in completed.items()})
    except Exception as error:
        recovered.update(state='incomplete', error=str(error))
        dump(output / 'stage.json', recovered)
        try:
            dump(output / 'failure-readback.json', adapter.snapshot(target))
            if placement_started:
                dump(output / 'failure-save.json', adapter.run(['sch', 'save'], target=target))
        except Exception as read_error:
            dump(output / 'failure-readback-error.json', {'error': str(read_error)})
        raise
    dump(output / 'stage.json', recovered)
    return recovered
