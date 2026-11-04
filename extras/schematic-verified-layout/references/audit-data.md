# 附加 Python 审计数据

这是小而明确的**离线附加审计 schema 1**，不是 EasyEDA 的 canonical 1.4 或 compose 格式。
同一目标生成 canonical 与审计视图后核对全量 pins/net/NC/ownership；工程始终以保留的目标源为权威。
`led_demo.py` 是完整可执行样例（含两块模块框，以及「同网表跨模块连线」的反例）。测试运行：`python -m unittest discover -s <skill>/tests -v`。

## target.json

```json
{
  "schemaVersion": 1,
  "components": [
    {"id":"a","ref":"R1","pins":["1","2"]},
    {"id":"b","ref":"LED1","pins":["1","2"]}
  ],
  "nets": [
    {"id":"v","name":"3V3","scope":"global","pins":[["a","1"]]},
    {"id":"series","name":"LED_A","scope":"page","pins":[["a","2"],["b","1"]]},
    {"id":"g","name":"GND","scope":"global","pins":[["b","2"]]}
  ],
  "nc": [],
  "modules": [{"id":"lamp","members":["a","b"],"core":["b"],"direct":[[["a","2"],["b","1"]]]}]
}
```

此极小数据片段的电源网只有一脚，只说明格式，不承诺通过 EDA DRC；可执行 LED 示例包括电源输入端子。

- `pins` 是准确物理脚号字符串，所有脚恰好属于一个 net 或 `nc`。未知/待确认状态不能通过。
- component `id` 稳定，`ref` 是当前位号。可选 `device:{libraryUuid,uuid}` 使对账同时校验库身份；真实落图目标应具有它们。
- `modules[].members` 恰好分配每件器件一次；`core` 必须非空且属于该模块。`direct` 每项至少两个同网引脚，它们必须处于同一物理线树。`modules` 同时是可读性审计的模块归属来源（配合 `scene.moduleFrames`）。
- `nets[].scope` 仅支持 `global`、`page`，必须映射到官方实际作用域。跨页本地信号用各自 net ID；层级网/总线不在此检查器支持范围。
- 可选 `context:{projectUuid,documentUuid}`：对账时锁定工程和单页，正式现场证据必须填写。演示省略不证明目标窗口正确。
- 元件参数、手册证据、电流等可附字段，但通用审计器**不理解电路物理功能**。LED 电流估算由例子单独算，不证明保护、时序、模拟稳定性或额定值。

## scene.json

```json
{
  "schemaVersion":1,"units":"raw","grid":5,"evidence":"synthetic-fixture","geometryScope":"full",
  "pins":[{"pin":["a","1"],"page":"P1","at":[100,100],"direction":"left"}],
  "wires":[{"id":"w1","page":"P1","points":[[80,100],[100,100]]}],
  "labels":[{"id":"pwr","page":"P1","at":[80,100],"net":"v"}],
  "junctions":[],
  "bodies":[{"id":"body-a","componentId":"a","page":"P1","bbox":[110,90,130,110]}],
  "textInventory":["name-a"],
  "texts":[{"id":"name-a","componentId":"a","role":"designator","page":"P1","bbox":[110,120,140,130]}]
}
```

上面是字段片段；实际输入必须给出 target 的全部物理脚。坐标 raw、y 向上；direction 为 left/right/up/down，是已旋转后的世界方向，不重复加组件旋转。

- `wires[].points` 是连续正交折线，消除多余共线 waypoint 后构图。官方 `flat-segments` 的每四个数是**独立线段**，适配时分别生成 wire，不得连接数组前后段制造不存在的线。
- 端点/T/共线重叠导通；严格内部 X 默认不导通，有显式 `junctions` 才连接。此为声明的离线模型，最终以安装态 EDA 回读确认。
- 不使用目标 net 名给导线“自动连接”。只有真实几何接触和实际存在的 labels 参与并查集。direct 在加入标签边之前检查。
- `bbox` 是 `[minX,minY,maxX,maxY]`；body 不包含引脚外伸线，否则合法接线会成为假穿件。文字 bbox 采用含边界碰撞；body 检查内部穿越。
- `textInventory` 来自全量原生文字清单分类后的工程避让对象；`texts` 必须逐 ID 覆盖它。不能手填空清单绕过缺测。符号内部脚号/图签合法包含关系先分类，不把它们错误地当成独立外部障碍。
- 完整几何默认 `geometryScope:"full"`：每件器件必须恰有一个绑定 `componentId` 的 body 和一个绑定的 `role:"designator"` bbox。全部参数/标签仍须由原生清单补足，不能由这些最低覆盖判据推出完整性。`topology-only` 只允许合成单元测试，不允许 `evidence:"measured"`，不可作为落图几何门。
- 可选 `allowWireIds` 只能出现在 `role:"net-label"` 的文字上，且只含一根线；必须提供真实 `markerId`，同页标记 anchor 必须位于该承载线上。body 和普通文字的线豁免会失败；不能用它忽略真实冲突。
- `evidence` 为 `synthetic-fixture` 或 `measured`，这是调用方来源声明，审计器不自动认证；保留来源文件、变换和哈希。所有报告仍是 offline 范围。
- 可选 `paper:{"name":"A4","usable":[minX,minY,maxX,maxY]}`：给出实际绘图可用区（已扣图框/图签的实测值），
  可读性审计用它判越界并算占地；缺省时跳过纸张检查并计入 `coverage.unverified`。`usable` 与 pin/wire 同单位、y-UP。
- 可选 `moduleFrames:[{"moduleId":"lamp","page":"P1","bbox":[minX,minY,maxX,maxY],"titleTextId":"t1"}]`：
  模块化布局的框。`moduleId` 必须是 target `modules[].id`，每个模块至多一个框；`titleTextId` 指向
  `texts` 里的标题文字。缺省时框相关规则（越框/重叠/缺框/空带/标题）跳过并计入
  `coverage.unverified`；跨模块实体穿透与模块跨页不依赖它，始终检查。
  框本身不是导线（用分区/矩形图元画），框内包含该模块的引脚、body、文字和本模块导线。

## 可读性审计（readability_audit.py）

`readability_audit.py` 与 `circuit_audit.py` 共用 target/scene schema 与接触模型（同一份并查集实现），
但回答不同问题：电气审计问「接对没有」，可读性审计问「能不能读」。

- 前置：`scene.grid` 必须实测（共线最小间距的 gcd），不是估计值；引脚清单与数量必须与 target 完全一致，
  否则报 `invalid-input` 而不是静默跳过。
- hard 规则：`invalid-input`、`net-short-in-geometry`、`wire-without-net`、`foreign-net-crossing`、
  `two-terminal-bends`、`net-not-physically-connected`、`out-of-usable`、`module-physical-crossing`、
  `module-split-pages`；声明 `moduleFrames` 后另加 `module-out-of-frame`、`module-overlap`、`module-frame-missing`。
- advisory 规则：`label-bridged-net`、`parallel-spacing`、`short-pin-exit`、`redundant-overlap`；
  声明 `moduleFrames` 后另加 `module-gutter`、`module-title-missing`。
- 阈值：`--max-crossings`（默认 0）、`--max-bends`（默认 2）、`--min-spacing-grid`（默认 2.0）、
  `--min-module-gutter-grid`（默认 4.0，模块框之间的空带）。
- 退出码：0=PASS / 2=FAIL。默认只有 hard 失败才 FAIL；`--strict` 时 advisory 也失败。
- 报告字段：`checkerVersion`、`passed`、`scope:"offline-readability"`、`strict`、`thresholds`、
  `findings[{rule,severity,objects}]`、`metrics`（含 crossings/bendsHistogram/minParallelGapGrid/modules/
  moduleFrames/minModuleGapGrid/paperName/occupancy）、`coverage.unverified`、`inputSha256`、`liveVerified:false`。
  `--out` 拒绝覆盖已有文件。
- 它**不覆盖**：O1/O2/O3/O5 这类语义（页面流向、锚点选择、归属、绕行合理性）、模块分组的业务意图、
  符号内部文字、原生 DRC、save/reload。这些由门 B 图面审阅与原生 gate 补足，见 [可读性门](readability-gate.md)。

## 对账与退出码

`compare` 接受 `sch list --include-device-identity --include-pins ...` 原始响应 envelope。比较单页 part 清单、可选精确库身份、所有脚号/net/NC；不能接受 net 缺失、歧义或字符串形式的 NC 布尔值。将多个页面拼入一个响应不受支持。

`target/scene/compare` 通过返回 0，失败返回 2，报告列 rule 与对象。报告文件必须新建，输入 SHA-256 为实际字节哈希。畸形 CLI 参数/文件也会非零；不要只看是否生成了 JSON。

本检查器没有覆盖：纸张/图签、铜/PCB、复杂总线/层级、ERC 方向类型、模拟/数字仿真、器件额定值、测量完备性、未声明的外围归属、保存持久化。它也没有通用寻路或装箱功能。缺少某项证据时报告未验证，继续使用 easyeda-agent 与原生导图补足，不扩大 `passed` 的含义。
纸张适配由可读性审计在提供 `paper.usable` 时判越界，但那只证明「内容框在可用区内」，不证明图签/图框正确、也不证明版面好看。
