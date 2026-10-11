#include "passport_claude_model.h"
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

int main(void) {
    const int64_t now = INT64_C(1789201000000);
    const passport_claude_snapshot_t baseline = {
        .source="claude_code", .observed_utc_ms=now, .expires_utc_ms=now+180000,
        .today_known=true, .lifetime_known=true, .today_tokens=0, .lifetime_tokens=1234567,
        .tokens_complete=true, .quota_observed_utc_ms=now-1000, .quota_expires_utc_ms=now+299000,
        .quota_known={true,true}, .quota_used_percent={0,100}, .quota_reset_ms={now+30000,now+86400000}
    };
    passport_claude_snapshot_t s = baseline;
    assert(passport_claude_snapshot_valid(&s));
    assert(passport_claude_tokens_fresh(&s, now));
    assert(passport_claude_tokens_display_known(&s, 0)); /* Real zero, not unknown. */
    assert(!passport_claude_tokens_display_known(&s, 2));
    assert(!passport_claude_tokens_fresh(&s, now-1));
    assert(!passport_claude_tokens_fresh(&s, now+180000));
    assert(passport_claude_quota_fresh(&s, 0, now));
    assert(!passport_claude_quota_fresh(&s, 0, now+30000));
    assert(passport_claude_quota_fresh(&s, 1, now+30000)); /* Reset of 5h does not expire 7d. */
    assert(passport_claude_quota_display_known(&s, 0)); /* Retain the last measured quota. */
    assert(!passport_claude_quota_fresh(&s, 1, now+299000));
    assert(!passport_claude_quota_fresh(&s, 2, now));
    assert(!passport_claude_quota_fresh(&s, 1, now-1001));
    s.expires_utc_ms=now;
    assert(passport_claude_snapshot_valid(&s));
    assert(!passport_claude_tokens_fresh(&s, now));
    assert(passport_claude_tokens_display_known(&s, 1));
    s=baseline; s.expires_utc_ms=now+180001;
    assert(!passport_claude_snapshot_valid(&s));
    s=baseline; s.quota_expires_utc_ms=now+299001;
    assert(!passport_claude_snapshot_valid(&s));
    s=baseline; s.expires_utc_ms=now-1;
    assert(!passport_claude_snapshot_valid(&s));
    s=baseline; s.tokens_complete=false;
    assert(passport_claude_tokens_fresh(&s, now)); /* Partial known totals are labelled separately. */
    s.today_known=false;
    assert(!passport_claude_tokens_display_known(&s, 0));
    assert(passport_claude_tokens_fresh(&s, now));
    s.tokens_complete=true;
    assert(!passport_claude_snapshot_valid(&s));
    s=baseline; s.today_tokens=s.lifetime_tokens+1;
    assert(!passport_claude_snapshot_valid(&s));
    s=baseline; s.lifetime_tokens=PASSPORT_CLAUDE_MAX_TOKENS;
    assert(passport_claude_snapshot_valid(&s));
    s.lifetime_tokens++;
    assert(!passport_claude_snapshot_valid(&s));
    const float invalid[] = {NAN, INFINITY, -0.1f, 100.01f};
    for (unsigned i=0; i<sizeof(invalid)/sizeof(*invalid); ++i) {
        s=baseline; s.quota_used_percent[1]=invalid[i];
        assert(!passport_claude_snapshot_valid(&s));
        assert(!passport_claude_quota_display_known(&s, 1));
    }
    s=baseline; s.quota_reset_ms[0]=-1;
    assert(!passport_claude_snapshot_valid(&s));
    s=(passport_claude_snapshot_t){.source="claude_code"};
    assert(passport_claude_snapshot_valid(&s));
    assert(!passport_claude_tokens_fresh(&s, now));
    assert(!passport_claude_tokens_display_known(&s, 0));
    assert(!passport_claude_quota_display_known(&s, 0));
    s.today_known=true;
    assert(!passport_claude_snapshot_valid(&s));
    s=(passport_claude_snapshot_t){.source="claude_code", .quota_reset_ms={now,0}};
    assert(!passport_claude_snapshot_valid(&s));
    s=baseline; memset(s.source, 'x', sizeof(s.source));
    assert(!passport_claude_snapshot_valid(&s));
    assert(!passport_claude_snapshot_valid(NULL));

    /* Identity loss clears both sources immediately. Older frames cannot
     * resurrect the previous profile's observations, even inside their TTL. */
    passport_claude_state_t state;
    passport_claude_state_init(&state);
    assert(passport_claude_state_apply(&state,&baseline));
    assert(passport_claude_quota_fresh(&state.snapshot,0,now));
    const passport_claude_snapshot_t unknown={.source="claude_code"};
    assert(passport_claude_state_apply(&state,&unknown));
    assert(state.snapshot.observed_utc_ms == 0 && state.snapshot.quota_observed_utc_ms == 0);
    assert(!passport_claude_tokens_display_known(&state.snapshot,1));
    assert(!passport_claude_quota_display_known(&state.snapshot,0));
    assert(!passport_claude_quota_fresh(&state.snapshot,0,now));
    assert(passport_claude_state_apply(&state,&baseline));
    assert(state.snapshot.observed_utc_ms == 0 && state.snapshot.quota_observed_utc_ms == 0);
    passport_claude_snapshot_t latest=baseline;
    latest.observed_utc_ms+=1000; latest.expires_utc_ms+=1000;
    latest.quota_observed_utc_ms+=1000; latest.quota_expires_utc_ms+=1000;
    assert(passport_claude_state_apply(&state,&latest));
    assert(passport_claude_tokens_fresh(&state.snapshot,now+1000));
    assert(passport_claude_quota_fresh(&state.snapshot,0,now+1000));
    assert(passport_claude_state_apply(&state,&baseline));
    assert(state.snapshot.observed_utc_ms == now+1000);
    assert(state.snapshot.quota_observed_utc_ms == now);
    latest.expires_utc_ms=latest.observed_utc_ms;
    latest.quota_expires_utc_ms=latest.quota_observed_utc_ms;
    assert(passport_claude_state_apply(&state,&latest));
    assert(!passport_claude_tokens_fresh(&state.snapshot,now+1000));
    assert(!passport_claude_quota_fresh(&state.snapshot,0,now+1000));
    assert(passport_claude_tokens_display_known(&state.snapshot,1));
    assert(passport_claude_quota_display_known(&state.snapshot,0));
    latest.expires_utc_ms+=180000; latest.quota_expires_utc_ms+=300000;
    assert(passport_claude_state_apply(&state,&latest)); /* Old fresh frame cannot revive Last. */
    assert(!passport_claude_tokens_fresh(&state.snapshot,now+1000));
    assert(!passport_claude_quota_fresh(&state.snapshot,0,now+1000));
    latest.observed_utc_ms+=1000; latest.expires_utc_ms+=1000;
    latest.quota_expires_utc_ms=latest.quota_observed_utc_ms;
    assert(passport_claude_state_apply(&state,&latest));
    assert(passport_claude_tokens_fresh(&state.snapshot,now+2000));
    assert(!passport_claude_quota_fresh(&state.snapshot,0,now+2000));
    latest.quota_observed_utc_ms=latest.quota_expires_utc_ms=0;
    memset(latest.quota_known,0,sizeof(latest.quota_known));
    memset(latest.quota_reset_ms,0,sizeof(latest.quota_reset_ms));
    assert(passport_claude_state_apply(&state,&latest));
    assert(passport_claude_tokens_fresh(&state.snapshot,now+2000));
    assert(!passport_claude_quota_display_known(&state.snapshot,0));
    assert(passport_claude_state_apply(&state,&baseline));
    assert(state.snapshot.quota_observed_utc_ms == 0); /* Only quota was cleared. */
    latest=baseline;
    latest.quota_observed_utc_ms=now+3000; latest.quota_expires_utc_ms=now+303000;
    assert(passport_claude_state_apply(&state,&latest));
    assert(state.snapshot.observed_utc_ms == now+2000); /* Older token data was ignored independently. */
    assert(passport_claude_quota_fresh(&state.snapshot,0,now+3000));
    assert(!passport_claude_state_apply(NULL,&baseline));
    assert(!passport_claude_state_apply(&state,NULL));

    uint8_t config[PASSPORT_CLAUDE_CONFIG_BYTES];
    bool enabled=false;
    passport_claude_config_encode(true, config);
    assert(!memcmp(config,"APC1",4) && config[4] == 1);
    assert(passport_claude_config_decode(config,sizeof(config),&enabled) && enabled);
    passport_claude_config_encode(false, config);
    assert(passport_claude_config_decode(config,sizeof(config),&enabled) && !enabled);
    config[4]=2;
    assert(!passport_claude_config_decode(config,sizeof(config),&enabled) && !enabled);
    config[4]=0; config[0]='X';
    assert(!passport_claude_config_decode(config,sizeof(config),&enabled));
    assert(!passport_claude_config_decode(NULL,0,&enabled));
    assert(!passport_claude_config_decode(config,sizeof(config)-1,&enabled));
    assert(!passport_claude_config_decode(config,sizeof(config),NULL));
    puts("Claude model: PASS (independent clocks, zero/unknown, TTL/reset, partial/stale, identity clearing, delayed frames, separate preferences)");
}
