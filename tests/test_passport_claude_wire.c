#include "passport_claude_wire.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

static const char *payload = "{\"source\":\"claude_code\",\"observed_utc_ms\":1789201000000,"
    "\"expires_utc_ms\":1789201180000,\"today_tokens\":0,\"lifetime_tokens\":100,\"tokens_complete\":true,"
    "\"quota_observed_utc_ms\":1789201000000,\"quota_expires_utc_ms\":1789201300000,"
    "\"five_hour_used_percent\":12.5,\"seven_day_used_percent\":100,"
    "\"five_hour_reset_ms\":1789201060000,\"seven_day_reset_ms\":0}";

static bool parse(const char *text, passport_claude_snapshot_t *s, bool *has_enabled, bool *enabled) {
    return passport_claude_wire_parse((const unsigned char *)text, strlen(text), s, has_enabled, enabled);
}

static void replace(char out[1024], const char *old, const char *replacement) {
    const char *start = strstr(payload, old);
    assert(start);
    size_t prefix=(size_t)(start-payload);
    assert(prefix+strlen(replacement)+strlen(start+strlen(old)) < 1024);
    memcpy(out,payload,prefix);
    strcpy(out+prefix,replacement);
    strcat(out,start+strlen(old));
}

static void reject(const char *text) {
    passport_claude_snapshot_t s={.source="claude_code"};
    unsigned char saved[sizeof(s)];
    memcpy(saved,&s,sizeof(s));
    bool has_enabled=true, enabled=true;
    assert(!parse(text,&s,&has_enabled,&enabled));
    assert(!memcmp(saved,&s,sizeof(s)) && has_enabled && enabled); /* No partial output on failure. */
}

int main(void) {
    passport_claude_snapshot_t s;
    bool has_enabled, enabled;
    assert(strlen(payload) <= 512);
    assert(parse(payload,&s,&has_enabled,&enabled));
    assert(!has_enabled && !enabled);
    assert(s.today_known && s.today_tokens == 0 && s.lifetime_tokens == 100);
    assert(s.quota_used_percent[0] == 12.5f && s.quota_used_percent[1] == 100);
    char changed[1024];
    replace(changed,"\"lifetime_tokens\":100","\"lifetime_tokens\":9007199254740991");
    assert(parse(changed,&s,&has_enabled,&enabled) && s.lifetime_tokens == PASSPORT_CLAUDE_MAX_TOKENS);
    replace(changed,"}",",\"enabled\":true}");
    assert(parse(changed,&s,&has_enabled,&enabled) && has_enabled && enabled);
    replace(changed,"}",",\"enabled\":false}");
    assert(parse(changed,&s,&has_enabled,&enabled) && has_enabled && !enabled);
    replace(changed,"\"seven_day_reset_ms\":0","\"seven_day_reset_ms\":null");
    assert(parse(changed,&s,&has_enabled,&enabled) && s.quota_reset_ms[1] == 0);
    replace(changed,"\"five_hour_used_percent\":12.5","\"five_hour_used_percent\":1000e-2");
    assert(parse(changed,&s,&has_enabled,&enabled) && s.quota_used_percent[0] == 10);
    replace(changed,"\"five_hour_used_percent\":12.5","\"five_hour_used_percent\":-0.0");
    assert(parse(changed,&s,&has_enabled,&enabled) && s.quota_used_percent[0] == 0);
    replace(changed,"\"seven_day_used_percent\":100","\"seven_day_used_percent\":null");
    assert(parse(changed,&s,&has_enabled,&enabled) && !s.quota_known[1]);
    replace(changed,"\"expires_utc_ms\":1789201180000","\"expires_utc_ms\":1789201000000");
    assert(parse(changed,&s,&has_enabled,&enabled) && !passport_claude_tokens_fresh(&s,1789201000000));

    const char *invalid_numbers[] = {"-1","+1","01","1.5","1e1","9007199254740992","18446744073709551616"};
    for (unsigned i=0; i<sizeof(invalid_numbers)/sizeof(*invalid_numbers); ++i) {
        char field[128];
        snprintf(field,sizeof(field),"\"today_tokens\":%s",invalid_numbers[i]);
        replace(changed,"\"today_tokens\":0",field);
        reject(changed);
    }
    const char *invalid_percent[] = {"-0.1","100.00000001","NaN","Infinity","01","1.",".5","1e","1e+","1e999"};
    for (unsigned i=0; i<sizeof(invalid_percent)/sizeof(*invalid_percent); ++i) {
        char field[128];
        snprintf(field,sizeof(field),"\"five_hour_used_percent\":%s",invalid_percent[i]);
        replace(changed,"\"five_hour_used_percent\":12.5",field);
        reject(changed);
    }
    replace(changed,"claude_code","other"); reject(changed);
    replace(changed,"\"tokens_complete\":true","\"tokens_complete\":0"); reject(changed);
    replace(changed,"\"today_tokens\":0","\"today_tokens\":null"); reject(changed); /* complete cannot be true. */
    replace(changed,"\"today_tokens\":0","\"today_tokens\":101"); reject(changed);
    replace(changed,"}",",\"source\":\"claude_code\"}"); reject(changed);
    replace(changed,"}",",\"extra\":0}"); reject(changed);
    replace(changed,"}",",\"enabled\":null}"); reject(changed);
    replace(changed,"\"source\"","\"\\u0073ource\""); reject(changed);
    replace(changed,"\"today_tokens\":0","\"today_tokens\":{}"); reject(changed);
    replace(changed,"\"seven_day_reset_ms\":0","\"seven_day_reset_ms\":0,"); reject(changed);
    replace(changed,"}","} extra"); reject(changed);
    reject("{}"); reject("[]");
    const char *unknown="{\"source\":\"claude_code\",\"observed_utc_ms\":0,\"expires_utc_ms\":0,"
        "\"today_tokens\":null,\"lifetime_tokens\":null,\"tokens_complete\":false,"
        "\"quota_observed_utc_ms\":0,\"quota_expires_utc_ms\":0,\"five_hour_used_percent\":null,"
        "\"seven_day_used_percent\":null,\"five_hour_reset_ms\":0,\"seven_day_reset_ms\":0}";
    assert(parse(unknown,&s,&has_enabled,&enabled) && !s.today_known && !s.quota_known[0]);
    unsigned char padded[513];
    memset(padded,' ',sizeof(padded));
    memcpy(padded,payload,strlen(payload));
    assert(passport_claude_wire_parse(padded,512,&s,&has_enabled,&enabled));
    assert(!passport_claude_wire_parse(padded,513,&s,&has_enabled,&enabled));
    padded[10]=0;
    assert(!passport_claude_wire_parse(padded,512,&s,&has_enabled,&enabled));
    assert(!passport_claude_wire_parse(NULL,0,&s,&has_enabled,&enabled));
    assert(!passport_claude_wire_parse((const unsigned char *)payload,strlen(payload),NULL,&has_enabled,&enabled));
    puts("Claude wire: PASS (bounded flat JSON, exact schema, null/zero, optional preference, numeric limits, malformed and duplicate rejection)");
}
