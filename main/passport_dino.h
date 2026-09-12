#pragma once
#include "bsp_button.h"
#include "dino_logic.h"
#include "lvgl.h"
#include <stdbool.h>
#include <stdint.h>

/* Caller owns the screen, application worker and LVGL lock. No timer/task,
 * network, filesystem or NVS calls exist in this module. */
bool passport_dino_create(lv_obj_t *screen, uint32_t best_score);
void passport_dino_destroy(void); /* call before deleting the owning screen */
void passport_dino_tick(int64_t mono_ms);
void passport_dino_raw_key(bsp_btn_t key, bsp_btn_ev_t event);
void passport_dino_action(bsp_btn_ev_t event); /* classified OK only; parent retains long/triple */
void passport_dino_suspend(void); /* required on screen off, input loss, hiding or exit */
/* Suspend first when leaving, then drain this into the parent's save worker
 * outside the LVGL lock. Updates arise only at pause, game-over or exit. */
bool passport_dino_take_best(uint32_t *out);
void passport_dino_get_status(dino_game_t *out);
