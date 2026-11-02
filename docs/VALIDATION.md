# 发布与安装验证：0.2.1

- 本机64项自动测试通过：61项单元/模拟测试（含7项安装/编码测试），3项真实上游CLI离线集成。
- 安装器已在隔离目录实际创建专用环境、安装依赖及完整Skill；launcher、离线Demo与缺参流程通过。
- 安装覆盖拒绝覆盖未管理文件、备份与重复安装、路径交叠、pip失败保留旧Skill、含空格/引号的命令路径。
- Windows 云端烟雾测试发现中文 Markdown 默认编码失败；新增非UTF-8环境回归并统一文件/JSON/上游输出为UTF-8。
- GitHub Actions 配置 Python 3.10/3.13 单测，以及 Linux/macOS/Windows 安装与重复安装烟雾测试。
  云端实际结果见 [Actions](https://github.com/HXHakf/easyeda-schematic-orchestrator/actions)，配置本身不代表通过。
- 所有发布准备/安装测试均未向 EDA 写入电路；以下0.2.0和0.1是历史验证记录。

# 工作流验证记录：0.2.0

日期：2026-11-02。以下为0.2当前实现；后附0.1记录仅供追溯。

- 57项自动测试通过：54项单元/模拟流程，3项真实上游CLI离线集成。
- 真实离线集成覆盖 materialize、派生测量输入→lib-layout、compose、apply dry-run 与已知兼容反例。
- 新增回归：默认随条件重算、显式值保留、类型切换、每轮三问、帮我选、失败复验撤销旧成功、证据变更阻断交付。
- 测量适配回归：完整pagePrimitives/可用性、归属对象、逐脚库存、PA1/PA10区分、原始SVG哈希、测量清单绑定、部分放置与禁止盲重放。
- verify/deliver 未通过返回3，输入或操作拒绝返回2，不能只看命令无异常就放行。
- 独立模型15条触发/不触发表达评估已完成，见 [评估](TRIGGER-EVAL-0.2.md)。这是行为评估，非桌面隐式选择成功率统计。
- stage→collect→prepare 的宿主交互使用模拟适配器；未把模拟结果称为现场通过。

当前已知阻断：上游1.16.0与现场十一类快照不兼容。stage在首次写入前拒绝该组合，保留原始数据。
完整真实 place/wire/save/reload/ERC/工程交付及硬件功能**尚未验收**，不能据接口实现称已完成。
本轮没有执行真实电路写入。

---

# 0.1 历史验证记录

日期：2026-11-02。版本：workflow MVP 0.1.0。验证范围以本页为准。

## 自动验证

| 检查 | 结果 | 覆盖 |
|---|---|---|
| 27 项单元/模拟集成测试 | 通过 | 条件补问、默认来源跨轮、类型/冲突、确认失效、全引脚、源对账、受限动作、哈希变更、失败锁、失败回读、严格保存布尔、重载后对账 |
| 2 项真实 easyeda v1.16.0 离线集成 | 通过 | materialize→apply dry-run；compose→受保护接线队列→apply dry-run，含十一类快照兼容反例 |
| Skill frontmatter 验证 | 通过 | name/description/命名与结构 |
| 离线 demo_workflow | 通过 | 缺参→示例回答→默认记录→Contract 草稿→计划，零 EDA 调用、无 approval |
| wheel 打包及独立安装 | 通过 | Python 包与四个 schema 可分发；在另一个虚拟环境安装 wheel，从仓库外运行 eso assess 成功 |
| 独立 Skill 行为推演 | 完成 | 正确识别“只开发流程”；据发现修正提前索取工程、模块上拉默认、默认来源丢失及文档入口 |

模拟适配器返回的“verified”只是测试数据；这些测试不能证明真实 EDA 电路已完成。
CI 配置已提供，本地只运行当前 Python 3.13 环境；未声称已在 GitHub 云端或 Python 3.10 完成运行。

复现：

```bash
python -m unittest discover -s tests -v
EASYEDA_BIN=/path/to/easyeda-v1.16.0 python -m unittest discover -s tests -v
python scripts/demo_workflow.py --out runs/offline-demo
```

## 本机只读验证

已观察：CLI/daemon v1.16.0、Connector 1.16.0、EasyEDA Pro 4.1.93、专用空白测试原理图，
STM32F103C8T6/BMP280 的真实库搜索与 device 关联，以及官方 SVG 中的纸张内框/图签占位。
CLI 初始不在 PATH，工作区下载 Release arm64 并核验 SHA256；未安装 Gateway，未替换插件。
原始响应留在本地私有工作目录，公开包不含个人工程 UUID 与路径。

用户随后明确以**开发工作流**为重点，本轮没有执行电路 place/wire/apply，也没有清理任何工程。
daemon 为连接检查曾在本机启动，关闭 Skill 自动更新与 daemon autosave；没有自动改客户端配置。

## 已发现的兼容问题

1. main 文档有 `sch compose schema`，Release v1.16.0 没有。必须探测实际 help。
2. Release 源码只接受十类 pagePrimitives，本次现场返回十一类（含 pins）。
   用十一类合成快照对同一发布 CLI 的离线 compose 可复现 `incomplete class inventory`；
   十类合成快照编译/dry-run通过。版本号一致不能证明结构兼容。
3. `layout-lint` 是 bare JSON，以 `ok` 判定；DRC/check/bridge 是 result envelope，以 `passed` 判定。
4. DRC 可能没有完整计数或 UI 明细；缺失必须原样说明。

## 未验证/未实现

- **未验证**：完整真实电路绘制、规则/ERC 结果、保存重载后图纸一致性、最终导出交付、硬件运行。
- **尚需 handoff**：逻辑 plan 到真实库/全引脚/实测几何采集与上游布局输入；当前 prepare 从已形成的 composition 接入。
- **未支持**：自动重排已有图纸、重要工程、任意电路的确定性规划、多电源/USB/晶振拓扑、PCB、事务回滚。
- **未认证**：通用自然语言理解、电气设计正确性、完整 GUI ERC 覆盖；宿主模型与原厂证据仍承担设计审查。

下一步优先解决上游快照版本兼容与测量适配，再另行授权实际测试工程端到端验收。
