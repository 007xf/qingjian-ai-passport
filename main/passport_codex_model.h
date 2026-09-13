#pragma once
#include <stdbool.h>
#include <stdint.h>

typedef struct {
    bool used_known;
    float used_percent;
    int duration_min;
    int64_t reset_s;
} passport_quota_window_t;

typedef struct {
    int64_t observed_utc_ms;
    int64_t expires_utc_ms;
    char source[24];
    passport_quota_window_t window[2];
} passport_codex_snapshot_t;

bool passport_codex_snapshot_valid(const passport_codex_snapshot_t *s);
bool passport_codex_snapshot_fresh(const passport_codex_snapshot_t *s, int64_t now_utc_ms);

/* Last valid observations remain displayable after expiry/reset. This does
 * not claim freshness and never converts unknown values to zero. */
bool passport_codex_snapshot_display_known(const passport_codex_snapshot_t *snapshot, unsigned index);
