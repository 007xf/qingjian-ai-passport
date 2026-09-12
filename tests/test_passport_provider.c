#include "passport_provider_model.h"
#include <assert.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>

static const int64_t NOW = INT64_C(1789300000000);
static passport_provider_snapshot_t ready(passport_provider_id_t provider) {
    passport_provider_snapshot_t s;
    passport_provider_snapshot_init(&s, provider);
    s.update_id = 100;
    s.metric_kind = provider == PASSPORT_PROVIDER_CURSOR ? PASSPORT_PROVIDER_METRIC_REQUESTS : PASSPORT_PROVIDER_METRIC_TOKENS;
    s.metric_value_known = true;
    s.metric_value = 123;
    s.metric_status = PASSPORT_PROVIDER_METRIC_READY;
    s.metric_at_ms = NOW - 86400000;
    s.last_activity_ms = s.metric_at_ms;
    s.source = provider == PASSPORT_PROVIDER_CURSOR ? PASSPORT_PROVIDER_SOURCE_CURSOR_CODE_TRACKING :
               provider == PASSPORT_PROVIDER_GEMINI ? PASSPORT_PROVIDER_SOURCE_GEMINI_CLI : PASSPORT_PROVIDER_SOURCE_CODEX_LOCAL;
    strcpy(s.model, "model-1");
    return s;
}
static void hook(passport_provider_snapshot_t *s) {
    s->state = PASSPORT_PROVIDER_STATE_WORKING;
    s->state_at_ms = NOW;
    s->state_until_ms = NOW + 180000;
    s->last_activity_ms = NOW;
    s->active_sessions_known = true;
    s->active_sessions = 2;
}

static void test_defaults_and_enums(void) {
    _Static_assert(PASSPORT_PROVIDER_CODEX == 0 && PASSPORT_PROVIDER_CURSOR == 1 && PASSPORT_PROVIDER_GEMINI == 2, "Wire ID order");
    for (int i = 0; i < PASSPORT_PROVIDER_COUNT; ++i) {
        passport_provider_snapshot_t s;
        passport_provider_snapshot_init(&s, i);
        assert(passport_provider_snapshot_valid(&s));
        assert(!passport_provider_state_fresh(&s, NOW));
        assert(!passport_provider_metric_available(&s, NOW));
        passport_provider_id_t parsed = -1;
        assert(passport_provider_parse_name(passport_provider_name(i), &parsed) && (int)parsed == i);
    }
    passport_provider_id_t id = PASSPORT_PROVIDER_CURSOR;
    assert(!passport_provider_parse_name("Cursor", &id) && id == PASSPORT_PROVIDER_CURSOR);
    assert(!passport_provider_parse_name(NULL, &id));
    assert(!passport_provider_parse_name("cursor", NULL));
    passport_provider_state_t state;
    for (int i = 0; i <= PASSPORT_PROVIDER_STATE_ERROR; ++i)
        assert(passport_provider_parse_state(passport_provider_state_name(i), &state) && (int)state == i);
    passport_provider_metric_kind_t kind;
    for (int i = 0; i <= PASSPORT_PROVIDER_METRIC_TOKENS; ++i)
        assert(passport_provider_parse_metric_kind(passport_provider_metric_kind_name(i), &kind) && (int)kind == i);
    passport_provider_metric_status_t status;
    for (int i = 0; i <= PASSPORT_PROVIDER_METRIC_UNAVAILABLE; ++i)
        assert(passport_provider_parse_metric_status(passport_provider_metric_status_name(i), &status) && (int)status == i);
    passport_provider_source_t source;
    for (int i = 0; i <= PASSPORT_PROVIDER_SOURCE_CODEX_LOCAL; ++i)
        assert(passport_provider_parse_source(passport_provider_source_name(i), &source) && (int)source == i);
    assert(!passport_provider_source_name(-1));
    assert(!passport_provider_snapshot_valid(NULL));
    passport_provider_snapshot_init(NULL, PASSPORT_PROVIDER_CODEX);
}

static void test_independent_metrics_and_hook_ttl(void) {
    passport_provider_snapshot_t s = ready(PASSPORT_PROVIDER_CURSOR);
    assert(passport_provider_snapshot_valid(&s));
    assert(passport_provider_metric_available(&s, NOW));
    assert(!passport_provider_state_fresh(&s, NOW)); /* a database is not a working agent */
    hook(&s);
    assert(passport_provider_state_fresh(&s, NOW));
    assert(passport_provider_state_fresh(&s, NOW + 179999));
    assert(!passport_provider_state_fresh(&s, NOW + 180000));
    assert(!passport_provider_state_fresh(&s, NOW - 1));
    assert(passport_provider_metric_available(&s, NOW + 86400000));
    s.state = PASSPORT_PROVIDER_STATE_UNKNOWN; s.active_sessions_known = false;
    assert(passport_provider_snapshot_valid(&s)); /* collector retains expired timestamps */
    assert(!passport_provider_state_fresh(&s, NOW));
    s.state_until_ms++;
    assert(!passport_provider_snapshot_valid(&s));
}

static void test_metric_status_and_zero(void) {
    passport_provider_snapshot_t s = ready(PASSPORT_PROVIDER_GEMINI);
    s.metric_value = 0;
    assert(passport_provider_metric_available(&s, NOW)); /* a genuine observed zero */
    for (int i = PASSPORT_PROVIDER_METRIC_NO_RECORDS; i <= PASSPORT_PROVIDER_METRIC_UNAVAILABLE; ++i) {
        s.metric_status = i; s.metric_value_known = false;
        assert(passport_provider_snapshot_valid(&s));
        assert(!passport_provider_metric_available(&s, NOW));
        s.metric_value_known = true;
        assert(!passport_provider_snapshot_valid(&s));
    }
    s = ready(PASSPORT_PROVIDER_GEMINI);
    s.metric_at_ms = 0;
    assert(!passport_provider_snapshot_valid(&s));
    s = ready(PASSPORT_PROVIDER_CURSOR);
    s.metric_kind = PASSPORT_PROVIDER_METRIC_TOKENS;
    assert(!passport_provider_snapshot_valid(&s));
    s = ready(PASSPORT_PROVIDER_GEMINI);
    s.metric_kind = PASSPORT_PROVIDER_METRIC_REQUESTS;
    assert(!passport_provider_snapshot_valid(&s));
    s = ready(PASSPORT_PROVIDER_CODEX);
    s.source = PASSPORT_PROVIDER_SOURCE_GEMINI_CLI;
    assert(!passport_provider_snapshot_valid(&s));
}

static void test_bounds_ascii_and_future_observations(void) {
    passport_provider_snapshot_t s = ready(PASSPORT_PROVIDER_CODEX);
    s.update_id = PASSPORT_PROVIDER_MAX_UINT53; s.metric_value = PASSPORT_PROVIDER_MAX_UINT53;
    assert(passport_provider_snapshot_valid(&s));
    s.update_id++; assert(!passport_provider_snapshot_valid(&s));
    s = ready(PASSPORT_PROVIDER_CODEX); s.metric_value = PASSPORT_PROVIDER_MAX_UINT53 + 1;
    assert(!passport_provider_snapshot_valid(&s));
    s = ready(PASSPORT_PROVIDER_CODEX); memset(s.model, 'a', 32); s.model[32] = 0;
    assert(passport_provider_snapshot_valid(&s));
    s.model[32] = 'a'; assert(!passport_provider_snapshot_valid(&s));
    s = ready(PASSPORT_PROVIDER_CODEX); s.model[0] = '\n'; assert(!passport_provider_snapshot_valid(&s));
    s = ready(PASSPORT_PROVIDER_CODEX); s.model[0] = (char)0x80; assert(!passport_provider_snapshot_valid(&s));
    s = ready(PASSPORT_PROVIDER_CODEX); hook(&s); s.active_sessions = INT32_MAX;
    assert(passport_provider_snapshot_valid(&s));
    s.active_sessions = -1; assert(!passport_provider_snapshot_valid(&s));
    s = ready(PASSPORT_PROVIDER_CODEX); s.metric_at_ms = NOW + 1; s.last_activity_ms = NOW + 1;
    assert(passport_provider_snapshot_valid(&s));
    assert(!passport_provider_metric_available(&s, NOW));
    s.last_activity_ms = NOW; assert(!passport_provider_snapshot_valid(&s));
    s = ready(PASSPORT_PROVIDER_CODEX); s.state = PASSPORT_PROVIDER_STATE_WORKING;
    assert(!passport_provider_snapshot_valid(&s)); /* no event time, no state */
    s = ready(PASSPORT_PROVIDER_CODEX); s.metric_at_ms = PASSPORT_PROVIDER_MIN_UTC_MS - 1;
    assert(!passport_provider_snapshot_valid(&s));
    s = ready(PASSPORT_PROVIDER_CODEX); s.metric_at_ms = PASSPORT_PROVIDER_MAX_UTC_MS;
    assert(!passport_provider_snapshot_valid(&s));
}

int main(void) {
    test_defaults_and_enums();
    test_independent_metrics_and_hook_ttl();
    test_metric_status_and_zero();
    test_bounds_ascii_and_future_observations();
    puts("Provider model: PASS (wire enums, 13-key semantics, independent hook TTL/counters, null/zero/status, bounds, ASCII, source types)");
    return 0;
}
