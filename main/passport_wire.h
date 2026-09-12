#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>

typedef enum { AP_WIRE_STATUS, AP_WIRE_TIME, AP_WIRE_SCREEN } ap_wire_kind_t;
typedef struct {
    ap_wire_kind_t kind;
    int64_t utc_ms;
    int offset_min;
    bool screen_on;
} ap_wire_command_t;
bool ap_wire_parse(const char *line, ap_wire_command_t *out);
// Bound the flat badge JSON contract before invoking a recursive JSON parser.
bool ap_wire_flat_object(const unsigned char *text, size_t size);
