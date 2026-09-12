#pragma once
#include "lvgl.h"
#include "passport_provider_model.h"

/* Caller owns screen/LVGL lock. Page 1 Cursor, 2 Gemini, 3 agent overview.
 * The view occupies y=42..319 and creates no timers, tasks or storage. */
bool passport_provider_view_create(lv_obj_t *screen, int page);
void passport_provider_view_update(const passport_provider_snapshot_t snapshots[PASSPORT_PROVIDER_COUNT],
                                    int64_t now_utc_ms, uint8_t visible_features);
void passport_provider_view_destroy(void);
