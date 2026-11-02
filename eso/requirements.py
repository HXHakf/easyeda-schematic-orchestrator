"""Profile-based questions and dependency-aware candidate defaults."""
import re
from copy import deepcopy
from .core import Refused, validate

PROMPTS = {
    'circuit_type': '想画哪类电路：MCU 最小系统、MCU + 传感器、LDO 电源，还是其他功能？',
    'function_description': '电路要实现什么功能？请说明输入、输出及主要约束。',
    'mcu': 'MCU 的完整型号和封装是什么？也可以回答“帮我选”。',
    'sensor': '传感器的准确型号是什么？未选型可回答“帮我选”，并说明要测什么。',
    'sensor_form': '传感器使用裸芯片还是已有模块？模块可能自带上拉。',
    'input_voltage_v': '输入电压是多少 V？',
    'power_source': '输入来自稳压电源、USB 还是电池？',
    'logic_voltage_v': 'MCU/传感器的逻辑电源是多少 V？',
    'target_project': '获准写入的可丢弃测试工程名称是什么？',
    'pullup_ohm': '已有或拟新增上拉的有效阻值是多少 Ω？',
    'i2c_pullups': '总线需要新增上拉，还是模块/其他器件已有上拉？',
    'bus_capacitance_pf': '快速 I2C 的估算总线电容是多少 pF？',
    'clock': '使用内部时钟还是外部晶振？需核实所选 MCU 的时钟能力。',
    'debug': '需要保留哪种调试接口？',
    'crystal_hz': '外部晶振频率是多少 Hz？',
    'output_voltage_v': 'LDO 需要输出多少 V？',
    'output_current_ma': '输出最大负载电流是多少 mA？',
    'regulator_model': 'LDO 的完整型号是什么？也可以回答“帮我选”。',
}
PROFILE_NAMES = {'mcu-sensor': 'MCU + 传感器', 'mcu-minimum': 'MCU 最小系统',
                 'ldo-power': 'LDO 电源', 'generic': '其他电路'}
CHOOSABLE = {'mcu', 'sensor', 'regulator_model'}
PROFILE_FIELDS = {
    'mcu-minimum': {'mcu', 'clock', 'debug', 'crystal_hz', 'logic_voltage_v', 'bus', 'bus_speed_hz', 'i2c_pullups', 'pullup_ohm', 'bus_capacitance_pf'},
    'mcu-sensor': {'mcu', 'clock', 'debug', 'crystal_hz', 'logic_voltage_v', 'sensor', 'sensor_form', 'bus', 'bus_speed_hz', 'i2c_pullups', 'pullup_ohm', 'bus_capacitance_pf'},
    'ldo-power': {'output_voltage_v', 'output_current_ma', 'regulator_model'},
    'generic': set(),
}


def parse_seed(text):
    """Seed extraction only. Model semantic review still handles negation and unknown constraints."""
    values = {'schema_version': 1, 'raw_request': text}
    upper = text.upper()
    mcus = set(re.findall(r'STM32[A-Z0-9]+', upper))
    sensors = set(re.findall(r'(?<![A-Z0-9])(?:BMP280|BME280|SHT30|SHT31)(?![A-Z0-9])', upper))
    if len(mcus) == 1: values['mcu'] = mcus.pop()
    if len(sensors) == 1: values['sensor'] = sensors.pop()
    if re.search(r'\bLDO\b|线性稳压', upper): values['circuit_type'] = 'ldo-power'
    elif 'STM32' in upper or values.get('mcu'):
        values['circuit_type'] = 'mcu-sensor' if re.search(r'传感器|SENSOR|BMP280|BME280|SHT3[01]', upper) else 'mcu-minimum'
    if re.search(r'裸芯片|裸片|BARE.CHIP', upper) and not re.search(r'模块|MODULE', upper):
        values['sensor_form'] = 'bare-chip'
    elif re.search(r'传感器模块|SENSOR MODULE|BMP280\s*模块|BME280\s*模块', upper):
        values['sensor_form'] = 'module'
    volts = set(float(v) for v in re.findall(r'(?<![\d.])(\d+(?:\.\d+)?)\s*V(?![A-Z0-9])', upper))
    if len(volts) == 1: values['input_voltage_v'] = volts.pop()
    if re.search(r'已稳压|外部稳压|REGULATED', upper): values['power_source'] = 'external-regulated'
    if re.search(r'I[²2]C', upper): values['bus'] = 'i2c'
    speed = re.search(r'(\d+)\s*KHZ', upper)
    if speed: values['bus_speed_hz'] = int(speed[1]) * 1000
    if re.search(r'(无需|不用|不要|不需要)\s*外部晶振', upper): values['clock'] = 'internal'
    elif re.search(r'外部晶振|EXTERNAL CRYSTAL', upper): values['clock'] = 'external-crystal'
    elif re.search(r'内部时钟|内部RC|INTERNAL', upper): values['clock'] = 'internal'
    if re.search(r'已有上拉|现有上拉|EXISTING PULL', upper): values['i2c_pullups'] = 'existing'
    projects = re.findall(r'(?<![A-Z0-9])ESO-TEST-[A-Z0-9_-]+', upper)
    if len(set(projects)) == 1: values['target_project'] = projects[0]
    return values


def merge_answers(requirements, answers):
    """Explicit answers supersede candidate defaults, including an explicitly reaffirmed value."""
    values = deepcopy(requirements)
    if 'circuit_type' in answers and answers['circuit_type'] != values.get('circuit_type'):
        relevant = PROFILE_FIELDS.get(answers['circuit_type'], set())
        if answers['circuit_type'] != 'generic':
            for field in set().union(*PROFILE_FIELDS.values()) - relevant:
                values.pop(field, None)
    selections = set(values.get('selection_requests', []))
    if 'circuit_type' in answers and answers['circuit_type'] != 'generic':
        selections &= PROFILE_FIELDS.get(answers['circuit_type'], set())
    for key, value in answers.items():
        if key == 'default_provenance': raise Refused('answers cannot supply default provenance')
        if key in CHOOSABLE and value in ('帮我选', 'help-me-choose'):
            values.pop(key, None); selections.add(key)
        elif value is None:
            values.pop(key, None); selections.discard(key)
        else:
            values[key] = value
            selections.discard(key)
    values['selection_requests'] = sorted(selections)
    values['default_provenance'] = [d for d in values.get('default_provenance', []) if d['field'] not in answers]
    validate(values, 'requirements')
    return values


def evaluate(requirements, require_target=False):
    validate(requirements, 'requirements')
    v = deepcopy(requirements)
    # Rebuild every previously inferred field from its current dependencies. An explicit
    # changed value is preserved; merge_answers also handles reaffirming the same value.
    for record in v.pop('default_provenance', []):
        if record['field'] in v and v[record['field']] == record['value']:
            v.pop(record['field'])
    defaults, questions, selections = [], [], []
    def default(key, value, reason, dependencies=()):
        if key not in v:
            v[key] = value
            defaults.append({'field': key, 'value': value, 'reason': reason,
                             'depends_on': {k: deepcopy(v.get(k)) for k in dependencies}})
    def need(key, reason, priority=20):
        if key in v: return
        if key in CHOOSABLE and key in v.get('selection_requests', []):
            selections.append({'field': key, 'reason': reason, 'action': '根据功能与约束查证候选型号，再补入准确型号及证据'})
        else:
            questions.append({'field': key, 'question': PROMPTS[key], 'reason': reason, 'priority': priority})
    if 'circuit_type' not in v:
        if v.get('mcu') and v.get('sensor'):
            default('circuit_type', 'mcu-sensor', '根据已给出的 MCU 与传感器识别类型', ('mcu', 'sensor'))
        elif v.get('mcu'):
            default('circuit_type', 'mcu-minimum', '根据已给出的 MCU 识别最小系统候选', ('mcu',))
        elif v.get('regulator_model'):
            default('circuit_type', 'ldo-power', '根据已给出的 LDO 型号识别类型', ('regulator_model',))
    need('circuit_type', '先明确电路类型，避免发送不相关的问题', 0)
    profile = v.get('circuit_type')
    if profile:
        if profile != 'generic':
            need('input_voltage_v', '决定电源与器件电气条件', 5)
            need('power_source', '区分稳压、USB 与电池输入', 5)
        default('passive_package', '0603', '初始装配封装候选，需在设计摘要确认', ('circuit_type',))
    if profile in ('mcu-minimum', 'mcu-sensor'):
        need('mcu', '完整型号与封装决定引脚、供电和时钟', 10)
        if str(v.get('mcu', '')).upper().startswith('STM32'):
            default('clock', 'internal', 'STM32 内部时钟初始候选，执行前核实资料', ('mcu',))
            default('debug', 'swd', 'STM32 初始保留 SWD', ('mcu',))
        elif 'mcu' in v:
            need('clock', '不能假设所有 MCU 都有可用内部时钟')
            need('debug', '调试接口由 MCU 能力决定')
        if v.get('power_source') == 'external-regulated' and v.get('input_voltage_v') == 3.3:
            default('logic_voltage_v', 3.3, '外部稳压 3.3V 直接供逻辑电源', ('power_source', 'input_voltage_v'))
        else: need('logic_voltage_v', '输入与逻辑电压可能不同', 10)
        if v.get('clock') == 'external-crystal': need('crystal_hz', '晶振频率为条件必填')
    if profile == 'mcu-sensor':
        need('sensor', '必须满足实际测量功能，不能默认替代型号', 10)
        need('sensor_form', '裸芯片与模块的外围条件不同', 15)
        default('bus', 'i2c', '当前传感器流程的接口候选', ('circuit_type',))
    if profile == 'ldo-power':
        need('output_voltage_v', '输出电压决定稳压器规格', 5)
        need('output_current_ma', '负载电流影响选型、压差与散热', 5)
        need('regulator_model', '需核实压差、稳定性电容与封装', 15)
    if profile == 'generic': need('function_description', '由功能生成进一步的器件/接口条件问题', 5)
    if selections and profile in ('mcu-minimum', 'mcu-sensor') and not v.get('constraints'):
        need('function_description', '选型前需明确要实现的功能与约束', 10)
    if profile != 'ldo-power' and v.get('bus') == 'i2c':
        default('bus_speed_hz', 100000, '初始标准模式候选', ('bus',))
        if profile == 'mcu-sensor' and v.get('sensor_form') == 'bare-chip':
            default('i2c_pullups', 'new', '裸芯片候选新增上拉，仍需确认总线已有器件', ('circuit_type', 'sensor_form'))
        elif profile != 'mcu-sensor' or v.get('sensor_form') == 'module':
            need('i2c_pullups', '防止与模块已有上拉重复')
        if v.get('i2c_pullups') == 'new' and v.get('bus_speed_hz') == 100000:
            default('pullup_ohm', 4700, '短总线 100kHz 初始候选，非通用保证', ('i2c_pullups', 'bus_speed_hz', 'sensor_form'))
        elif 'i2c_pullups' in v: need('pullup_ohm', '需明确有效上拉阻值')
        if v['bus_speed_hz'] > 100000: need('bus_capacitance_pf', '快速模式需检查 RC 上升时间')
    if require_target: need('target_project', '真实执行必须绑定获准测试工程', 30)
    questions.extend(dict(q, priority=0) for q in v.get('unresolved', []))
    questions.sort(key=lambda q: q.get('priority', 20))
    errors = []
    if profile in ('mcu-minimum', 'mcu-sensor') and all(k in v for k in ('input_voltage_v', 'logic_voltage_v')):
        if v.get('power_source') == 'external-regulated' and v['input_voltage_v'] != v['logic_voltage_v']:
            errors.append('输入与逻辑电源不同，需明确稳压/电平转换拓扑')
    if profile == 'ldo-power' and all(k in v for k in ('input_voltage_v', 'output_voltage_v')):
        if v['input_voltage_v'] <= v['output_voltage_v']: errors.append('LDO 输入必须高于输出，并按具体型号校验压差')
    if profile != 'ldo-power' and v.get('bus') == 'i2c' and all(k in v for k in ('pullup_ohm', 'bus_capacitance_pf')):
        tr_ns = 0.8473 * v['pullup_ohm'] * v['bus_capacitance_pf'] / 1000
        limit = 1000 if v['bus_speed_hz'] <= 100000 else 300
        if tr_ns > limit: errors.append(f'I2C RC 上升时间估算 {tr_ns:.1f}ns 超过 {limit}ns；需调整参数')
    v['default_provenance'] = defaults
    return {'values': v, 'defaults': defaults, 'questions': questions, 'next_questions': questions[:3],
            'selection_tasks': selections, 'errors': errors, 'ready': not questions and not selections and not errors,
            'semantic_review_required': True, 'profile': profile}


def contract_from(requirements, target=None):
    report = evaluate(requirements)
    if not report['ready']: raise Refused('requirements incomplete: ' + str(report['questions'] or report['selection_tasks'] or report['errors']))
    contract = {'schema_version': 1, 'requirements': report['values'], 'defaults': report['defaults'],
                'scope': 'test-project-only', 'acceptance': ['exact-pin-net-nc', 'native-drc', 'structural-check',
                'bridge-check', 'geometry', 'save-reload-readback'], 'exclusions': ['pcb', 'firmware', 'hardware-validation']}
    if target:
        if target['project_name'] != report['values'].get('target_project'): raise Refused('target project differs from requirements')
        contract['target'] = target
    validate(contract, 'contract')
    return contract


def contract_summary(contract):
    """Human-facing content; the agent manages hashes separately."""
    r = contract['requirements']
    lines = ['# 原理图设计确认', '', f"电路类型：{PROFILE_NAMES.get(r.get('circuit_type'), '待核实')}。", '']
    labels = {'function_description': '目标功能', 'mcu': 'MCU', 'sensor': '传感器', 'sensor_form': '传感器形态',
              'input_voltage_v': '输入电压（V）', 'power_source': '供电来源', 'logic_voltage_v': '逻辑电压（V）',
              'output_voltage_v': '输出电压（V）', 'output_current_ma': '最大输出电流（mA）', 'regulator_model': 'LDO 型号',
              'bus': '接口', 'bus_speed_hz': '总线频率（Hz）', 'i2c_pullups': '上拉方案', 'pullup_ohm': '上拉阻值（Ω）',
              'clock': '时钟', 'crystal_hz': '晶振频率（Hz）', 'debug': '调试接口', 'passive_package': '被动件封装'}
    translations = {'bare-chip':'裸芯片','module':'模块','external-regulated':'外部稳压电源','usb':'USB','battery':'电池',
                    'internal':'内部时钟','external-crystal':'外部晶振','swd':'SWD','none':'不保留','new':'新增一对上拉','existing':'使用已有上拉','i2c':'I2C'}
    for k, label in labels.items():
        if k in r: lines.append(f"- {label}：{translations.get(str(r[k]), r[k])}")
    if r.get('constraints'):
        lines += ['', '其他约束：', *[f'- {x}' for x in r['constraints']]]
    if contract['defaults']:
        lines += ['', '以下为可修改的候选默认值：', *[f"- {labels.get(d['field'], d['field'])}：{translations.get(str(d['value']), d['value'])}；{d['reason']}" for d in contract['defaults'] if d['field'] != 'circuit_type']]
    target = contract.get('target')
    lines += ['', f"执行目标：{target['project_name']}（已内部绑定具体页面）。" if target else '执行目标：未绑定；当前仅为离线草稿。',
              '', '验收：逐脚连接/NC、规则、桥接和几何检查，保存并重载后重新回读。',
              '不包含：PCB、固件与硬件实测。', '', '请确认上述设计或说明要修改的内容。']
    return '\n'.join(lines) + '\n'
