# 执行、文字测量与验收

每次先读 [现场复盘与恢复协议](live-recovery.md)：写前能力预检、同 C 号库身份消歧、
元数据重置文字位置、部分 Apply 恢复及属性 ID 重建后的语义对账，都是本流程的前置约束。

## 1. 确认能力和输入

先读同级 `easyeda-agent/SKILL.md`，再按需读它的 `references/schematic-data.md`、`auto-layout-sop.md`、`schematic-wiring.md`。本技能不复制其大体量 schema。
每轮用 `health` 和当前命令 `--help` 核实版本/接口，版本差异是诊断，不凭文字推定 action 可用或不可用。
`health` 同时用来解析窗口：同一工程可能开在多个 EasyEDA 窗口里，windowId 在刷新/重连/`doc reload` 后都会变，必须每次重新取，不能写死；确认精确工程/页、实际加载的 Connector 和所需 ownerType 能力，不能只选高版本。连接器退化、假失败、导出卡死的处置见 [eda-cli-pitfalls.md](eda-cli-pitfalls.md) §A/§G。

通过 typed 命令保留工程文档列表和逐页完整快照：

```text
easyeda sch list --project <project> --doc <page> --include-device-identity --include-pins --include-bbox --include-wires --include-page-primitives
easyeda sch connectivity --all-pages --project <project>
easyeda sch designator-geometry --project <project> --doc <page> --out <run>/designators.json
easyeda sch sheet-geometry --project <project> --doc <page> --json
easyeda sch export-image --project <project> --doc <page> --format svg --out <run>/before.svg
```

新电路先查询精确器件/完整物理引脚及数据手册。需要在 EDA 内放置真实器件测量时，用独立的测量页并记录 `measurement-only`；仅放件且保持未连接，不将测量页当成完成的电路，也不覆盖用户工作页。没有测量时可继续做明确标记的离线探索。

目标源保留：组件稳定 ID、库身份、全部引脚、net/NC、模块归属、核心/外围/串联关系、direct 引脚组、作用域、阅读顺序及已授权姿态。Python 审计格式只是同一目标的派生视图，必须逐 pin 核对转换与 canonical 1.4 数据完全一致；禁止独立维护两份不同的电路。

## 2. 原生 SVG 全部文字

`designator-geometry` 只给位号；型号、参数、标签、自由文字、模块标题需要补测。

```text
python <skill>/scripts/measure_svg_text.py <run>/before.svg --transform a b c d e f --out <run>/text-measurement.json
```

矩阵将 SVG 根用户坐标变换到 EDA raw：`X=a*x+c*y+e; Y=b*x+d*y+f`。
先用 SVG 中能按 ID 对应的至少三个不共线现场引脚/导线端点校准。常见原生导图可能仅需 `1 0 0 -1 0 0`，但不能不核对就套用。保存校准点和原始文件 SHA-256；不得用文字 bbox 反推自己的校准正确。

脚本在新的临时 Chromium profile 中离线渲染静态 SVG，等待字体加载，使用 `getBBox()` 并合并父层 transform；保留每条文字的 ID、原文、字体和 bbox，不靠字数估宽。需要本机 Edge/Chrome/Chromium；可用 `--browser <absolute-path>` 指定。

输出的 `unverified` 不是成功豁免：

- 对照原生 EDA 导图确认字体和形态，字体替换要处理后重测。含 stroke/filter 的文字须补上实际占位；本脚本只测填充几何。
- `<path>/<use>` 字形、嵌入图像中的字不能由 `<text>` 枚举证明完整，保留未覆盖项。商标/图框内固定内容可明确归为装饰；工程标签不能静默排除。
- 根据对象 ID/官方属性关联建立 owner/type/page 映射，**不要按最近位置或字符串相等猜归属**。采集全部文字清单，然后分类：位号、参数/型号、网络标签、自由文字、图签、符号内部脚名/脚号。
- 所有需要避让的文字均进入障碍表；符号内部脚名/图签结构的合法包含关系用明确对象关联处理。网络标签与其自己的承载线允许交叠时只豁免那根线，不豁免同网所有导线。
- 一张 SVG 的文字不能当另一字体、另一内容、另一旋转姿态的测量。若无法取得某一项，覆盖状态为 `unverified`。

有 bbox 后按宽度/受限程度降序安排标签，枚举网格合法位置；每次先检查所有文字、引脚出口、符号和线路，再接受移动。参数文字的碰撞不能靠隐藏显示来消除。若现有求解器不能接收这些障碍，增加外围间距/标签通道等源约束并重算，或扩展算法后测试；不将检查失败的数据交给 Apply。

## 3. 计算与独立审计

优先复用安装态支持的：

```text
easyeda sch lib-layout --from <run>/layout-input.json --out <run>/composition.json
easyeda sch layout-plan --help
easyeda sch layout-sheet-plan --help
easyeda sch compose --help
```

模板表达“核心、外围、端口、直连组和相对方向”；模块内部由实测几何计算。每个模块必须有独立框与标题（落成 `scene.moduleFrames`），模块之间只走标签/netport，框间留 ≥4g 空带；跨模块的连续导线是结构错误，不是风格问题。重复模块可参数化复制并重新分配 ID/ref/net，不能复制保留同名本地信号造成误连。模块装箱按整套合法几何平移（框与标题随块移动）；搜索过程固定枚举顺序、种子和预算。

页边界必须来自真实绘图区和图签；估算 A4 内缩边界仅作候选探索。多页保持标签作用域/层级含义；本附加检查器仅支持 global/page 两种简单作用域，层级端口/总线先用 canonical 和官方网表展开验证，不能强行改成 global。

目标、候选及原始测量保持独立：检查器从几何重建物理线岛，再按合法标签建立逻辑网络；分别核对 short/open/direct/NC。对生成器的新能力至少加入：正确例、删线、异网 T/端点接触、同名标签替代 direct、文字碰线、漏脚/漏测、平移保持不变量和预算耗尽反例。

本包的 Python 示例演示单个小电路参数化计算和独立验错。通用自动路由、有限搜索、多模块装箱由 easyeda-agent 工具执行；本包没有重新实现它们，更没有证明其任意复杂图的成功率。

## 4. 落图与持久化

1. 保存完整候选与全量检查，取得新鲜 `before`。
2. 由 `compose --from ... --before ... --playbook ...` 编译；现有相同器件重排采用当前文档支持的 `--replace --preserve-instances`，精确连接变化另声明，不擅自删页。
3. 根据当前 help 执行 `sch apply <queue> --dry-run`，检查后执行正常 Apply。只有用户要求离线预览时不执行这一步。
4. 逐页回读，用 `design-diff`/`connectivity-diff` 和附加审计对账；比较命令的 scope/unverified，不把退出码 0 当所有字段一致。
5. `scripts/readability_audit.py --target ... --scene ... --out readability-<rev>.json --strict`（门 A，规则表见 [readability-gate.md](readability-gate.md)）；模块化硬门里的跨模块实体穿透与模块跨页始终检查，声明 `moduleFrames` 后再加越框、框重叠、缺框。随后在干净窗口导出的原生 SVG/PNG 上按门 B 追主路径、留审阅记录；`sch gate --strict --json` 及原生 DRC；全部文字和页面范围单独报告。门 A、门 B 与原生 gate 都过才算完成。
6. `sch save` 布尔 `saved:true` → `doc reload <page> --json` → 新鲜回读/导图 → 重做对账及受影响检查。
7. 必要时导出 `.epro2`。压缩包完整不等于重新导入恢复成功；分别陈述。

失败与卡死的处置顺序（详见 [eda-cli-pitfalls.md](eda-cli-pitfalls.md) §G）：

- **先复核再重发**：超时/`DISPATCH_FAILED`/`connector did not respond` 都可能是「假失败」，动作已落地。先 `sch list`/`doc ready` 轻读确认，盲目重发会造出重复端口与重复旗子。
- **先保存再恢复**：卡死时 `sch save` 通常仍可用；拿到 `saved:true` 后才 `web reload`（需活跃文档 + 精确 uuid）或 `project open --allow-discard-unsaved`。恢复后 windowId 已变，重新 `health`。
- **导出要趁干净窗口**：`export-image` 失败会留下 stuck toast 并连锁超时。在刚重开、还没做变更的窗口里导出，成功后再继续改图；`--scale` 参数不存在。
- **strict 判据以当前参数和原报告为准**：warning 可能阻断通过，保留严格失败；独立检查的预声明 advisory 可逐项解释，不能将原始 FAIL 改成 PASS，不能一律放行模式告警。
- **netlist 逐脚对账**：包装 JSON 按原始字节和实际编码解析，不把 shell 重定向的 UTF-16 当作 CLI 固定协议。真网表从 `result.artifactPath` 读取。冻结意图使用 `nets`、`nc`、显式 `allow_swap`，用 `scripts/netlist_diff.py --enet <页.enet> --expect <expected.json>` 逐脚 diff；无极性件引脚互换只在声明范围内放行。
- **原生 gate 不管可读性**：`sch gate`/DRC 通过只说明电气与几何硬项为 0；异网交叉、多折弯、标签掩盖断线、平行间距、纸张越界、模块穿透/越框/框重叠由 `scripts/readability_audit.py` 判（门 A），页面流向/锚点/归属/绕行/模块划分是否合理只能在渲染图上追（门 B）。两门都过才允许报完成，见 [readability-gate.md](readability-gate.md)。
- **调用 CLI 走包装器**：用 `scripts/edacall.py --job job.json` 传 argv 列表，避免 shell 吞引号、BOM 与 stderr 混入 JSON；`easyeda health` 这类命令会把人类可读信息写 stderr、JSON 写 stdout，混流必崩。

修复只改目标副本、测量或生成器，留存失败证据后重新生成。不要修改快照、SVG、journal 或生成队列来使报告变绿。边界清晰的局部修改可增量完成上述流程，不需要重新询问已授权动作。
