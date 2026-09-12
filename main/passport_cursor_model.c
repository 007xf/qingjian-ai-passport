#include "passport_cursor_model.h"
#include <math.h>
#include <string.h>

bool passport_cursor_snapshot_valid(const passport_cursor_snapshot_t *s) {
    if (!s || !memchr(s->source, 0, sizeof(s->source)) || strcmp(s->source, "cursor_app_api") ||
        !memchr(s->plan, 0, sizeof(s->plan))) return false;
    for (const unsigned char *p = (const unsigned char *)s->plan; *p; ++p)
        if (*p < 32 || *p > 126) return false;
    if (s->observed_utc_ms == 0 && s->expires_utc_ms == 0) {
        if (s->used_known[0] || s->used_known[1] || s->reset_at_ms != 0) return false;
    } else if (s->observed_utc_ms < INT64_C(1704067200000) ||
        s->observed_utc_ms >= INT64_C(4102444800000) ||
        s->expires_utc_ms <= s->observed_utc_ms ||
        s->expires_utc_ms > s->observed_utc_ms + 300000) return false;
    if (s->reset_at_ms < 0 || s->reset_at_ms > INT64_C(4102444800000)) return false;
    for (unsigned i = 0; i < 2; ++i)
        if (s->used_known[i] && (!isfinite(s->used_percent[i]) ||
            s->used_percent[i] < 0 || s->used_percent[i] > 100)) return false;
    return true;
}

bool passport_cursor_snapshot_fresh(const passport_cursor_snapshot_t *s, int64_t now) {
    return passport_cursor_snapshot_valid(s) && s->observed_utc_ms > 0 &&
        (s->used_known[0] || s->used_known[1]) && now >= s->observed_utc_ms &&
        now < s->expires_utc_ms && (s->reset_at_ms == 0 || now < s->reset_at_ms);
}
