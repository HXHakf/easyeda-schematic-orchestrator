---
name: easyeda-schematic-orchestrator
description: "当用户说“我要画原理图”“帮我设计电路”“画一个 STM32 最小系统”，或在原理图任务中要求补全外围、修改连接时使用。默认面向嘉立创 EDA 专业版，先整理需求、追问缺参并确认 Design Contract，再复用 easyeda-agent 绘图、回读与验收。用户无需指定 Skill 名；纯原理讲解、仅 PCB 操作或普通软件工作流开发不自动触发。首版写入仅限专用测试工程。"
metadata:
  version: "0.2.1"
---

# EasyEDA Schematic Orchestrator / Designer

你负责设计决策、需求与证据闭环。绘图、几何规划、保存及回读复用独立安装的
`easyeda-agent`，不安装官方 Run API Gateway，不重写绘图，不用 GUI 或任意 JS 绕过 typed 接口。

## 触发条件与工作模式

- **默认触发：设计原理图**。用户表达绘制、新建、补全或修改原理图的意图即使用本 Skill，
  不要求逐字匹配关键词、不要求提及 Skill 名或“工作流”。例如“我要画原理图”“帮我设计一个
  STM32 + I2C 传感器电路”“补齐这个最小系统的复位和去耦”“修改原理图里的连接”。
  本 Skill 主控需求与确认，easyeda-agent 承担实际 EDA 工具调用。
- **平台选择**。沿用用户已经指定的 EDA；当前默认是嘉立创 EDA 专业版。
  用户明确指定其他 EDA 时遵从该选择，不擅自切换到嘉立创。
  当前执行适配只有嘉立创；其他平台可先做需求/方案，明确工具缺口，不虚构执行能力。
- **只规划或预览**。同样可以整理需求、追问并生成设计草稿；只规划/预览的授权不延伸为工程写入。
- **不自动触发的场景**。单纯问“I2C 是什么”“解释这张原理图”、仅要求 PCB 布局布线或普通软件工作流开发，
  不据此启动原理图设计流程；按用户问题直接回答或使用对应工具。
- **维护此工作流**。仅当用户明确要求开发、修改或测试这个 Skill 时进入维护模式，默认不写 EDA；
  可完全离线完成，不把连接状态、测试工程或具体器件选型作为开发的前置条件。
- **恢复未完成任务**：先读运行目录、journal、新鲜现场快照；不能根据旧截图或失败退出码盲重放。

## 工作流

实际绘图时同时读取已安装的 `schematic-verified-layout/SKILL.md`，按其全部文字、模块边界与保存重开
验收执行；通过宿主技能目录定位，不猜安装路径。现场写入前和失败恢复前读取
[现场经验与恢复约束](references/live-recovery.md)。维护本仓库保持离线。

1. **识别意图与电路类型**。先区分 MCU 最小系统、MCU + 传感器、LDO 电源或其他电路。
   只规划时可离线开始；不要为一张 LDO 电路追问 MCU/传感器。触发代表进入需求流程，不代表已获绘图授权。
2. **理解与分轮补问**。读 [输入与追问](references/input-and-questions.md)。宿主模型复核原文的
   功能、否定、纠正、多电源域与约束；`intake` 只作种子提取。运行 `assess`，面向用户只展示
   `next_questions` 中最关键的至多三项，其余 blockers 留在内部。用户说“帮我选”时用 `update`
   记录，按 `selection_tasks` 查证候选，不能反复逼问准确型号或擅自选搜索第一项。
3. **重算候选默认**。每轮回答后运行 `update` / `assess`。默认值连同推断条件保存，条件变化
   重新计算；用户明确选定的值保留。未解决语义项仍阻断，不能把 unknown 当作 NC 或空数据。
4. **设计确认**。`contract` 同时输出 JSON 和中文确认页。向用户展示功能、供电、接口、型号、
   外围/假设、执行范围与验收；内部管理哈希，不要求用户手输长哈希。确实收到该版本确认后
   才运行 `confirm`。设计或目标变化使旧确认失效；已确认版本不重复问。
5. **电路计划与真实库绑定**。创建 `schemas/plan.json`，完整列出物理脚 net/NC、模块核心和
   专属外围附件及原厂依据。`plan` 内置 STM32F103 最小系统/BMP280 参考；其他电路由 Agent
   依据资料编写并 `validate-plan`，需求类型支持不等于所有型号都有自动拓扑。通过上游
   `lib search`、`lib device get` 精确核对库、封装与型号，形成逐位号 bindings；不以实例 UUID 代替库 UUID。
6. **上游绘图与测量闭环**。读 [上游适配](references/upstream-adapter.md)、
   [测量衔接](references/measurement-bridge.md)，按需读取上游 `schematic-data.md` / `auto-layout-sop.md`。
   `stage` 仅在已确认的空白测试页调用上游 materialize/apply 初始放置；写前检查兼容性，失败不盲重试。
   `collect` 回读本轮实例、位号 bbox、原始官方 SVG，生成保留原始观测的派生布局输入，调用上游 lib-layout。
   原始属性可见性为 null 或缺 bbox 时复用安装态布局技能的隔离 SVG 测量，保留 raw 值并核对完整文字清单；测量依赖及限制见可靠性说明。
   `prepare --placement-run` 核验归属及快照后，以 preserve-instances 编译无损队列；dry-run 后才 execute。
   不重写求解/绘图，不抹掉未支持的快照字段；已有其他器件/线条的页面不自动清理。
7. **复验并使旧结论失效**。`verify` 开始即撤销上轮通过状态；显式 saved:true → real reload
   → fresh readback，再做 pin/net/NC、原生 DRC、结构、桥接与几何检查。中途错误记 incomplete。
   每轮检查绑定输入、证据哈希与 attempt；`deliver` 重新核验，不能用历史成功或变更后的证据交付。
8. **工程交付**。提供中文设计确认页、计划、绑定/测量来源、原始快照、队列/journal、检查与差异；
   按本机支持导出官方图、网表/BOM和原生工程。列明 offline/live 检查范围与未验证项。
   参考 [验收](references/verification.md)；尚未现场完成时明确保持 incomplete。

## 可执行入口

优先使用已经配置独立运行环境的 `eso --help`（先用 `command -v eso` 确认）；
所有下述子命令可直接作为 `eso <command>` 调用。未安装 launcher 时使用 Python ≥3.10
并安装本目录 pyproject.toml 中的依赖后，运行 `python <skill>/scripts/eso.py --help`。
不要直接假定系统默认 python3 已具备依赖。离线工作不需要 API Key 或 daemon。
完整安装/更新与离线烟雾测试见 [AI 安装指南](docs/AI-SETUP.md)；
命令例子与试用路径见 [快速开始](README.md)，阶段的输入/输出/退出条件见
[完整工作流](docs/WORKFLOW.md)。上游版本事实见 [能力调研](docs/RESEARCH.md)。

用户要求实际绘图、已确认对应 Contract 且目标是获准测试工程后，才进入 live execute。
维护工作流时运行单元测试和离线 CLI 集成；
实际工程与未验证状态保留在忽略的运行目录，不将私有 UUID、绝对路径或凭据发布到 GitHub。
