#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define PASSPORT_CLAUDE_CONFIG_BYTES 5
#define PASSPORT_CLAUDE_MAX_TOKENS UINT64_C(9007199254740991)

/* Claude Code local Token observations and statusLine quota observations have
 * independent clocks. They never contribute to the badge evolution counter. */
typedef struct {
    char source[12]; /* "claude_code" */
    int64_t observed_utc_ms;
    int64_t expires_utc_ms;
    bool today_known;
    bool lifetime_known;
    uint64_t today_tokens;
    uint64_t lifetime_tokens;
    bool tokens_complete;
    int64_t quota_observed_utc_ms;
    int64_t quota_expires_utc_ms;
    bool quota_known[2]; /* five-hour, seven-day */
    float quota_used_percent[2];
    int64_t quota_reset_ms[2]; /* 0 means unknown */
} passport_claude_snapshot_t;

/* Clearing a profile must not let a delayed frame revive the previous one.
 * Keep ordering barriers separately from the visible (possibly unknown) data. */
typedef struct {
    passport_claude_snapshot_t snapshot;
    int64_t token_watermark_ms;
    int64_t quota_watermark_ms;
    bool tokens_cleared;
    bool quota_cleared;
} passport_claude_state_t;

bool passport_claude_snapshot_valid(const passport_claude_snapshot_t *snapshot);
bool passport_claude_tokens_fresh(const passport_claude_snapshot_t *snapshot, int64_t now_utc_ms);
bool passport_claude_quota_fresh(const passport_claude_snapshot_t *snapshot, unsigned index, int64_t now_utc_ms);
bool passport_claude_tokens_display_known(const passport_claude_snapshot_t *snapshot, unsigned index);
bool passport_claude_quota_display_known(const passport_claude_snapshot_t *snapshot, unsigned index);
void passport_claude_state_init(passport_claude_state_t *state);
bool passport_claude_state_apply(passport_claude_state_t *state, const passport_claude_snapshot_t *snapshot);
/* A separate NVS key preserves the original APF1 feature-mask format. */
void passport_claude_config_encode(bool enabled, uint8_t out[PASSPORT_CLAUDE_CONFIG_BYTES]);
bool passport_claude_config_decode(const uint8_t *data, size_t length, bool *enabled);
