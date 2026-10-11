#include "passport_claude_model.h"
#include <math.h>
#include <string.h>

static bool observation_valid(int64_t observed, int64_t expires, int64_t ttl) {
    if (observed == 0 && expires == 0) return true;
    return observed >= INT64_C(1704067200000) && observed < INT64_C(4102444800000) &&
           expires >= observed && expires <= observed + ttl;
}

bool passport_claude_snapshot_valid(const passport_claude_snapshot_t *s) {
    if (!s || !memchr(s->source, 0, sizeof(s->source)) || strcmp(s->source, "claude_code") ||
        !observation_valid(s->observed_utc_ms, s->expires_utc_ms, 180000) ||
        !observation_valid(s->quota_observed_utc_ms, s->quota_expires_utc_ms, 300000)) return false;
    if ((s->today_known || s->lifetime_known || s->tokens_complete) && !s->observed_utc_ms) return false;
    if ((s->today_known && s->today_tokens > PASSPORT_CLAUDE_MAX_TOKENS) ||
        (s->lifetime_known && s->lifetime_tokens > PASSPORT_CLAUDE_MAX_TOKENS) ||
        (s->tokens_complete && (!s->today_known || !s->lifetime_known)) ||
        (s->today_known && s->lifetime_known && s->today_tokens > s->lifetime_tokens)) return false;
    for (unsigned i = 0; i < 2; ++i) {
        if (s->quota_known[i] && (!s->quota_observed_utc_ms || !isfinite(s->quota_used_percent[i]) ||
            s->quota_used_percent[i] < 0 || s->quota_used_percent[i] > 100)) return false;
        if (s->quota_reset_ms[i] < 0 || s->quota_reset_ms[i] > INT64_C(4102444800000) ||
            (s->quota_reset_ms[i] && !s->quota_observed_utc_ms)) return false;
    }
    return true;
}

bool passport_claude_tokens_fresh(const passport_claude_snapshot_t *s, int64_t now) {
    return passport_claude_snapshot_valid(s) && s->observed_utc_ms > 0 &&
        (s->today_known || s->lifetime_known) && now >= s->observed_utc_ms && now < s->expires_utc_ms;
}

bool passport_claude_quota_fresh(const passport_claude_snapshot_t *s, unsigned index, int64_t now) {
    return index < 2 && passport_claude_snapshot_valid(s) && s->quota_known[index] &&
        s->quota_observed_utc_ms > 0 && now >= s->quota_observed_utc_ms &&
        now < s->quota_expires_utc_ms && (!s->quota_reset_ms[index] || now < s->quota_reset_ms[index]);
}

bool passport_claude_tokens_display_known(const passport_claude_snapshot_t *s, unsigned index) {
    return index < 2 && passport_claude_snapshot_valid(s) && s->observed_utc_ms > 0 &&
        (index == 0 ? s->today_known : s->lifetime_known);
}

bool passport_claude_quota_display_known(const passport_claude_snapshot_t *s, unsigned index) {
    return index < 2 && passport_claude_snapshot_valid(s) && s->quota_observed_utc_ms > 0 && s->quota_known[index];
}

void passport_claude_state_init(passport_claude_state_t *state) {
    if (!state) return;
    memset(state, 0, sizeof(*state));
    strcpy(state->snapshot.source, "claude_code");
}

bool passport_claude_state_apply(passport_claude_state_t *state, const passport_claude_snapshot_t *incoming) {
    if (!state || !passport_claude_snapshot_valid(incoming)) return false;
    if (!passport_claude_snapshot_valid(&state->snapshot)) passport_claude_state_init(state);
    passport_claude_snapshot_t *saved = &state->snapshot;
    if (incoming->observed_utc_ms == 0 || incoming->observed_utc_ms > state->token_watermark_ms ||
        (!state->tokens_cleared && incoming->observed_utc_ms == state->token_watermark_ms)) {
        int64_t expires = incoming->expires_utc_ms;
        if (incoming->observed_utc_ms > 0 && incoming->observed_utc_ms == saved->observed_utc_ms &&
            saved->expires_utc_ms < expires) expires = saved->expires_utc_ms;
        saved->observed_utc_ms = incoming->observed_utc_ms;
        saved->expires_utc_ms = expires;
        saved->today_known = incoming->today_known;
        saved->lifetime_known = incoming->lifetime_known;
        saved->today_tokens = incoming->today_tokens;
        saved->lifetime_tokens = incoming->lifetime_tokens;
        saved->tokens_complete = incoming->tokens_complete;
        state->tokens_cleared = incoming->observed_utc_ms == 0;
        if (incoming->observed_utc_ms > state->token_watermark_ms)
            state->token_watermark_ms = incoming->observed_utc_ms;
    }
    if (incoming->quota_observed_utc_ms == 0 || incoming->quota_observed_utc_ms > state->quota_watermark_ms ||
        (!state->quota_cleared && incoming->quota_observed_utc_ms == state->quota_watermark_ms)) {
        int64_t expires = incoming->quota_expires_utc_ms;
        if (incoming->quota_observed_utc_ms > 0 && incoming->quota_observed_utc_ms == saved->quota_observed_utc_ms &&
            saved->quota_expires_utc_ms < expires) expires = saved->quota_expires_utc_ms;
        saved->quota_observed_utc_ms = incoming->quota_observed_utc_ms;
        saved->quota_expires_utc_ms = expires;
        memcpy(saved->quota_known, incoming->quota_known, sizeof(saved->quota_known));
        memcpy(saved->quota_used_percent, incoming->quota_used_percent, sizeof(saved->quota_used_percent));
        memcpy(saved->quota_reset_ms, incoming->quota_reset_ms, sizeof(saved->quota_reset_ms));
        state->quota_cleared = incoming->quota_observed_utc_ms == 0;
        if (incoming->quota_observed_utc_ms > state->quota_watermark_ms)
            state->quota_watermark_ms = incoming->quota_observed_utc_ms;
    }
    return true;
}

void passport_claude_config_encode(bool enabled, uint8_t out[PASSPORT_CLAUDE_CONFIG_BYTES]) {
    if (!out) return;
    memcpy(out, "APC1", 4);
    out[4] = enabled ? 1 : 0;
}

bool passport_claude_config_decode(const uint8_t *data, size_t length, bool *enabled) {
    if (!data || !enabled || length != PASSPORT_CLAUDE_CONFIG_BYTES || memcmp(data, "APC1", 4) || data[4] > 1)
        return false;
    *enabled = data[4] != 0;
    return true;
}
