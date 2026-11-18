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

新流程先用 prepare 编译每个候选并完成 dry-run。每个候选提供 run_dir、source 路径及 preview 对象。
source 使用受保护运行的 layout-page.json，未指定整页时使用 composition.json。preview 包含 path、
source_sha256、scene_sha256、render_method 和 pixels_per_raw。按同一比例制作实际 SVG 或 PNG 预览，保留真实生成方式。
比较先核对同一已确认契约及电气计划，核对 scene 中的脚、器件、导线、标记、框和已测量文字与编译结果，
再运行几何及可读性检查。候选可采用不同图面分组。manifest 可传入上轮已保护的 contract 与 plan 文件路径，
明确固定比较依据。manifest 另需 author_context_id 和 visual_comparison。后者使用 scope=independent-layout-comparison，
以比较对象的规范摘要绑定审阅调用，evaluations 按候选 ID 记录 accepted、assessment。
通过机器和图面验收的评估还需记录 clarity_rank（从 1 起，越小越清晰，同等级允许并列）
及完整 drawing_review。首先按读图质量排序，同等级继续用实测的折弯、线长与图面包络面积排序。

## 补充验收索引

```sh
eso evidence-index --run-dir runs/final --reviews runs/reviews.json --out runs/evidence-index.json
eso deliver --run-dir runs/final --reviews runs/reviews.json --out runs/manifest.json
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
当前索引直接重查 scene 中的无接点交叉和全部文字穿框，旧报告的通过状态仍需符合当前图面检查。
scene.junctions 用于当前交付时，每项还需 svgId 指向同轮原生 SVG 的实心接点 circle。
当前支持顶层 shapeBox 中无变换、可见且使用不透明十六进制填充的原生接点，核对 x／负 y 坐标与半径。
缺少接点或其他未支持的表示方式返回缺项，按实际导图补齐可核验的证据。
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

### 逐项图面审阅

0.5.2 起，当前 visual 报告还提供 drawing_review 对象。其中 checks 必须包含下面六项，
每项提供严格布尔 passed、非空 objects 列表和非空 assessment 观察说明。

| checks 键 | 审阅内容 |
|---|---|
| reading_order | 页面读者任务、输入到输出的阅读顺序，以及模块间是否折返 |
| peripheral_grouping | 复位、去耦、上拉／下拉等外围与实际功能引脚的归属和绕行 |
| repeated_branches | 重复支路、接口的相对排布及标题是否一致，无重复支路时说明情况 |
| orientations_and_labels | 电源／地符号、器件和标签方向，局部命名是否影响阅读 |
| typography | 按最终显示或打印尺寸核对字号、文字层级、标题和自由说明的净空 |
| page_balance | 图幅、模块密度、留白、标题锚点和注释区域 |

drawing_review.open_findings 为列表。发现的问题写明对象、位置与处理状态，修正后用新导图复核，
已关闭问题保留在独立审阅历史中。当前 open_findings 为空、六项均通过且对象和判断完整时才可完成图面审阅。
宿主根据实际图面填写内容，Python 核对记录完整性及几何规则。

带 design_evidence 的当前契约还要求 visual 文件清单中的 invocation。原始报告的 independent_review
记录 reviewer（id、context_id、independent=true）、subject_sha256 与同一 invocation（path、sha256）。
subject 由该轮 binding 与 snapshot、svg、preview 的实际摘要组成。invocation 原文记录 reviewer_id、
context_id、author_context_id、subject_sha256，以及实际发出的 request 和收到的 response。
response 为包含实际审阅结论的 JSON 对象或其原始序列化文本。候选审阅的结论包含完整 evaluations，
最终审阅包含 passed、coverage 和 drawing_review。更新排名、结论或未关闭问题须有匹配的新响应。
候选比较使用相同调用格式，subject 由 `comparison_subject` 对全部候选的输入和预览生成。
作者与审阅者使用不同上下文。新策略的 verify 在两次一致的现场读回之间运行官方整页 SVG 与 PNG 导出。
当轮归档中的 native-page.svg 与 native-page.png 是新策略的视觉输入。当前几何及图面审阅引用同一个原生 SVG。

deliver 需要当前完整的 core、geometry 和 visual。缺少 --reviews、缺层或图面问题未关闭时输出
state=incomplete，返回退出码 3，并保留 core_state 说明原有机器检查状态。
已有核心报告和契约可继续读取，原始内容及确认保留。历史索引用 --attempt 按原始报告复核，
当前最终交付补齐新的图面证据。电气及实物验证按项目范围单独记录。

## 新设计迭代与恢复

```sh
eso iteration-record --iteration-dir runs/design-iterations runs/event.json
eso iteration-status --iteration-dir runs/design-iterations
eso deliver --run-dir runs/selected --reviews runs/selected-reviews.json --iteration-dir runs/design-iterations --out runs/manifest.json
```

每个事件包含唯一 id、kind、contract_sha256、summary，可附 elapsed_seconds。完全相同的事件可安全重放，
同一 ID 内容变化时拒绝。事件文件出现在宿主私有运行目录，路径按该次调用的工作目录解释，建议使用明确的绝对路径。

| kind | 额外字段与通过条件 |
|---|---|
| attempt | candidate_id、run_dir、verification_sha256、reviews（path、sha256）。执行中断且未到验收时提供 execution_sha256（bundle.json 的文件摘要），保留原 journal |
| extra-start | 首次完整通过后指定新 candidate_id，给本设计唯一的一轮额外比较 |
| extra-result | candidate_id、outcome=evaluated、manifest 及 comparison 的 path/sha256。额外候选写入时同时提供其对应 attempt 字段 |
| extra-result | 本次预算内没有可用布局时提供 outcome=search-exhausted、search_input 与原始 search_receipt 的 path/sha256 |
| restored | 本轮候选已写入且未取代基线时，用同一基线 candidate_id、新受保护修订目录及对应 attempt 字段登记恢复结果 |
| blocked / resume | summary 记录实际阻塞与恢复依据，继续已开始的轮次 |

每次验收和恢复在独立运行目录保留原始对象。保留基线的额外候选无需写入，若已改现场则从最近写入的
运行用 `prepare --revision-of` 恢复基线源。重新保存、重开并取得当前独立审阅后，才可登记为已恢复。
交付会重新核对额外轮的原证据，并读取当前精确原理图页。历史图纸与当前页有差异时返回 incomplete。
