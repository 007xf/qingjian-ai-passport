#include "passport_avatar_codec.h"
#include <assert.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <zlib.h>

static void put32(uint8_t *p, uint32_t n) { for (unsigned i=0;i<4;i++) p[i]=(uint8_t)(n>>(i*8)); }
static void file_bytes(const char *p, void *bytes, size_t n) {
    FILE *f=fopen(p,"rb"); assert(f); assert(fread(bytes,1,n,f)==n); assert(fgetc(f)==EOF); fclose(f);
}
int main(int argc, char **argv) {
    uint8_t packet[8224]={0};
    uint16_t guarded[128*128+2]={0}, raw[56*56];
    guarded[0]=0xcafe; guarded[128*128+1]=0xbeef;
    uint16_t *pixels=guarded+1;
    if (argc==3) {
        uint16_t expected[128*128];
        file_bytes(argv[1],packet,sizeof(packet)); file_bytes(argv[2],expected,sizeof(expected));
        assert(passport_avatar_decode(packet,sizeof(packet),pixels));
        assert(!memcmp(pixels,expected,sizeof(expected)));
    } else {
        for(unsigned i=0;i<56*56;i++) raw[i]=(uint16_t)((i*73)^0x5a3c);
        memcpy(packet,"APZ1",4);packet[4]=56;packet[6]=56;
        uLongf compressed=sizeof(packet)-16;
        assert(compress2(packet+16,&compressed,(uint8_t *)raw,sizeof(raw),9)==Z_OK);
        put32(packet+8,(uint32_t)compressed);put32(packet+12,sizeof(raw));
        assert(passport_avatar_decode(packet,sizeof(packet),pixels));
        for(unsigned i=0;i<128*128;i++) assert(pixels[i]==raw[(i/128*56/128)*56+(i%128*56/128)]);
        assert(!passport_avatar_decode(packet,sizeof(packet)-1,pixels));
        packet[4]=255;assert(!passport_avatar_decode(packet,sizeof(packet),pixels));packet[4]=56;
        packet[6]=57;assert(!passport_avatar_decode(packet,sizeof(packet),pixels));packet[6]=56;
        packet[8223]=1;assert(!passport_avatar_decode(packet,sizeof(packet),pixels));packet[8223]=0;
        packet[16+compressed-1]^=1;assert(!passport_avatar_decode(packet,sizeof(packet),pixels));
        memset(packet,0,sizeof(packet));packet[0]=0x00;packet[1]=0xf8; /* legacy red palette */
        assert(passport_avatar_decode(packet,sizeof(packet),pixels));
        for(unsigned i=0;i<128*128;i++) assert(pixels[i]==0xf800);
    }
    assert(guarded[0]==0xcafe && guarded[128*128+1]==0xbeef);
    puts("Avatar RGB565 codec: PASS");
}
