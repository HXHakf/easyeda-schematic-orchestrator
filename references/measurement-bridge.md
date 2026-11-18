# 测量与上游绘图衔接（0.2）

完整 Skill 安装包含 [配套布局指南](../extras/schematic-verified-layout/SKILL.md) 和附加审计工具。
文字测量使用 `eso.svg_text` 及本地 Chromium/Edge/Chrome。`ESO_LAYOUT_SKILL` 可覆盖兼容入口位置。

此层只转换数据、核对来源并调用上游，不实现布局求解或基础绘图。

## 进入条件

- 用户要求真实绘图，已确认当前 Contract；目标为明确绑定 UUID 的 ESO-TEST-* 空白原理图页。
- 计划已经 validate-plan；逐位号 bindings 包含 libraryUuid、deviceUuid、verified_model、expected_name、evidence。
- verified_model 是计划中的 query；expected_name 是已经人工/Agent 核实的真实库名称。
- stage 再用 lib device get 查验名称和库身份，不把第一个检索结果当作绑定。

## 调用链

```bash
eso stage --contract contract.json --approval approval.json --plan plan.json \
  --bindings bindings.json --run-dir runs/staging
eso collect --run-dir runs/staging
eso prepare --contract contract.json --approval approval.json --plan plan.json \
  --composition runs/staging/composition.json --before runs/staging/measured-snapshot.json \
  --placement-run runs/staging --run-dir runs/final
eso execute --run-dir runs/final
eso verify --run-dir runs/final
eso deliver --run-dir runs/final --reviews runs/reviews.json --out runs/manifest.json
```

`reviews.json` 关联本轮完整几何与逐项图面审阅，格式见[辅助工具](workflow-assistance.md)。
核心检查通过后继续完成图面复核，缺少审阅或仍有图面问题时 deliver 输出 incomplete 并返回退出码 3。

stage 调用上游 materialize/apply，仅在原始空白测试页放置本轮器件、绑定 canonical ID，并逐一核实物理引脚和显式保存。
它不是完整原理图，不报告电路连接完成。超时后保留状态、锁与新回读，不直接重复放置。

collect 在同一页串行读取 parts、designator-geometry、官方页面 SVG 与精确 sheet-geometry；
将原始观测另存，检查前后场景未变，再生成 layout-input.json，交给上游 sch lib-layout。
源副本中的 pin.net 是已确认的**目标意图**；原始现场空网/NC/未知状态不被改写。未知网、缺 pin、
库身份不符、位号不可见/无官方 bbox、SVG 哈希不匹配都会拒绝。

`collect --max-candidates N` 记录本次布局预算，范围 1–1000000，默认 20000。候选用尽时保留
测量和失败状态，按[布局搜索复盘](live-recovery.md#布局搜索与整页方案)区分模块求解和整页装箱问题，
再调整已明确的归属条件、几何策略或预算。

## 初始放置恢复

```bash
eso recover-stage --run-dir runs/interrupted --out runs/recovered
eso collect --run-dir runs/recovered --max-candidates 100000
```

恢复核对原契约、确认、绑定、初始空白页、队列摘要、日志捕获的实例和新鲜库存。
完整的已放置器件进入保存与测量阶段，缺件交由上游从新的 canonical 子集生成放置队列。
同一库身份的器件共用一次库查询，各自实例独立保留。输出使用新目录，原始日志和锁保留。

恢复范围是归属明确、未接线的初始放置。未知库存、实例替换、已有绘图或不完整证据会保留具体错误。
新恢复记录及原证据副本纳入 prepare 的受保护摘要，后续篡改使执行失效。

prepare --placement-run 只接受该次 stage/collect 的哈希、实例 ID 和场景，使用上游
--replace --preserve-instances；不能借此接管已有图纸。没有 stage 归属证据仍只接受空白页。
执行队列若需要删除非预期图元，会被适配层拒绝，不用全页清空绕过。

## 已知阻断与覆盖

安装的 Release 1.16.0 对十类/十一类 pagePrimitives 有结构不兼容。
stage 在任何真实放置前使用原始快照和合成输入运行离线 compose/dry-run 探针，
按实际编译结果拒绝不兼容组合，不删除 pins 字段迎合旧解析器。
需要上游一致、已验证的兼容构建才能继续现场闭环；本项目不自动升级 CLI/Connector。

自动测试覆盖 stage→collect→prepare、放置恢复、真实上游布局/编译与 dry-run。
NPN/NMOS 的现场数据链和图面已通过后续复核，范围见 [现场记录](../docs/plans/npn-nmos-eda-acceptance.md)。

除位号外，所有属于本轮器件/引脚的可见文字均需测量。raw 可见性为 null 或 bbox 缺失时，
collect 自动通过随包的隔离 SVG 测量器补充派生证据，保留原始值，
核对完整 SVG 文字清单与全部物理脚校准；缺依赖或漏测仍阻断。ESO_LAYOUT_SKILL 可指定该技能目录。
prepare 会核验并保留新增的文字测量与覆盖报告；同样经过原始证据哈希保护。详见
[可靠性说明](../docs/RELIABILITY.md)。中断后可先用 `eso diagnose` 只读检查，不清锁重试。

## 已选整页几何

上游 `layout-sheet-plan` 的完整单页经宿主选定后，可用 `prepare --layout-page selected-page.json`
交给上游固定编译。先明确该页的完整几何、框、间距和来源；上游仍核对纸张、模块、物理脚及连接。
prepare 将所选页复制到运行包并纳入摘要。原文件后续变化不改已固定包，运行包内的页被修改会阻断执行与验收。
既有不传此参数的入口保持原行为。

日志格式差异、连续恢复历史及接线阶段恢复的适用边界见[放置与执行恢复](live-recovery.md#放置与执行恢复)。


## 主路径与联合分组

`plan.primary_paths` 每项声明 `id/title/links`，link 使用 `from/to/net`。
端点为 `{componentId,pinNumber}`，net 为计划中的网络名。不同网络逐段列出。
`drawing_groups` 每项声明 `id/title/modules`，所有逻辑模块唯一归属一个绘图组。
省略时按逻辑模块一对一投影。原始逻辑模块、核心与 attach 关系保存在受保护 plan 中。

`eso.main_paths.project_drawing_groups` 统一提供 canonical 模块投影。
联合组的多个逻辑核心全部保留在 `coreComponents`，测量 bridge 的 `coreComponentId`
选取第一个核心作为布局锚点，其他核心沿显式主路径引脚关系定位。
现有 CLI 已通过两个核心的真实离线 lib-layout、compose 和 apply dry-run 验证。
缺少可用于定位的主路径关系时返回具体错误，可提供完整 authored composition。

主路径网络优先采用 `direct`，其余逻辑边界采用标签策略。上游策略作用于整个网络，
可能同时连接未声明支路。collect 与 prepare 会从 source 的物理引脚和显式导线重建线树，
检查已有接触是否超出声明关系。主路径与本地 attach 的完整连通要求由包含派生引线的编译后门核验。额外分支失败时调整分组或提供完整 composition，
并通过同一审计。标签无法满足实体主路径。

补充审计 target 保留逻辑 modules，并通过 `audit_projection(plan,target_nets)`
生成精确 direct、primary_paths 与 drawing_groups。target 的 link.net 使用审计网络 ID，
转换依据完整脚清单与网络名，禁止猜测 `net-` 前缀。
新 scene 每个组只有一个 `drawingGroupId` 外框，需提供真实矩形 `primitiveId` 与标题 `titleTextId`。
交付核对原生矩形数量、位置、实际标题内容、全量文字测量和受保护 plan，
并重跑当前主路径及可读性检查。改变分组后旧框和旧图面证据需要重建。


编译后资格还核对最终受保护 drawing 的引脚、线段、框与实际队列。
`schematic.power.connect_pin` 的 marker/terminal 引线按已核实的四方向正交偏移契约纳入物理图，
仅接受精确的五单位格网坐标与正偏移，涉及未知或需要推测的几何即拒绝。
所有审阅过的本地 attach 都须同树，`connectionMode=net-labels` 不能替代。
`prepare` 在 dry-run 与 prepared 状态之前执行，`execute` 再次核对固定输入。
源模块的 title、canonical 模块 name 与编译后真实 frame.title 均与当前 plan 对账，
因此显式 layout-page 覆盖也经过相同标题检查。

wheel 在 `share/eso/auditors` 保存同一份附加审计脚本，运行时优先使用源码位置，
安装态从当前解释器 `sys.prefix` 定位。最终单页 scene 的引脚、线、节点、标签、器件、
文字和框必须使用同一页面别名，并核对原始快照的工程与文档身份。


source 图尚未包含 compiler 生成的 marker/terminal 引线，允许声明内的关系等待完整编译后验证。
它继续拒绝显式的未授权接触。编译后门仍须在 prepared 和任何 Apply 之前证明所有路径及 attach。
当上游确认当前图纸与候选等价时，队列先读取并核对当前的器件、图形和归属。实体审计使用该保护步骤中的原有标记引线。
需要重绘的候选仍逐一核对计算结果与实际 connect_pin 操作。
旧 `moduleId` 框省略 page 时，使用其余实测对象已确定的唯一页面别名。
显式冲突、显式空值以及现代 `drawingGroupId` 缺页继续拒绝，保留原始证据字节。
