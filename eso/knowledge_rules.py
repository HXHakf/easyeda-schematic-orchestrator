"""Conservative DC checks; limits are reviewed operating limits, not typical values."""
import math

COMMON = ('voltage_limit_v','current_limit_a','power_limit_w','rated_ambient_c','off_input_max_v','input_leakage_max_a')
NPN = ('vbe_min_v','vbe_max_v','vcesat_max_v','sat_test_ic_a','sat_test_ib_a')
MOS = ('rds_on_max_ohm','rds_test_vgs_v','gate_voltage_limit_v')
RESISTOR = ('resistance_ohm','tolerance_fraction','power_limit_w','voltage_limit_v','rated_ambient_c')
PARAMS = ('supply_voltage_v','control_high_min_v','control_high_max_v','control_low_max_v',
          'gpio_source_limit_ma','ambient_max_c','frequency_hz','gpio_leakage_max_ua')


def _check_switch(topic, materials, p):
    errors = []
    def err(field,message): errors.append((field,message))
    def number(value): return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)
    for key in PARAMS:
        v = p.get(key)
        if not number(v): err('parameters.' + key, '缺少有限数值 ' + key)
        elif key in ('ambient_max_c',):
            if not -40 <= v <= 125: err('parameters.' + key,'环境温度超出首版范围 -40..125°C')
        elif key in ('control_low_max_v','frequency_hz','gpio_leakage_max_ua'):
            if v < 0: err('parameters.' + key,'参数不能为负')
        elif v <= 0: err('parameters.' + key, '参数必须大于零')
    if p.get('frequency_hz') != 0: err('parameters.frequency_hz','仅支持静态 DC，PWM 需要动态损耗/驱动核验')
    if p.get('default_state') != 'off': err('parameters.default_state','首版仅支持下拉默认关闭')
    mos = topic == 'nmos-low-side-switch'
    values = {}
    for role in ('switch','series','bias','load'):
        m = materials.get(role)
        if not isinstance(m,dict): err('roles.'+role,'缺少器件'); continue
        f, sections = m.get('facts'), m.get('fact_sections')
        if not isinstance(f,dict) or not isinstance(sections,dict): err('roles.'+role+'.facts','需要参数摘录和章节'); continue
        required = COMMON + (MOS if mos else NPN) if role == 'switch' else RESISTOR
        for key in required:
            v = f.get(key)
            if not number(v): err('roles.'+role+'.facts.'+key,'需要手册保证值/审阅后的工作限值 '+key)
            elif abs(v) > 1e12 or (v != 0 and abs(v) < 1e-12):
                err('roles.'+role+'.facts.'+key,'参数超出支持的数值范围 1e-12..1e12')
            elif key == 'tolerance_fraction':
                if not 0 <= v <= .2: err('roles.'+role+'.facts.'+key,'阻值公差应为 0..0.2 比例')
            elif key == 'rated_ambient_c':
                if not -40 <= v <= 125: err('roles.'+role+'.facts.'+key,'需明确首版温度范围内的适用性')
            elif key == 'input_leakage_max_a':
                if v < 0: err('roles.'+role+'.facts.'+key,'漏电流上限不能为负')
            elif v <= 0: err('roles.'+role+'.facts.'+key,'保证值/工作限值需大于零')
            if not isinstance(sections.get(key),str) or not sections[key].strip():
                err('roles.'+role+'.fact_sections.'+key,'缺少该参数的手册章节及条件审阅依据')
        values[role] = f
    if errors: return {}, errors
    s, rs, rb, load = (values[k] for k in ('switch','series','bias','load'))
    low, high = p['control_high_min_v'], p['control_high_max_v']
    if not p['control_low_max_v'] < low <= high <= 12:
        err('parameters','需 VOL_max < VOH_min <= VOH_max <= 12V')
    if p['supply_voltage_v'] > 60: err('parameters.supply_voltage_v','首版仅支持 60V 以内低压电路')
    def bounds(r): return r['resistance_ohm']*(1-r['tolerance_fraction']), r['resistance_ohm']*(1+r['tolerance_fraction'])
    rs_min, rs_max = bounds(rs); rb_min, rb_max = bounds(rb); load_min, _ = bounds(load)
    current = p['supply_voltage_v']/load_min
    leakage = s['input_leakage_max_a'] + p['gpio_leakage_max_ua']/1e6
    if leakage*rb_max > s['off_input_max_v']:
        err('roles.bias','高阻默认态漏电压超过关断上限，需调整下拉')
    if mos:
        gate_min = (low - s['input_leakage_max_a']*rs_max) * rb_min / (rs_max+rb_min)
        gate_max = high * rb_max / (rs_min+rb_max) + s['input_leakage_max_a']*rs_max
        gpio = high/rs_min  # Turn-on charging-current conservative upper bound.
        power = current**2*s['rds_on_max_ohm']
        if gate_min < s['rds_test_vgs_v']: err('roles.switch','实际最低 VGS 未覆盖 RDS(on) 保证测试栅压')
        if gate_max > s['gate_voltage_limit_v']: err('roles.switch','最高 VGS 超过审阅后的工作限值')
        off = p['control_low_max_v'] * rb_max/(rs_min+rb_max)
        details = {'gate_min_v':gate_min,'gate_max_v':gate_max,'rds_on_max_ohm':s['rds_on_max_ohm']}
    else:
        if not s['vbe_min_v'] <= s['vbe_max_v'] < low:
            err('roles.switch','VBE 边界无效或最低控制电压不足')
        ratio = s['sat_test_ic_a']/s['sat_test_ib_a']
        base_min = (low-s['vbe_max_v'])/rs_max-s['vbe_max_v']/rb_min
        gpio = max(0, (high-s['vbe_min_v'])/rs_min)
        power = current*s['vcesat_max_v']+gpio*s['vbe_max_v']
        if current > s['sat_test_ic_a']: err('roles.switch','负载电流超过本次饱和保证测试条件')
        if ratio <= 1 or base_min < current/ratio: err('roles.series','基极驱动不足，需按饱和 IC/IB 与下拉分流核验')
        off = p['control_low_max_v']
        details = {'base_current_min_a':base_min,'base_current_required_a':current/ratio,
                   'series_resistance_max_ohm':(low-s['vbe_max_v'])/(current/ratio+s['vbe_max_v']/rb_min)}
    off = max((p['control_low_max_v']+s['input_leakage_max_a']*a)*b/(a+b)
              for a in (rs_min,rs_max) for b in (rb_min,rb_max))
    if off > s['off_input_max_v']: err('roles.switch','低电平超过已审阅的关断输入上限')
    if gpio > p['gpio_source_limit_ma']/1000: err('parameters.gpio_source_limit_ma','GPIO 最大输出电流不足')
    if current > s['current_limit_a']: err('roles.switch','负载电流超过工作限值')
    if p['supply_voltage_v'] > s['voltage_limit_v']: err('roles.switch','供电超过开关工作电压限值')
    if power > s['power_limit_w']: err('roles.switch','开关功耗超过已审阅的散热工作限值')
    resistor_power = {'series':high**2/rs_min,'bias':high**2/rb_min,'load':p['supply_voltage_v']**2/load_min}
    for role in ('switch','series','bias','load'):
        f = values[role]
        if p['ambient_max_c'] > f['rated_ambient_c']: err('roles.'+role,'温度超过参数、降额与散热依据适用范围')
        if role != 'switch':
            voltage = p['supply_voltage_v'] if role == 'load' else high
            if voltage > f['voltage_limit_v'] or resistor_power[role] > f['power_limit_w']:
                err('roles.'+role,'电阻电压或功耗超过工作限值')
    return {'load_current_max_a':current,'gpio_current_max_a':gpio,'switch_power_max_w':power,
            'resistor_power_max_w':resistor_power,**details}, errors


def check_switch(topic, materials, parameters):
    try:
        calculations, errors = _check_switch(topic, materials, parameters)
        def finite(value):
            return all(finite(v) for v in value.values()) if isinstance(value, dict) else math.isfinite(value)
        if not finite(calculations):
            return {}, [('parameters', '计算超出有限数值范围，请核对单位与参数')]
        return calculations, errors
    except (OverflowError, ZeroDivisionError):
        return {}, [('parameters', '计算超出支持数值范围，请核对单位与参数')]
