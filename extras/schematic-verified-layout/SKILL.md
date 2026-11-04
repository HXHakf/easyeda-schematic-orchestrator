---
name: schematic-verified-layout
description: Use when creating, redrawing, arranging, or changing connections in EasyEDA Pro / 嘉立创EDA专业版 schematics, including 墨水屏转接板 and Python-driven circuit drawing; also covers modular layout (module frames, cross-module label-only boundaries), netport/label-based bulk wiring (sch autoconnect), partition frames and title blocks, readability gates (crossings/bends/spacing/page fit), drawing grammar and page-flow review, netlist reconciliation, and connector-crash recovery. Apply for every schematic drawing task; not for PCB routing or image-only circuit illustrations.
---

# 可验证的原理图绘制

以 **电路目标 → 实测几何 → 程序规划 → 独立校验 → EDA 写入 → 保存重开验证** 为默认流程。
AI 负责功能划分、选型和电气意图；确定性程序负责坐标、走线、文字避让与模块装箱。
本技能是已安装 `easyeda-agent` 的上层流程，先读取它的 `SKILL.md`，复用其 typed CLI 和数据契约。
本文件补充的“全部可见文字检查”比上游只检查位号的几何规则更广，不能用上游 gate 代替。

**可读性是交付标准，不是审美偏好。** 线接通只是第一道关：一页还要过正面构图规则
（[画法语法](references/drawing-grammar.md)）、确定性可读性门与渲染图面审阅
（[可读性门](references/readability-gate.md)）。失败事件按 [自迭代](references/self-iteration.md)
落成规则与合成测试，同一个错误不出现第二次。

**模块化布局是硬要求。** 每个功能模块有自己的框、标题和内部走线；模块之间只准标签/netport，
禁止一根连续导线穿过去（GND 也各模块内用符号落地）。模块跨页、内容越框、框重叠相切都判失败。

## 每次绘制的必经步骤

**写前先读 [现场复盘与恢复协议](references/live-recovery.md)。** 核实实际加载的 Connector
与 CLI/Skill/daemon 能力组合及精确目标；先定商品属性再测几何。Name/型号更新可能重置文字位置，
须失效旧文字/模块边界检查。部分 Apply 失败先回读并生成局部 delta；保存重开产生的属性 ID
变化按完整 raw 语义对账，禁止排除整类属性。维护工作流默认离线，不操作用户图纸或屏幕。

1. **冻结电路意图。** 从需求、手册和实际库身份得到完整物理引脚、网络、明确 NC；为每件器件指定唯一功能模块和核心/外围关系。记录必须实体直连的引脚组、跨模块标签的作用域、供电及极性。先过目标数据检查，不把缺失引脚设为 NC。小电路可在一页，分页服从阅读和接口关系；分页前先写**每页的读者任务**（一句话）与**主路径起止**，分页方案是产物，不是事后解释。每页先立一条主干（左→右主路径），再挂支路。**每个模块必须有独立区域与标题**（落成 `scene.moduleFrames`），跨模块的网络在目标里标成标签边界。
2. **采集并保存真实几何。** 保留当前工程/页、原始回读和库来源；采集 body、引脚出口、当前姿态的位号、型号/参数、网络标签、模块标题、绘图区内框和图签。用原生 SVG/官方 bbox 测文字；文字内容、字体或姿态变更后重测。假符号与估计尺寸只用于离线探索。
3. **先算模块内部。** 模板规定相对结构，不写死项目绝对坐标。固定连接，再用 `lib-layout` 或 `layout-plan` 的实测输入求位置和正交线路。首段沿引脚向外，核心外围短线直连；跨模块再用标签，**模块内走线不得越出自己的框**。长文字/约束多的标签优先搜索。不能用删线换标签、隐藏参数或缩短真实型号解决冲突。每个支路先按**角色**选画法（series / shunt / bank / divider / decoupling / lane / rail，见画法语法 G4）；间距用实测栅格 `g` 表达：引脚出线 ≥2g、组内 2–4g、组间 ≥2 倍组内、单页最远相关器件 ≤12g。
4. **再装入页面。** 用 `layout-sheet-plan` 或明确的确定性装箱程序选择合法模块变体，按整块刚体平移器件、线、标记、框和标题。模块框随模块整体移动，框间留 ≥4g 空带，**不重叠、不相切**。保留阅读顺序与重复阵列；可比较有限的顺序/页面宽度，不拆散专属外围。优化目标依次为硬约束通过、可读性、转折/线长、占地；保留合法基线。预算耗尽只表示本次搜索停止。重复通道用同一模板同一相对坐标，只改绝对偏移；小电路用小图幅/单页，不摊在 A3 上凑版面。
5. **独立检查候选。** 从坐标重新构图，不读取生成器的“已连接/合法”布尔值。分别检查物理线树与标签后的逻辑网络、异网短接、断路、NC、必须直连和几何碰撞。无节点内部 X 与 T/端点接触分开；全部可见文字及图框另检。再跑**可读性门 A**（`readability_audit.py`：异网交叉、多折弯、标签掩盖断线、平行间距、纸张适配，以及**模块化硬门**——跨模块实体线与模块跨页始终检查，声明 `moduleFrames` 后再查越框、框重叠、缺框），结构检查器覆盖不到的页面流向/锚点/归属/绕行，必须在干净窗口导出的原生 SVG/PNG 上按**门 B** 追主路径并留审阅记录。缺测输出 `unverified/unsupported`，不能计成零问题。使用下面脚本作补充；它们的覆盖范围不等于完整 EDA 验收。
6. **受保护写入。** 按当前 `--help` 和 easyeda-agent 的 1.4 数据路径生成 `compose → apply`；有 dry-run 先运行。每次绑定工程与页，同一窗口串行。失败保留输入/返回，先回读再修源重算；不盲重放、不手改生成队列、不用 GUI/debug JS 绕过缺失接口。
7. **以实际结果收尾。** 对账器件身份、每个物理脚的网络/NC、所有 direct 组及实际几何；逐页运行严格 gate 和原生 DRC。显式 `save` 必须返回布尔 `saved:true`，然后 `doc reload → 新回读 → 新原生 SVG/PNG → 重做受影响检查`。汇报检查范围及未覆盖项，离线通过不能称为现场通过或硬件可用。本次的失败与假失败写进 `events.jsonl` 与 `lessons.md`（见 [自迭代](references/self-iteration.md)），能落成规则的必须配合成测试。

局部编辑按同样顺序只重算受影响模块与页，并证明其他电气目标未变；不为改一个标签重建整工程。
只有离线预览的任务停在候选校验，不擅自落图。用户当前授权内的常规选择自主执行，不添加一律审批环节。

## 顺序与恢复的硬规则

这些规则来自真实工程的失败复盘，违反其中任一条都会产生难清理的残留：

1. **先定几何，再接线。** `place` → 定姿态/坐标 → `sch list --include-pins` 量引脚 → 才 `connect`/`autoconnect`。接线后再移动器件会把桩线、旗、端口留在原处，留下悬空脚与 dangling wire；已接线的器件要挪，先 `disconnect` 摘干净。栅格 `g` 由实测共线最小间距求得，不用「大概 2.54mm」代替。
2. **每次变更前重新解析窗口。** windowId 在刷新/重连/`doc reload` 后都会变；`easyeda health` 后确认精确工程/页及所需能力，不仅凭版本高选窗口，不写死 ID。
3. **假失败先复核再重发。** `DISPATCH_FAILED`、超时、`connector did not respond` 都可能已经落地；先轻读（`sch list`/`doc ready`），再决定重发，否则会造出重复端口/重复旗。
4. **先保存，再修复。** 卡死时 `sch save` 往往仍可用；拿到 `saved:true` 之后才允许 `web reload` 或 `project open --allow-discard-unsaved`。
5. **导出要趁干净窗口。** `export-image` 失败会留下 stuck toast 并连锁超时；放在刚重开、还没做变更时做，成功后再改图。
6. **结构问题只改结构。** 加标签接续断线、缩短真实型号、隐藏参数、改引脚类型、加 PWR_FLAG、堆间距豁免，都是掩盖而不是修复。改画法方向、锚点位置与朝向、折行或分页。
7. **判据先写，再动手。** 可读性图面审阅与对抗式复核开始前，先写下读者任务、主路径、允许的例外与最大轮数；否则会无限返工或自我确认。
8. **模块化布局是硬要求。** 模块边界只准标签/netport，一根连续导线穿过两个模块即结构错误；模块跨页、内容越框、框重叠或相切同样失败。改法是断开成标签、扩大框或重新分区，不是缩小字号、挪动框线或删掉框。

## 两种布线模式（都合法，要显式选择并写进交付说明）

- **实体走线**：核心—外围用真实导线，适合模块少、引脚疏、需要力导向阅读的页；受「共线段自动合并」静默短路约束，必须逐段核对。
- **标签式（netport / netflag 批量 `sch autoconnect`）**：适合引脚密集、需要分区阅读的页；仍需逐脚 netlist 对账，目标声明的 direct 组保持实体相连。可能出现 `peripheral-not-direct` 启发式告警，须逐项核查，不能自动豁免 strict 失败。

命令形状、dry-run 打分语义、BOM/编码、图框与图签、netlist 解析、连接器恢复剧本见
[EDA 实操陷阱与恢复剧本](references/eda-cli-pitfalls.md)，落图前先读一遍。

## 可执行工具

脚本路径相对于本技能目录，工作产物写入当前项目的新版本目录，不写回技能样例。

```powershell
python <skill>/scripts/led_demo.py --out-dir <project>/schematic-runs/led-001
python <skill>/scripts/circuit_audit.py target --target target.json --out target-check-001.json
python <skill>/scripts/circuit_audit.py scene --target target.json --scene scene.json --out geometry-check-001.json
python <skill>/scripts/circuit_audit.py compare --target target.json --readback page-after.json --out compare-001.json
python <skill>/scripts/readability_audit.py --target target.json --scene scene.json --out readability-001.json [--strict] [--min-module-gutter-grid 4.0]
python <skill>/scripts/edacall.py --job job.json --out result.json
python <skill>/scripts/netlist_diff.py --enet page.enet --expect expected.json --out report.json
python <skill>/scripts/snapshot_semantic_diff.py --before before.json --after after.json --out semantic-diff.json
```

- `led_demo.py`：计算两针输入＋1kΩ＋黄色 LED 的**合成几何示例**，验证正例并拒绝断线反例；同时给出两个模块框的坏/好例对——同一网表下模块化画法通过，跨模块连续导线被 `module-physical-crossing` 点名拒绝。给出声明条件下的直流电流估算。不是 SPICE，也不是实际库尺寸或 EDA 写入程序。
- `circuit_audit.py`：标准库实现的独立并查集、引脚状态、目标对账和矩形/线段检查；输入见 [数据格式与边界](references/audit-data.md)。这是附加审计格式，**不能直接作为 compose/Apply 文件**。
- `readability_audit.py`：复用同一连通性模型的可读性门 A；判异网导线交叉、两端网络折弯数、标签掩盖断线、平行间距、引脚出线长度与纸张适配；**模块化硬门**分两级——跨模块实体穿透与模块跨页只依赖 target `modules`，始终检查；声明 `scene.moduleFrames` 后再加内容越框、框重叠/相切、缺框与框间空带。规则表、阈值、豁免与门 B 图面审阅协议见 [可读性门](references/readability-gate.md)。默认只有 hard 失败才算 FAIL，`--strict` 时 advisory 也失败；exit 0=PASS / 2=FAIL，`--out` 拒绝覆盖已有证据。不声明 `moduleFrames` 时框规则跳过，必须写进未验证项。
- `measure_svg_text.py`：隔离的本地 Chromium 渲染原生 SVG，取得可见 `<text>` 的真实字体 bbox 和变换；使用方式与校准见 [执行流程](references/workflow.md)。不向 EDA 注入 JS，不接触用户浏览器会话。
- `edacall.py`：把 `{"argv": [...]}` 形式的任务文件转成 argv 列表直接调 easyeda CLI，规避 shell 吞引号、BOM、stderr 混流；输出 `{exitCode,jsonParsed,json,stdout,stderr}`。
- `netlist_diff.py`：读 `sch netlist` 落盘的 `.enet`，与冻结的 `{"nets":{净:[ref:pin…]},"nc":[…],"allow_swap":[…]}` 逐脚对账；`allow_swap` 放行无极性件（电容）的 p1/p2 互换。exit 0=PASS / 1=mismatch。

通用放置、寻路、装箱优先复用 easyeda-agent 的已安装算法，不为每张图再写一套未经验证的路由器。
`snapshot_semantic_diff.py` 是只读快照比较：仅忽略属性自身 primitiveId，保留其余完整 raw 语义与重复项；exit 0=相等、1=差异、2=输入/输出错误，不能单独证明电气或保存成功。输入契约见现场复盘协议。
若它们没有覆盖全部文字，将实测文字占位作为附加障碍/间距约束重新求解；不能宣称现有求解器已原生支持任意文字障碍。

## 证据与停止条件

每次保留：目标与原始测量、生成脚本/版本/参数、输入输出 SHA-256、完整候选、失败报告、Apply journal、实际回读、对账、DRC、保存重载回执、原生导图、可读性门 A 报告、门 B 审阅记录与对应版本导图、`moduleFrames` 与模块框实际位置、`events.jsonl` 与 `lessons.md`。
证据文件不覆盖；改源后旧候选/报告失效，必须重算。

关键接口缺失、测量不完整、路径预算耗尽且无合法候选时，保留可复现失败，报告明确未完成项；不把部分方案推进为完整绘制成功。
报告分别标记：电气目标检查、几何与文字覆盖、可读性与图面审阅、EDA 持久化、硬件/仿真，并说明哪些数字是量的、哪些是算的。新安装技能或脚本通过测试，不代表未来复杂电路已验证。

摆件与走线的正面规则、角色画法表、间距与修复顺序读 [drawing-grammar.md](references/drawing-grammar.md)；可读性门 A 规则表与门 B 图面审阅协议读 [readability-gate.md](references/readability-gate.md)；事件账本、归因分层与「先校准检查器」读 [self-iteration.md](references/self-iteration.md)。
操作命令、文字处理和模板扩展按需读 [workflow.md](references/workflow.md)；数据适配与检查边界读 [audit-data.md](references/audit-data.md)；CLI 陷阱、图框/图签/网表细则与卡死恢复读 [eda-cli-pitfalls.md](references/eda-cli-pitfalls.md)。
