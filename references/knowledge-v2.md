# 资料复用与有限模块组合

本版开发能力覆盖 0.4 的离线资料流程和 0.5 的有限组合。真实电路验收状态见 `docs/plans/knowledge-real-material-feasibility.md`。

## 用户与宿主输入

用户提供制造商、完整型号、封装、手册位置和工作条件。宿主阅读手册、核验真实库并整理结构化摘录。`examples/knowledge-v2` 中的器件、参数、库身份全部为合成示例。

资料按原始事实、条件化推导与工程计算管理。器件记录的 `facts` 包含 `value`、规范单位、`basis`、`bound`、章节与测试条件。`basis` 可为 `guaranteed`、`typical`、`derived`。必需保证参数拒绝典型值。推导引用同一记录的事实，支持命名参数及四则运算，检查公式与结果一致性。公式的物理意义、量纲和边界方向由宿主审阅。

`coverage` 是宿主为全部使用参数审阅的共同适用包络，包含环境温度、电源、控制电压、负载电流、频率范围和散热上下文。`coverage_review` 记录依据。单个测试点向更宽工况的延伸须有明确推导与依据。电阻公差必须覆盖所声明温度下的初始公差与温漂。软件校验结构、计算及包络包含关系，宿主负责核对原文真实性和语义。

开关实例新增 `supply_min_v`、`ambient_min_c`、`on_current_min_a`、`on_current_max_a`、`off_current_max_a` 和 `thermal_context`。原有 `supply_voltage_v` 表示最大电源电压。程序检查电气限制及导通电流上下界、关闭漏电上界。关闭漏电保证须覆盖已声明的最大关闭输入电压。

## 私有缓存与接口

默认缓存位于当前用户数据目录。macOS 使用用户 Library/Application Support/eso/materials，Linux 使用 XDG_DATA_HOME/eso/materials，Windows 使用 LOCALAPPDATA/eso/materials。可设置 `ESO_MATERIAL_STORE` 或全局 `--material-store`。仓库内缓存必须位于 Git 忽略目录。

```sh
eso kb-material-add runs/reviewed-material.json --document /absolute/datasheet.pdf
eso kb-material-list --manufacturer onsemi --model MMBT3904LT1G
eso kb-material-withdraw RECORD_SHA256 --reason '引脚摘录错误'
eso kb-import runs/manifest.json --out runs/import-draft.json
eso kb-evidence-status runs/contract.json
```

资料身份由制造商、完整型号和封装确定，记录版本由完整内容哈希确定。多个版本并存时，导入清单可显式指定 `record_sha256`。`kb-import` 输出含 `knowledge` 的草稿，宿主将其并入完整 requirements，补齐当前库核验后运行 `assess`。草稿中的 `ready=false` 表示仍需审阅和补充。

简化清单包含 `materials` 和 `circuits`。每个材料行包含 `id`、`manufacturer`、`model`、`package`、`document`，可选固定版本。导入会核对文件哈希并报告缺失、歧义和库核验项。

相同记录再次添加可更新文档定位，记录内容保持不可变。工程契约保存完整快照及哈希，文件路径保存在私有定位表。移动文件后使用同一记录和新路径重新添加。后续操作检查文件内容、记录撤回状态、当前知识依赖与校验器内容。

共享缓存适合复用已审阅的事实与条件化推导。当前工程的电流预算、供电和散热适用性仍需检查。撤回会阻断后续确认与执行，历史报告仍保留原始结果。撤回记录目前在本机管理。

## MCU 组合

`requirements.composition` 支持现有 STM32F103C8T6 LQFP48 最小系统和 BMP280 参考方案，使用 `knowledge.schema_version=2`。

组合需要固定的控制器资料、完整 `base_bindings`、逐实例 `connections`、外部 `supplies`、剩余端口与芯片电流预算及审阅依据。模式固定为 `stable-powered-static`。每个连接声明物理 GPIO 编号、供电网络、参考地及复位高阻依据。控制器引脚表使用 `语义名称_物理编号` 键，保留重复供电脚的身份。

首版允许 PA0–PA7、PB0–PB1、PB5–PB11 中当前未占用的引脚。组合核验占用情况、供电范围、驱动与漏电边界、单脚及端口／芯片预算。额外 GPIO 能力需要补充对应校验后扩展。所有独立支路保持完整，新增位号避开基础位号。

## 诊断、演示和测量

```sh
eso doctor
eso doctor --live --target runs/target.json --out runs/doctor.json
python scripts/demo_knowledge_v2.py --out runs/knowledge-v2-demo
```

诊断报告包含本地工具、渲染器、连接、配套版本、快照与指定工程的历史验收状态。连接可用且存在版本差异时，连接检查通过，版本检查给出 warning 及上游修复信息。指定现场 target 后，`upstream_compile` 使用本次原始快照运行离线 compose 和 apply dry-run 探针，报告快照兼容性。正式 prepare 继续检查具体设计。正式执行仍要求精确设计确认和完整执行门禁。

渲染器随 ESO 包分发。`ESO_BROWSER` 可指定 Chromium 可执行文件，`ESO_LAYOUT_SKILL` 可覆盖兼容工具位置。

`kb-metrics` 继续接受旧列表格式，也接受 `schema_version=2` 的记录对象。新记录保留 success、blocked、failed、interrupted 状态，包含总耗时、人工等待、工具耗时、分阶段耗时、补参次数和资料整理次数。阶段耗时为互不重叠的墙钟片段，未知值写 null。只有双方任务全部成功时才标记可作成功任务比较。

## 参数的工作状态

参数可以添加 `applicability`，包含 `state` 和完整 `coverage`。`state` 取 `on`、`off` 或 `all`，表示该参数适用的电路状态。省略此字段时沿用器件记录的公共范围。

导通电阻、饱和压降和基极驱动相关参数按导通状态检查。MOS 使用计算得到的最低／最高栅压，NPN 使用已审阅的 VBE 范围。关断参数必须覆盖从 0 V 到声明关断输入上限的范围。器件电压、功率和漏电等跨状态参数继续核验完整工作范围。记录级公共范围仍须覆盖整个设计。

推导参数的状态、温度、电源、控制电压、电流、频率与散热条件必须包含在每个输入参数的适用范围内。数值公式与上下界检查同时生效。MCU 组合也逐项检查 GPIO 参数的适用条件。

这些字段由宿主按原始资料审阅填写。软件能发现条件不包含和状态不匹配，手册未给出的保证仍需补充依据。字段格式更新使校验器内容哈希变化，现有契约需要重新生成并确认。

多份手册缺失、损坏或被撤回时，资料检查集中列出全部受影响的材料 ID。

## 分阶段录入真实资料

资料可先以 `status=draft` 保存。草稿保留完整器件身份、手册哈希及定位、已核对的引脚、带原始测试条件的数值和 `missing_evidence`。草稿省略尚未审阅的 coverage 与 coverage_review，missing_evidence 至少列出一项具体缺口。草稿保存原始保证值或典型值，条件化推导在完整审阅版本中录入。

草稿沿用 kb-material-add 和 kb-material-list 接口。列表显示 status，导入报告通过 draft_candidates 列出匹配的草稿版本及缺项。即使清单明确指定草稿哈希，导入也保留缺项，设计契约和执行检查继续阻断。

完成审阅后另存完整记录，填写 coverage、coverage_review 等依据，设置 status=reviewed 或省略 status，并移除 missing_evidence。新增记录生成独立哈希，原草稿保留。升级后的完整记录仍需通过当前工程的 assess 与库绑定核验。

合成草稿样例位于 examples/knowledge-v2/draft-material.json，配套文档为 documents/switch.txt。真实原厂 PDF、草稿和库查询回执保存在用户私有目录。
