# 应用电路知识库

常用元件的基础知识与 25 类覆盖见[元件知识库](component-knowledge.md)。本页说明应用电路及兼容的首版资料格式。
新增资料复用、功能条件与 MCU 组合使用 [v2 流程](knowledge-v2.md)。

应用知识库以嘉立创资料组织通用主题。具体项目以用户所选模块的官方数据手册及应用电路为设计基线，元件卡补充连接与检查方法。
现有主题、规则与资产索引位于 `eso/data/knowledge.json`，元件基础索引位于 `eso/data/components.json`。
来源跨目录按 ID 复用，重复 ID 或 URL 会被检查拒绝。保存原创摘要和引用定位。

## 支持范围

| 主题 | 当前能力 |
|---|---|
| NPN 低侧开关 | 自动检查并生成 `resistive-dc` 静态电阻负载方案 |
| NMOS 低侧开关 | 自动检查并生成 `resistive-dc` 静态电阻负载方案 |
| 电源防反接 | 知识卡与候选变体，可检索，暂不自动生成 |
| ADC 分压采样 | 知识卡与检查要点，可检索，暂不自动生成 |
| GPIO LED 指示 | 通用灌电流连接、限流计算与错误反例，嘉立创主来源已齐备 |
| I²C 开漏电平转换 | 嘉立创开漏／上拉规则与原厂双 MOS 连接参考，嘉立创主来源已齐备 |

两个可执行主题均限共地、默认关闭、静态 DC、正电源不超过 60 V、控制高电平不超过 12 V。每个实例有开关、串联驱动电阻、下拉电阻和电阻负载四个角色。PWM、感性负载、容性浪涌、负载模块自带保护、电源时序、多个电源参考地之间的接口、共享上拉自动合并，需要新增经审阅的变体。当前不会把未实现的拓扑当成已验证方案。

生成的电路通过 `supply`、`ground`、`control` 网络与外部供电和控制电路连接。首版不自动添加 MCU、连接器或其他供电模块，必须核实外部端口与设计意图一致。知识库首版仅接入 `circuit_type=generic` 独立电路，已有 STM32/BMP280 和 LDO 需求流程保持原行为。

## 用户输入与宿主职责

知识库维护和通用检索阶段使用主题、别名、连接、公式、错误反例和来源。具体项目进入设计阶段后，
再填写以下器件与工况输入。kb-search 返回的 missing_parameters 用于展示项目应用所需的条件。

用户提供所选模块或器件型号、功能和工作条件，已有手册可一并提供。宿主负责检索并下载缺少的官方手册，
填写内部本地路径，按[官方手册与应用电路](input-and-questions.md#官方手册与应用电路)审阅应用图、型号与连接，
读取真实器件库后整理 `requirements.knowledge`。所有器件（包括被动件）都需要对应资料和库证据。清单可在多轮中补齐，`assess` 集中返回缺项。

Python 只检查文件是否存在、内容哈希、摘录身份的一致性、物理脚映射、参数条件与已编码公式。它不会从 PDF 自动判断“这确实是该型号”，也不能证明宿主录入的限值正确。`reviewed_by` 是责任记录，不是软件认证。

输入结构以 `eso/knowledge_input.py` 为准。两份完整的**虚构离线示例**位于 `examples/knowledge/npn.json` 和 `nmos.json`。示例型号、UUID 和参数不是实际选型，不能用于真实 EDA。

`knowledge.materials` 每项包含：

- `id`：清单内唯一器件资料 ID。同一器件可被多个角色引用，引用不意味着合并物理器件。
- `model`、`package`、`kind`：完整型号、封装和 `npn` / `nmos` / `resistor`。
- `datasheet`：绝对 `path`、文件 `sha256`、已读出的 `model`/`package`、`revision`、`pinout_section` 和 `reviewed_by`。
- `pins`：语义脚到物理编号的完整映射。例如 NPN 使用 B/E/C，NMOS 使用 G/S/D，电阻使用 1/2。实际编号来自具体封装手册。
- `facts` 和 `fact_sections`：数值及每个数值的章节/页码与工作条件定位。单位写在字段名中。
- `library`：真实 `libraryUuid`、`deviceUuid`、`expected_name`、`verified_model`、`verified_package`、`pins`、查询核验 `evidence`。与已读手册和实际库器件一致后才能确认。

`knowledge.circuits` 每项包含 `id`、唯一 `topic`、`variant`、角色到资料 ID 的 `roles`、`ports` 和 `parameters`。实例 ID 与网络名使用字母开头的字母/数字/下划线。内部网络由实例 ID 隔离，不能用外部端口覆盖。

## 参数与电气核验

每个实例提供最大供电 `supply_voltage_v`，控制端保证的 `control_high_min_v` / `control_high_max_v` / `control_low_max_v`，`gpio_source_limit_ma`，高阻态 GPIO 最大漏电 `gpio_leakage_max_ua`，`ambient_max_c`，`frequency_hz=0` 与 `default_state=off`。电流限额应是考虑 GPIO 端口/芯片总预算后分配给该网络的预算。

| 参数组 | 必需事实及含义 |
|---|---|
| 所有电阻 | `resistance_ohm`、`tolerance_fraction`（0.01 表示 1%）、`power_limit_w`、`voltage_limit_v`、`rated_ambient_c` |
| 开关通用 | `voltage_limit_v`、`current_limit_a`、`power_limit_w`、`rated_ambient_c`、`off_input_max_v`、`input_leakage_max_a` |
| NPN | `vbe_min_v`、`vbe_max_v`、`vcesat_max_v`、`sat_test_ic_a`、`sat_test_ib_a` |
| NMOS | `rds_on_max_ohm`、`rds_test_vgs_v`、`gate_voltage_limit_v` |

`*_limit_*` 必须是经过降额、热条件与安全工作区审阅的工作限值。`rated_ambient_c` 表示摘录的全部保证和限值在该环境温度内适用，不能简单填入最大结温。MOS 的 RDS(on) 需覆盖实际温度及工作条件，不能直接采用 25°C 典型值。`off_input_max_v` 是在所需漏电/负载关闭标准下经审阅的输入上限，不能把阈值电压当成该保证。

计算采用电阻公差的保守边界。NPN 按饱和测试 IC/IB 核验最小基极电流，并扣除下拉分流。NMOS 按串联电阻、下拉与漏电计算最低栅压，再与 RDS(on) 的保证测试栅压比较。两者均检查电压、电流、功耗、温度、关断条件与高阻默认态。MOS 还使用 VOH_max/Rseries_min 对初始栅极充电峰值作保守限流检查，此项不等于开关瞬态分析。

共用控制网络时叠加各支路 GPIO 电流上界。共用电源网络必须具有相同电压和参考地。共控高阻态同时检查各支路漏电耦合的保守电压上界。共享网络定义只保留一份，各支路的串联电阻、下拉、负载和开关保留独立实例。首版不接受隐式共享外围器件声明。

## 命令与操作顺序

```sh
eso kb-check
eso kb-search '三极管低侧驱动'
eso kb-search 'NMOS 低侧开关' --parameters runs/parameters.json
eso assess runs/requirements.json --out runs/assessment.json
eso contract runs/requirements.json --out runs/contract.json
eso plan runs/contract.json --out runs/plan.json
eso kb-bindings runs/contract.json --out runs/bindings.json
eso validate-plan runs/plan.json --contract runs/contract.json
```

`kb-search` 返回唯一主题、变体条件、所需参数/缺项和来源定位。候选主题可以检索，但不会通过自动生成检查。`kb-check` 检查来源、别名、资产、主题、规则引用是否重复或悬空。`assess` 的 `ready` 必须由调用者检查，沿用原 CLI 的退出码习惯。

生成草稿不会创建 approval。实际执行前阅读中文确认页，补入获准空白测试工程的 `target_project` 与 target 文件，重新生成 contract。取得对该版本的明确确认后，沿用既有 `confirm → stage → collect → prepare → execute → verify → deliver`。不自动清理当前页面，不自动选第一个在线工程。

知识卡、来源表、输入条件、资料内容或库绑定变化会使知识证据不再匹配。`confirm`、`stage`、`collect`、`prepare`、`execute`、`verify` 和 `deliver` 的相关校验会重新检查资料。生成的 plan 和 bindings 需要按新 contract 重建，不直接修改自动生成计划来绕过核验。

文件路径与运行产物只保存到私有 `runs/`。plan 用内容哈希引用本地资料，避免直接包含绝对路径；contract 和交付清单仍可能包含私有路径与工程信息，不直接公开。

## 离线演示、测量与维护

```sh
python scripts/demo_knowledge.py --out runs/knowledge-demo
python -m unittest discover -s tests -v
python scripts/demo_workflow.py --out runs/legacy-demo
```

知识库演示生成 NPN/NMOS 两套虚构草稿、bindings、检查报告和状态，不连接 EDA、不伪造用户确认。已存在的输出目录会被拒绝，避免覆盖证据。

`eso kb-metrics records.json --out comparison.json` 读取实测记录列表。每条包含 `case_id`、`workflow`（legacy/knowledge）、`scope`（offline/live）、`elapsed_seconds`、`question_rounds`、`connection_errors`。未测指标写 `null`。仅比较相同 case_id 与 scope 的两种流程，输出均值与 knowledge 减 legacy 的差值。样例 `examples/knowledge/metrics.json` 为空，不预设提效结论。

新增内容先搜索主题和别名。相同功能/拓扑并入现有卡，参数差异由输入表达，连接结构或适用条件差异作为变体。来源/资产登记一次，主题按 ID 引用。每个可执行变体必须有嘉立创主来源、辅助证据、正反测试和完整拓扑，复核代码生成器支持后才能标为可执行。资料冲突在卡内保留，未解决时停用生成；解决后更新版本与依据。仿真、EDA、PCB、实物分别记录，不能互相替代。

嘉立创的原生复用模块和 AI 检索扩展继续作为后续适配入口。本版使用已有 ESO plan/bindings 接口，不依赖安装这些扩展，也尚未验证原生 CBB 资产导入。
