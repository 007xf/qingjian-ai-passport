#include "passport_avatar.h"
#include "passport_avatar_codec.h"
#include "src/misc/cache/instance/lv_image_cache.h"
#include "passport_assets.h"
#include "passport.h"
#include "bsp_display.h"
#include "mbedtls/sha256.h"
#include "nvs.h"
#include <stdlib.h>
#include <string.h>

static uint8_t s_custom[PASSPORT_AVATAR_BYTES];
static bool s_have_custom;
static unsigned s_custom_stage;
static uint16_t s_pixels[128 * 128];
static unsigned s_stage;
static char s_custom_hash[65];
static lv_image_dsc_t s_image = {
    .header = {.magic = LV_IMAGE_HEADER_MAGIC, .cf = LV_COLOR_FORMAT_RGB565,
               .w = 128, .h = 128, .stride = 256},
    .data_size = sizeof(s_pixels), .data = (const uint8_t *)s_pixels,
};

static void digest(const void *bytes, size_t size, char hash[65])
{
    static const char digits[] = "0123456789abcdef";
    unsigned char out[32];
    mbedtls_sha256(bytes, size, out, 0);
    for (unsigned i = 0; i < 32; ++i) {
        hash[i * 2] = digits[out[i] >> 4]; hash[i * 2 + 1] = digits[out[i] & 15];
    }
    hash[64] = 0;
}

static void decode(void)
{
    const uint16_t *data = s_stage == 2 ? passport_avatar_red :
                          s_stage == 1 ? passport_avatar_mage :
                          passport_avatar_winter;
    bool custom = s_have_custom && s_stage == s_custom_stage &&
                  passport_avatar_decode(s_custom, sizeof(s_custom), s_pixels);
    /* Built-in art is already at the 160px display size and is read straight
     * from flash. Retain the 128px scratch buffer only for custom uploads. */
    lv_image_cache_drop(&s_image);
    unsigned size = custom ? 128 : PASSPORT_BUILTIN_AVATAR_SIZE;
    s_image.header.w = s_image.header.h = size;
    s_image.header.stride = size * 2;
    s_image.data_size = size * size * 2;
    s_image.data = (const uint8_t *)(custom ? s_pixels : data);
}

void passport_avatar_init(void)
{
    nvs_handle_t handle;
    if (nvs_open("passport_art", NVS_READONLY, &handle) == ESP_OK) {
        size_t size = PASSPORT_AVATAR_BYTES + 1;
        uint8_t *blob = malloc(size);
        s_have_custom = blob && nvs_get_blob(handle, "avatar", blob, &size) == ESP_OK &&
                        size == PASSPORT_AVATAR_BYTES + 1 && blob[0] < 3;
        if (s_have_custom) { s_custom_stage = blob[0]; memcpy(s_custom, blob + 1, sizeof(s_custom)); }
        free(blob);
        nvs_close(handle);
    }
    if (s_have_custom && !passport_avatar_decode(s_custom, sizeof(s_custom), s_pixels)) s_have_custom = false;
    if (s_have_custom) digest(s_custom, sizeof(s_custom), s_custom_hash);
    else digest(passport_avatar_winter, sizeof(passport_avatar_winter), s_custom_hash);
    decode();
}

const lv_image_dsc_t *passport_avatar_current(void) { return &s_image; }

void passport_avatar_set_stage(unsigned stage)
{
    if (stage > 2 || s_stage == stage) return;
    s_stage = stage;
    decode();
}

bool passport_avatar_install(const uint8_t *data, size_t size, unsigned stage)
{
    if (!data || size != PASSPORT_AVATAR_BYTES || stage > 2) return false;
    /* BLE leaves too little contiguous heap for a second 32KB image plus
     * inflate state. Validate using the existing buffer under the renderer
     * lock, then restore the currently selected image BEFORE unlocking.
     * No incomplete or unpersisted upload is ever rendered. */
    if (!bsp_lvgl_lock(1000)) return false;
    bool valid = passport_avatar_decode(data, size, s_pixels);
    decode();
    bsp_lvgl_unlock();
    if (!valid) return false;
    uint8_t *blob = malloc(size + 1), *readback = malloc(size + 1);
    if (!blob || !readback) { free(blob); free(readback); return false; }
    blob[0] = (uint8_t)stage;
    memcpy(blob + 1, data, size);
    nvs_handle_t handle;
    esp_err_t e = nvs_open("passport_art", NVS_READWRITE, &handle);
    if (e == ESP_OK) {
        e = nvs_set_blob(handle, "avatar", blob, size + 1);
        if (e == ESP_OK) e = nvs_commit(handle);
        if (e == ESP_OK) {
            size_t n = size + 1;
            e = nvs_get_blob(handle, "avatar", readback, &n);
            if (e == ESP_OK && (n != size + 1 || memcmp(blob, readback, n))) e = ESP_FAIL;
        }
        nvs_close(handle);
    }
    free(blob); free(readback);
    if (e != ESP_OK) return false;
    // No NVS or slow work while holding the renderer lock.
    if (!bsp_lvgl_lock(1000)) return false;
    memcpy(s_custom, data, size);
    s_have_custom = true;
    s_custom_stage = stage;
    digest(data, size, s_custom_hash);
    decode();
    bsp_lvgl_unlock();
    return passport_avatar_changed();
}

void passport_avatar_hash(char out[65])
{
    out[0] = 0;
    if (bsp_lvgl_lock(500)) {
        memcpy(out, s_custom_hash, 65);
        bsp_lvgl_unlock();
    }
}

bool passport_avatar_is_custom(void)
{
    bool result = false;
    if (bsp_lvgl_lock(500)) { result = s_have_custom; bsp_lvgl_unlock(); }
    return result;
}

unsigned passport_avatar_custom_stage(void)
{
    unsigned result = 0;
    if (bsp_lvgl_lock(500)) { result = s_custom_stage; bsp_lvgl_unlock(); }
    return result;
}

bool passport_avatar_reset(void)
{
    nvs_handle_t handle;
    if (nvs_open("passport_art", NVS_READWRITE, &handle) != ESP_OK) return false;
    esp_err_t e = nvs_erase_key(handle, "avatar");
    if (e == ESP_ERR_NVS_NOT_FOUND) e = ESP_OK;
    if (e == ESP_OK) e = nvs_commit(handle);
    nvs_close(handle);
    if (e != ESP_OK || !bsp_lvgl_lock(1000)) return false;
    s_have_custom = false;
    digest(passport_avatar_winter, sizeof(passport_avatar_winter), s_custom_hash);
    decode();
    bsp_lvgl_unlock();
    return passport_avatar_changed();
}
