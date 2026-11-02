# 测量与上游绘图衔接（0.2）

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

prepare --placement-run 只接受该次 stage/collect 的哈希、实例 ID 和场景，使用上游
--replace --preserve-instances；不能借此接管已有图纸。没有 stage 归属证据仍只接受空白页。
执行队列若需要删除非预期图元，会被适配层拒绝，不用全页清空绕过。

## 已知阻断与覆盖

安装的 Release 1.16.0 对十类/十一类 pagePrimitives 有结构不兼容。
stage 在任何真实放置前检查这一已复现组合并拒绝，不删除 pins 字段迎合旧解析器。
需要上游一致、已验证的兼容构建才能继续现场闭环；本项目不自动升级 CLI/Connector。

当前自动测试覆盖模拟 stage→collect→prepare，以及真实 upstream lib-layout 接收派生输入、
compose/apply dry-run。真实 EDA place/wire/save/reload 完整链尚未验收，不能把模拟通过写成现场通过。
