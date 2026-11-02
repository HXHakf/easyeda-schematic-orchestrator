#!/usr/bin/env python3
"""Offline workflow demonstration. Never creates approval or writes to EDA."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from eso.core import digest, dump
from eso.requirements import contract_from, contract_summary, evaluate, parse_seed
from eso.planner import make_plan
from eso.workflow import validate_plan

p=argparse.ArgumentParser()
p.add_argument("--out",default="runs/offline-demo")
args=p.parse_args()
root=Path(args.out)
seed=parse_seed("希望用 STM32 做一个 I2C 传感器原理图，缺什么先问我")
dump(root/"01-seed.json",seed)
dump(root/"02-questions.json",evaluate(seed))
complete={"schema_version":1,"raw_request":"离线演示：STM32F103C8T6 与 BMP280 裸芯片，外部稳压3.3V，I2C",
          "mcu":"STM32F103C8T6","sensor":"BMP280","sensor_form":"bare-chip","input_voltage_v":3.3,"power_source":"external-regulated"}
dump(root/"03-example-answers.json",complete)
report=evaluate(complete);dump(root/"04-assessment.json",report)
contract=contract_from(report["values"]);dump(root/"05-contract-DRAFT.json",contract)
(root/"05-contract-DRAFT.md").write_text(contract_summary(contract))
plan=make_plan(contract);validate_plan(plan,contract);dump(root/"06-plan-DRAFT.json",plan)
dump(root/"status.json",{"level":"offline-verified","state":"awaiting-human-confirmation",
     "contract_sha256":digest(contract),"approval_created":False,"EDA_calls":0,
     "next":"Review semantics and contract; only for an explicitly requested live demo bind a test target, reconfirm changed contract and collect upstream measurements."})
print(f"Offline workflow artifacts: {root.resolve()}; no approval, no EDA calls")
