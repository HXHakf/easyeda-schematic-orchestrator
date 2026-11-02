# 独立行为评估

2026-11-02，另一个 Agent 仅阅读 Skill、参考与接口，未操作 EDA、网络或代码，使用场景：

> 帮我开发一套立创 EDA 专业版画原理图的工作流，用户输入 STM32 + I2C 温湿度传感器，
> 缺什么会问，已有 EDA Agent Connector。现在只开发工作流，先不要画图。

预期行为：识别为开发模式；不把未知器件/供电/目标工程当事实，不为开发流程要求电路确认；
将准确型号/模块形态/供电作为未来设计的条件问题。不得把 BMP280 当温湿度传感器。

实际评估发现并修正：

| 问题 | 修正 | 验证 |
|---|---|---|
| 开发时也要求目标工程 | assess 默认不要求目标；--for-execution 才要求，Skill 明确离线开发分支 | target-is-only-required-for-execution |
| 模块未知时默认新增上拉 | 增加 sensor_form；模块需说明上拉状态，裸片才提出默认新增 | module-does-not-receive-unexamined-extra-pullups |
| 多轮问答丢失默认来源 | default_provenance 随规范字段保存；更改字段值自动移除旧来源 | defaults-survive-multiple-question-rounds |
| 安装后的 Skill 引用一次性的现场事实 | 移到带日期的 RESEARCH/VALIDATION；参考只描述如何获取新证据 | 文档复核 |
| README/WORKFLOW 尚缺入口 | 已补齐并检查全部 Markdown 相对链接 | 无断链 |

该评估属于行为推演，未声称验证了硬件或完整真实 EDA 任务。
