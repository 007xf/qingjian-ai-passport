#pragma once

#include <stdbool.h>
#include <stdint.h>
#include "passport_codex_model.h"
#include "passport_cursor_model.h"
#include "passport_provider_model.h"

typedef struct {
    int battery_soc;
    int battery_mv;
    bool screen_on;
    bool time_synced;
    int64_t utc_ms;
    int offset_min;
    char page[24];
    uint32_t free_heap;
    uint32_t feature_revision;
    uint8_t visible_features;
    uint8_t supported_features;
    bool features_all;
    uint8_t custom_features;
    uint32_t input_dropped;
    bool save_busy;
    int last_save_error;
    uint64_t threshold1;
    uint64_t threshold2;
    int64_t codex_observed_utc_ms;
    bool codex_ready;
    bool codex_stale;
    int64_t cursor_quota_observed_utc_ms;
    int64_t cursor_quota_reset_at_ms;
    char cursor_quota_plan[24];
    bool cursor_quota_ready;
    bool cursor_quota_stale;
    bool cursor_quota_known[2];
    float cursor_quota_used_percent[2];
    uint32_t dino_score;
    uint32_t dino_best;
    int dino_state;
} passport_status_t;

typedef struct {
    char name[25];
    char title[49];
    char intro[121];
    uint64_t tokens;
    bool tokens_known;
    unsigned stage;
    bool tokens_stale;
} passport_badge_t;

/* Initialize once after BSP display/LVGL/I2C. Creates bounded input and NVS
 * worker tasks, but never starts networking or audio. */
bool passport_start(void);

/* Thread-safe nonblocking commands; true means accepted into the bounded
 * queue, not applied. Invalid UTC (outside 2024..2099) or offset is rejected.
 * Read STATUS after acknowledgement to confirm applied state. */
bool passport_set_time(int64_t utc_ms, int offset_min);
bool passport_set_screen(bool on);

/* Thread-safe snapshot; no caller-owned strings or UI pointers escape. */
void passport_get_status(passport_status_t *out);
void passport_get_badge(passport_badge_t *out);
bool passport_set_badge(const char *name, const char *title, const char *intro,
                        uint64_t tokens, bool tokens_known);
bool passport_set_features(bool all, uint8_t mask);
bool passport_avatar_changed(void);
bool passport_set_tokens(uint64_t tokens, bool known);
bool passport_set_thresholds(uint64_t first, uint64_t final);
bool passport_set_codex(const passport_codex_snapshot_t *snapshot);
bool passport_set_cursor(const passport_cursor_snapshot_t *snapshot);
bool passport_set_pairing_display(bool visible, uint32_t passkey);
bool passport_request_pairing(void);
bool passport_set_provider(const passport_provider_snapshot_t *snapshot);
void passport_get_providers(passport_provider_snapshot_t out[PASSPORT_PROVIDER_COUNT]);
