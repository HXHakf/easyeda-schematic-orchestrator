# 架构 0.2

采用宿主模型 + 确定性 Python 编排 + 独立 easyeda-agent CLI/daemon/Connector。
模型负责语义、选型与电气论证；脚本负责字段规则、状态、版本和证据一致性；绘图计算仍由上游承担。

| 模块 | 职责 |
|---|---|
| requirements.py | 按类型补问；默认依赖重算；显式回答和“帮我选”；中文确认页 |
| core.py | Schema、精确确认哈希、原子文件写入 |
| planner.py | 完整物理脚计划；STM32最小系统/BMP280参考；库身份绑定 |
| adapter.py | 无shell的CLI调用、原始收据、目标核验与能力发现 |
| bridge.py | 本轮初始放置、完整观测/位号/SVG测量、派生上游布局输入；不求解几何 |
| workflow.py | 源/队列/目标守卫、保持本轮实例、一次性执行、先失效旧结论的复验 |
| cli.py | 用户入口、中文确认产物、退出码与交付证据核验 |

数据链：requirements→contract/approval→plan/bindings→stage→原始测量→上游lib-layout→composition
→prepare/compose/dry-run→execute→save/reload/readback→verification attempt→delivery。

持久事实原则：原始观测不改；目标网络是派生设计意图；快照、测量和编译输入以哈希相连；
旧默认不能覆盖新条件；旧成功不能覆盖新失败；部分成功不意味着回滚。

需求类型可扩展，但自动参考拓扑仅限所列模板；LDO/其他型号由模型依据资料编写计划。
现场执行目前仍受上游发布版快照结构不兼容阻断，须在兼容构建上另做实际端到端验收。
