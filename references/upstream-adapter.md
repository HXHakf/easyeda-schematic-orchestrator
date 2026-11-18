# 上游 CLI 适配

独立使用 easyeda-agent；不打包/重写引擎、Connector 或基础绘图，不安装 Gateway。
先核对实际 --version、actions、health 与相关 --help；版本号一致不证明 schema/handler 一致。
本适配器仅执行原理图任务。命令入口按原理图、库、目标文档和只读能力发现限定，
拒绝 PCB/Board 命令及其通用 typed-call 入口。上游原理图章节的完成节点即本流程终点。
直接使用上游 CLI 的人工恢复也遵守本范围，原理图交付不会自动派生 PCB 工作。
历史 v1.16.0 观察中，actions 自带 JSON、无 --json，compose schema 不支持。layout-lint 使用 bare JSON 的 ok，
DRC/check/bridge 使用 result envelope 的 passed。

## 两种编译入口

1. 已有合格 composition + 原始空白页：prepare 直接上游 compose/dry-run。
2. 当前流程新建电路：stage → collect → prepare --placement-run。
   初始库放置用 materialize，随后完整回读与上游 lib-layout；prepare 核验本轮归属、测量清单、
   场景与 composition 哈希，再经 preserve-instances 编译。详见 [测量衔接](measurement-bridge.md)。

不自动接管已有任意图纸或清除用户对象。源副本上的目标网络与原始观测分开保留，不能抹掉未知值。
原厂资料/库身份/物理引脚完整性必须独立查证，搜索结果不能直接选第一项。

## 失败与持久化

每次 prepare 使用新运行目录。stage/execute 一次性写入锁保留在失败后；超时可能已经落图，
先保存错误/journal并回读，禁止自动重放。collect 是只读采集，可在诊断后重新运行并使旧布局证明失效。

verify 先使旧成功失效，再执行严格 saved:true、真实 doc reload、新 connectivity 与规则检查。
任何中断或失败都不能使用历史 verified 交付。deliver 核对本轮 attempt、输入与证据哈希。
CLI验收未通过返回3；输入或操作拒绝返回2。

原生 `sch gate` 对无接点交叉可能返回 INFO 并保留通过结论。此回执只作原始证据，
候选选择与最终交付继续从当前 scene 重查零交叉、文字/器件/导线碰撞和文字穿框。

已复现的十类/十一类快照不兼容在写入前拒绝；保留原始字段，等待上游兼容构建，不在适配层伪造观测。

## v1.17.0 联合分组与已接线修订的离线证据

2026-11-17 使用实际 easyeda-agent v1.17.0 运行 `sch compose schema`、`sch compose` 和
`sch apply --dry-run`，覆盖 `tests/test_upstream_cli.py` 的合成正反例。该版本的 schema 入口可用。
验证级别为 offline-verified，现场写入次数为零。合成几何采用 sheet-bbox-fallback，
未提供实测内边框，测试覆盖编译与队列保护。

### 固定数据映射

- ESO 保留两个原逻辑模块及其核心、外围归属，向 canonical 1.4 的一个 `modules` 项投影。
  `coreComponents` 合并原核心 ID，`peripheralComponents` 合并原外围 ID，composition 的 `modules[].id`
  使用同一个绘图分组 ID。上游接收已有 canonical/composition 字段，ESO 的 `primary_paths` 和
  `drawing_groups` 由 ESO 保存并审计。
- 合成例含 4 个器件、8 个物理脚、6 个连接和 2 个 NC。`U1.2` 与 `U2.1` 通过 `MAIN` 导线直连，
  原核心与外围也通过导线连接。编译保留器件、库身份、net/NC 与成员归属，输出实际外框数为 1。
  `MAIN` 导线同时进入 computed layout、`schematic.wire.create` 和最终 `expectSchematic.drawing`，
  最终 ownership 保留该联合分组的全部成员。
- 每棵物理线树需要真实命名标记。仅填写 `wires[].net` 会被拒绝，错误包含
  `does not reach a named wire tree`。正例对每棵线树设置一个 `net_port_bi` 标记，保留跨核心主导线。
  将主路径断开后仅靠同名标签保持电气命名的情况仍需 ESO 的主路径物理审计拒绝。

### 已接线页面的完整保护条件

`compose --before --replace --preserve-instances --playbook` 接受同一页、同一完整器件集合的已接线快照。
该路径要求成功的 schematic context、active-page connectivitySummary、明确可用的 wire/net 读数、
完整物理引脚与稳定 ID 绑定，以及十一类显式 `pagePrimitives` 清单。实际线段使用
`result.wires` 的 `x0/y0/x1/y1/net`，原生图元清单中的 wires 保留 `Line/Net` 和完整样式字段。

原生实例字段完整保留 `uniqueId`、`name`、`subPartName`、`addIntoBom`、`addIntoPcb`、
`manufacturer`、`manufacturerId`、`supplier`、`supplierId`、`otherProperty`、`component`、`symbol`、`footprint`。
空字符串、null、布尔、数值和字面量属性保留原值。物理脚保留 primitiveId、pinNumber、otherProperty，
所属原生属性保持内容、样式与可见性保护，坐标和旋转跟随所属对象移动。

生成队列先核验完整 sourceScene，随后显式枚举 drawing-only 删除 ID，并在移动前核验完整残余清单。
本轮实测删除范围包括 wires、rectangles、texts，保留 parts、sheet、原生 part pins 及其属性。
上游源码还支持 netflag/netport/netlabel 的受保护删除，这些类别的删除路径仍待专门合成回归验证。
未知图形、孤立属性和身份冲突会拒绝编译。正例原图含 7 条线段、1 个旧外框和
1 个旧标题，删除队列精确包含这 9 个 ID，并断言 requested/total 都为 9。清理后仍保护全部原生实例、
引脚与属性，后续移动只写 x/y/rotation/mirror，最终与保存后继续核验实例守恒。

### 当前 ESO 接口缺口

联合分组新建队列通过现有 `validate_queue` 与实际 `apply --dry-run`。
已接线 preserve-instances 队列通过实际编译与 dry-run，但当前 `validate_queue` 拒绝
`schematic.primitives.delete`，测试明确断言这个拒绝。后续受保护修订适配须在本轮归属、完整基线、
显式删除范围及前后保护上下文中接纳该步骤，并将对应回归改为通过。当前保留生产校验行为。

反例分别证明成员丢失、核心/外围重复归属、几何 net 与 IR 不符、net/NC 冲突、原快照 net/NC 漂移、
实例字段缺失及未知 circles 图形会明确拒绝，失败时不会生成 composition 或 playbook 文件。

```sh
EASYEDA_BIN=easyeda python -m unittest discover -s tests -p test_upstream_cli.py -v
```
