"""Common component reference cards, sharing existing source and circuit identities."""
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit

from .core import Refused, load
from .knowledge import catalog as circuit_catalog, normalized, search as circuit_search

CATALOG = Path(__file__).parent / 'data' / 'components.json'
JLC_DOMAINS = ('lceda.cn', 'lceda.com', 'easyeda.com', 'jlc.com', 'jlcpcb.com',
               'szlcsc.com', 'lcsc.com', 'lckfb.com', 'oshwhub.com')


def text(value):
    return isinstance(value, str) and bool(value.strip())


def strings(value):
    return isinstance(value, list) and all(text(item) for item in value)


def source_index(data, circuits):
    sources, urls = {}, set()
    if not isinstance(data.get('sources'), list):
        raise Refused('component sources must be a list')
    for source in [*circuits['sources'], *data['sources']]:
        if (not isinstance(source, dict) or not all(text(source.get(key)) for key in ('id', 'title', 'url'))):
            raise Refused('component source requires an ID, title and URL')
        try:
            parsed = urlsplit(source['url'])
            port = parsed.port
        except ValueError as error:
            raise Refused('invalid component source URL') from error
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname
                or parsed.username is not None or parsed.password is not None):
            raise Refused('component source requires a web URL')
        authority = parsed.netloc.lower()
        if port == {'http': 80, 'https': 443}[parsed.scheme]:
            authority = authority.rsplit(':', 1)[0]
        url = urlunsplit((parsed.scheme, authority, parsed.path.rstrip('/'), parsed.query, ''))
        if source['id'] in sources or url in urls:
            raise Refused('duplicate source ID/URL across component and circuit knowledge')
        if source.get('role') not in ('core', 'auxiliary') or (source.get('ecosystem') != 'jlc' and source['role'] != 'auxiliary'):
            raise Refused('non-JLC component sources must be auxiliary')
        sources[source['id']] = source
        urls.add(url)
    return sources


def jlc_primary(source):
    if not source or source.get('ecosystem') != 'jlc' or source.get('role') != 'core':
        return False
    parsed = urlsplit(source['url'])
    host = parsed.hostname or ''
    if any(host == domain or host.endswith('.' + domain) for domain in JLC_DOMAINS):
        return True
    return host == 'github.com' and parsed.path.strip('/').split('/')[0].casefold() == 'easyeda'


def check_component_catalog(data, circuits=None):
    circuits = circuit_catalog() if circuits is None else circuits
    if (not isinstance(data, dict) or type(data.get('schema_version')) is not int or data['schema_version'] != 1
            or not text(data.get('version')) or not isinstance(data.get('components'), list) or not data['components']):
        raise Refused('invalid component catalog')
    sources = source_index(data, circuits)
    if not strings(data.get('shared_rule_ids')) or not set(data['shared_rule_ids']) <= circuits['rules'].keys():
        raise Refused('component catalog has unknown shared rules')
    topics = {topic['id'] for topic in circuits['topics']}
    ids, aliases = set(), {}
    for card in data['components']:
        if (not isinstance(card, dict) or not text(card.get('id'))
                or not re.fullmatch(r'component\.[a-z][a-z0-9-]*', card['id'])
                or not text(card.get('title')) or not text(card.get('version')) or card.get('status') != 'reference-only'):
            raise Refused('invalid component card identity or capability')
        if card['id'] in ids:
            raise Refused('duplicate component ID')
        ids.add(card['id'])
        for key in ('aliases', 'subtypes', 'uses', 'connection_guidance', 'selection_checks', 'common_mistakes',
                    'auxiliary_source_ids', 'related_topics', 'rule_ids'):
            if not strings(card.get(key)):
                raise Refused('component card requires a text list: ' + key)
        if any(not card[key] for key in ('subtypes', 'uses', 'connection_guidance', 'selection_checks', 'common_mistakes')):
            raise Refused('component card guidance is incomplete')
        for name in [card['id'], card['title'], *card['aliases']]:
            key = normalized(name)
            if not key or key in aliases and aliases[key] != card['id']:
                raise Refused('duplicate or empty component alias: ' + name)
            aliases[key] = card['id']
        if not text(card.get('primary_source_id')):
            raise Refused('component primary source requires a text ID')
        primary = sources.get(card['primary_source_id'])
        if not jlc_primary(primary):
            raise Refused('component primary source must be JLC core')
        if (not set(card['auxiliary_source_ids']) <= sources.keys()
                or not set(card['related_topics']) <= topics or not set(card['rule_ids']) <= circuits['rules'].keys()):
            raise Refused('component card has dangling source, circuit or rule references')
        evidence = card.get('evidence')
        if (not isinstance(evidence, list) or not evidence
                or any(not isinstance(entry, dict) or not text(entry.get('source_id')) or entry['source_id'] not in sources
                       or not text(entry.get('section')) for entry in evidence)
                or card['primary_source_id'] not in {entry['source_id'] for entry in evidence}):
            raise Refused('component card requires located primary evidence')
        calculations = card.get('calculations')
        if (not isinstance(calculations, list) or any(not isinstance(item, dict)
                or not text(item.get('expression')) or not text(item.get('conditions')) for item in calculations)):
            raise Refused('component calculations require formulas and conditions')
    return {'valid': True, 'component_count': len(ids), 'component_ids': sorted(ids),
            'component_catalog_version': data['version'], 'component_source_count': len(data['sources']),
            'total_source_count': len(sources)}


def catalog(circuits=None):
    data = load(CATALOG)
    check_component_catalog(data, circuits)
    return data


def entries(circuits):
    data = catalog(circuits)
    sources = source_index(data, circuits)
    return [{**card, 'kind': 'component', 'automatic_plan_generation': False,
             'shared_rule_ids': data['shared_rule_ids'],
             'sources': [sources[sid] for sid in sorted({card['primary_source_id'],
                         *card['auxiliary_source_ids'], *[entry['source_id'] for entry in card['evidence']]})]}
            for card in data['components']]


def search(query, parameters=None, *, kind='all'):
    if (not isinstance(query, str) or kind not in ('all', 'component', 'circuit')
            or parameters is not None and not isinstance(parameters, dict)):
        raise Refused('search requires text, all/component/circuit kind and optional parameter object')
    q = normalized(query)
    if not q:
        return {'matches': []}
    records = ([{**row, 'kind': 'circuit'} for row in circuit_search(query, parameters)['matches']]
               if kind != 'component' else [])
    if kind != 'circuit':
        records.extend({**row, 'missing_parameters': []} for row in entries(circuit_catalog()))
    exact, concepts, partial = [], [], []
    for row in records:
        names = [normalized(name) for name in [row['id'], row['title'], *row['aliases']]]
        details = [normalized(value) for value in [*row.get('subtypes', []), *row.get('uses', [])]]
        if q in names:
            exact.append(row)
        elif q in details:
            concepts.append(row)
        elif any(q in name for name in names + details):
            partial.append(row)
    return {'matches': exact or concepts or partial}
