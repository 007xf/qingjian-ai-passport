#pragma once
#include "lvgl.h"
#include "passport_claude_model.h"

/* Caller owns the screen and LVGL lock. No worker, timer, or account data is
 * accessed here; every number comes from the validated bridge snapshot. */
bool passport_claude_view_create(lv_obj_t *screen);
void passport_claude_view_update(const passport_claude_snapshot_t *snapshot, int64_t now_utc_ms);
void passport_claude_view_destroy(void);
