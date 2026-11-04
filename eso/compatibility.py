"""Offline capability probe. Synthetic geometry is never a circuit measurement."""
from pathlib import Path
import math
from .adapter import result
from .core import Refused, dump, file_digest


def probe_snapshot(adapter, snapshot, target, directory):
    """Compile against an unaltered snapshot, reserialized as JSON; never execute it."""
    root = Path(directory)
    dump(root / 'before.json', snapshot)
    # Deliberately synthetic library IDs/geometry. This queue is probe-only and
    # is never returned to stage/execute or included in a prepared run bundle.
    ir = {'schemaVersion': '1.4', 'projectId': target['project_uuid'], 'documentId': target['document_uuid'],
          'components': [{'id': 'compat-resistor', 'ref': 'R1',
                          'device': {'libraryUuid': '1'*32, 'deviceUuid': '2'*32, 'name': 'SYNTHETIC PROBE ONLY'},
                          'pins': [{'number': '1', 'name': '1'}, {'number': '2', 'name': '2'}]}],
          'nets': [{'id': 'a', 'name': 'A'}, {'id': 'b', 'name': 'B'}],
          'connections': [{'componentId': 'compat-resistor', 'pinNumber': n, 'netId': net, 'kind': 'netlist'} for n,net in (('1','a'),('2','b'))],
          'modules': [{'id': 'compat-module', 'name': 'SYNTHETIC PROBE', 'coreComponents': ['compat-resistor']}]}
    source = {'schemaVersion': 1, 'connectivity': ir,
              'sheet': {'minX': 0, 'minY': 0, 'maxX': 1170, 'maxY': 825}, 'keepouts': [],
              'modules': [{'id': 'compat-module', 'title': 'SYNTHETIC PROBE',
                           'placements': [{'primitiveId': 'synthetic-probe', 'designator': 'R1', 'value': 'SYNTHETIC',
                                           'x': 100, 'y': 100, 'rotation': 0, 'mirror': False,
                                           'bbox': {'minX': 90, 'minY': 90, 'maxX': 110, 'maxY': 110},
                                           'pins': [{'number': n, 'name': n, 'net': net, 'x': x, 'y': 100} for n,net,x in (('1','A',70),('2','B',130))]}],
                           'wires': [], 'flags': [], 'terminals': [{'designator': 'R1', 'pin': n, 'direction': direction, 'kind': 'net_port_bi'} for n,direction in (('1','left'),('2','right'))]}]}
    report = {'passed': False, 'level': 'offline-compatibility-only', 'synthetic': True,
              'snapshot_sha256': file_digest(root / 'before.json'), 'receipts': []}
    try:
        sheets = [c for c in result(snapshot)['components'] if c.get('componentType') == 'sheet']
        if len(sheets) != 1: raise Refused('compatibility probe requires one measured physical sheet')
        bounds = sheets[0].get('bbox', {})
        if (not isinstance(bounds, dict) or any(type(bounds.get(k)) not in (int, float) or not math.isfinite(bounds[k])
                for k in ('minX', 'minY', 'maxX', 'maxY'))
                or bounds['minX'] >= bounds['maxX'] or bounds['minY'] >= bounds['maxY']):
            raise Refused('compatibility probe requires measured sheet bbox')
        source['sheet'] = {k: bounds[k] for k in ('minX', 'minY', 'maxX', 'maxY')}
        dump(root / 'synthetic-composition.json', source)
        report['version'] = adapter.run(['--version'], json_output=False)['stdout'].strip()
        for command in (
            ['sch', 'compose', '--from', root/'synthetic-composition.json', '--before', root/'before.json',
             '--out', root/'computed.json', '--playbook', root/'probe-only.json', '--replace'],
            ['sch', 'apply', root/'probe-only.json', '--dry-run']):
            receipt = adapter.run(command, json_output=False)
            report['receipts'].append(receipt)
        report['passed'] = True
    except Exception as error:
        report['error'] = str(error)
        dump(root / 'report.json', report)
        raise Refused('upstream snapshot compatibility probe failed before placement: ' + str(error)) from error
    dump(root / 'report.json', report)
    return report
