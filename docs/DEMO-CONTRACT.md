# 参考 Contract（离线草稿）

本文件展示流程的确认内容，不代表已获真实工程写入授权。
机器版本见 `examples/contract-DRAFT.json`，计划见 `examples/circuit-plan.json`。

- STM32F103C8T6 / LQFP48 与 BMP280 裸芯片 / LGA8；真实库身份待执行前核实。
- 外部稳压 3.3V，直接供 MCU、VDDA、VBAT 和传感器 VDD/VDDIO，不增加板载稳压器。
- 内部 HSI 8MHz；I2C1 PB6/PB7，100kHz，2×4.7kΩ 上拉候选。
- BMP280 CSB 接 3.3V，SDO 接地，地址 0x76；其 5 脚为 SDO、7 脚为 GND。
- 自定义 1×5 SWD：1 VTref、2 GND、3 SWDIO、4 SWCLK、5 NRST。
- NRST 上拉/滤波，BOOT0/PB2 下拉；VDD/VDDA/VDDIO 去耦及输入 bulk；共17个器件。
- 核心与专属外围实体导线相连，完整物理引脚逐一 net/NC；未用脚的原因在计划中记录。
- 不包含 PCB、USB、晶振、固件或硬件实测。
- 验收要求：完整回读对账、ERC/结构/桥接/几何、显式保存、真实重载后的新回读及工程导出。

具体执行时绑定用户获准的测试工程/页面 UUID，并重新生成 SHA256 与摘要，取得该版本确认。
离线示例没有 `approval.json`，不会自动进入执行。
