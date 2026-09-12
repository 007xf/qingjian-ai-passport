#pragma once

#include "lvgl.h"
#include "passport_codex_model.h"
#include <stdbool.h>
#include <stdint.h>

/* Caller owns the screen and LVGL lock. This view occupies y=42..319 and
 * creates no task, timer, network request or persistent storage. The snapshot
 * is read only during update; no pointer to it is retained. */
bool passport_codex_view_create(lv_obj_t *screen);
void passport_codex_view_destroy(void); /* before deleting the owning screen */
void passport_codex_view_update(const passport_codex_snapshot_t *snapshot,
                                int64_t now_utc_ms, bool tokens_known,
                                uint64_t tokens);
