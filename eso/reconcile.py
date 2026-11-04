"""Compare exact physical pin intent against canonical upstream 1.4 readback."""


def checked_inventory(ir):
    """Validate references before reducing any list to a dictionary."""
    if ir.get('schemaVersion') != '1.4':
        raise ValueError('canonical schemaVersion 1.4 required')
    for field in ('components', 'nets', 'connections'):
        if not isinstance(ir.get(field), list): raise ValueError('missing array: ' + field)
    def index(rows, key):
        values = {}
        for row in rows:
            ident = row[key]
            if not isinstance(ident, str) or not ident or ident in values:
                raise ValueError('missing or duplicate ' + key)
            values[ident] = row
        return values
    components = index(ir['components'], 'id')
    index(ir['components'], 'ref')
    nets = index(ir['nets'], 'id')
    net_keys = set()
    for net in nets.values():
        if not isinstance(net.get('name'), str) or not net['name'].strip():
            raise ValueError('net name missing')
        key = (net['name'], net.get('scope'))
        if key in net_keys: raise ValueError('ambiguous duplicate net name/scope')
        net_keys.add(key)
    pins = {}
    for cid, component in components.items():
        if not isinstance(component.get('pins'), list) or not component['pins']:
            raise ValueError('physical pin array missing')
        for num, pin in index(component['pins'], 'number').items():
            if 'noConnected' in pin and type(pin['noConnected']) is not bool:
                raise ValueError('NC must be boolean')
            pins[cid, num] = pin
    edges = {}
    for edge in ir['connections']:
        key = (edge['componentId'], edge['pinNumber'])
        if key not in pins: raise ValueError('connection references absent component/pin')
        if edge['netId'] not in nets: raise ValueError('connection references absent net')
        if key in edges: raise ValueError('duplicate/ambiguous pin connection')
        if pins[key].get('noConnected') is True: raise ValueError('NC pin has a connection')
        net = nets[edge['netId']]
        edges[key] = (net['name'], net.get('scope'))
    return components, edges


def reconcile(expected, actual):
    differences, unknown = [], []
    try:
        ec, ee = checked_inventory(expected)
        ac, ae = checked_inventory(actual)
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        return {'passed': False, 'differences': ['invalid connectivity inventory: ' + str(error)],
                'unverified': [], 'coverage': 'inventory validation failed'}
    if expected.get("projectId") != actual.get("projectId") or expected.get("documentId") != actual.get("documentId"):
        differences.append("wrong project/document identity")
    if len(ac) != len(actual.get("components", [])):
        differences.append("duplicate actual component IDs")
    for c in sorted(ec.keys() - ac.keys()): differences.append(f"missing component {c}")
    for c in sorted(ac.keys() - ec.keys()): differences.append(f"unexpected component {c}")
    if set(ee) != set(ae): differences.append('complete connected-pin inventory differs')
    net_inventory = lambda ir: {(n['name'], n.get('scope')) for n in ir['nets']}
    if net_inventory(expected) != net_inventory(actual): differences.append('complete net name/scope inventory differs')
    if len(ae) != len(actual.get("connections", [])):
        differences.append("duplicate/ambiguous actual pin connections")
    for cid in sorted(ec.keys() & ac.keys()):
        e, a = ec[cid], ac[cid]
        if e["ref"] != a.get("ref"):
            differences.append(f"{cid}: designator changed")
        for key in ("libraryUuid", "deviceUuid"):
            if not a.get("device", {}).get(key): unknown.append(f"{cid}: missing {key}")
            elif e["device"][key] != a["device"][key]: differences.append(f"{cid}: {key} changed")
        ep, ap = {p["number"]: p for p in e["pins"]}, {p["number"]: p for p in a.get("pins", [])}
        if set(ep) != set(ap) or len(ap) != len(a.get("pins", [])):
            differences.append(f"{cid}: physical pin inventory differs")
        for num in ep.keys() & ap.keys():
            key = (cid, num)
            desired_nc = ep[num].get("noConnected") is True
            actual_nc = ap[num].get("noConnected") is True
            if desired_nc != actual_nc: differences.append(f"{cid}:{num}: NC differs")
            if ee.get(key) != ae.get(key): differences.append(f"{cid}:{num}: net {ae.get(key)!r} != {ee.get(key)!r}")
            if not actual_nc and key not in ae:
                unknown.append(f"{cid}:{num}: no confirmed net or NC")
    for issue in actual.get("issues", []):
        if issue.get("severity", "").lower() in ("error", "fatal"):
            differences.append(f"upstream IR issue: {issue}")
    return {"passed": not differences and not unknown, "differences": differences, "unverified": unknown,
            "coverage": "component identity, physical-pin inventory, NC and net names; geometry/ownership checked separately upstream"}
