#include "passport_dino.h"
#include "passport_assets.h"
#include "passport_layout.h"
#include "passport_dino_sprites.h"
#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#define DRAW_INTERVAL_MS 40 /* 25Hz drawing, independent bounded 60Hz physics. */
static lv_obj_t *s_root, *s_field, *s_player, *s_cloud;
static lv_obj_t *s_obstacles[DINO_OBSTACLE_COUNT], *s_road[2];
static lv_obj_t *s_score, *s_best, *s_message, *s_pause, *s_game_over, *s_restart;
static dino_game_t s_game;
static int64_t s_last_draw_ms;
static uint32_t s_drawn_score, s_drawn_best;
static dino_mode_t s_drawn_mode;
static dino_sprite_id_t s_player_frame, s_obstacle_frames[DINO_OBSTACLE_COUNT];
static bool s_force_redraw;

static void visible(lv_obj_t *object, bool show) {
    if (show) lv_obj_remove_flag(object, LV_OBJ_FLAG_HIDDEN);
    else lv_obj_add_flag(object, LV_OBJ_FLAG_HIDDEN);
}
static lv_obj_t *box(lv_obj_t *parent, uint32_t color) {
    lv_obj_t *object = lv_obj_create(parent);
    lv_obj_remove_style_all(object);
    lv_obj_remove_flag(object, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_remove_flag(object, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_style_bg_opa(object, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(object, lv_color_hex(color), 0);
    return object;
}
static lv_obj_t *label(lv_obj_t *parent, const char *value, int x, int y,
                        int width, const lv_font_t *font, uint32_t color) {
    lv_obj_t *object = lv_label_create(parent);
    lv_label_set_text(object, value);
    lv_obj_set_style_text_font(object, font, 0);
    lv_obj_set_style_text_color(object, lv_color_hex(color), 0);
    lv_obj_set_pos(object, x, y);
    lv_obj_set_width(object, width);
    return object;
}
static lv_obj_t *sprite(lv_obj_t *parent, const lv_image_dsc_t *image, int x, int y) {
    lv_obj_t *object = lv_image_create(parent);
    lv_image_set_src(object, image);
    lv_image_set_antialias(object, false);
    lv_obj_set_pos(object, x, y);
    /* No scale, recolor, mutable canvas or alpha-only conversion. Image
     * descriptors point directly at source-derived pixels in Flash. */
    return object;
}
static void draw_counter(lv_event_t *event) {
    lv_obj_t *object = lv_event_get_target(event);
    lv_layer_t *layer = lv_event_get_layer(event);
    bool best = object == s_best;
    char digits[11];
    snprintf(digits, sizeof(digits), "%05" PRIu32, best ? s_drawn_best : s_drawn_score);
    lv_area_t origin;
    lv_obj_get_coords(object, &origin);
    int x = origin.x1;
    lv_draw_image_dsc_t draw;
    lv_draw_image_dsc_init(&draw);
    if (best) {
        lv_area_t area = {x, origin.y1, x + 19, origin.y1 + 12};
        draw.src = &dino_image_hi;
        lv_draw_image(layer, &draw, &area);
        x += 24;
    }
    for (unsigned i = 0; digits[i]; ++i) {
        lv_area_t area = {x, origin.y1, x + 9, origin.y1 + 12};
        draw.src = dino_digit_images[digits[i] - '0'];
        lv_draw_image(layer, &draw, &area);
        x += 10;
    }
}
static lv_obj_t *counter(lv_obj_t *parent, int x, int y, int width) {
    lv_obj_t *object = lv_obj_create(parent);
    lv_obj_remove_style_all(object);
    lv_obj_remove_flag(object, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_remove_flag(object, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_pos(object, x, y);
    lv_obj_set_size(object, width, 13);
    lv_obj_add_event_cb(object, draw_counter, LV_EVENT_DRAW_MAIN, NULL);
    return object;
}
static void clear_objects(void) {
    s_root = s_field = s_player = s_cloud = NULL;
    s_score = s_best = s_message = s_pause = s_game_over = s_restart = NULL;
    memset(s_obstacles, 0, sizeof(s_obstacles));
    memset(s_road, 0, sizeof(s_road));
    s_force_redraw = false;
}
static void deleted(lv_event_t *event) {
    if (lv_event_get_target(event) == s_root) {
        dino_game_suspend(&s_game);
        clear_objects();
    }
}
static void draw_player(void) {
    dino_sprite_id_t frame = dino_game_player_frame(&s_game);
    if (frame != s_player_frame) {
        lv_image_set_src(s_player, dino_sprite_images[frame]);
        s_player_frame = frame;
    }
    lv_obj_set_pos(s_player, DINO_PLAYER_X, dino_game_player_top(&s_game));
}
static void draw_obstacle(unsigned index) {
    const dino_obstacle_t *obstacle = &s_game.obstacles[index];
    visible(s_obstacles[index], obstacle->active);
    if (!obstacle->active) return;
    dino_sprite_id_t frame = dino_obstacle_frame(&s_game, obstacle);
    if (frame != s_obstacle_frames[index]) {
        lv_image_set_src(s_obstacles[index], dino_sprite_images[frame]);
        s_obstacle_frames[index] = frame;
    }
    lv_obj_set_pos(s_obstacles[index], obstacle->x_q8 / 256, dino_obstacle_top(obstacle));
}
static void redraw(void) {
    if (!s_root) return;
    draw_player();
    for (unsigned i = 0; i < DINO_OBSTACLE_COUNT; ++i) draw_obstacle(i);
    int phase = (int)(s_game.distance_q8 / 256 % 1200);
    for (unsigned i = 0; i < 2; ++i) {
        int x = (int)i * 600 - phase;
        if (x <= -600) x += 1200;
        lv_obj_set_x(s_road[i], x);
    }
    int cloud_x = 160 - (int)(s_game.distance_q8 / (256 * 8) % (DINO_WORLD_WIDTH + 46));
    if (cloud_x < -46) cloud_x += DINO_WORLD_WIDTH + 46;
    lv_obj_set_x(s_cloud, cloud_x);
    uint32_t best = s_game.score > s_game.best_score ? s_game.score : s_game.best_score;
    if (s_drawn_score != s_game.score) {
        s_drawn_score = s_game.score;
        lv_obj_invalidate(s_score);
    }
    if (s_drawn_best != best) {
        s_drawn_best = best;
        lv_obj_invalidate(s_best);
    }
    /* Classic five digits normally share one row; preserve full uint32 scores
     * on separate rows when they grow, without truncation or fake rollover. */
    char digits[11];
    snprintf(digits, sizeof(digits), "%05" PRIu32, s_drawn_score);
    bool large_score = best > 99999 || s_drawn_score > 99999;
    lv_obj_set_pos(s_best, 16, large_score ? 81 : 85);
    lv_obj_set_pos(s_score, 224 - (int)strlen(digits) * 10, large_score ? 98 : 85);
    lv_obj_set_width(s_score, (int)strlen(digits) * 10);
    if (s_drawn_mode != s_game.mode) {
        visible(s_pause, s_game.mode == DINO_PAUSED);
        visible(s_game_over, s_game.mode == DINO_GAME_OVER);
        visible(s_restart, s_game.mode == DINO_GAME_OVER);
        lv_label_set_text(s_message, s_game.mode == DINO_READY ? "按 OK 开始" :
                                    s_game.mode == DINO_RUNNING ? "OK 暂停 / 长按 OK 退出" :
                                    s_game.mode == DINO_GAME_OVER ? "按 OK 再试一次" : "长按 OK 返回");
        s_drawn_mode = s_game.mode;
    }
    s_force_redraw = false;
}
bool passport_dino_create(lv_obj_t *screen, uint32_t best_score) {
    if (!screen || s_root) return false;
    dino_game_init(&s_game, UINT32_C(0xD1A05EED), best_score);
    s_root = lv_obj_create(screen);
    if (!s_root) return false;
    lv_obj_remove_style_all(s_root);
    lv_obj_remove_flag(s_root, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_size(s_root, 240, 320);
    lv_obj_set_pos(s_root, 0, 0);
    lv_obj_add_event_cb(s_root, deleted, LV_EVENT_DELETE, NULL);
    label(s_root, "Dino", 16, 51, 208, &passport_font_20, PASSPORT_LAYOUT_COLOR_PRIMARY);
    s_score = counter(s_root, 174, 85, 100);
    s_best = counter(s_root, 16, 85, 124);
    s_field = box(s_root, PASSPORT_LAYOUT_COLOR_BACKGROUND);
    lv_obj_set_pos(s_field, 12, 113);
    lv_obj_set_size(s_field, DINO_WORLD_WIDTH, 142);
    s_cloud = sprite(s_field, &dino_image_cloud, 160, 16);
    s_road[0] = sprite(s_field, &dino_image_horizon_0, 0, DINO_GROUND_Y - 13);
    s_road[1] = sprite(s_field, &dino_image_horizon_1, 600, DINO_GROUND_Y - 13);
    s_player = sprite(s_field, &dino_image_trex_stand, DINO_PLAYER_X, dino_game_player_top(&s_game));
    s_player_frame = DINO_SPRITE_TREX_STAND;
    for (unsigned i = 0; i < DINO_OBSTACLE_COUNT; ++i) {
        s_obstacles[i] = sprite(s_field, &dino_image_cactus_small, 0, 0);
        s_obstacle_frames[i] = DINO_SPRITE_CACTUS_SMALL;
    }
    /* Original GAME OVER and retry artwork leave the crashed dinosaur visible. */
    s_game_over = sprite(s_field, &dino_image_game_over, 12, 18);
    s_restart = sprite(s_field, &dino_image_restart, 90, 40);
    s_pause = box(s_field, PASSPORT_LAYOUT_COLOR_BACKGROUND);
    lv_obj_set_pos(s_pause, 0, 16);
    lv_obj_set_size(s_pause, DINO_WORLD_WIDTH, 66);
    lv_obj_t *pause_title = label(s_pause, "已暂停", 8, 5, 200, &passport_font_20, PASSPORT_LAYOUT_COLOR_PRIMARY);
    lv_obj_t *pause_hint = label(s_pause, "按 OK 继续", 8, 38, 200, &passport_font_14, PASSPORT_LAYOUT_COLOR_SECONDARY);
    lv_obj_set_style_text_align(pause_title, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_text_align(pause_hint, LV_TEXT_ALIGN_CENTER, 0);
    s_message = label(s_root, "", 12, 263, 216, &passport_font_14, PASSPORT_LAYOUT_COLOR_ACCENT);
    lv_obj_set_style_text_align(s_message, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_t *help = label(s_root, "上键跳跃 / 按住下键下蹲", 10, 291, 220,
                           &passport_font_14, PASSPORT_LAYOUT_COLOR_SECONDARY);
    lv_obj_set_style_text_align(help, LV_TEXT_ALIGN_CENTER, 0);
    s_last_draw_ms = -DRAW_INTERVAL_MS;
    s_drawn_score = 1;
    s_drawn_best = best_score + 1;
    s_drawn_mode = (dino_mode_t)-1;
    s_force_redraw = true;
    redraw();
    return true;
}

void passport_dino_tick(int64_t mono_ms) {
    if (!s_root) return;
    bool changed = dino_game_tick(&s_game, mono_ms);
    bool mode_changed = s_game.mode != s_drawn_mode;
    if (mode_changed || s_force_redraw || (changed && (mono_ms < s_last_draw_ms || mono_ms - s_last_draw_ms >= DRAW_INTERVAL_MS))) {
        redraw();
        s_last_draw_ms = mono_ms;
    }
}

void passport_dino_raw_key(bsp_btn_t key, bsp_btn_ev_t event) {
    if (!s_root || (key != BSP_BTN_UP && key != BSP_BTN_DOWN) ||
        (event != BSP_BTN_PRESS && event != BSP_BTN_RELEASE)) return;
    bool was_ducking = dino_game_crouching(&s_game);
    dino_game_key(&s_game, key == BSP_BTN_UP ? DINO_KEY_UP : DINO_KEY_DOWN, event == BSP_BTN_PRESS);
    s_force_redraw = true;
    if (was_ducking != dino_game_crouching(&s_game)) redraw();
}

void passport_dino_action(bsp_btn_ev_t event) {
    if (!s_root) return;
    if (event == BSP_BTN_CLICK) dino_game_action(&s_game);
    else if (event == BSP_BTN_DOUBLE && s_game.mode == DINO_RUNNING) dino_game_suspend(&s_game);
    else return;
    redraw();
}

void passport_dino_suspend(void) {
    if (!s_root) return;
    dino_game_suspend(&s_game);
    redraw();
}

bool passport_dino_take_best(uint32_t *out) {
    return dino_game_take_best(&s_game, out);
}

void passport_dino_get_status(dino_game_t *out) {
    if (out) *out = s_game;
}

void passport_dino_destroy(void) {
    if (s_root) {
        dino_game_suspend(&s_game);
        lv_obj_delete(s_root);
    }
    clear_objects();
}
