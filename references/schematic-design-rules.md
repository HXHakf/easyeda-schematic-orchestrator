# 原理图设计规范入口

本项目以嘉立创原理图设计规范组织绘图要求，辅助资料补充表达习惯。来源统一登记在 eso/data/knowledge.json，十个唯一规则条目位于 eso/data/schematic_rules.json。重复名称归入原条目的 aliases，细则和几何判据继续引用既有文件。

## 使用顺序

```sh
eso rules --stage design --out runs/design-rules.json
eso rules --stage layout --out runs/layout-rules.json
eso rules --stage verify --out runs/verify-rules.json
eso rules --query 图签 --stage all
eso rules --check
```

需求整理后读取 design 条目，确定模块、读者任务、纸张和字段约定，将影响图面的选择写入设计确认。放置和布局时读取 layout 条目，调用条目指向的已有检查器并保留实际输出。保存重开后读取 verify 条目，补充原生导出图审阅。

日常使用只加载当前阶段，遇到具体问题用别名查询。all 用于维护或完整审查。运行时离线读取本地索引，来源网页在录入或更新时查证。

每条规则区分 machine 与 review。命令输出 guidance-only、verified=false，表示选出了待检查事项。宿主应在私有运行目录按规则 ID 记录适用性、机器输出文件、图面审阅和未覆盖项。已有 verify 的现场状态按实际机器证据生成。

## 来源与复用关系

| 来源层级 | 覆盖条目 | 复用位置 |
|---|---|---|
| 嘉立创核心 | document-identity | 画法语法 G1、HR-11 |
| 嘉立创核心 | functional-modules | 画法语法 G0、可读性门的模块检查 |
| 嘉立创核心 | power-peripherals | 画法语法 G3、G4，已有外围直连检查 |
| 嘉立创核心 | pin-disposition | 资料核验、完整引脚和 NC 对账 |
| Autodesk 辅助 | signal-flow、net-semantics、connection-junctions、annotation-readability | 画法语法 G1、G5–G7，网络与文字检查 |
| Altium 与 Autodesk 辅助 | symbol-identity | 库身份、手册物理引脚及实测网格 |
| 项目执行规则 | final-verification | verification.md、live-recovery.md |

上述条目是现有细则的统一入口。外部来源的具体段落在 source_refs 中定位，完整 URL 通过唯一来源 ID 关联。

## 应用边界与冲突处理

嘉立创推荐 A4/A5 和标准纸输出，具体图幅仍应与已确认页面、内容边界和打印可读性一致。公司、作者等信息按真实资料填写，缺少身份信息时列为待核实项。与电气连接无关的文档信息不触发额外设计阻断。

功能分区沿用现有项目的独立模块区域、框和标签边界。嘉立创文章提出逻辑分区建议，现有 G0 的具体硬门属于项目执行约定，条目对此单独标明。

去耦要求仅适用于存在芯片电源旁路的设计。原理图中的靠近表达器件归属，实际封装距离和电流回路保留为独立物理设计事项。未使用引脚的 NC 意图必须有手册依据。

辅助资料中的标签作用域、100mil 默认网格、固定符号尺寸及软件菜单不能直接移植。使用嘉立创真实网络语义和库资产，沿用当前实测坐标与引脚。修改既有符号或封装须另行核验物理映射。

辅助资料与嘉立创规则不一致时，先保留嘉立创平台语义，并记录具体冲突。涉及器件参数时回到对应原厂手册；未解决的实际冲突按原有证据流程处理。

## 维护

新增条目前搜索 id、标题及 aliases，并核对现有 G0–G7 和验收规则。相同目的补充来源定位，新的适用场景放入原条目说明。只有新增独立检查目的才增加 ID。来源一次登记，多条规则引用同一来源。

改动后运行 rules --check、完整单元测试与离线演示。导入阅读规范或完成清单选择不产生设计确认；设计范围和确认继续按 SKILL.md 执行。
