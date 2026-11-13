# 证据归档与宿主辅助工具

0.5.0.dev8 将按轮次归档和目标锁接入正常入口，并提供以下宿主辅助命令。
命令通过现有 Python 校验及 typed CLI 工作。记录中包含私有工程与器件数据，输出放入私有运行目录。

## 复验归档和互斥

每次 verify 使用独立的 `verification-history/<attempt>/`，保留当轮全部 prepared 输入、bundle、
save/reload/连接/原生检查及检查前后完整 snapshot。检查期间状态变化会阻断该轮验收。中断轮保留 pending 报告及已取得文件，
失败轮继续撤销当前通过状态。当前目录的同名回执作为兼容入口，历史校验使用独立副本。

stage、recover-stage、collect、execute、verify 和文字恢复使用同一 project/doc 目标锁。
锁覆盖读状态、执行与状态发布，进程退出后由操作系统释放。锁文件留存，文件存在本身不表示占用。
原执行锁和 journal 继续决定是否允许恢复，重新取得目标锁后仍须读取现场。
默认锁目录为用户目录下 `.local/share/eso/locks`，`ESO_LOCK_DIR` 可覆盖。同机调用者应使用同一目录。
状态 JSON 继续使用现有临时文件、fsync 和原子替换。

旧格式报告沿用原校验。旧历史报告缺失的原始回执需要从留存证据中逐项核对，已有报告原文保留。
新轮次可独立复查，例如使用以下入口。

```sh
eso evidence-index --run-dir runs/final --attempt ATTEMPT --out runs/history-index.json
eso evidence-index --run-dir runs/final --out runs/current-index.json
```

显式历史查询返回 historical-verified 及 fresh_readback=false，当前输入变化不会改变历史文件。
当前材料依据的撤回与新设计确认继续按既有知识库流程核验。

## 目录核验记录

stage 在 materialize 前保存 `catalog-review.json`，列出绑定名称/库身份、厂商、MPN、Supplier Part、
symbol/footprint、缺项及原始目录记录。名称或 UUID 不符仍由原门禁阻断，完整物理脚继续由手册审阅和现场核验。
可先收集精确搜索得到的多个 device get 原始 envelope，保存为 JSON 数组，进行离线比较。

```sh
eso catalog-review --bindings runs/bindings.json --devices runs/devices.json --out runs/catalog-review.json
```

输出保持 review-required，pin_review 为 host-required。同料号候选列表用于审阅，实际绑定变更按契约及证据失效规则执行。

## 实测文字恢复

先使用同一目标、同一批实例及姿态的原始实测快照生成预览。

```sh
eso text-restore-preview --run-dir runs/final --reference runs/measured-snapshot.json --out runs/text-preview.json
eso text-restore-apply --run-dir runs/final --preview runs/text-preview.json --out runs/text-restoration
```

预览通过参考偏移和当前器件坐标计算文字位置，使用当前属性 ID 和完整 expected raw。
型号、实例、姿态、可见文字或外观字段变化时需重新测量。执行前再次检查原契约、受保护输入、完整现场基线和逐脚连接，
从预览保留的原始测量重新计算动作。旧预览、重复属性及未知坐标会被拒绝。

执行证据目录必须新建。任何写入前先使当前验收状态失效，typed attribute-move 完成后在同一目标锁内运行完整 verify。
失败保留动作、返回与新回读。该工具恢复已测得的偏移，图面净空仍需新导图和补充审计。

现场曾出现编辑后图签系统更新时间延迟刷新，使刚生成的预览过期；完整基线检查正确拒绝写入。
遇到这种已确认的系统元数据变化，先保留前后快照并定位差异，在当前授权内保存并取得稳定的新基线，
再重新生成预览。不得忽略时间字段或放宽完整快照比较；电路、身份或文字发生实质变化时按既有规则重新核验。
后续真实单项恢复及完整保存重开复验通过，范围见 [恢复验收](../docs/plans/recovery-live-acceptance.md)。

## 有限布局候选比较

为有限候选逐个运行既有 target/scene 和 strict readability 审计，再登记以下 manifest。
路径按当前工作目录解析。

```json
{
  "max_candidates": 4,
  "candidates": [
    {
      "id": "candidate-a",
      "target": "runs/a/target.json",
      "scene": "runs/a/scene.json",
      "geometry_report": "runs/a/geometry-report.json",
      "readability_report": "runs/a/readability-report.json"
    }
  ]
}
```

```sh
eso layout-compare runs/candidates.json --out runs/layout-comparison.json
```

所有候选共享同一目标意图。工具核对每份报告的输入摘要、完整几何 scope 与 strict readability scope，
排除失败候选，按转折数、线长、模块框总占地和稳定 ID 排序，记录预算及停止原因。
无通过候选时 selected=null。候选选择保留 execution_ready=false，
宿主将对应完整 composition/page 交给 prepare --layout-page 编译、dry-run，最后回读现场验收。

## 补充验收索引

```sh
eso evidence-index --run-dir runs/final --reviews runs/reviews.json --out runs/evidence-index.json
```

reviews 文件包含 attempt、contract_sha256、snapshot_sha256，以及 layers 对象。
contract_sha256 使用契约的规范 JSON 摘要，与确认摘要一致。snapshot_sha256 使用该轮归档 final-snapshot.json 的原始文件摘要。
每层提供 report 路径和 files 映射，每项包含 path、sha256，所有文件会重新计算摘要。
几何主报告和图面报告各自增加 binding，字段与 reviews 顶层的三个绑定一致。原报告可复制到新文件补充该绑定，原文保留。

| 层 | 必需 files 键 | 报告要求 |
|---|---|---|
| geometry | snapshot、target、scene、report、readability、native_texts、svg | report 为既有 full geometry 审计，readability 为 strict 审计，输入摘要对应实际 target/scene |
| visual | snapshot、svg、preview、report | scope 为 host-visual-review，提供 reviewer、原始文件摘要及覆盖审阅 |

geometry 会与该轮快照及计划核对引脚、net/NC、实体、导线、标签、模块框和外部文字测量。
visual 报告的 files 包含 snapshot/svg/preview 的摘要，coverage 对象必须包含下列严格布尔通过项。

```json
{
  "primary_paths": true,
  "module_ownership": true,
  "junctions": true,
  "external_text": true,
  "internal_text": true,
  "paper": true
}
```

索引保留 core 的原始状态，补充层标为 host-reviewed。当前 core、geometry 和 visual 均完整时，
eda_evidence_complete=true。电气保持独立状态。报告选择、审阅结论及原始采集真实性仍由宿主负责。
缺层显示 not_checked，旧报告、错图、缺项或文件替换会使对应关联失败。
