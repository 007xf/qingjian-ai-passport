#include "passport.h"
#include "passport_logic.h"
#include "passport_layout.h"
#include "passport_assets.h"
#include "passport_avatar.h"
#include "passport_codex_view.h"
#include "passport_cursor_view.h"
#include "passport_dino.h"
#include "passport_ble.h"
#include "passport_provider_view.h"
#include "bsp_battery.h"
#include "bsp_button.h"
#include "bsp_display.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "nvs.h"
#include "nvs_flash.h"
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const char *TAG = "passport";
/* Removed features keep their protocol bits, but no longer appear in the UI. */
#define SUPPORTED_FEATURES ((uint8_t)((1u << PASSPORT_CODEX) | (1u << PASSPORT_CURSOR) | \
                                    (1u << PASSPORT_GEMINI) | (1u << PASSPORT_AGENT) | (1u << PASSPORT_DINO)))
#define BADGE_BLOB_BYTES (4 + 25 + 49 + 121)
#define TOKEN_TTL_MS 180000
#define COLOR_BACKGROUND PASSPORT_LAYOUT_COLOR_BACKGROUND
#define COLOR_PRIMARY PASSPORT_LAYOUT_COLOR_PRIMARY
#define COLOR_SECONDARY PASSPORT_LAYOUT_COLOR_SECONDARY
#define COLOR_DIM PASSPORT_LAYOUT_COLOR_DIM
#define COLOR_ACCENT PASSPORT_LAYOUT_COLOR_ACCENT
#define COLOR_PANEL PASSPORT_LAYOUT_COLOR_PANEL
_Static_assert(PASSPORT_CONFIG_BYTES >= BADGE_BLOB_BYTES, "NVS buffer must hold badge text");
_Static_assert(PASSPORT_CONFIG_BYTES >= PASSPORT_THRESHOLDS_BYTES, "NVS buffer must hold thresholds");

typedef enum { PAGE_BADGE, PAGE_CODEX, PAGE_CURSOR, PAGE_GEMINI, PAGE_AGENTS, PAGE_DINO, PAGE_SETTINGS, PAGE_COUNT } page_t;
typedef enum { LAYER_HOME, LAYER_DETAIL, LAYER_SETTINGS, LAYER_FEATURES, LAYER_MODES } layer_t;
typedef enum { EVENT_KEY, EVENT_TIME, EVENT_SCREEN, EVENT_SAVE, EVENT_SAVED, EVENT_ARTWORK, EVENT_CODEX,
               EVENT_PAIR_DISPLAY, EVENT_PAIR_REQUEST, EVENT_PROVIDER, EVENT_CURSOR } event_type_t;
typedef enum { SAVE_BADGE, SAVE_FEATURES, SAVE_TOKENS, SAVE_THRESHOLDS, SAVE_DINO } save_kind_t;
typedef struct {
    save_kind_t kind;
    passport_badge_t badge;
    passport_config_t features;
    passport_thresholds_t thresholds;
    uint32_t dino_best;
    esp_err_t result;
} save_job_t;
typedef struct {
    event_type_t type;
    int key;
    bool pressed;
    uint32_t generation;
    int64_t stamp_ms;
    int64_t utc_ms;
    int offset_min;
    bool screen_on;
    save_job_t *job;
    passport_codex_snapshot_t codex;
    uint32_t passkey;
    passport_provider_snapshot_t *provider;
    passport_cursor_snapshot_t *cursor;
} app_event_t;

static QueueHandle_t s_events, s_saves;
static portMUX_TYPE s_lock = portMUX_INITIALIZER_UNLOCKED;
static passport_status_t s_status;
static passport_badge_t s_badge;
static passport_config_t s_features;
static passport_thresholds_t s_thresholds = {PASSPORT_FIRST_THRESHOLD, PASSPORT_FINAL_THRESHOLD};
static passport_clock_t s_clock;
static passport_input_t s_input;
static passport_evolution_t s_evolution;
static passport_codex_snapshot_t s_codex;
static passport_cursor_snapshot_t s_cursor;
static passport_provider_snapshot_t s_providers[PASSPORT_PROVIDER_COUNT];
static uint32_t s_dino_best, s_dino_saved_best;
static int64_t s_dino_save_retry;
static uint32_t s_generation = 1;
static bool s_input_lost, s_storage_ok;
static char s_notice[64] = "Edit badge in the Mac app";
static int s_battery_soc = -1, s_battery_mv = -1;
static int64_t s_token_updated_ms;
static page_t s_page = PAGE_BADGE, s_return_page = PAGE_BADGE;
static layer_t s_layer = LAYER_HOME;
static int s_selection;
static lv_obj_t *s_screen, *s_time_label, *s_battery_label, *s_avatar_image;
static lv_obj_t *s_token_label, *s_token_caption, *s_flash;
static int64_t s_transform_start;
static bool s_transform_swapped;
static lv_obj_t *s_pair_overlay;
static bool s_pair_visible;
static uint32_t s_pair_code;
static bool s_pair_overlay_dirty, s_dino_input_lost;

static int64_t now_ms(void) { return esp_timer_get_time() / 1000; }

static uint32_t generation(void) {
    taskENTER_CRITICAL(&s_lock);
    uint32_t value = s_generation;
    taskEXIT_CRITICAL(&s_lock);
    return value;
}

static bool page_visible(page_t page) {
    uint8_t mask = passport_visible_features(&s_features, SUPPORTED_FEATURES);
    if (page == PAGE_CODEX) return (mask & (1u << PASSPORT_CODEX)) != 0;
    if (page == PAGE_CURSOR) return (mask & (1u << PASSPORT_CURSOR)) != 0;
    if (page == PAGE_GEMINI) return (mask & (1u << PASSPORT_GEMINI)) != 0;
    if (page == PAGE_AGENTS) return (mask & (1u << PASSPORT_AGENT)) != 0;
    if (page == PAGE_DINO) return (mask & (1u << PASSPORT_DINO)) != 0;
    return page == PAGE_BADGE || page == PAGE_SETTINGS;
}

static const char *page_name(page_t page) {
    static const char *names[] = {"Badge", "Codex", "Cursor", "Gemini", "Agents", "Dino", "Settings"};
    return names[page];
}

static lv_obj_t *label_at(lv_obj_t *parent, const char *text, int x, int y,
                          int width, const lv_font_t *font, uint32_t color) {
    lv_obj_t *label = lv_label_create(parent);
    lv_label_set_text(label, text);
    lv_obj_set_style_text_font(label, font, 0);
    lv_obj_set_style_text_color(label, lv_color_hex(color), 0);
    lv_obj_set_pos(label, x, y);
    lv_obj_set_width(label, width);
    return label;
}

/* Layout geometry, typography and colors are generated from the JSON also
 * bundled by the Mac preview. No independent coordinates live in this path. */
static lv_obj_t *layout_label(const char *text, int x, int y, int width, int height,
                              const lv_font_t *font, uint32_t color, lv_text_align_t align,
                              lv_label_long_mode_t long_mode, int letter_spacing) {
    lv_obj_t *label = label_at(s_screen, text, x, y, width, font, color);
    lv_obj_set_height(label, height);
    lv_obj_set_style_text_align(label, align, 0);
    lv_label_set_long_mode(label, long_mode);
    lv_obj_set_style_text_letter_space(label, letter_spacing, 0);
    return label;
}

#define LAYOUT_LABEL(key, text) layout_label((text), \
    PASSPORT_LAYOUT_##key##_X, PASSPORT_LAYOUT_##key##_Y, \
    PASSPORT_LAYOUT_##key##_WIDTH, PASSPORT_LAYOUT_##key##_HEIGHT, \
    &PASSPORT_LAYOUT_##key##_FONT, PASSPORT_LAYOUT_##key##_COLOR, PASSPORT_LAYOUT_##key##_ALIGN, \
    PASSPORT_LAYOUT_##key##_LONG_MODE, PASSPORT_LAYOUT_##key##_LETTER_SPACING)

static void centered(lv_obj_t *label) {
    lv_obj_set_style_text_align(label, LV_TEXT_ALIGN_CENTER, 0);
}

static void update_avatar_image(void) {
    const lv_image_dsc_t *image = passport_avatar_current();
    lv_image_set_src(s_avatar_image, image);
    lv_image_set_scale(s_avatar_image, image->header.w == PASSPORT_LAYOUT_AVATAR_SOURCE_WIDTH ?
        PASSPORT_LAYOUT_AVATAR_SCALE_256 : (256 * PASSPORT_LAYOUT_AVATAR_WIDTH / image->header.w));
}

static void update_status_ui(void) {
    char hhmm[6];
    passport_clock_hhmm(&s_clock, now_ms(), hhmm);
    lv_label_set_text(s_time_label, hhmm);
    const char *symbol = s_battery_soc < 0 ? LV_SYMBOL_BATTERY_EMPTY :
                         s_battery_soc < 20 ? LV_SYMBOL_BATTERY_1 :
                         s_battery_soc < 50 ? LV_SYMBOL_BATTERY_2 :
                         s_battery_soc < 80 ? LV_SYMBOL_BATTERY_3 : LV_SYMBOL_BATTERY_FULL;
    if (s_battery_soc < 0) lv_label_set_text_fmt(s_battery_label, "%s --%%", symbol);
    else lv_label_set_text_fmt(s_battery_label, "%s %d%%", symbol, s_battery_soc);
    lv_obj_set_style_text_color(s_battery_label,
        lv_color_hex(s_battery_soc >= 0 && s_battery_soc < 20 ? PASSPORT_LAYOUT_COLOR_WARNING : COLOR_SECONDARY), 0);
    if (s_token_label) {
        if (s_badge.tokens_known) lv_label_set_text_fmt(s_token_label, "%" PRIu64, s_badge.tokens);
        else lv_label_set_text(s_token_label, "--");
        lv_label_set_text(s_token_caption, s_badge.tokens_stale ? PASSPORT_LAYOUT_TEXT_TOKEN_CAPTION_STALE : PASSPORT_LAYOUT_TEXT_TOKEN_CAPTION);
        lv_obj_set_style_text_color(s_token_label,
            lv_color_hex(s_badge.tokens_stale ? COLOR_SECONDARY : COLOR_ACCENT), 0);
    }
    int64_t utc = 0;
    passport_clock_now(&s_clock, now_ms(), &utc);
    passport_codex_view_update(&s_codex, utc, s_badge.tokens_known && !s_badge.tokens_stale, s_badge.tokens);
    passport_cursor_view_update(&s_cursor, utc);
    passport_provider_view_update(s_providers, utc, passport_visible_features(&s_features, SUPPORTED_FEATURES));
}

static const char *page_title(page_t page) {
    static const char *names[] = {"电子徽章", "Codex", "Cursor", "Gemini", "智能体", "Dino", "设置"};
    return names[page];
}

/* Called under the UI lock, with persistence deferred to the app worker. */
static void collect_dino_best(void) {
    uint32_t best;
    if (passport_dino_take_best(&best) && best > s_dino_best) s_dino_best = best;
}

static lv_obj_t *dark_panel(lv_obj_t *parent, int x, int y, int width, int height, bool selected) {
    lv_obj_t *panel = lv_obj_create(parent);
    lv_obj_remove_style_all(panel);
    lv_obj_remove_flag(panel, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_pos(panel, x, y);
    lv_obj_set_size(panel, width, height);
    lv_obj_set_style_radius(panel, 10, 0);
    lv_obj_set_style_bg_opa(panel, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(panel, lv_color_hex(selected ? PASSPORT_LAYOUT_COLOR_SELECTION : COLOR_PANEL), 0);
    return panel;
}

static void page_heading(const char *title, const char *subtitle) {
    label_at(s_screen, title, 16, 51, 208, &passport_font_20, COLOR_PRIMARY);
    if (subtitle && subtitle[0])
        label_at(s_screen, subtitle, 16, 82, 208, &passport_font_14, COLOR_SECONDARY);
}

static void footer(const char *text) {
    lv_obj_t *label = label_at(s_screen, text, 10, 294, 220, &passport_font_14, COLOR_DIM);
    centered(label);
    lv_obj_set_height(label, 18);
    lv_label_set_long_mode(label, LV_LABEL_LONG_DOT);
}

static void update_pairing_overlay(void) {
    s_pair_overlay_dirty = false;
    if (s_pair_overlay) { lv_obj_delete(s_pair_overlay); s_pair_overlay = NULL; }
    if (!s_pair_visible || !s_screen) return;
    s_pair_overlay = dark_panel(s_screen, 8, 67, 224, 206, false);
    centered(label_at(s_pair_overlay, "蓝牙配对", 10, 17, 204, &passport_font_20, COLOR_PRIMARY));
    centered(label_at(s_pair_overlay, "在 Mac 上输入此配对码", 10, 60, 204, &passport_font_14, COLOR_SECONDARY));
    char digits[7];
    snprintf(digits, sizeof(digits), "%06" PRIu32, s_pair_code % 1000000);
    centered(label_at(s_pair_overlay, digits, 10, 98, 204, &lv_font_montserrat_20, COLOR_ACCENT));
    centered(label_at(s_pair_overlay, "长按 OK 取消", 10, 163, 204, &passport_font_14, COLOR_SECONDARY));
}

/* The user explicitly requested a black badge, superseding the upstream pixel
 * demo theme. Pages own no timers or workers: a single app worker holds the
 * LVGL lock, and clears the transformation before replacing its screen. */
static void build_screen(void) {
    lv_obj_t *old = s_screen;
    s_pair_overlay = NULL;
    passport_dino_suspend();
    collect_dino_best();
    passport_dino_destroy();
    passport_codex_view_destroy();
    passport_cursor_view_destroy();
    passport_provider_view_destroy();
    s_flash = NULL;
    s_transform_start = 0;
    s_avatar_image = s_token_label = s_token_caption = NULL;
    passport_avatar_set_stage(s_badge.stage);
    s_screen = lv_obj_create(NULL);
    lv_obj_remove_style_all(s_screen);
    lv_obj_remove_flag(s_screen, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_bg_color(s_screen, lv_color_hex(COLOR_BACKGROUND), 0);
    lv_obj_set_style_bg_opa(s_screen, LV_OPA_COVER, 0);
    /* Loading the lightweight base under the LVGL lock bounds peak RAM without
     * making a partially built frame visible to the display task. */
    lv_screen_load(s_screen);
    if (old) lv_obj_delete(old);
    s_time_label = LAYOUT_LABEL(TIME, PASSPORT_LAYOUT_TEXT_TIME_UNKNOWN);
    s_battery_label = LAYOUT_LABEL(BATTERY, PASSPORT_LAYOUT_TEXT_BATTERY_UNKNOWN);

    if (s_layer == LAYER_HOME && s_page == PAGE_BADGE) {
        s_avatar_image = lv_image_create(s_screen);
        update_avatar_image();
        /* Both native 160px and custom 128px images share the same display bounds. */
        lv_image_set_pivot(s_avatar_image, PASSPORT_LAYOUT_AVATAR_PIVOT_X, PASSPORT_LAYOUT_AVATAR_PIVOT_Y);
        lv_obj_set_pos(s_avatar_image, PASSPORT_LAYOUT_AVATAR_X, PASSPORT_LAYOUT_AVATAR_Y);
        LAYOUT_LABEL(NAME, s_badge.name);
        LAYOUT_LABEL(TITLE, s_badge.title);
        LAYOUT_LABEL(INTRO, s_badge.intro);
        lv_obj_t *divider = lv_obj_create(s_screen);
        lv_obj_remove_style_all(divider);
        lv_obj_set_pos(divider, PASSPORT_LAYOUT_SEPARATOR_X, PASSPORT_LAYOUT_SEPARATOR_Y);
        lv_obj_set_size(divider, PASSPORT_LAYOUT_SEPARATOR_WIDTH, PASSPORT_LAYOUT_SEPARATOR_HEIGHT);
        lv_obj_set_style_bg_opa(divider, LV_OPA_COVER, 0);
        lv_obj_set_style_bg_color(divider, lv_color_hex(PASSPORT_LAYOUT_SEPARATOR_COLOR), 0);
        s_token_caption = LAYOUT_LABEL(TOKEN_CAPTION, PASSPORT_LAYOUT_TEXT_TOKEN_CAPTION);
        s_token_label = LAYOUT_LABEL(TOKEN_VALUE, PASSPORT_LAYOUT_TEXT_TOKENS_UNKNOWN);
    } else if (s_layer == LAYER_HOME && s_page == PAGE_CODEX) {
        passport_codex_view_create(s_screen);
    } else if (s_layer == LAYER_HOME && s_page == PAGE_CURSOR) {
        passport_cursor_view_create(s_screen);
    } else if (s_layer == LAYER_HOME && (s_page == PAGE_GEMINI || s_page == PAGE_AGENTS)) {
        passport_provider_view_create(s_screen, s_page == PAGE_GEMINI ? 2 : 3);
    } else if (s_layer == LAYER_HOME && s_page == PAGE_DINO) {
        passport_dino_create(s_screen, s_dino_best);
    } else if (s_layer == LAYER_HOME) {
        page_heading("设置", "徽章的每一处，都由你决定");
        label_at(s_screen, "头像、姓名与个人简介", 16, 139, 208, &passport_font_14, COLOR_PRIMARY);
        label_at(s_screen, "三种形态与成长阈值", 16, 174, 208, &passport_font_14, COLOR_PRIMARY);
        label_at(s_screen, "在 Mac 上编辑，连接后同步", 16, 214, 208, &passport_font_14, COLOR_SECONDARY);
        label_at(s_screen, "短按 OK 查看设置", 16, 252, 208, &passport_font_14, COLOR_ACCENT);
        footer("连按三次 OK 熄屏");
    } else if (s_layer == LAYER_SETTINGS) {
        page_heading("设置", NULL);
        static const char *const rows[] = {"熄灭屏幕", "在 Mac 上编辑", "功能展示", "设备信息", "蓝牙配对", "返回"};
        for (int i = 0; i < 6; i++) {
            bool selected = i == s_selection;
            lv_obj_t *panel = dark_panel(s_screen, 12, 82 + i * 34, 216, 30, selected);
            label_at(panel, rows[i], 12, 7, 181, &passport_font_14, selected ? COLOR_ACCENT : COLOR_PRIMARY);
            if (selected) label_at(panel, "›", 194, 7, 12, &passport_font_14, COLOR_ACCENT);
        }
        footer("上下键选择 · 长按 OK 返回");
    } else if (s_layer == LAYER_FEATURES) {
        char header[64];
        snprintf(header, sizeof(header), "%s · %u 项已显示", s_features.custom ? "自定义" : "全部支持项",
                 passport_popcount(passport_visible_features(&s_features, SUPPORTED_FEATURES)));
        page_heading("功能展示", header);
        unsigned row = 0;
        for (unsigned i = 0; i < PASSPORT_FEATURE_COUNT; i++) {
            if ((SUPPORTED_FEATURES & (1u << i)) == 0) continue;
            bool supported = (SUPPORTED_FEATURES & (1u << i)) != 0;
            bool visible = (passport_visible_features(&s_features, SUPPORTED_FEATURES) & (1u << i)) != 0;
            label_at(s_screen, passport_feature_name(i), 16, 116 + row * 25, 117, &passport_font_14,
                     supported ? COLOR_PRIMARY : COLOR_SECONDARY);
            const char *description = visible ? "已显示" : "已隐藏";
            lv_obj_t *state = label_at(s_screen, description,
                                      137, 116 + row * 25, 87, &passport_font_14,
                                      visible ? COLOR_ACCENT : COLOR_DIM);
            lv_obj_set_style_text_align(state, LV_TEXT_ALIGN_RIGHT, 0);
            row++;
        }
        footer("在 Mac 上选择展示功能");
    } else if (s_layer == LAYER_MODES) {
        page_heading("切换页面", "只显示已启用的功能");
        int row = 0;
        int first = s_selection >= 5 ? s_selection - 4 : 0;
        for (page_t page = PAGE_BADGE; page <= PAGE_SETTINGS; page++) {
            if (!page_visible(page)) continue;
            if (row < first || row >= first + 5) { row++; continue; }
            bool selected = row == s_selection;
            lv_obj_t *panel = dark_panel(s_screen, 12, 107 + (row - first) * 36, 216, 32, selected);
            label_at(panel, page_title(page), 12, 6, 184, &passport_font_14,
                     selected ? COLOR_ACCENT : COLOR_PRIMARY);
            row++;
        }
        footer("短按 OK 打开 · 长按 OK 返回");
    } else if (s_page == PAGE_BADGE) {
        lv_obj_t *name = label_at(s_screen, s_badge.name, 16, 51, 208, &passport_font_20, COLOR_PRIMARY);
        lv_obj_set_height(name, 25);
        lv_label_set_long_mode(name, LV_LABEL_LONG_DOT);
        lv_obj_t *title = label_at(s_screen, s_badge.title, 16, 85, 208, &passport_font_14, COLOR_SECONDARY);
        lv_obj_set_height(title, 18);
        lv_label_set_long_mode(title, LV_LABEL_LONG_DOT);
        lv_obj_t *intro = label_at(s_screen, s_badge.intro, 16, 116, 208, &passport_font_14, COLOR_SECONDARY);
        lv_obj_set_height(intro, 90);
        lv_label_set_long_mode(intro, LV_LABEL_LONG_DOT);
        s_token_caption = label_at(s_screen, "本周期 TOKEN", 16, 220, 208, &passport_font_14, COLOR_SECONDARY);
        s_token_label = label_at(s_screen, "--", 16, 245, 208, &lv_font_montserrat_20, COLOR_ACCENT);
        lv_obj_set_height(s_token_label, 25);
        lv_label_set_long_mode(s_token_label, LV_LABEL_LONG_DOT);
        footer("长按 OK 返回 · Mac 编辑");
    } else if (s_selection == 1) {
        page_heading("在 Mac 上编辑", NULL);
        label_at(s_screen, "通过 USB 或蓝牙连接 Mac。\n\n打开青笺，编辑头像、\n姓名、简介与成长阈值。\n\n点击上传后同步到徽章。", 16, 112, 208,
                 &passport_font_14, COLOR_SECONDARY);
        footer("长按 OK 返回");
    } else if (s_selection == 4) {
        page_heading("蓝牙配对", NULL);
        label_at(s_screen, "请在 Mac 青笺中搜索设备。\n\n连接时屏幕会显示配对码，\n在 Mac 输入后即可同步。\n\n配对窗口持续两分钟。", 16, 109, 208,
                 &passport_font_14, COLOR_SECONDARY);
        footer("长按 OK 返回");
    } else {
        page_heading("设备信息", NULL);
        char info[250], hhmm[6], battery[24];
        passport_clock_hhmm(&s_clock, now_ms(), hhmm);
        if (s_battery_mv < 0) strcpy(battery, "不可用");
        else snprintf(battery, sizeof(battery), "%d mV", s_battery_mv);
        snprintf(info, sizeof(info), "时间  %s\n电池  %s\n存储  %s\n\n连按三次 OK 熄屏\n按任意功能键唤醒", hhmm, battery,
                 s_storage_ok ? "可用" : "不可用");
        label_at(s_screen, info, 16, 104, 208, &passport_font_14, COLOR_SECONDARY);
        lv_obj_t *notice = label_at(s_screen, s_notice, 16, 246, 208, &lv_font_montserrat_14, COLOR_DIM);
        lv_obj_set_height(notice, 36);
        lv_label_set_long_mode(notice, LV_LABEL_LONG_DOT);
        footer("长按 OK 返回");
    }
    update_status_ui();
    update_pairing_overlay();
    taskENTER_CRITICAL(&s_lock);
    s_generation++;
    taskEXIT_CRITICAL(&s_lock);
}

static void rebuild(void) {
    if (bsp_lvgl_lock(1000)) { build_screen(); bsp_lvgl_unlock(); }
}

static void publish_status(void) {
    dino_game_t dino;
    passport_dino_get_status(&dino);
    taskENTER_CRITICAL(&s_lock);
    s_status.battery_soc = s_battery_soc;
    s_status.battery_mv = s_battery_mv;
    s_status.screen_on = s_input.screen_on;
    s_status.feature_revision = s_features.revision;
    s_status.features_all = !s_features.custom;
    s_status.custom_features = s_features.custom_mask;
    s_status.visible_features = passport_visible_features(&s_features, SUPPORTED_FEATURES);
    s_status.supported_features = SUPPORTED_FEATURES;
    s_status.threshold1 = s_thresholds.first;
    s_status.threshold2 = s_thresholds.final;
    s_status.dino_score = dino.score;
    s_status.dino_best = s_dino_best;
    s_status.dino_state = dino.mode;
    snprintf(s_status.page, sizeof(s_status.page), "%s%s", page_name(s_page), s_layer == LAYER_HOME ? "" : " detail");
    taskEXIT_CRITICAL(&s_lock);
}

void passport_get_status(passport_status_t *out) {
    if (!out) return;
    passport_clock_t clock;
    passport_codex_snapshot_t codex;
    passport_cursor_snapshot_t cursor;
    taskENTER_CRITICAL(&s_lock);
    *out = s_status;
    clock = s_clock;
    codex = s_codex;
    cursor = s_cursor;
    taskEXIT_CRITICAL(&s_lock);
    out->time_synced = passport_clock_now(&clock, now_ms(), &out->utc_ms);
    if (!out->time_synced) out->utc_ms = 0;
    out->offset_min = clock.offset_min;
    out->free_heap = (uint32_t)heap_caps_get_free_size(MALLOC_CAP_8BIT);
    out->codex_observed_utc_ms = codex.observed_utc_ms;
    out->codex_ready = codex.observed_utc_ms > 0 && (codex.window[0].used_known || codex.window[1].used_known);
    out->codex_stale = out->codex_ready && !passport_codex_snapshot_fresh(&codex, out->utc_ms);
    out->cursor_quota_observed_utc_ms = cursor.observed_utc_ms;
    out->cursor_quota_reset_at_ms = cursor.reset_at_ms;
    memcpy(out->cursor_quota_plan, cursor.plan, sizeof(out->cursor_quota_plan));
    out->cursor_quota_ready = passport_cursor_snapshot_valid(&cursor) && cursor.observed_utc_ms > 0 &&
        (cursor.used_known[0] || cursor.used_known[1]);
    const bool cursor_fresh = passport_cursor_snapshot_fresh(&cursor, out->utc_ms);
    out->cursor_quota_stale = out->cursor_quota_ready && !cursor_fresh;
    for (unsigned i = 0; i < 2; ++i) {
        out->cursor_quota_known[i] = cursor_fresh && cursor.used_known[i];
        out->cursor_quota_used_percent[i] = out->cursor_quota_known[i] ? cursor.used_percent[i] : 0;
    }
}

void passport_get_badge(passport_badge_t *out) {
    if (!out) return;
    taskENTER_CRITICAL(&s_lock);
    *out = s_badge;
    taskEXIT_CRITICAL(&s_lock);
}

void passport_get_providers(passport_provider_snapshot_t out[PASSPORT_PROVIDER_COUNT]) {
    if (!out) return;
    taskENTER_CRITICAL(&s_lock);
    memcpy(out, s_providers, sizeof(s_providers));
    taskEXIT_CRITICAL(&s_lock);
}

static bool send_event(app_event_t *event) {
    return s_events && xQueueSend(s_events, event, 0) == pdTRUE;
}

bool passport_set_time(int64_t utc_ms, int offset_min) {
    passport_clock_t check;
    int64_t stamp = now_ms();
    if (!passport_clock_set(&check, utc_ms, offset_min, stamp)) return false;
    app_event_t event = {.type = EVENT_TIME, .stamp_ms = stamp, .utc_ms = utc_ms, .offset_min = offset_min};
    return send_event(&event);
}

bool passport_set_screen(bool on) {
    app_event_t event = {.type = EVENT_SCREEN, .screen_on = on};
    return send_event(&event);
}

bool passport_set_codex(const passport_codex_snapshot_t *snapshot) {
    if (!passport_codex_snapshot_valid(snapshot)) return false;
    app_event_t event = {.type = EVENT_CODEX, .codex = *snapshot};
    return send_event(&event);
}

bool passport_set_cursor(const passport_cursor_snapshot_t *snapshot) {
    if (!passport_cursor_snapshot_valid(snapshot)) return false;
    passport_cursor_snapshot_t *copy = malloc(sizeof(*copy));
    if (!copy) return false;
    *copy = *snapshot;
    app_event_t event = {.type=EVENT_CURSOR, .cursor=copy};
    if (send_event(&event)) return true;
    free(copy);
    return false;
}

bool passport_set_pairing_display(bool visible, uint32_t passkey) {
    if (passkey >= 1000000) return false;
    app_event_t event = {.type=EVENT_PAIR_DISPLAY, .screen_on=visible, .passkey=passkey};
    return send_event(&event);
}

bool passport_request_pairing(void) {
    app_event_t event = {.type=EVENT_PAIR_REQUEST};
    return send_event(&event);
}

bool passport_set_provider(const passport_provider_snapshot_t *snapshot) {
    if (!passport_provider_snapshot_valid(snapshot) || snapshot->update_id == 0) return false;
    passport_provider_snapshot_t *copy = malloc(sizeof(*copy));
    if (!copy) return false;
    *copy = *snapshot;
    app_event_t event = {.type=EVENT_PROVIDER,.provider=copy};
    if (send_event(&event)) return true;
    free(copy);
    return false;
}

bool passport_avatar_changed(void) {
    app_event_t event = {.type = EVENT_ARTWORK};
    return send_event(&event);
}

static bool submit_job(save_job_t *job) {
    taskENTER_CRITICAL(&s_lock);
    bool busy = s_status.save_busy;
    if (!busy) { s_status.save_busy = true; s_status.last_save_error = 0; }
    taskEXIT_CRITICAL(&s_lock);
    if (busy) { free(job); return false; }
    app_event_t event = {.type = EVENT_SAVE, .job = job};
    if (send_event(&event)) return true;
    taskENTER_CRITICAL(&s_lock);
    s_status.save_busy = false;
    taskEXIT_CRITICAL(&s_lock);
    free(job);
    return false;
}

bool passport_set_badge(const char *name, const char *title, const char *intro,
                        uint64_t tokens, bool tokens_known) {
    if (tokens_known && tokens > PASSPORT_MAX_TOKENS) return false;
    save_job_t *job = calloc(1, sizeof(*job));
    if (!job) return false;
    job->kind = SAVE_BADGE;
    if (!passport_utf8_copy(job->badge.name, sizeof(job->badge.name), name) ||
        !passport_utf8_copy(job->badge.title, sizeof(job->badge.title), title) ||
        !passport_utf8_copy(job->badge.intro, sizeof(job->badge.intro), intro)) {
        free(job);
        return false;
    }
    job->badge.tokens = tokens_known ? tokens : 0;
    job->badge.tokens_known = tokens_known;
    job->badge.stage = passport_token_stage(tokens, tokens_known);
    return submit_job(job);
}

bool passport_set_tokens(uint64_t tokens, bool known) {
    if (known && tokens > PASSPORT_MAX_TOKENS) return false;
    save_job_t *job = calloc(1, sizeof(*job));
    if (!job) return false;
    job->kind = SAVE_TOKENS;
    job->badge.tokens = known ? tokens : 0;
    job->badge.tokens_known = known;
    job->badge.stage = passport_token_stage(tokens, known);
    return submit_job(job);
}

bool passport_set_features(bool all, uint8_t mask) {
    save_job_t *job = calloc(1, sizeof(*job));
    if (!job) return false;
    job->kind = SAVE_FEATURES;
    job->features.custom = !all;
    job->features.custom_mask = mask;
    return submit_job(job);
}

bool passport_set_thresholds(uint64_t first, uint64_t final) {
    passport_thresholds_t thresholds = {first, final};
    if (!passport_thresholds_valid(&thresholds)) return false;
    save_job_t *job = calloc(1, sizeof(*job));
    if (!job) return false;
    job->kind = SAVE_THRESHOLDS;
    job->thresholds = thresholds;
    return submit_job(job);
}

static void badge_encode(const passport_badge_t *badge, uint8_t blob[BADGE_BLOB_BYTES]) {
    memset(blob, 0, BADGE_BLOB_BYTES);
    memcpy(blob, "APB1", 4);
    memcpy(blob + 4, badge->name, sizeof(badge->name));
    memcpy(blob + 29, badge->title, sizeof(badge->title));
    memcpy(blob + 78, badge->intro, sizeof(badge->intro));
}

static bool badge_decode(const uint8_t *blob, size_t length, passport_badge_t *badge) {
    if (length != BADGE_BLOB_BYTES || memcmp(blob, "APB1", 4) ||
        !memchr(blob + 4, 0, 25) || !memchr(blob + 29, 0, 49) || !memchr(blob + 78, 0, 121)) return false;
    passport_badge_t candidate = {0};
    if (!passport_utf8_copy(candidate.name, 25, (const char *)blob + 4) ||
        !passport_utf8_copy(candidate.title, 49, (const char *)blob + 29) ||
        !passport_utf8_copy(candidate.intro, 121, (const char *)blob + 78)) return false;
    *badge = candidate;
    return true;
}

static void load_config(void) {
    s_features = (passport_config_t) {.custom = true, .custom_mask = 147};
    strcpy(s_badge.name, "苍崎青子");
    strcpy(s_badge.title, "MISS BLUE");
    strcpy(s_badge.intro, "如果你惹怒了我，我将会开启3技能");
    esp_err_t result = nvs_flash_init();
    s_storage_ok = result == ESP_OK;
    if (!s_storage_ok) {
        s_features.custom = true;
        s_features.custom_mask = 0;
        snprintf(s_notice, sizeof(s_notice), "Storage unavailable: %s", esp_err_to_name(result));
        ESP_LOGW(TAG, "NVS unavailable, preserving storage: %s", esp_err_to_name(result));
        return;
    }
    nvs_handle_t handle;
    result = nvs_open("passport", NVS_READONLY, &handle);
    if (result == ESP_ERR_NVS_NOT_FOUND) return;
    if (result != ESP_OK) { s_storage_ok = false; return; }
    uint8_t blob[PASSPORT_CONFIG_BYTES];
    size_t length = sizeof(blob);
    result = nvs_get_blob(handle, "features", blob, &length);
    if (result != ESP_ERR_NVS_NOT_FOUND &&
        (result != ESP_OK || !passport_config_decode(blob, length, &s_features))) {
        s_features = (passport_config_t) {.custom = true};
        strcpy(s_notice, "Display choices need repair in Mac app");
    }
    length = sizeof(blob);
    result = nvs_get_blob(handle, "badge", blob, &length);
    if (result == ESP_OK && !badge_decode(blob, length, &s_badge))
        strcpy(s_notice, "Badge data invalid; edit in Mac app");
    length = sizeof(blob);
    result = nvs_get_blob(handle, "thresholds", blob, &length);
    if (result != ESP_ERR_NVS_NOT_FOUND &&
        (result != ESP_OK || !passport_thresholds_decode(blob, length, &s_thresholds)))
        strcpy(s_notice, "Thresholds invalid; defaults active");
    length = sizeof(blob);
    if (nvs_get_blob(handle, "dino_best", blob, &length) == ESP_OK && length == 8 && !memcmp(blob, "APD1", 4)) {
        s_dino_best = (uint32_t)blob[4] | ((uint32_t)blob[5] << 8) | ((uint32_t)blob[6] << 16) | ((uint32_t)blob[7] << 24);
        s_dino_saved_best = s_dino_best;
    }
    nvs_close(handle);
}

static void save_worker(void *unused) {
    (void)unused;
    int64_t last_write = -5000;
    save_job_t *job;
    while (xQueueReceive(s_saves, &job, portMAX_DELAY) == pdTRUE) {
        int64_t delay_ms = 5000 - (now_ms() - last_write);
        if (delay_ms > 0) vTaskDelay(pdMS_TO_TICKS(delay_ms));
        uint8_t blob[PASSPORT_CONFIG_BYTES], readback[PASSPORT_CONFIG_BYTES];
        size_t length;
        const char *key;
        if (job->kind == SAVE_BADGE) {
            badge_encode(&job->badge, blob);
            length = BADGE_BLOB_BYTES;
            key = "badge";
        } else if (job->kind == SAVE_FEATURES) {
            passport_config_encode(&job->features, blob);
            length = PASSPORT_CONFIG_BYTES;
            key = "features";
        } else if (job->kind == SAVE_DINO) {
            memcpy(blob, "APD1", 4);
            for (unsigned i = 0; i < 4; ++i) blob[4+i] = (uint8_t)(job->dino_best >> (8*i));
            length = 8;
            key = "dino_best";
        } else {
            passport_thresholds_encode(&job->thresholds, blob);
            length = PASSPORT_THRESHOLDS_BYTES;
            key = "thresholds";
        }
        nvs_handle_t handle;
        job->result = s_storage_ok ? nvs_open("passport", NVS_READWRITE, &handle) : ESP_FAIL;
        if (job->result == ESP_OK) {
            job->result = nvs_set_blob(handle, key, blob, length);
            if (job->result == ESP_OK) job->result = nvs_commit(handle);
            if (job->result == ESP_OK) {
                size_t read_len = sizeof(readback);
                job->result = nvs_get_blob(handle, key, readback, &read_len);
                if (job->result == ESP_OK && (read_len != length || memcmp(readback, blob, length)))
                    job->result = ESP_FAIL;
            }
            nvs_close(handle);
            last_write = now_ms();
        }
        app_event_t event = {.type = EVENT_SAVED, .job = job};
        xQueueSend(s_events, &event, portMAX_DELAY);
    }
    vTaskDelete(NULL);
}

static void start_transform(unsigned previous_stage) {
    if (s_page != PAGE_BADGE || s_layer != LAYER_HOME || !s_input.screen_on || !s_avatar_image) return;
    if (!bsp_lvgl_lock(1000)) return;
    passport_avatar_set_stage(previous_stage);
    update_avatar_image();
    s_flash = lv_obj_create(s_screen);
    lv_obj_remove_flag(s_flash, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_pos(s_flash, PASSPORT_LAYOUT_AVATAR_X - 4, PASSPORT_LAYOUT_AVATAR_Y - 4);
    lv_obj_set_size(s_flash, PASSPORT_LAYOUT_AVATAR_WIDTH + 8, PASSPORT_LAYOUT_AVATAR_HEIGHT + 8);
    lv_obj_set_style_radius(s_flash, 8, 0);
    lv_obj_set_style_border_width(s_flash, 0, 0);
    lv_obj_set_style_bg_color(s_flash, lv_color_hex(0xFFFFFF), 0);
    lv_obj_set_style_opa(s_flash, LV_OPA_TRANSP, 0);
    s_transform_start = now_ms();
    s_transform_swapped = false;
    bsp_lvgl_unlock();
}

static void finish_job(save_job_t *job) {
    bool animate = false;
    unsigned old_stage = s_badge.stage;
    bool needs_rebuild = job->kind == SAVE_FEATURES ||
        (job->kind == SAVE_BADGE && (strcmp(job->badge.name, s_badge.name) ||
         strcmp(job->badge.title, s_badge.title) || strcmp(job->badge.intro, s_badge.intro)));
    if (job->result == ESP_OK) {
        strcpy(s_notice, job->kind == SAVE_TOKENS ? "Tokens updated from Mac" : "Saved in device");
        if (job->kind == SAVE_BADGE || job->kind == SAVE_TOKENS) {
            job->badge.stage = passport_token_stage_with_thresholds(job->badge.tokens, job->badge.tokens_known, &s_thresholds);
            animate = passport_evolution_update_with_thresholds(&s_evolution, job->badge.tokens, job->badge.tokens_known, &s_thresholds);
            taskENTER_CRITICAL(&s_lock);
            if (job->kind == SAVE_TOKENS) {
                s_badge.tokens = job->badge.tokens;
                s_badge.tokens_known = job->badge.tokens_known;
                s_badge.stage = job->badge.stage;
            } else s_badge = job->badge;
            s_badge.tokens_stale = false;
            s_token_updated_ms = now_ms();
            taskEXIT_CRITICAL(&s_lock);
        } else if (job->kind == SAVE_FEATURES) {
            s_features = job->features;
            if (!page_visible(s_page)) { s_page = PAGE_BADGE; s_layer = LAYER_HOME; }
        } else if (job->kind == SAVE_DINO) {
            s_dino_saved_best = job->dino_best;
        } else {
            s_thresholds = job->thresholds;
            taskENTER_CRITICAL(&s_lock);
            s_badge.stage = passport_token_stage_with_thresholds(s_badge.tokens, s_badge.tokens_known, &s_thresholds);
            taskEXIT_CRITICAL(&s_lock);
            /* Editing a threshold is not earned activity and never flashes. */
            s_evolution = (passport_evolution_t) {
                .tokens = s_badge.tokens, .known = s_badge.tokens_known,
                .stage2_seen = s_badge.tokens_known && s_badge.tokens >= s_thresholds.final
            };
        }
    } else {
        snprintf(s_notice, sizeof(s_notice), "Save failed: %s", esp_err_to_name(job->result));
        ESP_LOGW(TAG, "%s", s_notice);
    }
    taskENTER_CRITICAL(&s_lock);
    s_status.save_busy = false;
    s_status.last_save_error = job->result;
    taskEXIT_CRITICAL(&s_lock);
    free(job);
    if (needs_rebuild) rebuild();
    /* Periodic token-only updates keep the page generation and any in-progress
     * key gesture intact. They also avoid reallocating the whole LVGL screen. */
    if (animate && s_avatar_image && s_input.screen_on) start_transform(old_stage);
    else if (bsp_lvgl_lock(1000)) {
        if (s_transform_start && old_stage != s_badge.stage) {
            lv_obj_delete(s_flash);
            s_flash = NULL;
            s_transform_start = 0;
        }
        if (!s_transform_start) {
            passport_avatar_set_stage(s_badge.stage);
            if (s_avatar_image) update_avatar_image();
        }
        update_status_ui();
        bsp_lvgl_unlock();
    }
}

static void handle_save(save_job_t *job) {
    bool changed = false;
    if (job->kind == SAVE_BADGE) {
        changed = strcmp(job->badge.name, s_badge.name) || strcmp(job->badge.title, s_badge.title) ||
                  strcmp(job->badge.intro, s_badge.intro);
    } else if (job->kind == SAVE_FEATURES) {
        passport_config_t prepared;
        changed = passport_config_prepare(&s_features, &job->features, &prepared);
        job->features = prepared;
    } else if (job->kind == SAVE_THRESHOLDS) {
        changed = job->thresholds.first != s_thresholds.first || job->thresholds.final != s_thresholds.final;
    } else if (job->kind == SAVE_DINO) {
        changed = job->dino_best > s_dino_saved_best;
    }
    if (!changed) {
        job->result = ESP_OK;
        finish_job(job);
        return;
    }
    strcpy(s_notice, "Saving...");
    if (xQueueSend(s_saves, &job, 0) != pdTRUE) {
        job->result = ESP_ERR_TIMEOUT;
        finish_job(job);
    }
}

static void on_key(bsp_btn_t key, bsp_btn_ev_t event, void *unused) {
    (void)unused;
    if (event != BSP_BTN_PRESS && event != BSP_BTN_RELEASE) return;
    app_event_t message = {.type = EVENT_KEY, .key = key, .pressed = event == BSP_BTN_PRESS,
                           .generation = generation(), .stamp_ms = now_ms()};
    if (!send_event(&message)) {
        taskENTER_CRITICAL(&s_lock);
        s_status.input_dropped++;
        s_input_lost = true;
        taskEXIT_CRITICAL(&s_lock);
    }
}

static void apply_screen(bool on) {
    bsp_display_backlight(on ? 75 : 0);
    /* Preserve the Dino run and current page; the wake gesture is consumed by
     * the global classifier. No game simulation runs while the screen is off. */
    if (bsp_lvgl_lock(1000)) {
        passport_dino_suspend();
        collect_dino_best();
        if (s_flash) lv_obj_delete(s_flash);
        s_flash = NULL;
        s_transform_start = 0;
        passport_avatar_set_stage(s_badge.stage);
        if (s_avatar_image) update_avatar_image();
        bsp_lvgl_unlock();
    }
    taskENTER_CRITICAL(&s_lock);
    s_generation++;
    taskEXIT_CRITICAL(&s_lock);
}

static void handle_input(passport_input_result_t input) {
    if (input.action == PASSPORT_INPUT_NONE) return;
    if (input.action == PASSPORT_INPUT_WAKE || input.action == PASSPORT_INPUT_OFF) {
        apply_screen(input.action == PASSPORT_INPUT_WAKE);
        return;
    }
    if (input.generation != generation()) return;
    if (s_pair_visible) {
        if (input.key == BSP_BTN_OK && input.action == PASSPORT_INPUT_LONG)
            passport_ble_open_pairing(0);
        return;
    }
    if (input.key == BSP_BTN_OK && input.action == PASSPORT_INPUT_LONG) {
        s_page = s_layer == LAYER_HOME ? PAGE_BADGE : s_return_page;
        if (!page_visible(s_page)) s_page = PAGE_BADGE;
        s_layer = LAYER_HOME;
        rebuild();
        return;
    }
    if (s_layer == LAYER_HOME) {
        if (s_page == PAGE_DINO) {
            dino_game_t game;
            passport_dino_get_status(&game);
            if (input.key == BSP_BTN_OK) {
                if (bsp_lvgl_lock(100)) {
                    passport_dino_action(input.action == PASSPORT_INPUT_DOUBLE ? BSP_BTN_DOUBLE : BSP_BTN_CLICK);
                    collect_dino_best();
                    bsp_lvgl_unlock();
                }
                return;
            }
            /* Before starting, UP/DOWN browse pages. Once a run has started,
             * its raw keys own jumping and crouching; long OK still goes home. */
            if (game.mode != DINO_READY) return;
        }
        if (input.key == BSP_BTN_UP || input.key == BSP_BTN_DOWN) {
            if (input.action == PASSPORT_INPUT_LONG) {
                s_return_page = s_page;
                s_layer = input.key == BSP_BTN_UP ? LAYER_MODES : LAYER_FEATURES;
                s_selection = 0;
            } else {
                int count = input.action == PASSPORT_INPUT_DOUBLE ? 2 : 1;
                int step = input.key == BSP_BTN_UP ? -1 : 1;
                while (count--) {
                    do { s_page = (page_t)((s_page + step + PAGE_COUNT) % PAGE_COUNT); } while (!page_visible(s_page));
                }
            }
            rebuild();
        } else if (input.key == BSP_BTN_OK) {
            s_return_page = s_page;
            if (s_page == PAGE_CODEX || s_page == PAGE_CURSOR || s_page == PAGE_GEMINI || s_page == PAGE_AGENTS) return;
            s_layer = s_page == PAGE_SETTINGS ?
                (input.action == PASSPORT_INPUT_DOUBLE ? LAYER_FEATURES : LAYER_SETTINGS) : LAYER_DETAIL;
            s_selection = 0;
            rebuild();
        }
        return;
    }
    if (s_layer == LAYER_SETTINGS || s_layer == LAYER_MODES) {
        int count = 6;
        if (s_layer == LAYER_MODES) {
            count = 0;
            for (page_t page = PAGE_BADGE; page < PAGE_COUNT; page++) if (page_visible(page)) count++;
        }
        if (input.key != BSP_BTN_OK) {
            int steps = input.action == PASSPORT_INPUT_LONG ? 4 : input.action == PASSPORT_INPUT_DOUBLE ? 2 : 1;
            s_selection += (input.key == BSP_BTN_UP ? -steps : steps);
            if (s_selection < 0) s_selection = 0;
            if (s_selection >= count) s_selection = count - 1;
            rebuild();
        } else if (input.action == PASSPORT_INPUT_CLICK) {
            if (s_layer == LAYER_MODES) {
                int row = 0;
                for (page_t page = PAGE_BADGE; page <= PAGE_SETTINGS; page++) {
                    if (page_visible(page) && row++ == s_selection) { s_page = page; break; }
                }
                s_layer = LAYER_HOME;
            } else if (s_selection == 0) {
                passport_input_set_screen(&s_input, false, now_ms());
                apply_screen(false);
                return;
            } else if (s_selection == 2) s_layer = LAYER_FEATURES;
            else if (s_selection == 5) s_layer = LAYER_HOME;
            else if (s_selection == 4) {
                passport_ble_open_pairing(120000);
                s_layer = LAYER_DETAIL;
            }
            else s_layer = LAYER_DETAIL;
            rebuild();
        }
    }
}

static void app_worker(void *unused) {
    (void)unused;
    int64_t last_battery = -5000, last_ui = -1000;
    rebuild();
    for (;;) {
        app_event_t event;
        bool received = xQueueReceive(s_events, &event, pdMS_TO_TICKS(20)) == pdTRUE;
        taskENTER_CRITICAL(&s_lock);
        bool lost = s_input_lost;
        s_input_lost = false;
        taskEXIT_CRITICAL(&s_lock);
        if (lost) {
            passport_input_cancel(&s_input, now_ms());
            s_dino_input_lost = true;
        }
        if (received) {
            if (event.type == EVENT_KEY) {
                handle_input(passport_input_tick(&s_input, generation(), event.stamp_ms));
                bool game_key = s_input.screen_on && !s_input.barrier && event.generation == generation() &&
                    !s_pair_visible && s_layer == LAYER_HOME && s_page == PAGE_DINO && event.key != BSP_BTN_OK;
                handle_input(passport_input_raw(&s_input, event.key, event.pressed, event.generation, event.stamp_ms));
                if (game_key && s_input.screen_on && !s_input.barrier && event.generation == generation()) {
                    if (bsp_lvgl_lock(100)) {
                        passport_dino_raw_key(event.key, event.pressed ? BSP_BTN_PRESS : BSP_BTN_RELEASE);
                        bsp_lvgl_unlock();
                    } else s_dino_input_lost = true;
                }
            } else if (event.type == EVENT_TIME) {
                taskENTER_CRITICAL(&s_lock);
                passport_clock_set(&s_clock, event.utc_ms, event.offset_min, event.stamp_ms);
                taskEXIT_CRITICAL(&s_lock);
                last_ui = -1000;
            } else if (event.type == EVENT_SCREEN) {
                passport_input_set_screen(&s_input, event.screen_on, now_ms());
                apply_screen(event.screen_on);
            } else if (event.type == EVENT_PAIR_DISPLAY) {
                bool was_visible = s_pair_visible;
                s_pair_visible = event.screen_on;
                s_pair_code = event.screen_on ? event.passkey : 0;
                s_pair_overlay_dirty = true;
                if (event.screen_on) {
                    passport_input_set_screen(&s_input, true, now_ms());
                    apply_screen(true);
                } else if (was_visible) {
                    /* Consume any key still held as the modal closes. */
                    passport_input_set_screen(&s_input, s_input.screen_on, now_ms());
                    taskENTER_CRITICAL(&s_lock);
                    s_generation++;
                    taskEXIT_CRITICAL(&s_lock);
                }
                if (bsp_lvgl_lock(1000)) { update_pairing_overlay(); bsp_lvgl_unlock(); }
            } else if (event.type == EVENT_PAIR_REQUEST) {
                passport_input_set_screen(&s_input, true, now_ms());
                apply_screen(true);
                passport_ble_open_pairing(120000);
            } else if (event.type == EVENT_PROVIDER) {
                taskENTER_CRITICAL(&s_lock);
                s_providers[event.provider->provider] = *event.provider;
                taskEXIT_CRITICAL(&s_lock);
                free(event.provider);
                last_ui = -1000;
            } else if (event.type == EVENT_CODEX) {
                taskENTER_CRITICAL(&s_lock);
                s_codex = event.codex;
                taskEXIT_CRITICAL(&s_lock);
                last_ui = -1000;
            } else if (event.type == EVENT_CURSOR) {
                taskENTER_CRITICAL(&s_lock);
                s_cursor = *event.cursor;
                taskEXIT_CRITICAL(&s_lock);
                free(event.cursor);
                last_ui = -1000;
            } else if (event.type == EVENT_SAVE) handle_save(event.job);
            else if (event.type == EVENT_SAVED) finish_job(event.job);
            else if (event.type == EVENT_ARTWORK) rebuild();
        }
        int64_t stamp = now_ms();
        if (s_dino_input_lost && bsp_lvgl_lock(100)) {
            passport_dino_suspend();
            collect_dino_best();
            s_dino_input_lost = false;
            bsp_lvgl_unlock();
        }
        if (s_pair_overlay_dirty && bsp_lvgl_lock(100)) { update_pairing_overlay(); bsp_lvgl_unlock(); }
        if (s_input.screen_on && !s_pair_visible && !s_dino_input_lost && s_layer == LAYER_HOME && s_page == PAGE_DINO && bsp_lvgl_lock(100)) {
            passport_dino_tick(stamp);
            collect_dino_best();
            bsp_lvgl_unlock();
        }
        if (s_dino_best > s_dino_saved_best && stamp >= s_dino_save_retry) {
            save_job_t *job = calloc(1, sizeof(*job));
            if (job) {
                job->kind = SAVE_DINO;
                job->dino_best = s_dino_best;
                submit_job(job);
            }
            s_dino_save_retry = stamp + 5000;
        }
        /* Drain timestamped releases before classifying a hold against now;
         * otherwise a short click queued during a slow redraw could become LONG. */
        if (uxQueueMessagesWaiting(s_events) == 0)
            handle_input(passport_input_tick(&s_input, generation(), stamp));
        if (stamp - last_battery >= 5000) {
            /* I2C work never holds the LVGL lock and never runs in callbacks. */
            s_battery_soc = bsp_battery_soc();
            s_battery_mv = bsp_battery_mv();
            last_battery = stamp;
            last_ui = -1000;
        }
        if (s_badge.tokens_known && !s_badge.tokens_stale && stamp - s_token_updated_ms >= TOKEN_TTL_MS) {
            taskENTER_CRITICAL(&s_lock);
            s_badge.tokens_stale = true;
            taskEXIT_CRITICAL(&s_lock);
            last_ui = -1000;
        }
        if (stamp - last_ui >= 1000 && bsp_lvgl_lock(100)) {
            update_status_ui();
            bsp_lvgl_unlock();
            last_ui = stamp;
        }
        if (s_transform_start && bsp_lvgl_lock(100)) {
            int64_t elapsed = stamp - s_transform_start;
            if (elapsed >= 350 && !s_transform_swapped) {
                passport_avatar_set_stage(s_badge.stage);
                update_avatar_image();
                s_transform_swapped = true;
            }
            if (elapsed >= 1200) {
                lv_obj_delete(s_flash);
                s_flash = NULL;
                s_transform_start = 0;
            } else {
                int opacity = elapsed < 350 ? (int)(elapsed * 255 / 350) : (int)((1200 - elapsed) * 255 / 850);
                lv_obj_set_style_opa(s_flash, (lv_opa_t)opacity, 0);
            }
            bsp_lvgl_unlock();
        }
        publish_status();
    }
}

bool passport_start(void) {
    if (s_events) return false;
    for (unsigned i = 0; i < PASSPORT_PROVIDER_COUNT; ++i)
        passport_provider_snapshot_init(&s_providers[i], (passport_provider_id_t)i);
    load_config();
    if (bsp_lvgl_lock(1000)) { passport_avatar_init(); bsp_lvgl_unlock(); }
    passport_input_init(&s_input);
    s_status.battery_soc = s_status.battery_mv = -1;
    s_status.screen_on = true;
    bsp_battery_init_readonly();
    s_events = xQueueCreate(24, sizeof(app_event_t));
    s_saves = xQueueCreate(1, sizeof(save_job_t *));
    if (!s_events || !s_saves) {
        if (s_events) vQueueDelete(s_events);
        if (s_saves) vQueueDelete(s_saves);
        s_events = s_saves = NULL;
        return false;
    }
    TaskHandle_t save_task;
    if (xTaskCreate(save_worker, "passport_nvs", 3072, NULL, 3, &save_task) != pdPASS) {
        vQueueDelete(s_events);
        vQueueDelete(s_saves);
        s_events = s_saves = NULL;
        return false;
    }
    if (xTaskCreate(app_worker, "passport_ui", 6144, NULL, 4, NULL) != pdPASS) {
        vTaskDelete(save_task);
        vQueueDelete(s_events);
        vQueueDelete(s_saves);
        s_events = s_saves = NULL;
        return false;
    }
    esp_err_t buttons = bsp_button_init(on_key, NULL);
    if (buttons != ESP_OK) ESP_LOGW(TAG, "Buttons unavailable: %s", esp_err_to_name(buttons));
    bsp_display_backlight(75);
    ESP_LOGI(TAG, "Badge ready. Raw key classifier: long=%dms gap=%dms; OK x3 screen off.",
             PASSPORT_LONG_MS, PASSPORT_CLICK_GAP_MS);
    return true;
}
