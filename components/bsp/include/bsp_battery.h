// components/bsp/include/bsp_battery.h
// CellWise CW2017 电量计:I2C 0x63,与 ES8311 共用总线。
// BSP 写入自定义电池 profile,芯片直接给 SOC%,无需外部分压电阻与查表。
#pragma once

#include "esp_err.h"

// 初始化。内部会调 bsp_i2c_init()(幂等)。
// 芯片不应答时返回 ESP_ERR_NOT_FOUND —— 上层可据此在 UI 上标记该项不可用。
esp_err_t bsp_battery_init(void);

// 只接入现有电量计，不写 profile/告警/工作模式；用于已有设备的保守读取。
// 电量计须为 ACTIVE 且已有 profile 标志，否则返回 ESP_ERR_INVALID_STATE。
// 不验证电芯匹配或 SOC 标定精度；后台任务中串行调用这些 API。
esp_err_t bsp_battery_init_readonly(void);

// 剩余电量百分比 0..100;读失败返回 -1。
int bsp_battery_soc(void);

// 电池电压 mV;读失败返回 -1。
int bsp_battery_mv(void);
