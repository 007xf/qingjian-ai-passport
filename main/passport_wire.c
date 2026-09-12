#include "passport_wire.h"
#include <errno.h>
#include <stdlib.h>
#include <string.h>

bool ap_wire_flat_object(const unsigned char *text, size_t size)
{
    if (!text || !size || size > 512) return false;
    bool quoted = false, escaped = false;
    unsigned depth = 0, objects = 0;
    for (size_t i = 0; i < size; ++i) {
        unsigned char c = text[i];
        if (!c) return false;
        if (quoted) {
            if (c < 32) return false;
            if (escaped) { escaped = false; continue; }
            if (c == '\\') { escaped = true; continue; }
            if (c == '"') quoted = false;
        } else if (c == '"') quoted = true;
        else if (c == '[' || c == ']') return false;
        else if (c == '{') { if (depth || ++objects > 1) return false; depth = 1; }
        else if (c == '}') { if (!depth) return false; depth = 0; }
    }
    return !quoted && !escaped && depth == 0 && objects == 1;
}

static bool integer(const char **p, int64_t *value)
{
    if (!(**p == '-' || (**p >= '0' && **p <= '9'))) return false;
    char *end;
    errno = 0;
    long long parsed = strtoll(*p, &end, 10);
    if (errno || end == *p) return false;
    *value = (int64_t)parsed;
    *p = end;
    return true;
}

bool ap_wire_parse(const char *line, ap_wire_command_t *out)
{
    if (!line || !out) return false;
    ap_wire_command_t c = {0};
    if (!strcmp(line, "@AP STATUS")) c.kind = AP_WIRE_STATUS;
    else if (!strcmp(line, "@AP SCREEN 0") || !strcmp(line, "@AP SCREEN 1")) {
        c.kind = AP_WIRE_SCREEN;
        c.screen_on = line[11] == '1';
    } else if (!strncmp(line, "@AP TIME ", 9)) {
        const char *p = line + 9;
        int64_t offset;
        c.kind = AP_WIRE_TIME;
        if (!integer(&p, &c.utc_ms) || *p++ != ' ' ||
            !integer(&p, &offset) || *p != '\0') return false;
        if (c.utc_ms < INT64_C(1577836800000) ||
            c.utc_ms >= INT64_C(4102444800000) || offset < -720 || offset > 840)
            return false;
        c.offset_min = (int)offset;
    } else return false;
    *out = c;
    return true;
}
