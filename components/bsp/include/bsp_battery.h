// components/bsp/include/bsp_battery.h
// CellWise CW2017 电量计:I2C 0x63,与 ES8311 共用总线。
// BSP 写入自定义电池 profile,芯片直接给 SOC%,无需外部分压电阻与查表。
#pragma once

#include "esp_err.h"
#include <stdbool.h>
#include <stdint.h>

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

/* Read-only sample, safe to retry from one worker every few seconds. A failed
 * attach is retried on the next call. No profile, mode or alert writes. */
esp_err_t bsp_battery_sample_readonly(int *soc, int *mv);
/* Recovery for the board's known cell: first compare all 80 existing profile
 * bytes, then only for an exact match wake CONFIG 0x30 -> 0x00 and verify.
 * Preserves every nonempty profile. A verified all-zero profile on A0 with
 * mode F0/30 and UPDATE_FLAG=0 may be initialized with the known board profile,
 * with full readback and at most one write attempt per firmware boot. Only
 * that blank initialization sets UPDATE_FLAG, preserving alert threshold bits.
 * A matching profile is sufficient when the host-owned flag is zero.
 * Serial battery-worker calls only; wake adds 30 ms of task delay. */
esp_err_t bsp_battery_sample_preserving_profile(int *soc, int *mv);

typedef struct {
    bool attached;
    int version;      /* -1 when no response has been received */
    int mode;         /* raw CONFIG, -1 when unavailable */
    int profile_flag; /* 0/1, -1 when unavailable */
    int profile_matches; /* 0/1 against known board profile, -1 when unread */
    int profile_blank; /* 0/1 from all 80 bytes, -1 when unread */
    uint32_t profile_fingerprint; /* FNV-1a diagnostic, never used as equality gate */
    unsigned wake_count;
    unsigned profile_init_count;
    esp_err_t error;
} bsp_battery_diagnostics_t;
/* Cached diagnostics; no bus access. Call from the same battery worker. */
void bsp_battery_get_diagnostics(bsp_battery_diagnostics_t *out);
