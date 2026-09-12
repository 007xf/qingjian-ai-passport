#pragma once
#include "lvgl.h"
#include "passport_cursor_model.h"

/* Caller owns the screen and LVGL lock. No timer, worker or storage is created. */
bool passport_cursor_view_create(lv_obj_t *screen);
void passport_cursor_view_update(const passport_cursor_snapshot_t *snapshot, int64_t now_utc_ms);
void passport_cursor_view_destroy(void);
