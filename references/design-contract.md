# 用户确认与内部版本

contract 同时生成机器 JSON 和中文 .md 确认页。用户看到功能、型号/封装、供电、接口、
默认假设、约束、执行范围与验收项；Agent 内部管理 SHA256，不要求用户复制长哈希。

确实收到用户对该版本的确认后，Agent 才调用 confirm 记录已发生的确认。
记录不是身份认证或数字签名。供电、型号、功能、默认选择或目标 UUID 改变时旧确认失效。
一般布局取舍在同一授权范围内完成，不反复询问同一版本。

“只做方案/预览”可以产出草稿，不能由此进入 stage/execute。开发工作流时同样不生成真人 approval。

## 应用电路依据与调整记录

宿主在生成设计确认前，把所选模块、手册版本、应用电路的章节／页码／图号及调整摘要写入现有
`requirements.constraints`，随契约进入确认页。资料哈希和定位复用已有资料记录。
计划的 `evidence` 中用 `url` 与 `section` 保留原始资料和图页定位，`notes` 记录具体调整、保留或省略的外围及理由。

用户确认页展示本次采用的官方应用电路和关键改动，包含与需求有关的接口、电源、参数及已有模块外围。
读取手册和生成基线沿用[官方手册与应用电路](input-and-questions.md#官方手册与应用电路)。

## 新设计依据及独立电气审阅

`requirements.design_review` 保存 `policy_version`、`status`、`design`、`review` 和可选 `search_paths`。
`status` 可取 `pending` 或 `analyzed`，ready 根据实际资料与逐项审阅重新计算。
`design` 和 `review` 都是包含 `path`、`sha256` 的文件记录。
`contract.design_evidence` 固定 `policy_version`、这两项文件记录及 `electrical_intent_sha256`。
旧输入保持原先规则，核验返回 `review_mode = legacy`。孤立标记或旧新证据混用会拒绝。

私有设计文件的必填字段如下。

- `policy_version`、`requirements_sha256`、`function`、`operating_conditions`、`author_context_id`。
- `applicability` 为 `power`、`load`、`interfaces`、`assembly` 各保存 `applicable`、`reason`、`decision`。
- `assembly` 保存 `method`、`passive_package`，`passive_component_ids` 必须完整列出 R、RN、C、CP、L、FB 位号的被动件，无此类器件时明确使用空数组。
  被动件例外使用 `package_exceptions`，每项保存 `component_id/package/reason/source_ids`，随独立审阅和集中确认展示。
- `electrical_intent` 由 `electrical_intent(plan)` 生成，包含 `components`、`modules`、`nets`、`primary_paths`。
  components 保留 `id/ref/query/role/module/pins/attach`，modules 保留 `id/core_ref`。
- `sources` 每项保存 `id/path/sha256/url/section`，以原始文件内容及章节定位查证。
- `selections` 每项保存 `role/model/package/component_ids/source_ids/pinout_section/application_section/required_capabilities/capabilities`。
  功能能力必须覆盖所需能力，例如湿度采集必须声明并查证湿度能力。
- `required_parts` 每项保存 `role/component_ids/source_ids`。外接件可另加 `external_parts`，每项为 `model/package`。
- `defaults` 每项保存 `field/value/reason/depends_on/source_ids`，无默认值时使用空数组。
- `bom` 每项保存 `model/package/quantity/component_ids/source_ids/quote`。外接件使用空 component_ids 并声明 `external = true`。
  quote 未知时使用 null，已知时保存 `unit_price/purchase_quantity/tier_quantity/currency/queried_at/source_id`。
- `build_quantity` 为采购套数，`calculations` 保存电气计算、限值或不适用理由。
  可选 `binding_budget` 使用 `amount/currency`，`complete_cost_comparison` 为布尔值。

独立审阅由宿主实际调用另一上下文完成，向审阅者提供设计文件及原始来源。私有审阅文件使用以下字段。

- `policy_version = 1`、`subject_sha256 = review_subject(design)`。
- `reviewer` 保存 `id/context_id/independent`，上下文须区别于设计文件的 author_context_id。
- `invocation` 保存调用记录文件的 `path/sha256`。调用记录保存 `reviewer_id/context_id/author_context_id/subject_sha256/request/response`。
- `checks` 逐项保存 `id/status/conclusion/source_ids`，覆盖 `requirements/selection/pinout/peripherals/electrical/bom`，全部 closed。
- `issues` 通过时为空数组。有待解决问题时继续修订并重新审阅。

程序核对调用记录、身份声明、资料摘要、检查覆盖与关闭状态。宿主负责真实独立调用及原文审阅。
中文确认页展示功能模块、供电与接口、装配、选型、完整 BOM 成本状态、主路径、来源及假设。
独立电气审阅通过后集中取得一次用户确认，`plan` 再补入最终契约摘要。

`validate_design_evidence(contract, plan=None)` 可在任意阶段重核证据。
`eso design-evidence-check contract.json --plan plan.json --search-path <directory>` 提供只读 CLI 入口。
原路径缺失时可在明确指定目录的直接文件中按摘要重新定位。当前原文件内容变化会使待确认设计证据失效。
`stage` 和 `prepare` 在依赖写入前将文件原字节原子固定为运行目录的 `design-evidence--<sha256>`，并重核摘要。
这些文件进入 stage 记录或 bundle。历史核验优先使用指定运行目录中的固定副本，源文件后续变化与固定副本被篡改分别处理。
固定副本的核验失败会拒绝后续操作。

坐标、模块标题与 drawing_groups 变化保留电气确认。型号、物理脚、net/NC、功能归属、外围附件或主路径变化要求重新审阅。

新策略同时使用知识库时，固定清单自动包含全部知识库器件及组合控制器的手册。历史核验按 evidence_root 中的内容摘要读取固定副本，材料记录及撤回状态继续核验。契约生成和确认前，审阅意图须与有界知识库或组合计划的电气拓扑一致。
