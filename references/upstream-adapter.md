# 上游 CLI 适配

独立使用 easyeda-agent；不打包/重写引擎、Connector 或基础绘图，不安装 Gateway。
先核对实际 --version、actions、health 与相关 --help；版本号一致不证明 schema/handler 一致。
Release1.16.0 actions 自带JSON、无 --json；compose schema 不支持；layout-lint 使用 bare JSON 的 ok，
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

已复现的十类/十一类快照不兼容在写入前拒绝；保留原始字段，等待上游兼容构建，不在适配层伪造观测。
