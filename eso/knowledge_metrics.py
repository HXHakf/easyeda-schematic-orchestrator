"""Compare user-measured runs only when task and verification scope match."""
import math
from collections import defaultdict
from statistics import mean
from .core import Refused

METRICS = ('elapsed_seconds','question_rounds','connection_errors')


def compare_runs(records):
    if isinstance(records, dict):
        from .knowledge_metrics_v2 import compare_measured
        return compare_measured(records)
    if not isinstance(records,list): raise Refused('metrics must be a list of measured run records')
    groups=defaultdict(lambda: defaultdict(list))
    for row in records:
        if not isinstance(row,dict) or set(row) != {'case_id','workflow','scope',*METRICS}:
            raise Refused('metrics record requires case_id, workflow, scope and three metrics')
        if not isinstance(row['case_id'],str) or not row['case_id'].strip(): raise Refused('case_id required')
        if row['workflow'] not in ('legacy','knowledge') or row['scope'] not in ('offline','live'):
            raise Refused('workflow must be legacy/knowledge; scope must be offline/live')
        for key in METRICS:
            value=row[key]
            if value is None: continue  # Unknown measurements must never become zero.
            if isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value) or value<0:
                raise Refused('invalid measured metric: '+key)
            if key!='elapsed_seconds' and not isinstance(value,int): raise Refused('counts must be integers: '+key)
        groups[(row['case_id'],row['scope'])][row['workflow']].append(row)
    comparisons, unpaired=[],[]
    for (case,scope), variants in sorted(groups.items()):
        if set(variants)!= {'legacy','knowledge'}:
            unpaired.append({'case_id':case,'scope':scope,'available':sorted(variants)}); continue
        means={name:{key:mean(r[key] for r in rows) if all(r[key] is not None for r in rows) else None
                     for key in METRICS} for name,rows in variants.items()}
        delta={key:means['knowledge'][key]-means['legacy'][key]
               if means['knowledge'][key] is not None and means['legacy'][key] is not None else None for key in METRICS}
        comparisons.append({'case_id':case,'scope':scope,'samples':{k:len(v) for k,v in variants.items()},
                            'means':means,'delta':delta})
    return {'comparisons':comparisons,'unpaired':unpaired,'delta_definition':'knowledge minus legacy; missing observations remain null'}
