# 能力调研（2026-11-02）

## 可复核来源

- 上游：https://github.com/zhoushoujianwork/easyeda-agent
- 调研 main：`4a74c5636670e91c9894ac413f0badcf425757a9`。
- 实际运行版本：Release `v1.16.0`，tag 对象 `9282b0267a79aee721e2af6df1853fee0c353eb1`。
- tag 解引用源码提交：`b82be9c32404e164864e07901dd97a1bb1ad11dc`；区别于更新中的 main。
- macOS arm64 binary SHA256：`0256b36d8ce5df22a1c11f16b3c2d9f299395ad6f4862ff1055919249ff73fcb`，与 Release checksums.txt 相同。
- 本次宿主 EasyEDA Pro 4.1.93；CLI/daemon/Connector 1.16.0；测试工程 `ESO-TEST-STM32`。
- 首次检测 PATH 没有 easyeda；CLI 仅放工作区。已启动单一 60832 daemon，禁用 Skill 自动更新及 daemon autosave。未安装 Gateway，未重装 Connector。

## 事实与边界

| 能力 | 证据 | 本次验证级别 |
|---|---|---|
| action 发现 | `easyeda actions` 返回 JSON（没有 --json flag） | offline-verified |
| 工程、文档、空白页确认 | health、project info/doc、sch list；页面只有原生 A4 sheet | live-verified（只读） |
| 库检索 | STM32F103C8T6 C8734、BMP280 C83291 返回真实库 UUID | live-verified（只读） |
| 放置 | sch materialize → apply；缺 library UUID/5 raw 网格即拒绝 | source-only，等待现场 |
| 几何生成 | sch lib-layout / layout-plan → compose | CLI help 已核对，现场未验收 |
| 回读 | sch read、sch connectivity（1.4 IR）、sch list | 接口已核实；完整电路未验收 |
| 规则 | sch check、bridge-check、layout-lint、原生 sch drc | CLI help/Go 源码核对 |
| 持久化 | sch save 必须 saved:true；doc reload 真实关闭重开，再 fresh read | source-only，等待现场 |
| 导出 | sch export-image、sch netlist、project export-source | CLI help 已核对 |
| compose schema | main 有此命令，Release 1.16.0 报 unknown command | 当前版本 unsupported |

不能用 action catalog 的存在证明 Connector handler 在宿主可用；现场结果优先。
额外兼容发现：Release 1.16.0 的 `schComposeProtectedPage` 严格接受十类 pagePrimitives；
本次现场 Connector 返回十一类（额外 pins）。同版版本号仍可能出现输入结构不兼容。
离线合成十一类快照可复现 CLI 拒绝；真实 compose/apply 未执行。保留原始字段并停在 incomplete，
后续优先上游修复/一致构建，不能删掉 pins 冒充完整快照。十类合成快照的 compose/dry-run 已通过。
上游 DRC help 对退出码的说明与实际 Go 检查有差异；适配层同时核对退出码与布尔 passed，缺值即失败。
DRC 可能只有布尔/汇总，不能宣称拿到了完整 UI/ERC 警告清单。sch check、bridge-check、几何检查分别保留。
apply 非事务操作：超时/部分成功后停止，保留 journal，并先回读再决定修复；禁止盲重试或自动清页。
main 的 Web-only 协作限制不代表桌面宿主没有能力；本用户明确指定已安装桌面宿主。本次连接 health 证实 electron，实际能力逐项验证。

## 复用策略

不复制或修改上游引擎；以独立 CLI 进程适配。公开 Skill 按需引用上游 easyeda-agent 的 design-flow、part-selection、schematic-data、auto-layout-sop、schematic-wiring 和 environment-setup。
上游主要 MIT；extension/src/beautify 是 Apache-2.0，分发编译 Connector 须带对应 LICENSE/NOTICE。本项目不打包 Connector、二进制或上游源码。

## 电路资料

- [ST DS5319 Rev 20](https://www.st.com/resource/en/datasheet/stm32f103c8.pdf)：LQFP48 pinout、供电、HSI、SWD。
- [Bosch BMP280 DS001-26](https://www.bosch-sensortec.com/media/boschsensortec/downloads/datasheets/bst-bmp280-ds001.pdf)：§5.2、§6.1–6.3，CSB 接 VDDIO，SDO 选择地址，VDD/VDDIO 各 100 nF。
- 库 UUID 是现场搜索结果，绝不把型号、实例 UUID 或搜索第一项直接当已验证绑定。BMP280 的 5 脚是 SDO、7 脚是 GND。

此报告是能力基线，实际完成状态以 VALIDATION.md 和运行证据为准。
