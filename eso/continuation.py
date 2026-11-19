"""Host-facing next steps. These hints never authorize writes or certify delivery."""
from pathlib import Path

from .core import Refused, load


def action(task, owner='assistant'):
    return {'owner': owner, 'task': task}


def assessment_action(report):
    if report.get('next_questions'):
        return action('复用会话已有答案，只询问仍需用户决定且影响方案的条件；同时推进独立查证。', 'user')
    if report.get('research_tasks') or report.get('selection_tasks'):
        return action('执行 research_tasks 和 selection_tasks，补齐依据并复评。')
    if report['ready']:
        return action('生成并展示完整方案与契约，集中取得一次方案确认。')
    return action('检查 errors 和未满足的依赖；定位需用户决定的具体参数前先用现有资料排查。')


def run_status(directory):
    root = Path(directory)
    bundle = root / 'bundle.json'
    stage = root / 'stage.json'
    if bundle.is_file():
        record = load(bundle)
        state = record['state']
        tasks = {
            'prepared': '构造并检查该候选的实测 scene，在已确认范围内 execute --verify。',
            'applied-unverified': '执行 verify，保存重开并回读本次写入。',
            'verified': '完成本轮独立图面审阅、候选比较与 deliver，核对请求的交付物。',
            'incomplete': '读取本轮错误、journal 和 verification.json（若有），诊断后选择复验或受保护修订。',
            'verifying': '核对是否有活跃进程及最新 attempt，中断后从 verify 恢复。',
        }
        if state == 'prepared' and (root / 'execution-started.lock').exists():
            task = '执行已开始。核对活跃进程、journal 与新鲜回读，依据现场诊断恢复。'
        elif state == 'prepared' and (root / 'drawing-preflight.json').is_file() and load(root / 'drawing-preflight.json').get('passed') is False:
            task = '修正写前检查报告中的图面问题，重新编译候选并复查。'
        else:
            task = tasks.get(state)
    elif stage.is_file():
        record = load(stage)
        task = {
            'placing': '核对活跃进程、放置 journal 和新鲜回读；中断时依据归属恢复。',
            'incomplete': '诊断放置 journal 及实际器件，必要时 recover-stage 到新目录。',
            'placed': '执行 collect，测量已放置器件并生成布局候选。',
            'measuring': '核对测量进程和产物，确认中断后从 collect 恢复。',
            'measurement-incomplete': '检查测量回执和布局求解错误，修正输入、器件摆放或分组后接续布局。',
            'layout-compiled': '检查候选，使用已测量快照和 placement-run 执行 prepare。',
        }.get(record['state'])
    else:
        raise Refused('no stage.json or bundle.json in the selected run directory')
    return {**record, 'next_action': action(task or '检查运行记录和版本，核实当前阶段。')}


def iteration_action(state):
    tasks = {
        'designing': '读取候选问题，修正图面并记录下一次实际评估。',
        'qualified': '保留合格基线，开始额外一轮候选比较。',
        'extra-comparison': '完成本轮候选比较并记录 extra-result。',
        'restoring': '从受保护源恢复已合格版本，保存重开并重新验收。',
        'complete': '以选定版本和同轮审阅执行 deliver，核对实际交付物。',
        'incomplete': '核对 stop_reason 与现场状态；可恢复时记录 resume 后继续。'
    }
    return action(tasks[state])
