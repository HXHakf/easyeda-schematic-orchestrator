# 上游刚体来源浮点校验修复

easyeda-agent-rigid-roundoff.patch 面向 easyeda-agent 源码基线 `4a74c56`，仅包含
刚体来源比较、三组回归测试及布局流程说明，不包含其他本地修改。

合法的多轮旋转/平移可能因浮点运算次序产生微小差异，旧实现的 reflect.DeepEqual 将其误判
为几何被改写。补丁复用既有 variant 的有限坐标 `<1e-7 raw` 容差，只用于X/Y、bbox、文字框
和引脚坐标；身份、Value、镜像、朝向、针编号/名称/网络/数量仍精确比较。

在上游源码目录执行，先检查是否已包含等效修复：

```sh
git apply --check /path/to/easyeda-agent-rigid-roundoff.patch
git apply /path/to/easyeda-agent-rigid-roundoff.patch
go test ./...
```

本次 Go 全包测试通过，新增测试覆盖舍入、连续变换、超差、非有限坐标及身份/网络保护。
ESO使用隔离修复构建运行91项测试，90通过、1项POSIX用例在Windows跳过，包含真实Chromium
原生SVG测量及上游离线布局/编译/dry-run集成。补丁不等于已发布上游版本；完整电路纸张装箱
仍未通过，不自动更换 CLI、daemon 或 Connector。
