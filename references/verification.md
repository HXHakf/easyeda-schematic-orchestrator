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

`deliver` 只整理已有文件的 SHA256 与状态，不会把缺失检查变成通过；私有运行目录可包含工程 UUID/本机路径，发布 GitHub 前另做去标识。

状态用词：source-only=源码/文档支持；offline-verified=离线规则/编译通过；
live-incomplete=现场执行或证据未完整；live-verified=具体现场操作的指定检查通过。不得跨范围套用。

## 0.2 的复验失效规则

每次 verify 开始先把 bundle/report 标为 verifying，并分配新的 attempt；旧的通过结果立即失效。
任何保存、重载、回读或输入核验失败都写入 incomplete；每轮结果另存 verification-history。
成功报告绑定 prepared 输入摘要与 save/reload/readback/检查文件的 SHA256。

deliver 要求 bundle 与报告都为 verified、属于同一 attempt、输入摘要相同、全部证据文件未变更，
否则交付保持 incomplete。进程在复验途中退出也不能回退使用旧成功。
这些是指定快照的检查结论；后续人工修改 EDA 后必须重新回读复验。
