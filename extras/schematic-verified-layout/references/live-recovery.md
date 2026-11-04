# 现场复盘：能力、元数据与增量恢复

适用于 typed CLI 的原理图写入与恢复；维护此流程时保持离线，不顺便操作现有图纸。
遵守用户不直接控制屏幕的偏好。缺失接口不以 GUI 或任意设计编辑 JS 补齐。

## 写前预检与顺序

每轮记录 CLI、Skill、daemon、**实际加载的 Connector** 版本，必要时保留 bundle 哈希。
升级磁盘上的工具不代表编辑器已加载新连接器。通过 health 重新定位精确工程、页面和活窗口；
窗口版本高不等于目标正确或接口兼容。按当前 help/schema 核对所需能力，尤其 part 与 sheet
两类 attribute-move ownerType；part 成功不能证明 sheet 成功。不能为探测任意改用户图纸。

顺序为：**能力预检 → 精确库身份及商品属性 → 姿态/引脚实测 → 全文字测量 → 布局布线
→ 独立校验 → 受保护写入 → 新回读 → saved:true → 重开 → 新回读与原生导图复验**。
本流程是操作协议，不表示所有检查已由一个命令自动实现。

需要更新连接器时，先取得 saved:true，依上游支持的安装/重载协议操作，保留前后版本、哈希和回执；
若上游明确支持 connector-only debug exec 热重载，仅用于连接器生命周期，不能借此编辑设计。
重连后重新 health 和读取目标。没有受支持恢复路径时保留 incomplete，不强制丢弃未保存内容。

## 商品属性与身份

- 同一精确 C 号可能对应历史通用条目和完整厂商条目；不得直接选第一项或只看描述。
  保留精确搜索和 device get 原始结果，核对 C 号、制造商、MPN、Supplier、封装、symbol/footprint
  及全部物理脚。无法证实兼容时停止局部属性修复，不猜身份。
- ATTRMATCHCODE 表示器件属性与 Supplier Part 不匹配；先查原生报告和商品属性，不能直接归因于
  uniqueId 冲突。已证实同源符号/封装/引脚时，才用官方目录属性修正现有实例；保持实例与接线，
  不靠换 uniqueId、降低 DRC 或隐藏参数消警。
- 不同连接器可能把 device 投影为库 UUID 或工程组件 UUID。直接不同不等于换件：须以留存的
  canonical 绑定、组件 lineage、symbol/footprint、primitiveId/uniqueId、C 号及全部引脚对账。
  缺证据保持未验证，不为“归一化”删除身份字段。

## 变更使哪些证据失效

| 变更 | 必须重新读取和检查 |
|---|---|
| Name / Manufacturer Part / 商品属性 | 平台可能自动重置可见属性 XY；重新读完整 raw 属性，再测文字、模块边界与页面 |
| 字体、字号、显示状态、角度、位置 | 新原生 SVG、全部受影响文字及线路/边框碰撞；旧 bbox 失效 |
| 器件姿态/位置、线、标签、NC | 引脚几何、物理与逻辑连通、direct/NC、文字与模块边界、gate/DRC |
| 图签第一次显示 | X/Y 可能从 null 初始化；先回读，按实测单元格移动，再查固定印字与绑定文字 |
| 保存、重开、连接器升级 | 新目标上下文、属性 ID/身份投影、新快照与原生导图；旧 ID 不能直接复用 |

属性移动必须使用当前 typed 接口和完整 expected raw 状态，保留 null、缺字段、公式、类型及未知字段；
只修改声明的字段。不能把 null 编成 0。图签必填值应显示并移入合法单元格，禁止隐藏来绕过越界。
最终型号/商品属性先写完再排文字；若最后又改了元数据，先失效旧布局验收，即使原生 gate 已通过。

## 部分 Apply 与假失败

Apply 后段失败可能已有大量前置步骤落地，保存步骤却未执行；失败不等于自动回滚。
保留失败队列、原始返回及 journal，维持失败状态，不手改 journal/队列、不从头重放或假设支持 resume。
重新 health、轻读实际状态，将已落地、未落地、状态不明逐项对账，只从新基线生成必要的局部 delta。
先离线审计，再按 typed 接口执行。随后独立 save 必须返回布尔 saved:true，才可重开/重载复验。
涉及合并线树的 disconnect 先查实际物理段；被拒绝时重算受影响支路，不能盲删整树。

## 属性 ID 重建后的语义对账

保存/重开可重建 raw attributes 的 primitiveId。保留两份原文件与 SHA，按稳定 owner + Key
及其余**完整 raw 字段的多重集合**比较；必须保留重复项、null 与缺字段区别、公式、类型、未知字段。
仅忽略属性自身 primitiveId，不忽略 ParentPrimitiveId，也不忽略器件/线/其他图元身份。
不能按文字相同或最近距离猜匹配，也不能因数百个 ID 变化就排除整类属性。

```text
python <skill>/scripts/snapshot_semantic_diff.py --before before.json --after after.json --out semantic-diff.json
```

适配成功的 sch list envelope（ok:true、context.projectUuid/documentUuid、result.pagePrimitives）；
要求两侧目标一致、完整图元类集合一致。schema 不匹配应显式另写有证据的适配器，不能补空数组假装完整。
exit 0=此归一化范围内相等，1=真实差异，2=输入/输出错误；输出拒绝覆盖。
attributeIdsChanged 只表示 ID 多重集合变化，不是逐对象 lineage 结论。
差异中的 removed/added 须人工对照预先声明的局部修改，exit 1 不能自动授权或判通过。
此工具不验证输入采集完整性、电气目标、库身份、几何、DRC 或持久化；另行验收。

## 判据、测量与证据

- 不再使用“gate PASS 总允许 warn”的旧结论；以当前参数及原始报告为准，strict 可因 warning 失败。
  保留严格失败；独立可读性检查的预声明 advisory 可逐项分类，但不得改写原始结论或豁免整类碰撞。
- 估计旋转 bbox 与原生 SVG 不符时先校准坐标、字体、glyph/stroke，保留失败及复核证据。
  自己的文字与承载线/笔画仅能按对象关联限定豁免；极小间距要明确披露，不能据此称全部间距充裕。
- 搜索预算耗尽且无合法候选时，改用实测的确定性支路模板再 compose/校验；不能靠增加预算宣称成功。
  先读结构化失败报告区分锁定姿态搜索与来源校验误拒。显式允许的外围旋转可经上游求解验证；
  合法刚体坐标的浮点舍入与真实几何变化须分别判断。局部求解成功后仍按实际图签禁入区装箱，不能扩大或缩放图纸来伪造通过。
- CLI 用 argv 列表调用，stdout/stderr 分开，按实际编码解析；回执 exitCode/exit_code/exit
  需明确适配，字段缺失是未知，不能默认为 0。不要在 PowerShell 塞长 python -c 或内联 JSON。
- collect 拒绝 nullable 属性或 bbox 时保留错误。经显式适配的下层 typed 流程成功，不等于
  ESO 标准 stage→collect→execute→verify 自动闭环已现场通过；离线、下层现场、完整自动链分别报告。
