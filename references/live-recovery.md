# 现场经验与恢复约束

本页将一次真实绘图中的问题纳入 ESO 工作流；它是执行协议，不代表新增了全自动修复能力。
实际绘图同时使用安装态 schematic-verified-layout 及其 references/live-recovery.md；
easyeda-agent 的当前 typed 接口与数据契约仍为操作依据。维护工作流默认离线，不操作现有图纸或屏幕。

| 遇到的问题 | 执行规则 |
|---|---|
| CLI/Skill 已更新，但编辑器还加载旧 Connector | 记录 CLI/Skill/daemon/加载态 Connector 版本；health 精确匹配工程/页/窗口，核对所需 ownerType 能力，不只比较版本号 |
| 同 C 号存在通用旧库与完整厂商条目 | 精确查询并读取详情，核对制造商/MPN/Supplier、符号、封装及全部脚；ATTRMATCHCODE 先查商品属性，不能猜 uniqueId 冲突 |
| 型号更新后文字自动移出模块，原生 gate 仍通过 | 先定商品属性再排文字；改 Name/Manufacturer Part 后失效旧几何结论，新读完整属性、测文字并查模块边界 |
| 图签坐标为 null，显示后越纸 | 先设值显示、回读初始化坐标，再按实测单元格 typed 移动；保留 raw null，禁止隐藏必需内容消警 |
| Apply 后段失败，前面步骤已落地且没保存 | 保留失败 journal/返回，新读现场，逐项对账，只生成局部 delta；不重放整队列、不改 journal、不假设 resume |
| 保存重开后大量属性 primitiveId 改变 | 以稳定 owner+Key 及完整 raw 字段多重集合对账；只忽略属性自身 ID，保留重复、公式、null/缺字段、类型及未知字段 |
| 新 Connector 的 device 字段投影改变 | 用已留存 canonical 绑定、工程组件 lineage、符号/封装、实例身份及全部引脚交叉验证；无证据保持 incomplete |
| 旋转 bbox 与原生文字不同 | 先校准原生 SVG 字体、坐标和 glyph/stroke；保留失败及限定复核，不能排除全部文字碰撞 |
| strict 告警被误当可忽略 | 当前参数与原始报告为准；逐项解释独立检查 advisory，不能把 raw FAIL 改成 PASS |
| 求解预算耗尽 | 无合法候选即未完成；可改用实测确定性支路模板重新 compose 和校验 |
| PowerShell 引号/编码或回执解析错 | argv 列表、stdout/stderr 分存；exitCode/exit_code/exit 明确适配，缺字段不是成功 |

恢复后必须独立取得布尔 saved:true，才进行重开/重载；重新 health、新回读、逐脚网络/NC/direct
对账、新原生导图、全部受影响文字/模块边界、strict gate 与 DRC。维护恢复只使用上游支持的
连接器安装/生命周期协议，不以 GUI 或任意设计编辑 JS 绕过接口。

安装态布局技能提供只读 snapshot_semantic_diff.py，按完整 raw 语义报告属性 ID 重建之外的差异；
exit 0=限定范围相等，1=有差异需对照声明修改，2=输入/输出错误。它不能替代网表、几何或持久化验收。

**能力边界保持分开。** 下层 typed 绘图链的现场通过，不等于 ESO 标准
stage→collect→prepare→execute→verify 自动闭环已现场验收。collect 的 nullable 属性/bbox
现通过独立原生 SVG 测量适配；不支持的 schema、坐标或文字表示仍保留原始错误，派生适配不能
回写原始快照，也不能把模拟/留存数据回放说成新的现场成功。
