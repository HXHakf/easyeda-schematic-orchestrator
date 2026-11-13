"""Shape checks kept separate from evidence and electrical diagnostics."""
import math
from jsonschema import Draft202012Validator
from .knowledge_rules import PARAMS

STRING = {'type':'string'}
MAPPING = {'type':'object','additionalProperties':STRING}


def obj(properties, required=()):
    return {'type':'object','properties':properties,'required':list(required),'additionalProperties':False}


DOCUMENT = obj({k:STRING for k in ('path','sha256','model','package','revision','pinout_section','reviewed_by')})
LIBRARY = obj({**{k:STRING for k in ('libraryUuid','deviceUuid','verified_model','verified_package','expected_name','evidence')},'pins':MAPPING})
MATERIAL = obj({**{k:STRING for k in ('id','model','package','kind')},'datasheet':DOCUMENT,'pins':MAPPING,
                'facts':{'type':'object','additionalProperties':{'type':'number'}},'fact_sections':MAPPING,'library':LIBRARY})
CIRCUIT = obj({**{k:STRING for k in ('id','topic','variant')},'roles':MAPPING,'ports':MAPPING,
               'parameters':obj({**{k:{'type':'number'} for k in PARAMS},'default_state':STRING})},
              ('id','topic','variant','roles','ports','parameters'))
SCHEMA = obj({'materials':{'type':'array','items':MATERIAL},'circuits':{'type':'array','items':CIRCUIT}},('materials','circuits'))


def input_issues(data):
    issues = []
    def issue(path, message):
        issues.append({'field':'knowledge.'+'.'.join(map(str,path)), 'question':message,'reason':'输入结构无效'})
    for e in Draft202012Validator(SCHEMA).iter_errors(data): issue(e.path,e.message)
    def walk(value,path):
        if isinstance(value,float) and not math.isfinite(value): issue(path,'数值必须有限')
        elif isinstance(value,dict):
            for k,v in value.items(): walk(v,[*path,k])
        elif isinstance(value,list):
            for i,v in enumerate(value): walk(v,[*path,i])
    walk(data,[])
    return issues
