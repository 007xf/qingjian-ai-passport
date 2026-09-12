#pragma once
#include <stdbool.h>
#include <stdint.h>

#define PASSPORT_PROVIDER_COUNT 3
#define PASSPORT_PROVIDER_MAX_UINT53 UINT64_C(9007199254740991)
#define PASSPORT_PROVIDER_MIN_UTC_MS INT64_C(1704067200000)
#define PASSPORT_PROVIDER_MAX_UTC_MS INT64_C(4102444800000)

typedef enum {
    PASSPORT_PROVIDER_CODEX = 0, PASSPORT_PROVIDER_CURSOR = 1,
    PASSPORT_PROVIDER_GEMINI = 2
} passport_provider_id_t;
typedef enum {
    PASSPORT_PROVIDER_STATE_UNKNOWN, PASSPORT_PROVIDER_STATE_WORKING,
    PASSPORT_PROVIDER_STATE_IDLE, PASSPORT_PROVIDER_STATE_WAITING,
    PASSPORT_PROVIDER_STATE_ERROR
} passport_provider_state_t;
typedef enum {
    PASSPORT_PROVIDER_METRIC_NONE, PASSPORT_PROVIDER_METRIC_REQUESTS,
    PASSPORT_PROVIDER_METRIC_TOKENS
} passport_provider_metric_kind_t;
typedef enum {
    PASSPORT_PROVIDER_METRIC_READY, PASSPORT_PROVIDER_METRIC_NO_RECORDS,
    PASSPORT_PROVIDER_METRIC_PARTIAL, PASSPORT_PROVIDER_METRIC_UNAVAILABLE
} passport_provider_metric_status_t;
typedef enum {
    PASSPORT_PROVIDER_SOURCE_NONE, PASSPORT_PROVIDER_SOURCE_HOOKS,
    PASSPORT_PROVIDER_SOURCE_CURSOR_CODE_TRACKING,
    PASSPORT_PROVIDER_SOURCE_GEMINI_CLI, PASSPORT_PROVIDER_SOURCE_CODEX_LOCAL
} passport_provider_source_t;

/* Final wire object: 13 keys. Null active_sessions / metric_value use their
 * known flags; unknown times use 0, unknown model uses "". Source describes
 * the counter, while state timestamps independently describe hook evidence. */
typedef struct {
    passport_provider_id_t provider;
    uint64_t update_id;
    passport_provider_state_t state;
    int64_t state_at_ms;
    int64_t state_until_ms;
    bool active_sessions_known;
    int32_t active_sessions;
    passport_provider_metric_kind_t metric_kind;
    bool metric_value_known;
    uint64_t metric_value;
    passport_provider_metric_status_t metric_status;
    int64_t metric_at_ms;
    int64_t last_activity_ms;
    char model[33]; /* printable ASCII, at most 32 bytes plus NUL */
    passport_provider_source_t source;
} passport_provider_snapshot_t;

void passport_provider_snapshot_init(passport_provider_snapshot_t *snapshot, passport_provider_id_t provider);
bool passport_provider_snapshot_valid(const passport_provider_snapshot_t *snapshot);
bool passport_provider_state_fresh(const passport_provider_snapshot_t *snapshot, int64_t now_utc_ms);
/* Cumulative counters do not expire when hooks expire. Their observation time
 * stays the original event time and is displayed; future/invalid data is hidden. */
bool passport_provider_metric_available(const passport_provider_snapshot_t *snapshot, int64_t now_utc_ms);

const char *passport_provider_name(passport_provider_id_t value);
const char *passport_provider_state_name(passport_provider_state_t value);
const char *passport_provider_metric_kind_name(passport_provider_metric_kind_t value);
const char *passport_provider_metric_status_name(passport_provider_metric_status_t value);
const char *passport_provider_source_name(passport_provider_source_t value);
bool passport_provider_parse_name(const char *text, passport_provider_id_t *out);
bool passport_provider_parse_state(const char *text, passport_provider_state_t *out);
bool passport_provider_parse_metric_kind(const char *text, passport_provider_metric_kind_t *out);
bool passport_provider_parse_metric_status(const char *text, passport_provider_metric_status_t *out);
bool passport_provider_parse_source(const char *text, passport_provider_source_t *out);
