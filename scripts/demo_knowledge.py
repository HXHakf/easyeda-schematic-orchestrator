#!/usr/bin/env python3
"""Synthetic offline knowledge demo. No confirmation, real selection or EDA call."""
import argparse
import sys
from pathlib import Path
from time import perf_counter
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from eso.core import dump, file_digest, load
from eso.requirements import contract_from, contract_summary, evaluate
from eso.planner import make_plan
from eso.knowledge_design import make_bindings
from eso.workflow import validate_plan


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',default='runs/knowledge-demo')
    args=parser.parse_args()
    root=Path(args.out).resolve()
    if root.exists(): parser.error('choose a new output directory to preserve earlier evidence')
    root.mkdir(parents=True)
    started=perf_counter()
    for name in ('npn','nmos'):
        out=root/name; out.mkdir()
        requirements=load(Path(__file__).resolve().parents[1]/'examples/knowledge'/f'{name}.json')
        for material in requirements['knowledge']['materials']:
            path=out/(material['id']+'.txt')
            path.write_text('SYNTHETIC TEST DOCUMENT: '+material['id'],encoding='utf-8')
            material['datasheet'].update(path=str(path),sha256=file_digest(path))
        dump(out/'requirements.json',requirements)
        report=evaluate(requirements); dump(out/'assessment.json',report)
        contract=contract_from(requirements); dump(out/'contract.json',contract)
        (out/'contract.md').write_text(contract_summary(contract),encoding='utf-8')
        plan=make_plan(contract); validate_plan(plan,contract)
        dump(out/'plan.json',plan); dump(out/'bindings.json',make_bindings(contract))
    dump(root/'status.json',{'state':'awaiting-human-confirmation','level':'offline-verified-synthetic',
                           'EDA_calls':0,'approval_created':False,'elapsed_seconds':perf_counter()-started,
                           'question_rounds':None,'connection_errors':None,
                           'unverified':['real-component-selection','live-eda','simulation','pcb','hardware']})
    print('Synthetic knowledge drafts: '+str(root)+'; no EDA calls or human approval generated')
    return 0


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(main())
