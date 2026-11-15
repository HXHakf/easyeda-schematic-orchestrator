# 从 GitHub 链接安装并使用：给 AI 的执行指南

仓库：https://github.com/HXHakf/easyeda-schematic-orchestrator

本项目部署的是**本机 Skill + Python CLI**，没有网站、服务端、模型 API Key 配置或云端 EDA。
宿主 AI（例如 Codex）理解自然语言、查资料、发问和确认；`eso` 管理结构化输入、规则和执行证据。
`easyeda-agent` 提供绘图、回读与检查，EDA Agent Connector 运行在嘉立创 EDA 专业版中。

通用工作流的安装与发布检查覆盖知识库、设计规范、软件和代表性 EDA 执行流程。
具体项目开始后，再依据需求确定型号与工况。用户只提供型号时，宿主按
[官方手册与应用电路](../references/input-and-questions.md#官方手册与应用电路)获取手册并审阅参考电路。
安装后可先按[常用元件类别](../references/component-knowledge.md)查阅 25 类基础卡，再关联具体应用电路。

## 1. 安装前只读检查

- 检查 Git、Python ≥3.10（包括 `venv` 和 `pip`）；部分 Linux 发行版需另装对应的 python3-venv。
- 检查既有 `eso --version`、`easyeda --version`、两个 Skill 目录及已有 Connector。版本号一致不证明协议兼容。
- 无 Python ≥3.10 时先选择可用的新解释器；不要假设系统默认 `python3` 足够新。
- 复用已有 Connector；此时无需打开工程，也不启动 daemon，不调用真实写入。

## 2. 获取源码并安装

macOS/Linux（`python3` 须满足版本；若不满足，换成已安装的具体解释器）：

```bash
git clone https://github.com/HXHakf/easyeda-schematic-orchestrator.git
cd easyeda-schematic-orchestrator
python3 scripts/install.py --dry-run
python3 scripts/install.py
"$HOME/.local/bin/eso" --version
```

Windows PowerShell（先确认 `py -3 --version` ≥3.10）：

```powershell
git clone https://github.com/HXHakf/easyeda-schematic-orchestrator.git
cd easyeda-schematic-orchestrator
py -3 scripts/install.py --dry-run
py -3 scripts/install.py
& "$HOME\.local\bin\eso.cmd" --version
```

安装脚本仅复制明确列出的源码/参考文件，创建专用虚拟环境，通过 pip 安装项目及依赖，
生成 launcher；不下载上游二进制、不安装/替换 Connector、不访问 EDA，不修改 shell 配置。
完整 Skill 包含 `extras/schematic-verified-layout` 的布局规范及附加审计工具，文字测量复用
随 Python 包分发的 `eso.svg_text`。wheel 单独提供核心 CLI；完整 Skill 使用本安装器。
安装器生成的 launcher 使用 Python 隔离模式，从专用环境加载已安装版本。开发仓库源码时使用
`PYTHONPATH=. python -m eso.cli`；检查部署版本时调用安装器输出的 launcher。依据见
[Python 隔离模式文档](https://docs.python.org/3.13/using/cmdline.html#cmdoption-I)。
默认输出位置为下表。

| 内容 | 位置（相对用户主目录） |
|---|---|
| 完整 Skill | `.agents/skills/easyeda-schematic-orchestrator` |
| 专用 Python 环境 | `.local/share/easyeda-schematic-orchestrator/venv` |
| 命令入口 | `.local/bin/eso`；Windows 为 `eso.cmd` |

不在 PATH 时直接使用完整 launcher 路径，不必先改全局环境。
可通过 `--skill-dir`、`--runtime-dir`、`--bin-dir` 指定互不包含且在源码外的目录。
更新时在原 checkout 执行 `git pull --ff-only` 后重跑安装器；保留旧 Skill 备份，不删除用户文件。
遇到已有未标记的 Skill/命令时先检查；确认是应替换的副本后才加 `--replace`，旧副本会备份。
未标记的既有 runtime 不接管，请选新 `--runtime-dir`。安装失败不代表已整体回滚，依据错误重试或恢复备份。
卸载时仅删除安装输出中列出的专用目录/launcher；用户的运行证据与上游组件分别保留。

Codex 应发现两个独立 Skill：本项目负责需求编排，`easyeda-agent` 负责 EDA 工具。
若未发现新 Skill，让客户端重新加载或重启；不要为了测试发现机制创建用户聊天或修改模型设置。
其他宿主可用 `--skill-dir` 指向其支持的 Skill 位置。CI 覆盖 Linux、macOS、Windows 的
独立安装、重复安装和安装后回归，Codex Skill 发现与 EDA 现场验收已在 macOS 实测。
其他宿主的 Skill 发现方式仍需按该宿主核验。

## 3. 离线烟雾测试（无 EDA 写入）

使用安装输出中的专用 Python（macOS/Linux 示例）：

```bash
"$HOME/.local/share/easyeda-schematic-orchestrator/venv/bin/python" scripts/demo_workflow.py --out runs/offline-demo
"$HOME/.local/bin/eso" intake --text '我要画一个STM32最小系统' --out runs/intake.json
"$HOME/.local/bin/eso" assess runs/intake.json
"$HOME/.local/share/easyeda-schematic-orchestrator/venv/bin/python" -m unittest discover -s tests -v
```

Windows 将上述 Python 换为 `.local\share\easyeda-schematic-orchestrator\venv\Scripts\python.exe`，
命令入口换为 `eso.cmd`，PowerShell 用 `& "完整路径" 参数` 执行。
自定义目录时使用安装器实际输出，不能继续抄默认路径。
成功条件：版本输出、出现缺参问题、demo 产生中文 Contract **草稿**和电路计划，
状态仍待用户确认；不得出现 approval 或 EDA 调用。上游离线集成需要设置 `EASYEDA_BIN`，
未设置时对应测试显示跳过，具体数量以当前测试输出为准。

## 4. 补齐上游能力（已有则不重复安装）

上游源码/安装文档：[easyeda-agent](https://github.com/zhoushoujianwork/easyeda-agent)。
代表性 EDA 现场验收使用配套的 v1.17.0 CLI、daemon 与 Connector，范围见
[验证记录](VALIDATION.md)。历史 v1.16.0 快照兼容问题见下文。
优先保留本机已有安装，先检查实际能力再决定是否升级。

若缺 CLI 或上游 Skill，AI 按以下方式安装缺失项：

1. 从上游 Release 取得当前平台的 CLI、`skills.tar.gz` 和 `checksums.txt`，核验 SHA256。
   只安装 CLI/Skill，**不安装 Desktop、不替换 Connector、不安装 Gateway**。
   可审阅同一 Release 的官方 `install.sh` / `install.ps1` 再按其参数执行，避免不经审查执行 main 脚本。
2. `easyeda-agent` Skill 位于上游 `.agents/skills/easyeda-agent`；复制完整目录到宿主 Skill 位置。
   从源码复制时必须使用和 CLI 匹配的 tag，不能把 main Skill 当成 Release 已提供的动作。
3. 保留并核实用户已有 EDA Agent Connector；缺失时才按上游插件市场说明让用户安装、启用外部交互。
4. 执行 `easyeda --version`、`easyeda --help`、`easyeda actions`（无 `--json`），并读取本项目
   [上游事实与兼容问题](RESEARCH.md)。仅离线测试无需 daemon/Connector。

可选：设置 `EASYEDA_BIN` 为绝对 CLI 路径后运行单测，额外覆盖真实上游离线
materialize/lib-layout/compose/apply dry-run。该测试不会执行非 dry-run apply。

## 5. 用户开始设计时

读取 [SKILL.md](../SKILL.md)。自然语言触发示例：

> 我要画一个 STM32 + I2C 传感器原理图，缺什么先问我。

分类 → 每轮最多三问/选型 → 重算默认 → 中文 Design Contract → 用户确认 → 完整 plan 与真实库绑定。
用户只要方案或正在安装/开发工作流时，可停留离线，不追问测试工程。

实际绘图前才要求可丢弃的 `ESO-TEST-*` 空白原理图页，并核对工程/页面身份及 Connector 连接。
`eso discover` 只读采集连接、动作和现场快照（不自动启动 daemon）；需要启动时，
先检查当前 `easyeda daemon start --help`，按用户已授权的本机测试用途启动，避免自动 Skill 更新。
保存原始发现结果到忽略的私有运行目录。

**历史兼容问题**发生于 CLI v1.16.0，其 compose 接受十类 pagePrimitives，Connector 回读有十一类（含 pins）。
stage 在首次放置前拒绝此组合。配套 v1.17.0 已完成限定现场验收，当前环境仍须通过实际快照的
compose/dry-run 探针。遇到不兼容时保留原始快照与 `live-incomplete` 状态，按实际能力处理。

完整入口、参数、测量与重放约束见 [README](../README.md)、[工作流](WORKFLOW.md) 和
[测量衔接](../references/measurement-bridge.md)。内置 MCU／传感器参考包含 STM32F103C8T6 最小系统与 BMP280，
知识库另支持受限 NPN/NMOS 开关计划。通用元件卡提供宿主审阅资料，LDO 等其他电路由宿主依据原厂证据编写 plan。

## 6. 向用户报告安装结果

分别报告已安装路径、实际版本、离线测试结果、上游组件是否齐备、连接是否实测、现场绘图是否验收，
以及下一项具体阻断。不要用一个笼统“成功”覆盖这些状态。禁止将本机绝对路径/工程 UUID/运行记录提交公共仓库。
