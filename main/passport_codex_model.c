#include "passport_codex_model.h"
#include <math.h>
#include <string.h>

bool passport_codex_snapshot_valid(const passport_codex_snapshot_t *s) {
    if (!s || !memchr(s->source,0,sizeof(s->source)) ||
        (strcmp(s->source,"official_app_server") && strcmp(s->source,"local_log"))) return false;
    if (s->observed_utc_ms == 0 && s->expires_utc_ms == 0) {
        if (s->window[0].used_known || s->window[1].used_known) return false;
    } else if (s->observed_utc_ms < INT64_C(1704067200000) ||
        s->observed_utc_ms >= INT64_C(4102444800000) ||
        s->expires_utc_ms < s->observed_utc_ms ||
        s->expires_utc_ms > s->observed_utc_ms + 180000) return false;
    for (unsigned i=0;i<2;i++) {
        const passport_quota_window_t *w=&s->window[i];
        if (w->used_known && (!isfinite(w->used_percent) || w->used_percent<0 || w->used_percent>100)) return false;
        if (w->duration_min<0 || w->duration_min>5256000 || w->reset_s<0 || w->reset_s>INT64_C(4102444800)) return false;
    }
    return true;
}

bool passport_codex_snapshot_fresh(const passport_codex_snapshot_t *s, int64_t now) {
    if (!passport_codex_snapshot_valid(s) || s->observed_utc_ms<=0 || now<s->observed_utc_ms ||
        now>=s->expires_utc_ms || !(s->window[0].used_known || s->window[1].used_known)) return false;
    for (unsigned i=0;i<2;i++)
        if (s->window[i].used_known && s->window[i].reset_s>0 && now>=s->window[i].reset_s*1000) return false;
    return true;
}

bool passport_codex_snapshot_display_known(const passport_codex_snapshot_t *s, unsigned index) {
    return index < 2 && passport_codex_snapshot_valid(s) &&
           s->observed_utc_ms > 0 && s->window[index].used_known;
}
