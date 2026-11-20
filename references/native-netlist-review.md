# 原生网表补充审查

绘图前，电气审阅按设计涉及的电路域组织。围绕电源、保护、复位、接口等实际功能检查工作状态，并记录位号、物理引脚、网络、观察、影响和依据。需要手册才能判断的问题记为待核实，无法从网表得出的 PCB 或实物结论按原有验收范围标明。[官方审查清单](https://github.com/easyeda/easyeda-schematic-review-skill/blob/b1b9ebe0970bf6241c2cf6f7d65acb087001a371/references/review-checklist.md)

## 保存重开后的规则审查

复用 easyeda-agent 现有网表出口，保存与重开后从精确绑定的目标导出 JLCEDA `.enet`，保留导出回执和本轮快照。取 `easyeda sch netlist` 回执中的 `result.artifactPath`，使用实际的 `.enet` 文件。

```sh
eso review-netlist --netlist runs/current/design.enet --out runs/current/netlist-review
```

默认从 `~/.local/share/easyeda-schematic-review` 读取经验证的官方工作树，也可用 `--review-skill` 指定已有工作树。使用本机 Git 与 Node.js 18+。校验通过的上游版本为 1.0.0，固定到以下公开提交。官方项目单独分发，许可为 Apache-2.0。[官方源码](https://github.com/easyeda/easyeda-schematic-review-skill/tree/b1b9ebe0970bf6241c2cf6f7d65acb087001a371)

离线命令执行标准规则模式，报告和复制的网表保留在新的私有目录。缺少依赖时记录附加审查的覆盖情况，继续原有审阅。此步骤不连接官方 Gateway，也不请求逐包 AI 操作。

## 怎样处理报告

1. 核对 `eso-review.json` 记录的网表 SHA-256 与实际交付文件。结合 `lint.json` 的未覆盖规则和完整发现评估结果。打开 `report.html` 可以筛选问题。需要定位时，按位号和引脚用现有原生 SVG 核实，避免把页内 ID 当成整工程唯一标识。
2. 优先复核 P0/P1，例如疑似地线短接、电源脚开路、USB-C CC 缺失或 I²C 上拉缺失。紧接着看本任务所改电源、时序和接口的其余发现。每条保留规则 ID、对象、证据和处理结论。对照已确认的方案、NC 标记及器件手册判断有效问题、有依据的例外或待核实项目。
3. 助手修复已证实且在确认范围内的错误，保存重开后对新网表复跑。涉及已确认的电气取舍时再集中询问。正常连通性对账、EDA 检查及独立图面审阅继续用于最终验收。把规则报告作为附加证据随交付保留。

历史样例的网表可触发 R002 悬空提示，其中包括已声明的 NC。规则依赖网表与命名推断，另有 BOM 不在输入、布局无几何等覆盖限制。退出码 0 表示报告生成。具体电气结论以复核证据为准。[规则与误报说明](https://github.com/easyeda/easyeda-schematic-review-skill/blob/b1b9ebe0970bf6241c2cf6f7d65acb087001a371/references/rule-catalog.md)
