# EDA 实操陷阱与恢复剧本

先读 [现场复盘与恢复协议](live-recovery.md)。本页历史实测不能覆盖当前 strict 判据与接口契约。

本文件记录在真实工程里用 easyeda-agent typed CLI 落图时踩到的坑与已验证的处置方式。
所有数字都来自实测（EasyEDA Pro 4.1.78 / CLI 1.15.0 / connector 1.14–1.15），
换版本、换图纸时先按当前 `--help` 与 `sch sheet-geometry` 重新确认，再套用结论。

## A. 环境与连接器

1. **先解析窗口，再发命令。** 同一工程可能同时开在多个 EasyEDA 窗口里，只给 `--project` 会报
   `project "<uuid>" maps to 2 windows — pass --window <id>`。用 `easyeda health` 拿窗口列表，
   核对精确工程/页、实际加载版本和所需能力；不能只选版本最高的窗口。磁盘升级不代表 Connector 已重载，part 属性移动成功也不能证明 sheet ownerType 支持。
2. **窗口 ID 会变。** 页面刷新、`doc reload`、`web reload`、应用重连之后 windowId 都会重新生成；
   把 windowId 写进脚本或记忆里必然失败。每次批量操作前重新 `health`，并接受
   `window "…" is not connected, but 1 connector window(s) ARE — window IDs change on reconnect` 这个提示。
3. **假失败定律（最重要）。** `DISPATCH_FAILED / connector did not respond / context deadline exceeded`
   不代表动作没落地。`place`、`connect`、`disconnect`、`frame apply`、`titleblock` 都可能已经生效。
   处置顺序固定为：**先轻读复核（`sch list` / `doc ready`）→ 再决定是否重发**。
   盲目重发会产生重复端口、重复旗子、两根同名桩线，比失败本身更难清。
4. **读写能力不对等。** 连接器退化后常见「读还行、写超时」：`doc ready`、`sch list` 正常，
   但 `sch check`/`sch gate`/`export-image` 超时。此时不要继续堆命令。
5. **串行 + 间隔。** 连续发 8–24 条 connect/disconnect 很容易把连接器打到 `DEGRADED`。
   每条变更之间 sleep 300–500 ms，每 6–10 条插一次轻读；失败就停下重读，不要并发。
6. **别用 stale 重试掩盖问题。** `--force-stale-read` 已废弃且不解决根因。
7. **端口段被 Windows 动态预留（新，2026-10-28 实测）。** 症状是 `daemon start` 失败并打印
   `port 60832 owner PID is unknown or self (0, unknown); health did not identify a daemon; no process was terminated`，
   同时 `health` 全是 `unreachable`，但 EasyEDA 进程在、连接器也在重试（`netstat` 里能看到
   `127.0.0.1:xxxxx -> 127.0.0.1:60832 SYN_SENT`）。
   根因：Hyper-V / WinNAT 把一段动态端口预留成了「排除范围」，正好罩住 60832–60841；被排除的端口
   任何进程都 `bind` 不上（`WinError 10013 以一种访问权限不允许的方式做了一个访问套接字的尝试`），
   与防火墙、与 CLI 版本都无关。30 秒诊断：
   ```powershell
   netsh interface ipv4 show excludedportrange protocol=tcp   # 看 60832 是否落在某段里
   python -c "import socket;s=socket.socket();s.bind(('127.0.0.1',60832));print('ok')"  # 10013 = 被排除
   ```
   三条处置（按侵入性排序）：①**改端口**——daemon 用 `--ports 61007-61067` 起（会绑段首 61007），
   再在 EasyEDA 里把连接器扩展的用户配置 `daemonPorts` 设为 `61007`（连接器源码里
   `eda.sys_Storage.getExtensionUserConfig("daemonPorts")` 就是这个覆盖项，默认写死 `60832`，
   URL 固定 `ws://127.0.0.1:<port>/eda`，所以**只能改端口，不能改 host/IPv6**——`::1` 同样被排除）；
   ②**释放端口**——管理员执行 `net stop winnat` → 起 daemon → `net start winnat`（WinNAT 重启后会避开已占用端口）；
   ③重启电脑碰运气（动态预留会重排，不保证让出）。
   注意：daemon 只绑 `--ports` 的**段首那一个端口**，不会向后溢出，所以「多给几个端口」没用。

## B. 落位顺序与几何

1. **顺序铁律：place → 定姿态/坐标 → 量引脚 → 才接线。** 接线之后再 `sch modify` 挪器件，
   桩线、旗子、端口都留在原处，新位置引脚变悬空，并留下 dangling wire 与 orphan stub。
   ​已接线的器件要挪，只能 `sch disconnect --pin` 摘干净 → 挪 → 重新 `connect`。
2. **`sch modify` 只认 `--id <primitiveId>`。** 传 `--designator C3` 只会改名，不会定位；
   对非 part 的 primitive（属性、文字）会直接报 `Failed to read component`。
3. **尽早确定姿态并回读。** place 与 modify 后的属性姿态可能不同，不能假设文字跟随旋转。
   接线前实测引脚及全部文字，再做必要的 typed 属性移动，复核 `designator-out-of-frame`（见 B4）。
4. **`designator-out-of-frame` 的「frame」是功能分区框，不是器件本体。** 判据是位号 bbox 落在
   该器件所属 zone frame 之外。修法二选一：加宽所在 zone，或把该器件/位号移进框内。
   不要照抄「位号必须压在器件 bbox 里」的错误结论——同页其它器件位号同样在 bbox 外却不报错。
5. **极性先对齐再接线。** 二极管类符号默认 `p1 = K`（阴极，画在左）、`p2 = A`（阳极，画在右）。
   升压要 `SW → A → K → VGH`，反相泵要 `PUMP → 阴极`。放完立刻 `sch list --include-pins`
   核对 p1/p2 的实际坐标，再决定要不要 `--rotation 180`；接完线才发现极性反了，代价是四倍命令量。
6. **单位与密度（A4 默认图框实测）。** 图纸 1170×825，**1 单位 = 10 mil = 0.254 mm**；
   100mil 符号的引脚间距 = 10 单位；7 字符 netport 的 bbox ≈ 31×11 单位。
   所以相邻引脚挂标签必然 11 > 10 压行，必须奇偶交错桩长（如 18/78）或用 `staggerLabels`。
7. **图签禁入区**：`sch sheet-geometry` 给出的 titleBlock keepout 在本图框是 `[468,0]–[1170,198]`，
   左下角还有纸边安全区（≈12 单位）。桩长/标签算到边界就先看这两块，否则会得到一串
   `out-of-sheet` / `进图签禁入区` 的硬否决。

## C. 标签式布线（netport/netflag 批量）

小电路、引脚密集（≤0.1 inch 间距）或需要人工可读的分区页，用**标签式**比逐根画线更稳：
实体导线有「共线段自动合并」的静默短路风险，手工折线还要自己避让。

```json
{
  "connections": [
    {"pin": "U1:2",  "kind": "netport", "net": "GDR"},
    {"pin": "CN1:1", "kind": "power",   "net": "VDD_3V3"},
    {"pin": "CN1:2", "kind": "gnd",     "net": "GND"}
  ],
  "rules": {"avoidTitleBlock": true, "avoidPinFanout": true, "staggerLabels": true,
            "offsetRange": [18, 80], "offsetStep": 6, "minLabelGap": 14}
}
```

- `easyeda sch autoconnect --spec s.json` 幂等：同网跳过、异网需 `--replace`；
  **失败不阻塞其余**，退出码非零但 `succeeded[]` 里的都落地了，要分开记账。
- **先 `--dry-run`。** 打分语义：`score ≥ 1e6` = 硬否决（方向不沿引脚朝外 / 桩线穿过非目标引脚 /
  标签出可用区 / 进图签禁入区 / 与异网导线相碰）；`score ≈ 1000 级` = 软惩罚（压到别的引脚出口或别的标签）。
  出现硬否决先改版面，不要硬怼 `--replace`。
- 需要精确控制时用单脚命令：`sch connect --pin U1:9 --kind netport --net BUSY --direction left --offset 40`；
  反向删除用 `sch disconnect --pin U1:9`（会同时删桩线与旗/端口）。
- NC 一次多脚：`sch no-connect --designator U1 --pin 1,4,6,7,19,25,26`。
- **代价要提前说清**：逐脚 netlist 对账之外仍需检查目标 direct 组。
  `peripheral-not-direct` 启发式告警逐项核查，不能作为模式告警自动豁免 strict 失败。

## D. 图框、分区与图签

1. **分区框**：`sch frame apply --from frames.json`，JSON 形状
   `{schemaVersion:1, documentId:"<页面uuid>", frames:[{id,title,rect:{minX,minY,maxX,maxY},titleX,titleY,fontSize,color,lineType:1}]}`。
   `documentId` 必须是**页面 uuid**，且**不能同时传 `--doc`**（否则 `frame document conflicts with --doc`）。
2. **标题必须整段落在框内**：渲染器会实测标题宽度，超出就整帧失败
   （`rendered title outside module frame: {…MaxX:1082…}`）。中文标题 ≈ 每字 fontSize 宽，
   放之前先按「字数 × fontSize + 边距」估一遍；框太窄就加宽框或改短标题。
3. **图签字段在旧 connector 上会被静默吞掉**：1.14 返回 `notApplied:["Company"]` 且
   `Title block modify returned success but nothing was applied`；同一命令在 1.15 正常 `applied`。
   写不进去先 `sch titleblock --show` 读回，确认是版本问题还是字段名问题（字段名形如
   `Name / Drawed / Description / Company / Version`，首字母大写）。
4. **图签初次显示须回读定位**：Name/Description 原始 X/Y 可能为 null；设值并显示后，
   先读平台初始化的位置，再用当前 typed sheet 属性移动放入实测空白单元格。
   完整 expected 状态保留 null，不伪造 0；复查固定印字、绑定值和重复文字。不能关闭必需内容显示来消越界。
5. `missing-deliverable(partition/titleblock)` = 既没画分区框、图签又为空。多器件页两个都要有。

## E. 校验与网表

1. **`sch gate` 判据取决于当前参数和版本。** strict 可因 warning 失败；保留完整报告，
   不将非 strict 通过等同 strict 通过。独立文字、模块边界与保存重开仍需验收。
2. **`sch check` 计数项**（读输出前先认清含义）：floating pin / geom-net mismatch / net-marker mismatch /
   multi-net wire / wire-crossing / wire-over-pin / zero-length wire / dangling-wire / duplicate-net-marker /
   titleblock-overlap / out-of-sheet / marker-overlap / designator-overlap / missing-deliverable /
   folded-net-label / redundant-net-marker / reversed-net-flag / polarity-convention outlier。
3. **`sch netlist` 包装 JSON 按实际字节编码解析**；shell 重定向可能改变编码，不是固定 UTF-16 协议。
   真网表在 `result.artifactPath` 指向的 `.enet`。核对要用 `components[*].props.Designator` + `pinInfoMap[*].net` 还原成
   「net → [ref:pin…]」，与冻结意图逐脚 diff；`sch read` 的 net 字段同源，但不是逐脚表。
   本技能带 `scripts/netlist_diff.py` 做这件事：
   ```powershell
   python <skill>/scripts/netlist_diff.py --enet <页.enet> --expect expected.json --out report.json
   ```
   `expected.json` 形如 `{"nets":{"GDR":["Q1:1","R1:1","U1:2"]},"nc":["U1:1"],"allow_swap":["C3"]}`，
   exit 0 = PASS、1 = 不一致、2 = 读不动。`allow_swap` 用来放行无极性件的 p1/p2 互换。
4. **每次挪件/重接后都要重跑 netlist diff**：几何检查不保证网络没变（尤其
   `disconnect` 后 `connect` 中途失败时，同名端口会有两份）。
5. 电容等无极性件的 p1/p2 互换不改变电气，diff 时单独放行并在说明里注明，不要为对齐引脚号反复重放。

## F. 参数、编码与 PowerShell

1. **JSON 参数文件必须无 BOM**。Windows PowerShell 5.1 的 `Set-Content -Encoding utf8` 会写 BOM（PowerShell 7 行为不同），CLI 可能报
   `invalid --spec json: invalid character 'ï' looking for beginning of value`。
   正确写法：
   ```powershell
   [IO.File]::WriteAllText($path, $json, (New-Object System.Text.UTF8Encoding($false)))
   ```
2. **不要把 stderr 并进 stdout。** `2>&1 | Out-String` 会把 `easyeda : …` 错误行拼进 JSON，
   后续 `ConvertFrom-Json` 必失败。用 `1> out.json 2> err.txt`。
3. **别把带引号的 JSON 直接塞给原生 exe**：PowerShell 会吞引号，`--data` 报
   `invalid character 'N'`，`--%` 也救不回来。稳妥做法是写一个 8 行的 python 包装：
   读 job JSON → `subprocess.run(argv_list)` → 把 stdout/stderr 落盘。
4. **中文/长参数**一律走文件或包装器，避免编码二次转换；`Get-Content -Raw | ConvertFrom-Json`
   读 UTF-8 文件是安全的，读 UTF-16 输出要先按字节 decode。

## G. 恢复剧本（连接器/编辑器卡死）

按顺序试，每一步都以「读一次」验证，不靠猜：

1. `sch save` —— 常常还能成功。**先落盘再谈恢复**，后续任何 reload 才有意义。
2. `easyeda health` 重新解析窗口；换到活着的窗口重发。
3. `easyeda web reload --window <w> --project <uuid> --doc <pageUuid>` —— 需要**活跃文档 + 精确 uuid**；
   若报 `No active document`，先走第 4 步。
4. `easyeda project open --project-uuid <p> --page-uuid <page> --window <w> --allow-discard-unsaved`
   —— 会丢弃未保存内容，所以只在第 1 步 `saved:true` 之后使用；返回
   `{"opened":true,"verified":true,"documentVerified":true}` 才算恢复。
5. 恢复后 windowId 已变，重新 `health`；然后**先导出图**再继续改图。

**`export-image` 会卡住编辑器。** 失败信息是
`Export did not settle within 30000ms … the editor will show a stuck progress toast`，
它不只是本次失败：之后再发变更会连锁超时。规避法：
- 导出放在「刚重开工程、还没做任何变更」的窗口里做，成功率最高；
- 一次导出成功后不要紧接着改图再导出同一张页；
- 已卡住先按 §G 第 1 步保存并确认 `saved:true`，再执行第 4 步；无法保存时不得丢弃未保存内容。
- `--scale` 不存在（当前版本只有 `--format svg|png|pdf`、`--ids`、`--scope`、`--page`、`--out`）。

## H. 交付前自查（本技能建议的收尾清单）

- [ ] 当前 `sch gate --strict` 各阶段通过，保留原始报告；独立检查例外逐对象说明，不能篡改严格失败。
- [ ] `netlist_diff.py` 逐脚 PASS（exit 0），NC 数量一致，`allow_swap` 放行项在说明里点名。
- [ ] `sch save` 返回 `saved:true`。
- [ ] `doc reload` → 新 `sch list` → 新导图，三者与报告一致。
- [ ] 图签有 Name/Drawed/Version，分区框覆盖全部器件。
- [ ] 说明文档里写清：用什么模式连的线、哪些告警是刻意的、哪些参数是待厂方确认的。
