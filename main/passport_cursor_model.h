#pragma once
#include <stdbool.h>
#include <stdint.h>

/* App-reported billing buckets are percentages, never token estimates. */
typedef struct {
    int64_t observed_utc_ms;
    int64_t expires_utc_ms;
    int64_t reset_at_ms;
    char source[24];
    char plan[24];
    bool used_known[2]; /* Cursor models, other models */
    float used_percent[2];
} passport_cursor_snapshot_t;

bool passport_cursor_snapshot_valid(const passport_cursor_snapshot_t *snapshot);
bool passport_cursor_snapshot_fresh(const passport_cursor_snapshot_t *snapshot, int64_t now_utc_ms);

/* Last valid observations remain displayable after expiry/reset. This does
 * not claim freshness and never converts unknown values to zero. */
bool passport_cursor_snapshot_display_known(const passport_cursor_snapshot_t *snapshot, unsigned index);
