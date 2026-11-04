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
| `two-terminal-bends` | hard | 两端网络折弯数 >`--max-bends`（默认 2） | 缩短路径、改画法角色、拆块 |
| `net-not-physically-connected` | hard | 非电源网、无标签、却分散在多个接触树 | 补实体线；确认不是该用标签的出页网 |
| `out-of-usable` | hard | 内容框越出 `scene.paper.usable` | 缩小间距/换图幅/分页 |
| `label-bridged-net` | advisory | 网络靠标签跨树连接（物理不连） | 优先实体直连；确需标签则写进交付说明 |
| `parallel-spacing` | advisory | 异网平行段中心距 <`min-spacing-grid`×grid | 拉开到 ≥2g |
| `short-pin-exit` | advisory | 引脚首段 <2g 就拐弯 | 先沿引脚向外走满再拐 |
| `redundant-overlap` | advisory | 同网共线重叠（可被静默合并） | 合并成一根或错开 |

- 默认模式只有 hard 失败才算 FAIL；`--strict` 时 advisory 也失败（`.enet`/交付前用 strict）。
- 豁免只允许写进交付说明的文字理由，**不能改输入数据绕过**。单页超过 3 处间距豁免即判不通过。
- 报告里的 `coverage.unverified` 必须原样进交付说明，至少包括：渲染阅读路径、无 paper 时的纸张
  适配、模块分组意图、符号内部文字、原生 DRC、save/reload、额定值。

### 模块化硬门（G0）

模块归属来自 target `modules`，所以**边界规则始终生效**，不需要任何场景扩展：模块之间只准
标签/netport，禁止连续实体线；模块不得跨页。声明 `scene.moduleFrames` 后再加框相关规则；
不声明 frames 时框规则跳过并计入 `coverage.unverified`（「未验证」，不是「通过」）。

| 规则 | 级别 | 前置 | 阈值 / 触发条件 | 修复方向 |
| --- | --- | --- | --- | --- |
| `module-physical-crossing` | hard | 始终 | 一棵物理接触树里含 ≥2 个模块的引脚 | 断开成标签/netport；GND 各模块内落地 |
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
3. **逐模块归属**：先看每个模块是否有框和标题、框内是否只有本模块的东西；再看每个外围器件是否贴在
   赋予它功能的引脚 ≤3g 内（对照 [画法语法](drawing-grammar.md) G3）。脱离归属引脚 >3g 即失败，即使线是通的。
4. **标签审计**：逐个标签问「它的唯一角色是什么」；有无本可实体直连却用了标签（G6）。
5. **连接状态**：T 形分支有 junction；未连接交叉正交且远离端点/拐角/交汇点。
6. **文字**：位号/型号/网络名是否压线、是否与符号重叠（几何检查已覆盖部分，这里看可读性后果）。
7. **纸张适配**：小电路是否摊在大图幅上（G1/HR-11）。

### 审阅记录格式

每次审阅写 `review-<rev>.md`，表格逐行：

| 页 | 对象 | 规则 | 位置（坐标/引脚） | 等级 | 修复动作 | 状态 |
| --- | --- | --- | --- | --- | --- | --- |

- 「位置」必须是可复核的坐标、wire id 或引脚，不写「左侧那块」。
- 修复后**重导图重审受影响页**；旧 SVG/PNG 与旧记录作废，不能用旧图证明新状态。

### 失败判据（任一即不通过）

- 主路径穿越支路或器件 1 次以上；
- 外围器件脱离归属引脚 >3g；
- 本可直线实体连接却用标签接续；
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
