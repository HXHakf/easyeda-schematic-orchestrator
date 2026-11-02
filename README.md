# EasyEDA Schematic Orchestrator / Designer

面向嘉立创 EDA 专业版的 AI 原理图设计工作流 Skill，复用
[easyeda-agent](https://github.com/zhoushoujianwork/easyeda-agent) 的 CLI、daemon 和 EDA Agent Connector。
当前版本：**0.2.1**（MVP，离线已验证；现场全链路尚未验收）。

## 让 AI 从这个链接开始

将以下内容发给 Codex 或能读 GitHub、运行本机命令的 AI：

> 请读取 https://github.com/HXHakf/easyeda-schematic-orchestrator ，按根目录 AGENTS.md 和
> docs/AI-SETUP.md 安装此 Skill，先检查现有环境，运行离线烟雾测试。
> 复用已有 easyeda-agent 与 EDA Agent Connector；不要安装 Gateway。报告各层就绪状态，安装阶段不绘图。

AI 入口：[AGENTS.md](AGENTS.md) · [完整安装/更新/使用步骤](docs/AI-SETUP.md) · [主控 Skill](SKILL.md)。
本项目部署到本机，不需要另外配置模型 API Key，也没有需要部署的服务器。

macOS/Linux，Python ≥3.10：

```bash
git clone https://github.com/HXHakf/easyeda-schematic-orchestrator.git
cd easyeda-schematic-orchestrator
python3 scripts/install.py --dry-run
python3 scripts/install.py
"$HOME/.local/bin/eso" --version
```

Windows、已有安装的更新、路径自定义和上游缺失组件处理见 [安装指南](docs/AI-SETUP.md)。
安装脚本提供完整 Skill、专用 Python 环境和命令入口，已有副本保留备份；不访问 EDA 或修改 shell 配置。
默认安装路径若已有未受管理文件会拒绝覆盖，按错误说明检查并选择路径。

## 使用方式与能力边界

直接说“我要画原理图”“帮我设计一个 STM32 最小系统”“画一个 LDO 电源电路”即可进入需求流程。
自动触发不代表立即写入工程；先补齐关键条件，确认设计后才执行。

```text
电路类型 → 分轮补问/帮我选 → 中文设计确认 → 电路计划与真实库绑定
   → stage 初始放置 → collect 实测与上游布局 → prepare/dry-run → execute
   → save/reload/readback 验收 → 证据核验与交付
```

## 0.2 的改进

- 按 MCU 最小系统、MCU+传感器、LDO、其他电路组织问题。LDO 不再要求 MCU/传感器。
- 默认值每轮根据当前条件重算；裸芯片改模块、输入电压变化等会撤销旧推断，保留明确指定的值。
- next_questions 每轮至多三问；“帮我选”转为 Agent 选型任务，不当作芯片型号。
- contract 自动生成中文确认页，内部管理哈希；参数/目标变化使旧确认失效。
- stage/collect 衔接真实器件、完整引脚/位号/SVG 测量到上游 lib-layout，再无损编译本轮实例。
- 复验开始即使旧成功失效；失败、证据变化或复验中断不能继续交付为通过。

**当前尚未完成真实 EDA 全链路验收。** 安装的上游 v1.16.0 存在十类/十一类快照兼容问题，
会在放置前拒绝该组合。模拟流程、真实上游离线布局/compose/dry-run 不等于现场绘图通过。

需求分类支持不等于任意型号已有自动拓扑。内置计划为 STM32F103C8T6 最小系统及 BMP280 参考；
LDO 和其他型号可由宿主模型依据原厂资料编写 plan，再验证与执行。不能把 BMP280 当温湿度传感器。

## 离线使用

Python ≥3.10。保持整个 Skill 目录，包含 eso、schemas、scripts、references。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python scripts/demo_workflow.py --out runs/offline-demo
python -m unittest discover -s tests -v
```

安装了独立运行环境时直接使用 eso；否则用 python scripts/eso.py 替代。

```bash
eso intake --text '我要画一个STM32最小系统' --out runs/requirements.json
eso assess runs/requirements.json
eso update runs/requirements.json --answers answers.json --out runs/requirements-next.json
eso contract examples/requirements.json --out runs/contract.json
eso plan runs/contract.json --out runs/plan.json
eso validate-plan runs/plan.json --contract runs/contract.json
```

查看 contract 命令生成的 .md 中文确认页；不要把内部摘要当作用户需要输入的答案。
intake 只做保守种子提取，宿主模型继续复核否定、功能、纠正和约束；ready=true 不证明电气设计正确。

## 接入上游

独立安装 easyeda-agent CLI 与 Skill，复用已有 Connector；不安装 Gateway、不重写绘图。

```bash
eso --easyeda /path/to/easyeda discover --out runs/private/capabilities.json
EASYEDA_BIN=/path/to/easyeda python -m unittest discover -s tests -v
```

测试只调用上游的离线计算/编译与 dry-run；不调用真实工程写入。

## 真实绘图的前提与入口

先取得用户明确确认的 Contract 和精确测试工程/页面 UUID，核实真实库与器件引脚。
工程名称须 ESO-TEST-*，初始页必须为空白；不接管重要工程或已有任意图纸。

```bash
eso contract requirements.json --target target.json --out runs/contract.json
# 用户确认中文设计内容后，Agent 内部记录同一版本：
eso confirm runs/contract.json --sha256 REVIEWED_HASH --actor HUMAN_NAME --out runs/approval.json
eso stage --contract runs/contract.json --approval runs/approval.json --plan plan.json \
  --bindings bindings.json --run-dir runs/private/staging
eso collect --run-dir runs/private/staging
eso prepare --contract runs/contract.json --approval runs/approval.json --plan plan.json \
  --composition runs/private/staging/composition.json --before runs/private/staging/measured-snapshot.json \
  --placement-run runs/private/staging --run-dir runs/private/final
eso execute --run-dir runs/private/final
eso verify --run-dir runs/private/final
eso deliver --run-dir runs/private/final --out runs/private/manifest.json
```

具体证据与绑定要求见 [测量衔接](references/measurement-bridge.md)。
已有合格 composition 也可直接 prepare，未提供 placement-run 时仍只接受空白测试页。
每次 prepare 使用新目录。失败后先回读/诊断；不能盲目重复 stage/execute 或删除执行锁继续。
verify/deliver 未通过返回退出码3；输入/操作拒绝为2；正常命令为0。

## 文档

- [工作流](docs/WORKFLOW.md) · [架构](docs/ARCHITECTURE.md)
- [输入与追问](references/input-and-questions.md) · [设计确认](references/design-contract.md)
- [上游适配](references/upstream-adapter.md) · [验收](references/verification.md)
- [验证记录](docs/VALIDATION.md) · [独立行为评估](docs/TRIGGER-EVAL-0.2.md)
- [贡献方式](CONTRIBUTING.md)

MIT。与嘉立创、ST、Bosch 无官方隶属关系。私有运行目录可能含工程 UUID 和本机路径，发布前须去标识。
