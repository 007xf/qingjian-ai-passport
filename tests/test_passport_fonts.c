/* Native regression test: link the actual LVGL decoder and generated CJK
 * fonts. Only allocator/cache plumbing is provided by the host harness. */
#include "lvgl.h"
#include "src/core/lv_global.h"
#include "passport_assets.h"
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

lv_global_t lv_global;
void *lv_malloc(size_t size) { return malloc(size); }
void *lv_malloc_zeroed(size_t size) { return calloc(1, size); }
void lv_free(void *memory) { free(memory); }
void *lv_memcpy(void *dst, const void *src, size_t size) { return memcpy(dst, src, size); }
void *lv_utils_bsearch(const void *key, const void *base, size_t count, size_t size,
                       int (*compare)(const void *, const void *)) {
    return bsearch(key, base, count, size, compare);
}
uint32_t lv_draw_buf_width_to_stride(uint32_t width, lv_color_format_t format) {
    assert(format == LV_COLOR_FORMAT_A8);
    return width;
}
void lv_draw_buf_flush_cache(const lv_draw_buf_t *buffer, const lv_area_t *area) {
    (void)buffer;
    (void)area; /* Native coherent RAM requires no cache flush. */
}

static void verify(const lv_font_t *font, uint32_t codepoint) {
    lv_font_glyph_dsc_t glyph = {0};
    assert(font->get_glyph_dsc(font, &glyph, codepoint, 0));
    assert(glyph.box_w > 0 && glyph.box_h > 0);
    assert(glyph.box_w <= 32 && glyph.box_h <= 32);
    glyph.resolved_font = font;
    uint8_t pixels[32 * 32] = {0};
    lv_draw_buf_t buffer = {.data = pixels, .data_size = sizeof(pixels)};
    const void *result = font->get_glyph_bitmap(&glyph, &buffer);
#if LV_USE_FONT_COMPRESSED
    assert(result != NULL);
    unsigned covered = 0;
    for (size_t i = 0; i < sizeof(pixels); i++) covered += pixels[i] != 0;
    assert(covered > 0);
    printf("U+%04X font%u: %ux%u, %u visible pixels\n", (unsigned)codepoint,
           font->line_height, glyph.box_w, glyph.box_h, covered);
#else
    assert(result == NULL); /* Exact reproduction of the on-device blank text. */
#endif
}

int main(void) {
    const uint32_t glyphs[] = {0x4F60, 0x7684, 0x59D3, 0x540D, 0x9AD8, 0x6587, 0x4E30, 'A', '0'};
    for (size_t i = 0; i < sizeof(glyphs) / sizeof(glyphs[0]); i++) {
        verify(&passport_font_14, glyphs[i]);
        verify(&passport_font_20, glyphs[i]);
    }
#if LV_USE_FONT_COMPRESSED
    puts("Actual LVGL compressed-font decoding: PASS");
#else
    puts("Original blank-glyph failure reproduced with compression disabled: PASS");
#endif
    return 0;
}
