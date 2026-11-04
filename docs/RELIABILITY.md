# 本地可靠性改进

本轮为离线源码改进，未执行 EDA 写入，也未证明真实 place/wire/save/reload 全链路通过。

## 连接与空白页

`reconcile` 在建立索引前检查完整 components/nets/connections 数组、唯一 ID/位号/引脚号、
所有连接引用、重复连接及 NC 带连接。比较全部连接端点、网络名称和 scope；回读重分配 net ID
仍可通过，同名不同作用域不能混为同一网络。无效结构返回不通过，不将未知 netId 当成 NC。
`check_source` 同时检查编译输入与 plan 的网络角色/scope。

空白页仅接受已知完整十类或十一类 pagePrimitives，数组缺失、未知类别（即使为空）、
非空绘图、非图纸组件、顶层与原生图纸实例清单不一致均拒绝。允许原生图纸记录与 hydrated
记录的详细字段不同；只比较实例身份和类型。原始数据不删字段、不补空值。

## 写入前能力探针

stage 首先保存原始空白快照到 `compatibility/before.json`，将明确标为合成的电阻测试输入
交给真实上游 `sch compose --before ... --replace`，再运行生成队列的 `sch apply --dry-run`。
纸张边界使用原始快照中的实测图纸 bbox，要求唯一物理图纸且几何完整。
保留版本、快照哈希、收据与失败原因。探针队列不会交给 execute，也不成为用户电路的测量或设计证据。
它检查实际快照解析和离线编译能力；不保证所有器件、电路或 preserve-instances 功能都支持。
缺少上游要求的页面连接摘要时同样拒绝，需修正上游只读采集，而非编造摘要。

现场十一类快照不能通过删除 `pins` 兼容十类编译器。上游 main 已提供完整类清单及逐脚归属
校验，本地 v1.16.1-dev.1 构建复用了该修复；已装发布版是否支持仍由探针实测。
当前真实 CLI 集成测试要求支持十一类清单的构建，也验证缺类、未知类、不可用 pins 及孤立引脚拒绝。

同一工程存在多个连接器会话时，先从新鲜 `easyeda health` 核对完整工程/文档身份，再显式指定
`eso --window <windowId> ...`。该参数用于 stage、collect、execute、verify 和 live diagnose 的
现场调用，仍同时传递 project/doc 并执行身份守卫；离线 compose/dry-run 不附加窗口参数。
窗口 ID 重连后可能变化，旧 ID 应重新核对，不自动回退到其他窗口。

## 完整文字测量

位号继续采用官方 `designator-geometry`。属性可见性为 null 或缺 bbox 时，collect 自动复用
安装态 schematic-verified-layout 的隔离 Chromium 原生 SVG 测量器。以全部物理脚出口校准
原生坐标，核对 SVG SHA、完整可见 text 清单、属性 ID/owner、专用位号归属和唯一图签关系。
保留原始 null、公式和原始快照；未渲染属性只记为“本次 SVG 未渲染”，不把 raw 值改成 false。
可见文字漏测（包括 nullable 型号和位号）、重复/未知 ID、坐标漂移、哈希不符均拒绝。

测量和覆盖报告独立保存到 native-text-measurements.json / native-text-coverage.json，纳入
manifest，并由 prepare 核验和复制；不把额外审计字段塞进上游严格布局 schema。
安装依赖为 schematic-verified-layout + 本地 Chromium/Edge/Chrome；可用 ESO_LAYOUT_SKILL
指定技能目录。缺依赖明确失败，不控制用户浏览器或 EDA 屏幕。复杂 CSS 可见性、变换引脚锚点
等未支持输入保持拒绝；字体与 EDA 一致性、path/use/图片文字、stroke/filter 效果仍需独立审阅。

派生 canonical IR 中的引脚名称采用已经逐脚验证的真实库别名，与测量数据一致（如 VDD_1）；
计划的目标网络、NC 和物理脚号保持原意。留存 STM32 17 件器件/197 条文字通过采集适配回放；
随后真实上游 lib-layout 在该大模块耗尽候选预算，尚不能称此完整电路的标准自动链通过。

后续诊断发现：锁定姿态的紧凑搜索失败时，可用上游 layout-plan 的显式 allowedRotations
探查外围朝向，但这属于有记录的候选源，不能冒充新姿态的现场测量。上游原实现对多次刚体
变换使用浮点逐位比较，会把合法候选误拒为 not a rigid source transform；本地源码已改用
既有 variant 的有限坐标容差，离散身份/网络/引脚方向等仍精确核对。修复构建已完成四模块
离线求解；当前实测纸张扣除图签后仍容不下 MCU 框，不能据局部求解通过称可写入。
展开模式也保留物理连接但面积更大，不是纸张越界的豁免；任何模式都必须经过原纸张装箱。

## 显式多电源计划

plan 可增加完整 `nets` 数组：

```json
[
  {"name":"+5V","role":"power","scope":"global","voltage_v":5},
  {"name":"VBAT","role":"power","scope":"local","voltage_v":3.7},
  {"name":"GND","role":"ground","scope":"global","voltage_v":0},
  {"name":"ENABLE","role":"signal","scope":"local"}
]
```

名称必须唯一，必须恰好覆盖所有物理脚用到的网络；power/ground 必须给出电压，ground 为 0。
允许负电源。电压用于保留设计意图，不是电压兼容性或 ERC 证明。网络 role/scope 传入上游
canonical 数据，电源与地生成对应布局策略，不再依赖网名猜测。新参考计划显式输出此数组。
旧计划未声明 nets 时保留 +3V3/GND 的历史语义；扩展电源计划应显式声明全部网络。

## 中断诊断

```bash
eso diagnose --run-dir runs/private/final
eso diagnose --run-dir runs/private/final --snapshot fresh-snapshot.json --connectivity fresh-connectivity.json
eso diagnose --run-dir runs/private/final --live
```

默认只读本地状态、执行锁及 journal；提供快照/连接数据时报告缺失、额外、身份冲突器件及
逐脚连接差异。损坏/截断 journal 明确报告行号；journal 本身不能证明器件已落图。
`--live` 仅调用目标核验、snapshot 与 connectivity；它与离线输入互斥。没有新鲜数据时保留
未验证项，不自动拿历史成功或缓存代替。输出为 JSON，`state:diagnostic-only`，
`resume_allowed:false`；命令完成退出 0 不代表验收通过。

诊断不改运行目录、不删除锁、不续跑、不生成可自动执行的恢复队列。仍须核对新鲜现场后重新
形成受保护方案。调用失败返回 2；`--evidence` 仅按用户明确指定的位置记录 CLI 收据。
