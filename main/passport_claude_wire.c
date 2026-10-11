#include "passport_claude_wire.h"
#include "passport_wire.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>

typedef struct { const unsigned char *at, *end; } json_reader_t;

static void space(json_reader_t *r) {
    while (r->at < r->end && (*r->at == ' ' || *r->at == '\n' || *r->at == '\r' || *r->at == '\t')) ++r->at;
}

static bool literal(json_reader_t *r, const char *text) {
    size_t size = strlen(text);
    if ((size_t)(r->end - r->at) < size || memcmp(r->at, text, size)) return false;
    r->at += size;
    return true;
}

/* Schema names and the source identifier are fixed ASCII literals. Reject
 * escapes instead of accepting alias spellings that can hide duplicate keys. */
static bool string(json_reader_t *r, char *out, size_t capacity) {
    if (r->at == r->end || *r->at++ != '"') return false;
    size_t used = 0;
    while (r->at < r->end && *r->at != '"') {
        unsigned char c = *r->at++;
        if (c < 32 || c >= 127 || c == '\\' || used + 1 >= capacity) return false;
        out[used++] = (char)c;
    }
    if (r->at == r->end) return false;
    ++r->at;
    out[used] = 0;
    return true;
}

static bool boolean(json_reader_t *r, bool *out) {
    if (literal(r, "true")) { *out = true; return true; }
    if (literal(r, "false")) { *out = false; return true; }
    return false;
}

static bool integer(json_reader_t *r, uint64_t maximum, uint64_t *out) {
    if (r->at == r->end || *r->at < '0' || *r->at > '9') return false;
    bool leading_zero = *r->at == '0';
    uint64_t value = 0;
    unsigned digits = 0;
    while (r->at < r->end && *r->at >= '0' && *r->at <= '9') {
        unsigned digit = *r->at++ - '0';
        if ((leading_zero && digits) || value > (maximum - digit) / 10) return false;
        value = value * 10 + digit;
        ++digits;
    }
    *out = value;
    return true;
}

static bool timestamp(json_reader_t *r, int64_t *out, uint64_t maximum, bool nullable) {
    if (nullable && literal(r, "null")) { *out = 0; return true; }
    uint64_t value;
    if (!integer(r, maximum, &value)) return false;
    *out = (int64_t)value;
    return true;
}

static bool tokens(json_reader_t *r, bool *known, uint64_t *out) {
    *known = !literal(r, "null");
    if (!*known) { *out = 0; return true; }
    return integer(r, PASSPORT_CLAUDE_MAX_TOKENS, out);
}

static bool percent(json_reader_t *r, bool *known, float *out) {
    *known = !literal(r, "null");
    if (!*known) { *out = 0; return true; }
    const unsigned char *start = r->at;
    if (r->at < r->end && *r->at == '-') ++r->at;
    if (r->at == r->end || *r->at < '0' || *r->at > '9') return false;
    bool leading_zero = *r->at == '0';
    unsigned digits = 0;
    while (r->at < r->end && *r->at >= '0' && *r->at <= '9') {
        if (leading_zero && digits) return false;
        ++digits;
        ++r->at;
    }
    if (r->at < r->end && *r->at == '.') {
        ++r->at;
        const unsigned char *fraction = r->at;
        while (r->at < r->end && *r->at >= '0' && *r->at <= '9') ++r->at;
        if (fraction == r->at) return false;
    }
    if (r->at < r->end && (*r->at == 'e' || *r->at == 'E')) {
        ++r->at;
        if (r->at < r->end && (*r->at == '+' || *r->at == '-')) ++r->at;
        const unsigned char *exponent = r->at;
        while (r->at < r->end && *r->at >= '0' && *r->at <= '9') ++r->at;
        if (exponent == r->at) return false;
    }
    size_t length = (size_t)(r->at - start);
    char number[64];
    if (length >= sizeof(number)) return false;
    memcpy(number, start, length);
    number[length] = 0;
    double value = strtod(number, NULL);
    if (!isfinite(value) || value < 0 || value > 100) return false;
    *out = (float)value;
    return true;
}

bool passport_claude_wire_parse(const unsigned char *json, size_t size,
                               passport_claude_snapshot_t *out, bool *enabled_present, bool *enabled) {
    if (!out || !enabled_present || !enabled || !ap_wire_flat_object(json, size)) return false;
    json_reader_t r = {.at = json, .end = json + size};
    passport_claude_snapshot_t s = {0};
    bool show = false;
    unsigned seen = 0;
    static const char *const keys[] = {"source", "observed_utc_ms", "expires_utc_ms", "today_tokens",
        "lifetime_tokens", "tokens_complete", "quota_observed_utc_ms", "quota_expires_utc_ms",
        "five_hour_used_percent", "seven_day_used_percent", "five_hour_reset_ms", "seven_day_reset_ms", "enabled"};
    space(&r);
    if (!literal(&r, "{")) return false;
    for (;;) {
        char key[32];
        space(&r);
        if (!string(&r, key, sizeof(key))) return false;
        unsigned index;
        for (index = 0; index < sizeof(keys) / sizeof(*keys) && strcmp(key, keys[index]); ++index) {}
        if (index == sizeof(keys) / sizeof(*keys) || (seen & (1u << index))) return false;
        seen |= 1u << index;
        space(&r);
        if (!literal(&r, ":")) return false;
        space(&r);
        bool ok;
        switch (index) {
        case 0: ok = string(&r, s.source, sizeof(s.source)); break;
        case 1: ok = timestamp(&r, &s.observed_utc_ms, UINT64_C(4102444800000), false); break;
        case 2: ok = timestamp(&r, &s.expires_utc_ms, UINT64_C(4102444980000), false); break;
        case 3: ok = tokens(&r, &s.today_known, &s.today_tokens); break;
        case 4: ok = tokens(&r, &s.lifetime_known, &s.lifetime_tokens); break;
        case 5: ok = boolean(&r, &s.tokens_complete); break;
        case 6: ok = timestamp(&r, &s.quota_observed_utc_ms, UINT64_C(4102444800000), false); break;
        case 7: ok = timestamp(&r, &s.quota_expires_utc_ms, UINT64_C(4102445100000), false); break;
        case 8: ok = percent(&r, &s.quota_known[0], &s.quota_used_percent[0]); break;
        case 9: ok = percent(&r, &s.quota_known[1], &s.quota_used_percent[1]); break;
        case 10: ok = timestamp(&r, &s.quota_reset_ms[0], UINT64_C(4102444800000), true); break;
        case 11: ok = timestamp(&r, &s.quota_reset_ms[1], UINT64_C(4102444800000), true); break;
        default: ok = boolean(&r, &show); break;
        }
        if (!ok) return false;
        space(&r);
        if (literal(&r, "}")) break;
        if (!literal(&r, ",")) return false;
    }
    space(&r);
    if (r.at != r.end || (seen & 0xfffu) != 0xfffu || !passport_claude_snapshot_valid(&s)) return false;
    *out = s;
    *enabled_present = (seen & (1u << 12)) != 0;
    *enabled = show;
    return true;
}
