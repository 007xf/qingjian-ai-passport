#include "passport_provider_model.h"
#include <stddef.h>
#include <string.h>

static const char *const providers[] = {"codex", "cursor", "gemini"};
static const char *const states[] = {"unknown", "working", "idle", "waiting", "error"};
static const char *const kinds[] = {"none", "requests", "tokens"};
static const char *const statuses[] = {"ready", "no_records", "partial", "unavailable"};
static const char *const sources[] = {"none", "hooks", "cursor_code_tracking", "gemini_cli", "codex_local"};

#define ENUM_ACCESSORS(NAME, TYPE, TABLE) \
    const char *passport_provider_##NAME##_name(TYPE value) { \
        return (unsigned)value < sizeof(TABLE) / sizeof(TABLE[0]) ? TABLE[value] : NULL; \
    } \
    bool passport_provider_parse_##NAME(const char *text, TYPE *out) { \
        if (!text || !out) return false; \
        for (unsigned i = 0; i < sizeof(TABLE) / sizeof(TABLE[0]); ++i) \
            if (!strcmp(text, TABLE[i])) { *out = (TYPE)i; return true; } \
        return false; \
    }

ENUM_ACCESSORS(state, passport_provider_state_t, states)
ENUM_ACCESSORS(metric_kind, passport_provider_metric_kind_t, kinds)
ENUM_ACCESSORS(metric_status, passport_provider_metric_status_t, statuses)
ENUM_ACCESSORS(source, passport_provider_source_t, sources)

const char *passport_provider_name(passport_provider_id_t value) {
    return (unsigned)value < PASSPORT_PROVIDER_COUNT ? providers[value] : NULL;
}
bool passport_provider_parse_name(const char *text, passport_provider_id_t *out) {
    if (!text || !out) return false;
    for (unsigned i = 0; i < PASSPORT_PROVIDER_COUNT; ++i)
        if (!strcmp(text, providers[i])) { *out = (passport_provider_id_t)i; return true; }
    return false;
}

void passport_provider_snapshot_init(passport_provider_snapshot_t *snapshot, passport_provider_id_t provider) {
    if (!snapshot) return;
    memset(snapshot, 0, sizeof(*snapshot));
    snapshot->provider = provider;
    snapshot->metric_status = PASSPORT_PROVIDER_METRIC_UNAVAILABLE;
}

static bool time_valid(int64_t value) {
    return value == 0 || (value >= PASSPORT_PROVIDER_MIN_UTC_MS && value < PASSPORT_PROVIDER_MAX_UTC_MS);
}

bool passport_provider_snapshot_valid(const passport_provider_snapshot_t *s) {
    if (!s || !passport_provider_name(s->provider) || !passport_provider_state_name(s->state) ||
        !passport_provider_metric_kind_name(s->metric_kind) || !passport_provider_metric_status_name(s->metric_status) ||
        !passport_provider_source_name(s->source) || s->update_id > PASSPORT_PROVIDER_MAX_UINT53 ||
        s->metric_value > PASSPORT_PROVIDER_MAX_UINT53 || !memchr(s->model, 0, sizeof(s->model))) return false;
    for (unsigned i = 0; s->model[i]; ++i)
        if ((unsigned char)s->model[i] < 32 || (unsigned char)s->model[i] > 126) return false;
    if (!time_valid(s->state_at_ms) || !time_valid(s->state_until_ms) ||
        !time_valid(s->metric_at_ms) || !time_valid(s->last_activity_ms)) return false;
    if (s->state_at_ms == 0) {
        if (s->state_until_ms != 0 || s->state != PASSPORT_PROVIDER_STATE_UNKNOWN || s->active_sessions_known) return false;
    } else if (s->state_until_ms <= s->state_at_ms || s->state_until_ms > s->state_at_ms + 180000 ||
               s->source == PASSPORT_PROVIDER_SOURCE_NONE) return false;
    if (s->active_sessions_known && s->active_sessions < 0) return false;
    if (s->metric_kind == PASSPORT_PROVIDER_METRIC_REQUESTS && s->provider != PASSPORT_PROVIDER_CURSOR) return false;
    if (s->metric_kind == PASSPORT_PROVIDER_METRIC_TOKENS && s->provider == PASSPORT_PROVIDER_CURSOR) return false;
    if (s->source == PASSPORT_PROVIDER_SOURCE_CURSOR_CODE_TRACKING && s->provider != PASSPORT_PROVIDER_CURSOR) return false;
    if (s->source == PASSPORT_PROVIDER_SOURCE_GEMINI_CLI && s->provider != PASSPORT_PROVIDER_GEMINI) return false;
    if (s->source == PASSPORT_PROVIDER_SOURCE_CODEX_LOCAL && s->provider != PASSPORT_PROVIDER_CODEX) return false;
    if (s->metric_status == PASSPORT_PROVIDER_METRIC_READY) {
        if (!s->metric_value_known || s->metric_kind == PASSPORT_PROVIDER_METRIC_NONE || s->metric_at_ms == 0 ||
            s->source == PASSPORT_PROVIDER_SOURCE_NONE) return false;
    } else if (s->metric_value_known) return false;
    if (s->metric_at_ms > s->last_activity_ms || s->state_at_ms > s->last_activity_ms) return false;
    return true;
}

bool passport_provider_state_fresh(const passport_provider_snapshot_t *s, int64_t now) {
    return passport_provider_snapshot_valid(s) && s->state != PASSPORT_PROVIDER_STATE_UNKNOWN &&
           s->state_at_ms > 0 && now >= s->state_at_ms && now < s->state_until_ms;
}

bool passport_provider_metric_available(const passport_provider_snapshot_t *s, int64_t now) {
    return passport_provider_snapshot_valid(s) && s->metric_status == PASSPORT_PROVIDER_METRIC_READY &&
           s->metric_value_known && now >= s->metric_at_ms && now < PASSPORT_PROVIDER_MAX_UTC_MS;
}
