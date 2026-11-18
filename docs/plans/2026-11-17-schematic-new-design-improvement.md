# 原理图新建设计工作流改进计划

> **执行者要求** 使用 `subagent-driven-development` 或 `executing-plans` 逐项实施。任务采用复选框跟踪，每项完成后检查其接口与回归结果。

**目标** 从一句功能需求出发，在一次完整电气方案确认后，自主完成选型、原理图绘制、修正和独立审阅，并通过隔离 EDA 工程实画验收。

**架构** 保留宿主模型的需求理解、资料审阅与设计职责，由现有 Python CLI 校验结构、连接意图、证据和状态。绘图继续复用 `easyeda-agent` 的实测布局、完整 composition、受保护 Apply 和保存重开。新增能力接入正常入口及交付路径。

**技术栈** Python ≥3.10、现有 jsonschema Draft 2020-12、unittest、EasyEDA typed CLI、原生 SVG/PNG 和已安装的文字测量环境。

**设计依据** 本文“已确认的范围”记录 2026-11-17 用户确认的访谈结论。实现时同时读取 `AGENTS.md`、`CONTRIBUTING.md` 和各任务列出的既有入口。

## 已确认的范围

1. 优先支持从功能需求开始的新建原理图。
2. 只向用户追问显著影响方案、选型和成本的关键条件。手册事实由宿主查证，工程默认值记录依据与依赖。装配要求按项目确认并沿用。
3. 默认核验嘉立创/立创商城可采购料号，比较包含必要外围的完整 BOM 成本。
4. 绘图前集中确认一次完整电气方案，此后在已确认范围内自主完成图面迭代。
5. 同页主供电与主要信号路径尽量连续直连，其他跨模块连接使用标签。零无接点交叉、零文字碰撞，阅读清楚优先于进一步缩小面积。
6. 方案和最终导图各增加独立审阅。图面达标后保留合格版本，再比较一轮候选，新增候选通过验收后才可选用。
7. 升级验收包括离线回归，以及隔离 EDA 工程中的完整实画、保存重开和最终原生导图检查。
8. 首个样例由本轮访谈授权选定为单节锂电池供电、USB-C 充电、Wi-Fi 温湿度采集板。工作流结束点为原理图交付。

## 全局约束

- 复用现有解释器、依赖、锁文件、CLI 与 Connector，执行前核实实际版本和能力。
- 原始快照、库身份、实际物理脚号、net/NC、图面坐标和回执保持真实，并保留不可用字段的原值。
- 用户已有答案与既有授权按适用范围复用，普通布局迭代沿用电气方案确认。
- 关键电气意图、型号或工况变化重新计算影响，更新设计依据和对应确认。
- 旧契约、旧运行和历史报告保持可读，按其原有规则核验。新流程的通过结论需要新流程的证据。
- 新建页面的各次修订使用独立运行目录，复用目标锁、原子写入和新鲜基线检查。
- 所有现场动作限定在本轮隔离测试工程。资料与现场证据保存到忽略的私有运行目录，公开文件使用合成数据与相对路径。
- 源码、离线验证、现场持久化验收及实物验证分别报告实际覆盖范围。

## 基线与现有缺口

2026-11-17 核对安装态 `eso` 为 0.6.2，`easyeda-agent` 为 1.17.0。本次重新运行 229 项主测试和 76 项附加审计测试，全部通过。主测试包含真实上游的离线编译与 dry-run。

| 入口 | 当前事实 | 本轮需要补齐 |
|---|---|---|
| `eso/requirements.py::evaluate` | generic 主要要求功能说明，结构化“帮我选”只覆盖 MCU、传感器和 LDO | 通用需求审阅、事实检索任务、任意器件角色的选型任务 |
| `eso/materials.py` | 严格材料结构面向有限器件类别与已编码电路 | 通用设计依据复用资料摘要及来源定位，保留已有材料库适用范围 |
| `eso/planner.py::make_plan` | 固定模板及有限知识库组合 | 继续支持宿主依据资料编写完整 plan，并补齐通用方案核验 |
| `eso/bridge.py::build_layout_input` | 按模块布局，跨模块信号通常转成端口，电源转为本地标记 | 主路径的明确声明和可执行上游投影 |
| `readability_audit.py::module_structure` | 跨模块物理线树一律失败 | 精确到网络、页面、物理端点的主路径例外 |
| `eso/workflow.py::prepare` | 接受空白页或本轮完全未接线的 staged 页 | 本轮新建图纸接线后的受保护布局修订 |
| `eso/assistance.py::compare_layouts` | 先比折弯、线长和模块框面积 | 当前候选的独立图面判断进入选择依据 |
| `eso/evidence.py` | 核验 geometry/visual 与当前输入绑定 | 通用电气方案审阅、实际独立审阅记录及模块意图对账 |
| 当前编排 | 有局部候选预算和超时 | 首次达标、额外比较、最佳合格结果和恢复状态的统一记录 |

## 复用方案与实现选择

采用在现有编排器中增加契约、修订和审阅能力的方案。通用设计继续由宿主生成 source-backed plan，确定性检查负责明确不变量。这能复用既有库核验、实测文字、受保护编译和逐脚对账。

主路径采用“逻辑模块与绘图分组分别记录”的适配方案。逻辑模块继续表达外围归属，需要连续直连的功能块显式组成联合绘图区，再交给上游现有模块编译。每个联合绘图区使用真实外框及反映所含功能的标题，审计框与实际导图逐项对应。

上游文档和当前 `sch compose --help` 已说明按模块提供导线与标记，以及保留实例的图面重建能力。联合分组能否完整编译、保留多核心和生成主路径，列为任务 1 的前置离线探针。

嘉立创专业版的网络标签体现为导线名称属性，拖动网络名的设置会影响连接行为。主路径验收使用物理连接树与原生回读，标签位置及网名都纳入新鲜检查。[网络标签官方说明](https://prodocs.lceda.cn/cn/schematic/place-net-label/)

器件级审阅以所选具体型号的手册和官方清单为依据。Wi-Fi 样例的检查项按实际选定芯片或模块确定，覆盖供电、复位、启动配置、下载调试及外部接口。乐鑫的型号专属原理图清单可作为组织审阅项的参考。[ESP32 原理图检查清单](https://docs.espressif.com/projects/esp-hardware-design-guidelines/en/latest/esp32/schematic-checklist.html)

## 重点复核

以下五类条件分别由所属任务增加正反测试。

1. 旧记录与新入口混用时，保留原确认摘要，并明确实际采用的流程规则。
2. 某段主路径获准后，同网第三模块分支、其他网络和跨页导线仍须独立核对授权。
3. 器件移动、属性更新和保存重开可能使文字坐标或实例标识变化，最终审阅绑定新导图。
4. Apply 超时、部分成功或人工并发修改后，先读取现场并重新编译，恢复保持幂等。
5. 最佳历史方案与当前现场不同、额外候选失败或审阅文件错版时，当前交付状态需要真实恢复与复验。

## 数据与接口约定

### 新流程标记与设计依据

- `requirements.design_review.policy_version = 1` 标记新流程。新 `intake` 默认生成该标记和待分析状态。
- 无该标记的旧输入按原路径评估，返回 `review_mode = legacy`。新标记存在时，完成需求审阅、关键决策和必要资料任务后才可 ready。
- 私有 `design-review.json` 记录功能与工况、适用性、用户决策、有据默认、装配要求、选型、完整 BOM、来源引用和 `electrical_intent`。
- `electrical_intent` 包含功能模块、器件及物理脚、net/NC、核心外围归属和主路径。其规范化摘要排除 `contract_sha256`、坐标、绘图分组及审阅报告自身。绘图分组调整在既有电气意图和主路径范围内自主完成。
- `contract.design_evidence` 固定 `policy_version`、设计依据与独立电气审阅的文件摘要。先审阅电气意图，再生成契约及用户确认，正式 plan 最后补入契约摘要。
- 资料文件按内容摘要核验，单纯移动缓存文件后可凭相同摘要重新定位。电气意图变化使对应审阅和确认失效。

### 主路径与绘图分组

`plan.primary_paths[]` 使用 `id`、`title` 和有序 `links`。每段 link 包含 `from`、`to`、`net`，端点使用实际 component ID 与物理脚号。跨器件的不同网络分别声明。

`plan.drawing_groups[]` 使用 `id`、`title`、`modules`。每个逻辑模块唯一归属一个绘图分组，分组由设计明确声明。旧源缺省为逻辑模块一对一映射。

- 新增 `eso/main_paths.py::validate_primary_paths(plan)`，校验端点、同网、非 NC、归属及完整分组。
- 新增 `eso/main_paths.py::project_drawing_groups(plan)`，供 `bind_plan`、`check_source`、bridge 和证据检查复用同一投影。
- 上游 canonical 1.4 仅接收其已支持字段，ESO 保存原逻辑模块与投影对应关系。
- 新 scene 的 `moduleFrames` 项使用 `drawingGroupId` 标识实际联合外框。旧项继续使用 `moduleId`。两种标识互斥，覆盖检查根据对应映射执行。
- 主路径逐段检查同页物理连通，并核对所在物理树接触到的全部逻辑模块端点。允许端点之间的声明关系，额外跨模块分支继续返回具体问题。
- 单纯更改补充审计 target 的 ownership、主路径或分组无法扩大范围，交付时需与契约固定的电气意图和当前受保护 plan 对账。候选分组变化保留电气意图，并使其旧几何与图面证据失效。

### 独立审阅与迭代

- 电气审阅绑定 `electrical_intent`、BOM、来源与计算摘要，图面审阅绑定 candidate 或现场 attempt、snapshot、SVG 和预览摘要。
- 宿主实际调用独立上下文的审阅角色，提供原始资料及检查对象，留存调用记录和逐项结论。程序验证身份声明、摘要和问题关闭状态。
- `eso/iteration.py` 保存契约摘要、父运行、候选记录、最佳已验收运行、首次达标 attempt、额外比较状态及停止原因。
- `record_iteration(root, event)` 在锁内验证状态转换并原子写入。CLI 提供 `iteration-record` 和 `iteration-status`，宿主继续负责候选提出和实际工具调度。
- 初次达标后完成一轮额外候选比较。保留已合格候选，新候选失败或更差时继续使用合格候选。
- `design_qualification(run_dir, reviews)` 组合已有核心资格、独立电气/图面审阅与当前几何证据，返回 `qualified` 和 `issues`。首次达标据此判定，额外比较状态单独记录。新流程最终 `deliver` 还须核验额外比较已完成、最佳结果位于当前现场及其新鲜持久化证据。
- 候选预算和单次调用超时沿用现有配置。超时先诊断并恢复，进程重启根据记录继续。持续无改善时调整局部画法或模块组织，真实缺参或工具能力缺口应报告具体阻塞。

## 实施任务

### 任务 1　证明上游可执行路径

**文件** 扩展 `tests/test_upstream_cli.py`，必要的合成构造器放在同一测试模块。记录接口结论到 `references/upstream-adapter.md`。

**接口** 输入两组逻辑模块、明确的同网主路径和现有合成库几何，输出上游 composition、playbook 与离线校验回执。

- [ ] 添加 `test_joint_drawing_group_preserves_primary_wire`。同一 canonical 绘图分组包含原有核心与外围，器件清单和 net/NC 守恒，主路径导线进入源与受保护队列。
- [ ] 添加 `test_owned_wired_revision_compiles_with_preserved_instances`。从合成已接线快照经 `compose --before --replace --preserve-instances` 编译，保留原生实例及完整属性，生成受保护图面重建队列。
- [ ] 对生成队列运行现有 `validate_queue` 和 `apply --dry-run`。记录当前接口支持范围、完整 reset 判据和实际框数量。
- [ ] 新增丢失成员、重复归属及错误 net/NC 反例。断开的主路径由任务 3 的物理审计拒绝。
- [ ] 固定可执行的数据映射，再进入任务 3 和任务 4。若上游不能表达所需 source，记录最小复现及具体缺失字段，先修该依赖再继续对应任务。

```sh
EASYEDA_BIN=easyeda python -m unittest discover -s tests -p test_upstream_cli.py -v
```

验收要求新增正例成功编译并 dry-run，反例明确失败，现场写入次数为零。

### 任务 2　通用需求、完整选型依据与绘前确认

**文件** 新增 `eso/design_review.py`、`tests/test_design_review.py`。修改 `eso/requirements.py`、`eso/cli.py`、`eso/core.py`、`eso/workflow.py`、`schemas/requirements.json`、`schemas/contract.json`。同步 `references/input-and-questions.md`、`references/design-contract.md` 和主 `SKILL.md`。

**接口** `assess_design_review(requirements)` 输出 `questions`、`research_tasks`、`selection_tasks`、`defaults`、`ready`。`validate_design_evidence(contract, plan=None)` 重新核对已固定文件、意图及独立审阅。

- [ ] 添加 `test_new_intake_requires_review_beyond_function_text`。新 `intake → assess` 仅有功能说明时保持未完成，关键用户问题至多三项，可查事实进入检索任务。
- [ ] 添加 `test_design_evidence_binds_intent_without_digest_cycle`。完成审阅后正常生成 contract、确认并验证 plan。纯坐标变化沿用确认，选型、物理脚或关键参数变化使其失效。
- [ ] 实现需求适用性和有据默认检查。供电、负载、接口及装配按当前功能逐项说明适用性，复用项目已有答案，条件变化撤销受影响的推断。
- [ ] 实现任意器件角色的选型任务和来源清单。保留严格知识库专属计算路径，通用设计依据通过文件摘要、型号/封装、引脚资料和应用电路定位核验。
- [ ] `stage/prepare` 固定设计依据、独立审阅及资料清单到运行目录，再核验副本摘要并纳入 bundle。原资料位置变化通过内容摘要核对，资料内容变化按证据失效处理。
- [ ] BOM 保存料号、数量、单价、计价梯度、币种、查询时间和原始来源。候选比较采用相同采购数量条件，覆盖必要外围和外接件，未知报价显示缺项与已知小计。
- [ ] 添加外围遗漏、数量梯度不同、同料号不同封装和湿度功能选错传感器的反例。成本缺项影响已约定预算或候选比较时，保持对应成本结论未完成。
- [ ] 中文确认页展示功能框图、核心选型、供电与接口、装配、完整 BOM 成本状态、主路径、来源和默认假设。独立电气审阅先于集中确认完成。
- [ ] 保持旧输入的原字节与摘要。增加 legacy/new policy 混用、孤立标记、资料变化、空审阅和伪造通过布尔的回归。

```sh
python -m unittest discover -s tests -p test_design_review.py -v
python -m unittest discover -s tests -p test_profiles.py -v
python -m unittest discover -s tests -p test_workflow.py -v
```

验收要求新流程通过完整 CLI 入口闭环，现有模板和旧契约回归保持原结果。

### 任务 3　主路径连接规则贯通生成、检查与交付

**依赖** 任务 1 和任务 2。

**文件** 新增 `eso/main_paths.py`、`tests/test_main_paths.py`。修改 `schemas/plan.json`、`eso/planner.py`、`eso/bridge.py`、`eso/workflow.py`、`eso/evidence.py`、`extras/schematic-verified-layout/scripts/circuit_audit.py` 和 `extras/schematic-verified-layout/scripts/readability_audit.py`。同步 `references/measurement-bridge.md`，以及附属技能下的 `SKILL.md`、`references/audit-data.md`、`references/drawing-grammar.md` 和 `references/readability-gate.md`。

**接口** 输入经过确认的逻辑模块、主路径和分组，经共享投影生成上游模块。输出包含实际线树、分组外框和真实文字的完整候选及审计 scene。

- [ ] 测试 `validate_primary_paths` 与 `project_drawing_groups`。保留 `attach` 的原逻辑模块约束，验证每个声明端点、网络、成员和唯一归属。
- [ ] `bind_plan`、`check_source`、测量 bridge 和交付索引统一复用投影。主路径输入上游 direct 策略，其他边界沿用标签策略。
- [ ] 主路径逐段复用已有无标签物理接触图检查。扩展精确授权检查，加入“允许 A–B 后同网连接未授权 C”“同模块对的另一网络实体跨接”“跨页主路径”和“同名标签代替实体线”反例。
- [ ] 同网选取 direct 策略后，核对该树接触的全部端点。当前上游策略粒度覆盖整个网络，额外分支若违反逻辑边界，应重新组织分组或使用受支持的完整 composition，候选必须通过精确端点审计。
- [ ] 修改分组框的覆盖和净空检查。scene 中的框数、位置、标题和文字必须对应原生图，联合外框覆盖其实际成员及主路径导线。
- [ ] 在 evidence 中核对补充 target 的 members/core/direct、主路径及分组与正式 plan。篡改 ownership、扩大端点集或替换分组均需拒绝。
- [ ] 保持原有短接、NC、引脚出口、内部 X、文字对文字/器件/导线/框的规则。无新字段的旧源按既有语义测试。

```sh
python -m unittest discover -s tests -p test_main_paths.py -v
python -m unittest discover -s tests -p test_bridge.py -v
python -m unittest discover -s tests -p test_drawing_review.py -v
python -m unittest discover -s extras/schematic-verified-layout/tests -v
```

验收要求主路径在上游 source、受保护队列、实际回读和最终导图中一致，所有未声明跨模块连接反例保持失败。

### 任务 4　本轮新建图纸的受保护修订与恢复

**依赖** 任务 1。与任务 3 共享投影后完成集成。

**文件** 修改 `eso/workflow.py`、`eso/recovery.py`、`eso/cli.py`、`tests/test_workflow.py`、`tests/test_recovery.py`、`tests/test_reliability.py`。同步 `references/live-recovery.md`。

**接口** 新增 `Workflow.prepare_revision(parent_run, composition_path, before_path, *, layout_page=None)`，CLI 在 `prepare` 增加互斥的 `--revision-of` 与既有 `--placement-run` 路径。父运行提供不可变契约、计划、归属与 journal，新候选提供完整 source/page 和新鲜快照。

- [ ] 添加“首次已接线结果可重新布局且实例/属性/net/NC 守恒”的正常入口测试，保持空白页和未接线 staged 入口行为。
- [ ] 从父运行、原队列、journal 和当前完整图元清单核对本轮归属。复用 `observed_parts`、`journal_ownership`、`snapshot_fingerprint` 及已有 target lock。
- [ ] 使用新的运行目录重新编译 `compose → dry-run → execute → verify`，执行前重查新鲜基线，保留父运行及全部失败回执。
- [ ] 用合成适配器测试部分接线、超时但实际已落地、窗口变化、重复恢复、未知图元及人工修改。未知归属或电气意图漂移时，保留具体差异及 incomplete 状态。
- [ ] 进程重启后读取真实现场，生成新的差异和受保护队列。验证恢复操作幂等、同目标串行、不同目标可独立推进。

```sh
python -m unittest discover -s tests -p test_recovery.py -v
python -m unittest discover -s tests -p test_reliability.py -v
python -m unittest discover -s tests -p test_workflow.py -v
```

验收要求修订与恢复均有新基线、新队列和新验收，历史报告可独立复核。

### 任务 5　独立图面审阅与清晰度优先的候选比较

**依赖** 任务 2 和任务 3。

**文件** 修改 `eso/design_review.py`、`eso/assistance.py`、`eso/evidence.py`、`eso/cli.py`、`tests/test_assistance.py`、`tests/test_drawing_review.py`、`tests/test_attempts.py`。同步 `references/workflow-assistance.md` 与 `references/verification.md`。

**接口** 保留 `compare_layouts(candidates, budget)` 的调用方式。新增 `eso/evidence.py::design_qualification(run_dir, reviews)`，复用 `delivery_eligibility` 与 `evidence_index` 组合设计质量判断，独立于迭代完成状态。新流程候选增加独立审阅与同尺度预览的绑定。报告提供主路径、归属、绕行、文字、重复支路和页面判断，以及与已合格候选比较的对象和理由。

- [ ] 添加“小面积候选的主路径更难追踪”反例，验证选择清楚版本。添加无审阅、错候选、过期预览、同作者自签和未关闭问题的反例。
- [ ] 先通过电气/几何检查，再核对独立图面审阅和候选间阅读质量比较。在清晰程度相当的候选中沿用折弯、线长、实际包络面积和稳定 ID 排序。
- [ ] 几何指标直接计算。阅读质量保留对象化观察与比较依据，宿主对实际预览作判断。
- [ ] 方案审阅复用任务 2 的资料与意图绑定，最终图面审阅继续绑定同轮原生 snapshot/SVG/PNG。独立角色获得原始对象和依据，问题关闭后重新审阅受影响部分。
- [ ] 候选预览记录生成方式与源摘要。最终交付的图面审阅使用当前现场新导出的原生图。
- [ ] 新流程 `deliver` 核验方案审阅、当前图面审阅、完整机器验收和未关闭问题。保留原有 `core_state` 及历史读取方式，明确区分 EDA 验收与硬件实测状态。
- [ ] 针对移动/旋转后文字复位、保存重开实例变化、新图配旧审阅和报告文件被替换增加当前入口回归。

```sh
python -m unittest discover -s tests -p test_assistance.py -v
python -m unittest discover -s tests -p test_drawing_review.py -v
python -m unittest discover -s tests -p test_attempts.py -v
```

验收要求当前交付逐项关联真实独立审阅记录及原始导图，声明与证据绑定检查分别说明覆盖范围。

### 任务 6　自主迭代与达标后额外一轮

**依赖** 任务 4 和任务 5。

**文件** 新增 `eso/iteration.py`、`tests/test_iteration.py`，修改 `eso/cli.py`、`eso/assistance.py`、`eso/evidence.py`。同步主 `SKILL.md`、`agents/openai.yaml`、附属技能 `SKILL.md` 和 `docs/WORKFLOW.md`。

**接口** `record_iteration(root, event)` 和 `iteration-status` 围绕契约摘要、候选集合与已验收运行推进状态。状态包括 designing、qualified、extra-comparison、restoring、complete 和 incomplete，事件保存实际工具与审阅引用。

- [ ] 添加首次达标、额外一轮完成、重复提交同一事件和崩溃恢复测试。首次达标调用任务 5 的 `design_qualification`，由独立的额外轮状态控制后续完成条件。
- [ ] 实现候选状态清单、文件锁、原子写入和输入摘要校验。宿主提出有限候选并调用既有入口，记录每次问题、修正、结果及耗时。
- [ ] 额外候选只在完整通过验收且比较结果更好时替换最佳候选。候选更差、搜索无解或检查失败时保留已达标版本。
- [ ] 若现场已变为失败候选，调用任务 4 的受保护修订恢复最佳 source，重新保存重开、采集导图并完成当前验收后交付。
- [ ] 测试两进程重复推进、额外轮被执行两次、历史最佳与当前页不符、恢复期间人工修改和契约变化。记录停止原因与实际未完成项。
- [ ] 新流程最终 `deliver` 核验额外比较已完成、选定结果对应当前现场且保存重开与导图证据新鲜。添加“首次达标直接交付返回 incomplete”和“额外轮完成但现场仍为失败候选时拒绝交付”的入口测试，旧流程按 legacy 规则执行。
- [ ] 技能入口写明自主操作顺序、独立审阅交接和原理图交付终点，正常入口继续执行既有 schematic-only 范围校验。

```sh
python -m unittest discover -s tests -p test_iteration.py -v
python -m unittest discover -s tests -p test_regressions.py -v
```

验收要求已合格结果在优化失败和重启后可恢复，额外一轮计数与当前现场状态一致。

### 任务 7　安装回归与隔离工程完整实画

**依赖** 任务 2 至任务 6 全部通过。

**文件** 更新 `tests/test_install.py`、`scripts/demo_workflow.py`、`docs/VALIDATION.md` 和本计划的完成状态。发布时核对 `pyproject.toml`、`eso/__init__.py` 与技能 metadata 版本一致。

- [ ] 完整运行主测试、附加审计、实际上游离线集成和离线 demo。关键回归应在旧行为下失败、修正后通过。
- [ ] 通过现有安装器部署到实际使用的独立环境和技能目录，保留备份。核对 launcher 版本、隔离模式导入位置、schema/资料打包及源码与安装态摘要。
- [ ] 用安装态从一句样例需求开始，记录关键补问与默认值。样例优先核验适合需求的 ESP32 系列芯片或成品模块，在私有样例需求记录中提出装配、采样/上传、续航与电池条件等有依据的候选设置，完成独立电气审阅及用户对该具体方案的一次确认。
- [ ] 在新建隔离测试工程中绑定准确目标，按现有 native 导入/新建与 typed 接口完成真实库核验、放置、测量、主路径绘制和自主修正。
- [ ] 主路径逐段从原生回读核对，完成全量物理引脚 net/NC 对账及零无接点 X、零文字碰撞检查。审阅实际阅读路径、外围归属、字号、图幅及图签。
- [ ] 首次达标后额外比较一轮，保留真实合格版本。完成显式 `saved:true → reload → fresh readback → 原生 SVG/PNG → 独立图面审阅 → deliver`。
- [ ] 通过合成故障验证超时、部分成功和恢复，保留真实实画期间实际发生的失败及修正。每项报告写明实际验证层级。
- [ ] 最终提供可打开的原生工程、原生预览、BOM 和简明检查摘要。充电电流、低功耗和续航等实物表现按实际检查范围单独记录。

```sh
EASYEDA_BIN=easyeda python -m unittest discover -s tests -v
python -m unittest discover -s extras/schematic-verified-layout/tests -v
python scripts/demo_workflow.py --out runs/new-design-offline-demo
python scripts/install.py --dry-run
```

以上 `python` 指执行前确认的项目专用环境解释器。实际安装与现场命令按安装器输出、当前 help 和测试目标生成，原始回执保存在私有运行目录。

## 依赖顺序与交付节奏

先完成任务 1 的上游探针及任务 2 的通用设计依据，再贯通任务 3 的主路径。任务 4 的受保护修订可在探针通过后并行开发，集成时共享相同意图投影。任务 5 接入独立审阅和候选选择，随后完成任务 6 的迭代状态。最后执行任务 7 的安装态与现场验收。

建议按任务划分实现与独立代码复核，涉及同一文件的修改串行整合。每次提交的范围应保持可运行、可核验，既有 0.6.2 修改与本轮新增修改在评审中分别说明。

## 完成判据

| 用户共识 | 对应任务 | 完成证据 |
|---|---|---|
| 一句话开始新建设计 | 2、7 | 安装态 intake 到真实新工程交付的完整记录 |
| 关键补问、有据默认、装配沿用 | 2 | 需求正反例、依赖撤销与一次完整方案确认 |
| 嘉立创采购与完整成本 | 2、7 | 实际料号、相同计价条件和完整 BOM 成本状态 |
| 主路径直连、其他标签 | 1、3、7 | 源、队列、物理线树、最终导图一致 |
| 清晰优先、自主迭代 | 4、5、6 | 独立图面比较及失败候选恢复记录 |
| 独立方案和图面审阅 | 2、5、7 | 独立角色调用记录、输入摘要及逐项问题关闭 |
| 达标后额外一轮 | 6、7 | 首次达标、额外比较、最终当前验收的状态链 |
| 原理图交付终点 | 6、7 | schematic-only 回归与原理图交付清单 |

本文件的任务当前均为待实施。计划阶段已完成代码与接口核对、官方资料检索和 305 项既有基线测试。新增流程、主路径投影、受保护修订和完整样例现场验收的结论由实施记录逐项补充。
