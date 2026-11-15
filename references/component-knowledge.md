# 常用元件知识库

基础知识按元件类别组织，供宿主在项目选型前后检索。每类保留稳定 ID，NPN／PNP 等子类型归在同一基础卡，
功能电路通过 related_topics 关联。当前 17 类覆盖见 [README](../README.md#常用元件知识库)。

## 使用

```sh
eso kb-check
eso kb-search 三极管
eso kb-search 电容 --kind component
eso kb-search 运放 --kind component
eso kb-search '三极管低侧驱动' --kind circuit
```

kb-search 默认搜索元件与应用电路，按名称和别名的精确匹配优先，再做部分匹配。
--kind component 检索基础卡，--kind circuit 检索既有应用主题。
例如三极管、NPN、PNP、BJT 返回 component.bjt，NMOS 与 PMOS 返回 component.mosfet。
检索结果包含 kind、版本、来源定位、子类型、连接提示和选型检查。
kb-check 分别报告 component_count、topic_count 和 total_source_count，列出元件类别 ID。
source_count 保留应用目录的来源计数，component_source_count 计新增来源记录，total_source_count 为合并后的唯一来源数。

基础卡标记 status=reference-only、automatic_plan_generation=false。宿主根据连接方法、计算条件和错误反例提出方案。
具体项目以所选模块的官方手册及应用电路为设计基线，获取与审图沿用
[官方手册与应用电路](input-and-questions.md#官方手册与应用电路)。用户提供型号和需求后，宿主补齐缺少的官方资料。

开始项目后再确定工作电压、电流、频率、默认状态、温度、封装及器件资料。
选择具体型号时核对物理脚和真实库身份，采用的事实与推导写入现有 constraints、plan.evidence 和 notes。
应用卡的受限参数检查及计划生成见[应用电路知识库](knowledge-base.md)。

## 条目格式与维护

`eso/data/components.json` 保存基础卡和新来源。`eso/data/knowledge.json` 保存既有应用卡、共享规则与已登记来源。
来源跨目录按 ID 复用，检查器在同一索引中拒绝重复 ID／URL，保证主来源为嘉立创，并核对来源、规则和应用引用。
原厂资料用于补充机理与参数条件，具体型号的手册、版本和工程计算在项目资料记录中固定。

| 字段 | 内容 |
|---|---|
| id、title、version、aliases | 稳定身份、版本与检索别名 |
| subtypes、uses | 子类型、用途与类别差异 |
| connection_guidance | 常见连接角色和审图提示 |
| selection_checks | 选型与工作条件核对项 |
| common_mistakes | 常见错误和不适用推断 |
| calculations | 表达式及适用条件，由宿主计算并审阅 |
| primary_source_id、auxiliary_source_ids、evidence | 嘉立创主来源、辅助资料与章节定位 |
| related_topics、rule_ids | 关联既有应用卡和共享规则 |

共享的物理脚核验、工作边界与资料冲突处理通过规则 ID 复用。
电阻、电容、三极管等基础知识在对应元件卡维护，功能拓扑和受限自动生成规则在应用卡维护。
新增类别先查现有 ID 和别名，相同元件的型号、封装差异作为项目资料处理，子类型归入原类别。
新增类别经过同样的来源核对、内容审阅、别名与引用检查，再补充检索测试。

查询基础卡无需建立项目资料清单或本机材料缓存。
元件目录独立维护，既有电路方案继续固定其实际知识卡、资料与校验器依赖。
检索侧扩充基础卡时，检查器保留既有电路确认的依赖范围。

## 当前覆盖与后续项目

常见元件的连接和选型方法可先准备，完整型号、保证参数和官方应用图在具体项目中核验。
基础卡中的公式带适用条件，典型值、系列特征与个别测试点均需按具体资料判断适用范围。
原理图实现、图面质量、仿真与实物性能按各自证据报告，沿用现有交付检查。

当前覆盖为已列出的 17 类。其他器件和模块在项目中由宿主获取官方资料，按同一录入流程补充对应类别或应用。
原始网页、外部 PDF 与社区工程保留引用，仓库存放原创摘要和定位。
