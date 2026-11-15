# 架构

采用宿主模型 + 确定性 Python 编排 + 独立 easyeda-agent CLI/daemon/Connector。
宿主负责语义、选型、官方手册获取与应用电路审阅，以及项目电气论证。
Python 负责已编码的条件、计算、状态、版本和证据一致性，绘图计算由上游承担。
通用知识先维护拓扑、条件与来源，具体项目再固定所选型号、手册和真实库绑定。

| 模块 | 职责 |
|---|---|
| requirements.py | 按类型补问；默认依赖重算；显式回答和“帮我选”；中文确认页 |
| core.py | Schema、精确确认哈希、原子文件写入 |
| planner.py | 完整物理脚计划；STM32最小系统/BMP280参考；库身份绑定 |
| knowledge.py、knowledge_input.py | 稳定主题与别名检索、来源去重、首版资料输入 |
| component_knowledge.py | 常用元件基础卡、子类型归类、跨目录来源去重与统一检索；复用既有电路检索和共享规则 |
| knowledge_v2.py、fact_coverage.py、knowledge_rules.py | 资料适用条件、功能边界与开关参数校验 |
| materials.py、knowledge_design.py、knowledge_composition.py | 私有不可变资料版本与撤回、知识计划与绑定、有限 MCU 模块组合 |
| schematic_rules.py | 嘉立创核心规范的分阶段检索，引用既有检查器 |
| adapter.py | 无shell的CLI调用、原始收据、目标核验与能力发现 |
| compatibility.py | 原始快照 + 合成输入的离线 compose/dry-run 探针；不执行探针队列 |
| bridge.py | 本轮初始放置、完整观测/位号/SVG测量、派生上游布局输入；不求解几何 |
| workflow.py | 源/队列/目标守卫、保持本轮实例、一次性执行、先失效旧结论的复验 |
| diagnostics.py | 状态/锁/journal/现场差异只读诊断，不自动续跑 |
| doctor.py | 安装、离线、只读现场连接与指定目标历史验收的分项诊断 |
| recovery.py、locking.py | 初始放置缺件恢复、原实例保留与目标级跨进程互斥 |
| assistance.py、evidence.py | 目录核验、实测文字恢复、有限布局候选比较、分层证据与历史轮次核验 |
| cli.py | 用户入口、中文确认产物、退出码与交付证据核验 |

数据链：requirements→contract/approval→plan/bindings→stage→原始测量→上游lib-layout→composition
→prepare/compose/dry-run→execute→save/reload/readback→verification attempt→delivery。

持久事实原则：原始观测不改；目标网络是派生设计意图；快照、测量和编译输入以哈希相连；
旧默认不能覆盖新条件；旧成功不能覆盖新失败；部分成功不意味着回滚。

需求类型可扩展，但自动参考拓扑仅限所列模板；LDO/其他型号由模型依据资料编写计划。
现场执行通过写前离线探针检测实际快照兼容性，须在兼容构建上另做实际端到端验收。
新增数据契约与覆盖边界见 [可靠性说明](RELIABILITY.md)。

应用主题和资料来源维护在 `eso/data/knowledge.json`，元件基础卡与新增来源位于 `eso/data/components.json`，
跨目录来源按 ID 复用并检查唯一性。设计规范索引位于
`eso/data/schematic_rules.json`。项目文件位置保存在私有运行环境，资料版本按内容哈希固定，
条件或实际依赖变化时重新校验。具体格式见[知识库](../references/knowledge-base.md)与
[v2 资料复用](../references/knowledge-v2.md)。

Python 当前覆盖受限开关和 MCU 组合的已编码电气规则。整机 SPICE、启动、时序与实物性能
按具体项目另行验证，验收范围见[交付指南](../references/verification.md)。
