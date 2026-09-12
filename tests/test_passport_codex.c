#include "passport_codex_model.h"
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

int main(void) {
    const int64_t now = INT64_C(1789201000000);
    passport_codex_snapshot_t s = {.observed_utc_ms=now, .expires_utc_ms=now+180000,
        .source="official_app_server", .window={{.used_known=true, .used_percent=55,
        .duration_min=10080, .reset_s=1789805440}}};
    assert(passport_codex_snapshot_valid(&s));
    assert(passport_codex_snapshot_fresh(&s, now));
    assert(!passport_codex_snapshot_fresh(&s, now-1));
    assert(!passport_codex_snapshot_fresh(&s, now+180000));
    s.window[0].reset_s=now/1000+30;
    assert(passport_codex_snapshot_fresh(&s, now+29999));
    assert(!passport_codex_snapshot_fresh(&s, now+30000));
    s.window[0].used_percent=NAN;
    assert(!passport_codex_snapshot_valid(&s));
    s.window[0].used_percent=100.01f;
    assert(!passport_codex_snapshot_valid(&s));
    s.window[0].used_percent=0;
    assert(passport_codex_snapshot_valid(&s));
    s.expires_utc_ms=now+180001;
    assert(!passport_codex_snapshot_valid(&s));
    s=(passport_codex_snapshot_t){.source="local_log"};
    assert(passport_codex_snapshot_valid(&s));
    assert(!passport_codex_snapshot_fresh(&s, now));
    s.window[1].used_known=true;
    assert(!passport_codex_snapshot_valid(&s));
    memset(s.source,'x',sizeof(s.source));
    assert(!passport_codex_snapshot_valid(&s));
    assert(!passport_codex_snapshot_valid(NULL));
    puts("Codex quota model: PASS (unknown, freshness, rollback, reset, limits)");
}
