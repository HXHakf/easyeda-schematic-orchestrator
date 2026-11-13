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
eso deliver --run-dir runs/final --out runs/manifest.json
```

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
