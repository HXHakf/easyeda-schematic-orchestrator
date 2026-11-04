"""Read-only native inventory diff; only attribute primitiveId is ephemeral.

This does not validate electrical intent, allow an edit, or certify measurement.
Raw inputs are never rewritten. All fields other than attribute primitiveId,
including unknown fields, formulas, nulls and duplicate multiplicity, are compared.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys


REQUIRED_CLASSES = {'components', 'wires', 'buses', 'arcs', 'circles', 'rectangles',
                    'polygons', 'texts', 'attributes', 'objects'}


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':'))


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key: ' + key)
        result[key] = value
    return result


def inventory(snapshot):
    if not isinstance(snapshot, dict) or snapshot.get('ok') is not True:
        raise ValueError('successful raw snapshot envelope required')
    context = snapshot.get('context')
    if not isinstance(context, dict) or not all(isinstance(context.get(k), str) and context[k]
                                              for k in ('projectUuid', 'documentUuid')):
        raise ValueError('exact project/document context required')
    result = snapshot.get('result')
    page = result.get('pagePrimitives') if isinstance(result, dict) else None
    if not isinstance(page, dict) or not REQUIRED_CLASSES <= page.keys():
        raise ValueError('complete native pagePrimitives inventory required')
    for kind, rows in page.items():
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError('native primitive classes must be arrays of records: ' + kind)
    ids = []
    for row in page['attributes']:
        if not all(isinstance(row.get(k), str) and row[k]
                   for k in ('primitiveId', 'ParentPrimitiveId', 'Key')):
            raise ValueError('attribute identity, owner and key required')
        ids.append(row['primitiveId'])
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate native attribute IDs')
    canonical(page)  # Reject non-JSON values instead of normalizing them.
    return (context['projectUuid'], context['documentUuid']), page


def records(rows, ignore_attribute_id=False):
    return Counter(canonical({k: v for k, v in row.items() if k != 'primitiveId'}
                             if ignore_attribute_id else row) for row in rows)


def expanded(counter):
    return [json.loads(value) for value, count in sorted(counter.items()) for _ in range(count)]


def compare(before, after):
    before_target, left = inventory(before)
    after_target, right = inventory(after)
    if before_target != after_target:
        raise ValueError('snapshot target changed')
    if left.keys() != right.keys():
        raise ValueError('native class schema changed; require explicit schema review')
    differences = {}
    for kind in sorted(left):
        a = records(left[kind], kind == 'attributes')
        b = records(right[kind], kind == 'attributes')
        if a != b:
            differences[kind] = {'removed': expanded(a-b), 'added': expanded(b-a)}
    return {
        'schemaVersion': 1,
        'scope': 'native-page-primitives-with-attribute-id-churn',
        'target': {'projectUuid': before_target[0], 'documentUuid': before_target[1]},
        'rawEqual': canonical(before) == canonical(after),
        'semanticEqual': not differences,
        'attributeIdsChanged': Counter(r['primitiveId'] for r in left['attributes']) != Counter(r['primitiveId'] for r in right['attributes']),
        'changedClasses': list(differences),
        'differences': differences,
        'normalization': 'Array order ignored; only attributes[].primitiveId omitted. No value/type/visibility/formula defaults.',
        'unverified': ['electrical target/netlist/direct intent', 'measurement completeness',
                       'geometry/rendered clearance', 'canonical library identity resolution',
                       'DRC', 'persistence', 'hardware'],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', type=Path, required=True)
    parser.add_argument('--after', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    try:
        inputs = {name: path.read_bytes() for name, path in [('before', args.before), ('after', args.after)]}
        report = compare(*(json.loads(inputs[name].decode('utf-8-sig'), object_pairs_hook=unique_object) for name in ['before', 'after']))
        report['inputSha256'] = {name: hashlib.sha256(raw).hexdigest() for name, raw in inputs.items()}
        with args.out.open('x', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
        return 0 if report['semanticEqual'] else 1
    except (OSError, ValueError, TypeError) as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
