# 验收与工程交付

执行成功必须有独立事实：

1. 原始目标身份与 baseline 一致；所有电路器件、完整引脚库存、库身份、pin→net 与明确 NC 对账。
2. 原生 `sch drc --strict --json` 同时核对退出码和 boolean passed。缺计数就写 counts unavailable，不填 0。
3. `sch check --strict` 重建结构与几何问题；bridge-check 检查真实短路树；layout-lint 分别核查位置/间距。
4. 上游 compose/apply 对绘制几何、module ownership、外围直连与完整性做断言；不能仅用网名比较替代。
5. `sch save` 的 `result.saved` 必须严格为 true。`doc reload --json` 必须 status=passed、saved/closed=true，reloaded 匹配页面。
6. 真实重载后重新采集；旧回读、autosave、截图、版本一致或 dry-run 均不证明完成。

`verify` 的 live-verified 只表示它所列的机器检查，不表示硬件功能或完整发布验收。
官方导出图仍需人工/模型审阅可见文字与阅读顺序；若缺机器几何证据，标为未覆盖，不能用“看过了”代替。
完整交付还需要当前安装支持的官方预览、网表/BOM、原生工程导出；未产生文件明确列缺项。

`deliver` 核对核心报告与补充审阅，整理已有文件的 SHA256、状态和缺项。
私有运行目录可包含工程 UUID/本机路径，发布 GitHub 前另做去标识。

0.5.2 起最终交付需要 `deliver --reviews` 关联当前完整的几何与逐项图面审阅。
六类图面判断均需实际对象和观察说明，未关闭问题阻断交付。
同网无接点交叉和自由文字穿框由候选选择及当前交付重查，格式见[辅助工具](workflow-assistance.md#补充验收索引)。

状态用词：source-only=源码/文档支持；offline-verified=离线规则/编译通过；
live-incomplete=现场执行或证据未完整；live-verified=具体现场操作的指定检查通过。不得跨范围套用。

## 0.2 的复验失效规则

每次 verify 开始先把 bundle/report 标为 verifying，并分配新的 attempt；旧的通过结果立即失效。
任何保存、重载、回读或输入核验失败都写入 incomplete；每轮结果另存 verification-history。
成功报告绑定 prepared 输入摘要与 save/reload/readback/检查文件的 SHA256。

deliver 要求 bundle 与报告都为 verified、属于同一 attempt、输入摘要相同、全部证据文件未变更，
否则交付保持 incomplete。进程在复验途中退出也不能回退使用旧成功。
这些是指定快照的检查结论；后续人工修改 EDA 后必须重新回读复验。

绘图规范使用 [统一入口](schematic-design-rules.md) 的 verify 阶段清单；按规则 ID 关联已有机器报告与原生图面审阅，重复的细则继续引用原定义。

## 交付证据组织

宿主在私有运行目录关联下列证据，记录各自文件及 SHA-256、对应修订/attempt、检查范围、实际结果和未覆盖项。
该关联记录用于复核，现有 deliver 继续按其原有输入和门禁运行。

| 证据 | 复核重点 |
|---|---|
| core verification 与 manifest | 当前输入摘要、同一 attempt、save/reload/readback 和原生检查证据完整 |
| 补充 target/scene/strict 审计 | 从同一修订的新鲜观测构建，完整物理脚、NC、模块、标签和文字覆盖清楚，报告绑定实际输入摘要 |
| 图面审阅 | 最新官方 SVG 及预览摘要、渲染方式、主路径、外围归属、内部注释/图签及尚未关闭的问题 |
| 宿主修正 | 修正前的失败记录、当前状态核对、修改对象与依据、变更后的复验报告 |
| 工程导出 | 精确目标、导出回执、文件摘要及格式完整性检查，归档可检查 CRC |
| 官方应用电路与调整 | 所选模块与手册版本、原图页码／图号、实际采用的连接和参数、需求调整理由；由宿主审图核对 |
| 电气与效率 | 原厂依据缺项、工程假设、实际完成的验证及测量范围 |

core 报告列出的 visual export review 未覆盖项可由独立图面报告补充，交付说明引用该报告，原始 core 输出保持原文。
只取得原生检查通过时，继续完成对应图面复核。代码、离线演示、具体 EDA 样例及严格知识库生成路径的结论按实际对象报告。
宿主修订布局、恢复文字或重新绑定目录条目时，在交付说明中披露。

后续任何影响报告对象的修改都需要新的观测与检查。旧图、旧图面审阅和新快照不能混合组成当前修订的通过结论。
具体失败症状及恢复路径统一维护在[现场问题复盘](live-recovery.md)。

交付完整性要求全部 5 项核心检查及 7 个原始回执键存在，随后核对每项通过状态和文件摘要。
0.5.0.dev8 起新增轮次的原始回执自动归档，历史独立校验与补充层索引用法见[辅助工具](workflow-assistance.md)。
