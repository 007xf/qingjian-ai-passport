#include "passport_avatar_codec.h"
#include <stdlib.h>
#include <string.h>
#ifdef ESP_PLATFORM
#include "miniz.h"
#else
#include <zlib.h>
#endif

static uint32_t le32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

bool passport_avatar_decode(const uint8_t *data, size_t size, uint16_t *pixels)
{
    if (!data || !pixels || size != 8224) return false;
    if (memcmp(data, "APZ1", 4)) {
        for (unsigned i = 0; i < 128 * 128; ++i) {
            unsigned index = (data[32 + i / 2] >> ((i & 1) ? 0 : 4)) & 15;
            pixels[i] = data[index*2] | ((uint16_t)data[index*2+1] << 8);
        }
        return true;
    }
    unsigned width = data[4] | ((unsigned)data[5] << 8);
    unsigned height = data[6] | ((unsigned)data[7] << 8);
    size_t compressed = le32(data + 8), raw = le32(data + 12);
    if (width < 56 || width > 128 || height != width || raw != width * height * 2 ||
        !compressed || compressed > size - 16) return false;
    for (size_t i = 16 + compressed; i < size; ++i) if (data[i]) return false;
#ifdef ESP_PLATFORM
    // ROM miniz state is deliberately heap-owned: it exceeds the USB task stack.
    tinfl_decompressor *state = calloc(1, sizeof(*state));
    if (!state) return false;
    tinfl_init(state);
    size_t in_size = compressed, out_size = raw;
    tinfl_status result = tinfl_decompress(state, data + 16, &in_size,
        (uint8_t *)pixels, (uint8_t *)pixels, &out_size,
        TINFL_FLAG_PARSE_ZLIB_HEADER | TINFL_FLAG_USING_NON_WRAPPING_OUTPUT_BUF);
    free(state);
    if (result != TINFL_STATUS_DONE || out_size != raw || in_size != compressed) return false;
#else
    z_stream stream = {0};
    stream.next_in = (Bytef *)(data + 16); stream.avail_in = (uInt)compressed;
    stream.next_out = (Bytef *)pixels; stream.avail_out = (uInt)raw;
    if (inflateInit(&stream) != Z_OK) return false;
    int result = inflate(&stream, Z_FINISH);
    bool valid = result == Z_STREAM_END && stream.total_out == raw && stream.total_in == compressed;
    inflateEnd(&stream);
    if (!valid) return false;
#endif
    // Reverse traversal permits expanding a smaller source in the same buffer.
    for (int i = 128 * 128 - 1; i >= 0; --i) {
        unsigned source = ((unsigned)i / 128 * height / 128) * width + ((unsigned)i % 128 * width / 128);
        pixels[i] = pixels[source];
    }
    return true;
}
