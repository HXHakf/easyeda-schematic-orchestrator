# 可读性门：门 A（确定性）与门 B（图面审阅）

电气对账通过 ≠ 图能读。本门在「连接正确」之后再加一道关：先跑门 A 的确定性检查，
再做门 B 的渲染图面追路径。两门都过，才允许把一页称为完成。

门 A 覆盖结构；门 B 覆盖结构审计覆盖不到的语义（页面流向、锚点、归属、绕行）。
任何一门没过，交付说明里必须点名失败对象与位置，不能只写「基本可读」。

## 门 A：离线确定性可读性检查

```powershell
python <skill>/scripts/readability_audit.py --target target.json --scene scene.json `
  --out readability-001.json [--strict] [--min-spacing-grid 2.0] [--max-bends 2] [--max-crossings 0] `
  [--min-module-gutter-grid 4.0]
```

- 输入与 `circuit_audit.py` 共用 [target.json / scene.json](audit-data.md)；scene 必须给出 `grid`
  （实测，不是估计）与全部物理脚。坐标 raw、y-UP。模块边界规则由 target 的 `modules` 直接判定；
  框、标题与框间空带要在 scene 里声明 `moduleFrames`（字段见 [数据格式](audit-data.md)）。
  不声明 frames 时框规则为空转，必须当作未验证写进交付说明。
- `--out` 必须是不存在的新文件；已存在则拒绝运行（证据不覆盖）。exit 0=PASS / 2=FAIL。
- 先跑 `circuit_audit.py scene` 再跑本工具：电气不过时不需要看可读性，但两者报告都要留档。

### 判定表

| 规则 | 级别 | 阈值 / 触发条件 | 修复方向 |
| --- | --- | --- | --- |
| `invalid-input` | hard | scene 与 target 的引脚清单/数量不一致、schema 或 grid 非法 | 补测，不静默跳过 |
| `net-short-in-geometry` | hard | 物理接触把两个异网连成一棵树 | 拆线/改路径，先查共线合并 |
| `wire-without-net` | hard | 导线两端不接触任何引脚/标签 | 接回或删除 |
| `foreign-net-crossing` | hard | 异网导线在两者**内部**正交相交（默认 `--max-crossings 0`） | 重排支路或拆块；禁止加标签掩盖 |
| `same-net-crossing` | hard | 同网导线形成无显式接点的内部 X，计入同一交叉上限 | 优先重排为可直接追踪的支路；真实连接节点按电气意图表达 |
| `text-frame-overlap` | hard | 任意可见文字 bbox 与同页模块框线相交或相切，包含自由说明 | 将完整文字移到净空区域，或按内容重新安排模块 |
| `object-overlap` | hard | 同页文字与文字、文字与器件或器件之间 bbox 相交或相切 | 移开对象并重新测量完整外部文字 |
| `wire-text-overlap` | hard | 导线接触文字 bbox | 重排文字或导线路径 |
| `wire-body-overlap` | hard | 导线穿过器件 body 内部 | 核对引脚出口，重排器件或导线 |
| `invalid-wire-exemption` | hard | 声明的导线豁免缺少同页、同线的真实网络标记绑定 | 修正标记绑定，保留其他碰撞检查 |
| `two-terminal-bends` | hard | 两端网络折弯数 >`--max-bends`（默认 2） | 缩短路径、改画法角色、拆块 |
| `net-not-physically-connected` | hard | 非电源网、无标签、却分散在多个接触树 | 补实体线；确认不是该用标签的出页网 |
| `out-of-usable` | hard | 内容框越出 `scene.paper.usable` | 缩小间距/换图幅/分页 |
| `label-bridged-net` | advisory | 同模块内部网络靠标签跨树连接，或边界树缺少匹配标签 | 同模块优先实体直连。已声明跨模块边界只有在每模块内该网各有一棵物理树、且每棵树都有同名标签时通过；direct 组和未经声明的跨模块实体线检查独立执行 |
| `parallel-spacing` | advisory | 异网平行段中心距 <`min-spacing-grid`×grid | 拉开到 ≥2g |
| `short-pin-exit` | advisory | 引脚首段 <2g 就拐弯 | 先沿引脚向外走满再拐 |
| `redundant-overlap` | advisory | 同网共线重叠（可被静默合并） | 合并成一根或错开 |

- 默认模式只有 hard 失败才算 FAIL；`--strict` 时 advisory 也失败（`.enet`/交付前用 strict）。
- `crossings` 统计同网与异网的无接点内部 X，`sameNetCrossings` 单独列出同网数量，
  `textFrameCollisions` 列出文字穿框数量。显式连接节点和 T 形接触按连接模型核对。
  完整 ESO 分发中的 `eso.drawing_checks` 由本审计、候选比较和交付检查共同复用。
- `objectOverlaps`、`wireTextOverlaps` 和 `wireBodyOverlaps` 记录当前碰撞数。
  候选与交付入口直接重查当前 scene，旧报告即使记录通过也无法放行这些碰撞。
  `contentAreaRaw2` 和 `totalWireLengthRaw` 与折弯统计用于比较紧凑候选。
  所有候选仍需通过零碰撞检查及同尺度原生预览审阅。
- `pinAlignmentReview` 列出同页、两个器件脚实际直连、出口相向且路径有折弯的网络。每项给出位号与物理脚号、端点坐标、轴向、错位量、最少折弯路径的线长，以及线长超出当前端点曼哈顿距离的部分。
  错位导致的两折可能没有超额线长，仍应检查能否移器件对齐。此字段提示复核位置，移动后是否可走直线由重排、实际净空和复验决定。
- 器件移动、旋转或元数据更新后重新回读与测量外部文字。保存重开后的最终导图必须包含位号、
  数值、型号、网络名和自由说明。原生 `sch gate` 的 INFO 交叉仍计入本门。
- 豁免只允许写进交付说明的文字理由，**不能改输入数据绕过**。单页超过 3 处间距豁免即判不通过。
- 报告里的 `coverage.unverified` 必须原样进交付说明，至少包括：渲染阅读路径、无 paper 时的纸张
  适配、模块分组意图、符号内部文字、原生 DRC、save/reload、额定值。

### 模块化硬门（G0）

模块归属来自 target `modules`，**边界规则始终生效**。
跨模块实体线按显式主路径和本地 direct 关系检查准确端点，其他边界使用标签/netport。
模块不得跨页。声明 `scene.moduleFrames` 后再加实际绘图组的框规则；
不声明 frames 时框规则跳过并计入 `coverage.unverified`（「未验证」，不是「通过」）。

| 规则 | 级别 | 前置 | 阈值 / 触发条件 | 修复方向 |
| --- | --- | --- | --- | --- |
| `module-physical-crossing` | hard | 始终 | 跨逻辑模块的物理树含主路径及本地 direct 关系未授权的端点 | 按准确端点重构连接或标签边界 |
| `module-split-pages` | hard | 始终 | 同一模块的引脚分布在多页 | 拆页，不拆模块 |
| `module-out-of-frame` | hard | 需 frames | 模块的引脚/body/文字/本模块导线越出框 | 扩框或收回内容，不要删掉框 |
| `module-overlap` | hard | 需 frames | 两块框相交或相切（间距 ≤0） | 拉开到 ≥`--min-module-gutter-grid`×g |
| `module-frame-missing` | hard | 需 frames | 有引脚的模块未声明框 | 补框与标题 |
| `module-gutter` | advisory | 需 frames | 框间距 <`--min-module-gutter-grid`×g（默认 4g） | 拉开空带 |
| `module-title-missing` | advisory | 需 frames | 无标题文字或标题在框外 | 框内左上角加标题 |

### 判据合成

只有「门 A hard=0（strict 时 advisory 也为 0）＋ 模块化硬门 hard=0 ＋ 门 B 通过 ＋ 原生 gate 硬项全 0」
才能报完成。
`sch gate` 的 warn 分三类写进交付说明：美观类、模式类（如 `peripheral-not-direct`）、未覆盖类。

## 门 B：渲染图面审阅（结构检查器覆盖不到的部分）

O1/O2/O3/O5 这些规则**没有检查器**，只能在原生 SVG/PNG 上追路径。这是必做步骤，不是可选加分。
模块归属是否合理（哪些器件**应该**同属一块）、框线是否穿越别的信号，同样只能在图上判断。

### 开工前先写终止判据

在审阅开始前写下（写进当次运行目录的 `review-plan.md`）：

1. 本页的读者任务（一句话）；
2. 主路径的起点与终点（哪个引脚到哪个引脚）；
3. 允许的例外（例如某处必须跨页、某处必须用标签），逐条写理由；
4. 本轮审阅的最大轮数（默认 2 轮；第 3 轮仍不过就拆块/分页，不继续打补丁）。

判据没写就开审，会陷入「越看越觉得可以」或无限返工。

### 审阅顺序（固定，不要跳）

1. **整页缩略**：只回答一个问题——流向是否单一？有无明显的来回折返、跨页跳转、大片空白与拥挤并存。
2. **主路径**：从主输入沿实际导线追到主输出，逐段记录是否穿块、是否贴边、是否绕行。
   主路径上出现 1 处穿越即判失败（不是「问题不大」）。
3. **逐模块归属**：检查模块框、标题与器件归属，再沿实际导线核对外围所依附的功能引脚。
   记录分支的连接路径和实测文字净空，按 [画法语法](drawing-grammar.md) G3、G5 判断布局。
   逐段复核 `pinAlignmentReview` 提示的端点对，也主动找多端支路、较长的直线及折弯短桩。记录实际阻碍或给出移动、转向、缩短及重新测量的结果。
   `3g` 用于初始排布参考，审阅结论需说明实际符号、文字及出线空间。
4. **标签审计**：逐个标签问「它的唯一角色是什么」；有无本可实体直连却用了标签（G6）。
5. **连接状态**：T 形分支有 junction；未连接交叉正交且远离端点/拐角/交汇点。
6. **文字**：位号/型号/网络名是否压线、是否与符号重叠（几何检查已覆盖部分，这里看可读性后果）。
7. **纸张适配**：小电路是否摊在大图幅上（G1/HR-11）。

审阅还需逐项记录六类判断，包括阅读顺序、外围归属、重复支路、符号与标签方向、字号、页面留白。
每项写明实际对象与观察结果；单模块或无重复支路时说明对应情况。
发现的问题先进入 open_findings，修订后以新导图复核并关闭。
当前交付使用的结构见[分层验收索引](../../../references/workflow-assistance.md#补充验收索引)。

### 审阅记录格式

每次审阅写 `review-<rev>.md`，表格逐行：

| 页 | 对象 | 规则 | 位置（坐标/引脚） | 等级 | 修复动作 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |

- 「位置」必须是可复核的坐标、wire id 或引脚，不写「左侧那块」。
- 修复后**重导图重审受影响页**；旧 SVG/PNG 与旧记录作废，不能用旧图证明新状态。

### 失败判据（任一即不通过）

- 主路径穿越支路或器件 1 次以上；
- 外围归属错误，或连接路径绕过其他模块而无法清晰追踪；
- 有足够净空的对齐方案仍保留绕行，或本可直线实体连接却用标签接续；
- 页内主干方向混用导致交叉；
- 审阅记录里存在未关闭的失败项。

## 校准：先验证工具，再信结果

检查器也可能测错对象（真实工程里三个检查器曾静默测错）。每次新增/改动检查器或单位换算：

1. 对**已知坏例**跑一遍，确认它点名了预期对象与位置；
2. 对**已知好例**跑一遍，确认没有误报；
3. 校准对保留在 `tests/`，并写进报告（检查器版本、阈值、输入 SHA-256）。

现有校准对：

- 好例：`scripts/led_demo.py`（电气 + 可读性均 PASS，`minParallelGapGrid=2.0` 边界值不误报）；
- 坏例（电气合法 / 可读性不合法）：`tests/test_circuit_audit.py` 的 X 交叉夹具，
  必须报 `foreign-net-crossing` 且位置为 `20,10`。

报告字段必须自证范围：`checkerVersion`、`strict`、`thresholds`、`inputSha256`、`scope`、
`coverage.unverified`、`liveVerified:false`。离线 PASS 不是现场 PASS，也不是硬件可用。


主路径扩展使用 `primary_paths` 精确声明物理脚关系。检查器复用无标签接触图，
逐段要求同页同树，并检查树上全部逻辑端点。授权只沿显式路径与经计划核验的本地 direct 关系扩展。
`drawing_groups` 仅决定联合外框，不能扩大电气授权。scene 的新外框使用 `drawingGroupId`，
组覆盖、跨页、重叠、标题与净空按实际联合组判断。`primary-path-required` 是 hard 失败。
最终交付会重跑这些规则，并核对原生框位置、框数及标题文字。
