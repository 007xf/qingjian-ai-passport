#include "passport_cursor_model.h"
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

int main(void) {
    const int64_t now = INT64_C(1789201000000);
    const passport_cursor_snapshot_t baseline = {
        .observed_utc_ms=now, .expires_utc_ms=now+300000, .reset_at_ms=now+86400000,
        .source="cursor_app_api", .plan="Pro+", .used_known={true,true}, .used_percent={97,73}
    };
    passport_cursor_snapshot_t s = baseline;
    assert(passport_cursor_snapshot_valid(&s));
    assert(passport_cursor_snapshot_fresh(&s, now));
    assert(!passport_cursor_snapshot_fresh(&s, now-1));
    assert(passport_cursor_snapshot_fresh(&s, now+299999));
    assert(!passport_cursor_snapshot_fresh(&s, now+300000));
    s.reset_at_ms=now+10000;
    assert(passport_cursor_snapshot_fresh(&s, now+9999));
    assert(!passport_cursor_snapshot_fresh(&s, now+10000));
    s=baseline; s.used_percent[0]=0; s.used_percent[1]=100;
    assert(passport_cursor_snapshot_fresh(&s, now));
    s.used_known[0]=false;
    assert(passport_cursor_snapshot_fresh(&s, now));
    s.used_known[1]=false;
    assert(passport_cursor_snapshot_valid(&s));
    assert(!passport_cursor_snapshot_fresh(&s, now));
    const float invalid[] = {NAN, INFINITY, -0.1f, 100.01f};
    for (unsigned i=0; i<sizeof(invalid)/sizeof(invalid[0]); ++i) {
        s=baseline; s.used_percent[1]=invalid[i];
        assert(!passport_cursor_snapshot_valid(&s));
    }
    s=baseline; s.expires_utc_ms=now+300001;
    assert(!passport_cursor_snapshot_valid(&s));
    s.expires_utc_ms=now;
    assert(!passport_cursor_snapshot_valid(&s));
    s=baseline; s.observed_utc_ms=1;
    assert(!passport_cursor_snapshot_valid(&s));
    s=baseline; s.reset_at_ms=-1;
    assert(!passport_cursor_snapshot_valid(&s));
    s=baseline; strcpy(s.source,"local_log");
    assert(!passport_cursor_snapshot_valid(&s));
    s=baseline; memset(s.plan,'x',sizeof(s.plan));
    assert(!passport_cursor_snapshot_valid(&s));
    s=baseline; s.plan[0]='\n';
    assert(!passport_cursor_snapshot_valid(&s));
    s=(passport_cursor_snapshot_t){.source="cursor_app_api"};
    assert(passport_cursor_snapshot_valid(&s));
    assert(!passport_cursor_snapshot_fresh(&s, now));
    s.used_known[0]=true;
    assert(!passport_cursor_snapshot_valid(&s));
    s.used_known[0]=false; s.reset_at_ms=now;
    assert(!passport_cursor_snapshot_valid(&s));
    assert(!passport_cursor_snapshot_valid(NULL));
    puts("Cursor quota model: PASS (two buckets, unknown, expiry, reset, invalid input)");
}
