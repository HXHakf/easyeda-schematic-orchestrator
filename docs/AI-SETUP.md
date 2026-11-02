# 从 GitHub 链接安装并使用：给 AI 的执行指南

仓库：https://github.com/HXHakf/easyeda-schematic-orchestrator

本项目部署的是**本机 Skill + Python CLI**，没有网站、服务端、模型 API Key 配置或云端 EDA。
宿主 AI（例如 Codex）理解自然语言、查资料、发问和确认；`eso` 管理结构化输入、规则和执行证据。
`easyeda-agent` 提供绘图、回读与检查，EDA Agent Connector 运行在嘉立创 EDA 专业版中。

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
默认输出位置：

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
其他宿主可用 `--skill-dir` 指向其支持的 Skill 位置，但本项目仅实测 Codex 发现与 macOS 安装；
Windows/Linux 安装器分支及其他宿主尚未现场验证。

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
状态仍待用户确认；不得出现 approval 或 EDA 调用。未设置 `EASYEDA_BIN` 时三项上游离线集成会跳过。

## 4. 补齐上游能力（已有则不重复安装）

上游源码/安装文档：[easyeda-agent](https://github.com/zhoushoujianwork/easyeda-agent)。
本项目实测基线为 [v1.16.0 Release](https://github.com/zhoushoujianwork/easyeda-agent/releases/tag/v1.16.0)，
不是推荐其作为完整现场兼容组合。优先保留本机已有安装，先检查实际能力再决定是否升级。

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

**已知现场阻断**：实测 CLI v1.16.0 compose 接受十类 pagePrimitives，而 Connector 回读有十一类（含 pins）。
stage 在首次放置前拒绝此组合。不要删除 pins、伪造快照或跳过检查；保持 `live-incomplete`，
待上游兼容版本经测试后再执行。不要仅因为上游 main 有新代码就宣称已修复。

完整入口、参数、测量与重放约束见 [README](../README.md)、[工作流](WORKFLOW.md) 和
[测量衔接](../references/measurement-bridge.md)。内置确定性计划限 STM32F103C8T6 最小系统与 BMP280 参考；
LDO/其他电路由宿主 AI 依据原厂证据编写 plan，不宣称任意电路自动设计。

## 6. 向用户报告安装结果

分别报告已安装路径、实际版本、离线测试结果、上游组件是否齐备、连接是否实测、现场绘图是否验收，
以及下一项具体阻断。不要用一个笼统“成功”覆盖这些状态。禁止将本机绝对路径/工程 UUID/运行记录提交公共仓库。
