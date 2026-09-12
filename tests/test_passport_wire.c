#include "passport_wire.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

int main(void)
{
    ap_wire_command_t c;
    assert(ap_wire_parse("@AP STATUS", &c) && c.kind == AP_WIRE_STATUS);
    assert(ap_wire_parse("@AP SCREEN 0", &c) && !c.screen_on);
    assert(ap_wire_parse("@AP SCREEN 1", &c) && c.screen_on);
    assert(ap_wire_parse("@AP TIME 1789200000123 480", &c));
    assert(c.utc_ms == INT64_C(1789200000123) && c.offset_min == 480);
    assert(ap_wire_parse("@AP TIME 1789200000123 -720", &c));
    const char *bad[] = {"", "@AP STATUS extra", "@AP SCREEN 2", "@AP SCREEN 01",
        "@AP TIME 0 0", "@AP TIME 4102444800000 0", "@AP TIME 1789200000123 841",
        "@AP TIME 1789200000123 -721", "@AP TIME 1789200000123",
        "@AP TIME 1789200000123 0 extra", "@AP TIME 1789200000123 0x10",
        "@AP TIME 99999999999999999999999 0", "@AP TIME - 0", "@AP TIME +1789200000123 0"};
    for (unsigned i = 0; i < sizeof(bad) / sizeof(*bad); ++i) assert(!ap_wire_parse(bad[i], &c));
    assert(!ap_wire_parse(NULL, &c));
    assert(!ap_wire_parse("@AP STATUS", NULL));
    const char *flat = "{\"name\":\"A {B}\",\"title\":\"\",\"intro\":\"a \\\"b\\\"\"}";
    assert(ap_wire_flat_object((const unsigned char *)flat, strlen(flat)));
    const char *nested[] = {"{\"x\":{}}", "{\"x\":[]}", "{}{}", "{", "}", "{\"x\":\"unterminated}", "[]"};
    for (unsigned i = 0; i < sizeof(nested)/sizeof(*nested); ++i)
        assert(!ap_wire_flat_object((const unsigned char *)nested[i], strlen(nested[i])));
    const unsigned char nul[] = {'{',0,'}'};
    assert(!ap_wire_flat_object(nul, sizeof(nul)));
    unsigned char limit[513];
    memset(limit, ' ', sizeof(limit));
    limit[0]='{'; limit[1]='}';
    assert(ap_wire_flat_object(limit, 512));
    assert(!ap_wire_flat_object(limit, 513));
    puts("Passport USB command tests: PASS");
}
