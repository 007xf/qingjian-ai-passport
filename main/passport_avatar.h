#pragma once
#include "lvgl.h"
#include <stdbool.h>
#include <stddef.h>
// Initialize after ordinary NVS; never erases NVS or writes outside its namespace.
void passport_avatar_init(void);
const lv_image_dsc_t *passport_avatar_current(void);
// Stage mutation occurs in the application worker under the LVGL lock.
void passport_avatar_set_stage(unsigned stage);
// Persist a custom base avatar before exposing it. Caller is the USB worker.
bool passport_avatar_install(const uint8_t *data, size_t size, unsigned stage);
unsigned passport_avatar_custom_stage(void);
bool passport_avatar_reset(void);
bool passport_avatar_is_custom(void);
void passport_avatar_hash(char out[65]);
