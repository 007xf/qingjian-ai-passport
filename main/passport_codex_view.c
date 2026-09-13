#include "passport_codex_view.h"
#include "passport_assets.h"
#include "passport_layout.h"
#include <inttypes.h>
#include <stdio.h>
#include <string.h>

typedef struct {
    lv_obj_t *panel;
    lv_obj_t *duration;
    lv_obj_t *value;
    lv_obj_t *reset;
    lv_obj_t *rail;
    lv_obj_t *fill;
} quota_card_t;

static struct {
    lv_obj_t *root;
    lv_obj_t *state;
    lv_obj_t *empty_title;
    lv_obj_t *empty_help;
    lv_obj_t *tokens;
    lv_obj_t *source;
    lv_obj_t *age;
    quota_card_t cards[2];
} s_view;

static void show(lv_obj_t *obj, bool visible) {
    if (visible) lv_obj_remove_flag(obj, LV_OBJ_FLAG_HIDDEN);
    else lv_obj_add_flag(obj, LV_OBJ_FLAG_HIDDEN);
}

static void text(lv_obj_t *label, const char *value) {
    if (strcmp(lv_label_get_text(label), value) != 0) lv_label_set_text(label, value);
}

static lv_obj_t *label(lv_obj_t *parent, int x, int y, int w, int h,
                       const lv_font_t *font, uint32_t color,
                       lv_text_align_t align, const char *value) {
    lv_obj_t *obj = lv_label_create(parent);
    lv_obj_set_pos(obj, x, y);
    lv_obj_set_size(obj, w, h);
    lv_obj_set_style_text_font(obj, font, 0);
    lv_obj_set_style_text_color(obj, lv_color_hex(color), 0);
    lv_obj_set_style_text_align(obj, align, 0);
    lv_obj_set_style_text_letter_space(obj, 0, 0);
    lv_label_set_long_mode(obj, LV_LABEL_LONG_DOT);
    lv_label_set_text(obj, value);
    return obj;
}

static lv_obj_t *rect(lv_obj_t *parent, int x, int y, int w, int h,
                      uint32_t color, int radius) {
    lv_obj_t *obj = lv_obj_create(parent);
    lv_obj_remove_style_all(obj);
    lv_obj_remove_flag(obj, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_remove_flag(obj, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_pos(obj, x, y);
    lv_obj_set_size(obj, w, h);
    lv_obj_set_style_bg_color(obj, lv_color_hex(color), 0);
    lv_obj_set_style_bg_opa(obj, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(obj, radius, 0);
    return obj;
}

static void deleted(lv_event_t *event) {
    if (lv_event_get_target(event) == s_view.root) memset(&s_view, 0, sizeof(s_view));
}

bool passport_codex_view_create(lv_obj_t *screen) {
    if (!screen || s_view.root) return false;
    s_view.root = rect(screen, 0, 42, 240, 278, PASSPORT_LAYOUT_COLOR_BACKGROUND, 0);
    lv_obj_add_event_cb(s_view.root, deleted, LV_EVENT_DELETE, NULL);
    label(s_view.root, 14, 0, 108, 25, &passport_font_20,
          PASSPORT_LAYOUT_COLOR_PRIMARY, LV_TEXT_ALIGN_LEFT, "Codex");
    s_view.state = label(s_view.root, 126, 5, 100, 18, &passport_font_14,
                         PASSPORT_LAYOUT_COLOR_SECONDARY, LV_TEXT_ALIGN_RIGHT, "未同步");

    for (unsigned i = 0; i < 2; ++i) {
        quota_card_t *card = &s_view.cards[i];
        card->panel = rect(s_view.root, 14, 32 + (int)i * 84, 212, 76,
                           PASSPORT_LAYOUT_COLOR_PANEL, 10);
        card->duration = label(card->panel, 10, 7, 145, 18, &passport_font_14,
                               PASSPORT_LAYOUT_COLOR_SECONDARY, LV_TEXT_ALIGN_LEFT, "时长未知");
        label(card->panel, 159, 7, 43, 18, &passport_font_14,
              PASSPORT_LAYOUT_COLOR_SECONDARY, LV_TEXT_ALIGN_RIGHT, "剩余");
        card->value = label(card->panel, 10, 28, 83, 25, &passport_font_20,
                            PASSPORT_LAYOUT_COLOR_PRIMARY, LV_TEXT_ALIGN_LEFT, "--");
        card->reset = label(card->panel, 93, 33, 109, 18, &passport_font_14,
                            PASSPORT_LAYOUT_COLOR_SECONDARY, LV_TEXT_ALIGN_RIGHT, "重置未知");
        card->rail = rect(card->panel, 10, 63, 192, 4, PASSPORT_LAYOUT_COLOR_SEPARATOR, 2);
        card->fill = rect(card->rail, 0, 0, 192, 4, PASSPORT_LAYOUT_COLOR_ACCENT, 2);
    }
    s_view.empty_title = label(s_view.root, 14, 60, 212, 25, &passport_font_20,
                               PASSPORT_LAYOUT_COLOR_PRIMARY, LV_TEXT_ALIGN_CENTER, "额度未知");
    s_view.empty_help = label(s_view.root, 14, 99, 212, 40, &passport_font_14,
                              PASSPORT_LAYOUT_COLOR_SECONDARY, LV_TEXT_ALIGN_CENTER,
                              "尚未收到额度窗口\n请在 Mac 中同步");
    label(s_view.root, 14, 204, 212, 18, &passport_font_14,
          PASSPORT_LAYOUT_COLOR_SECONDARY, LV_TEXT_ALIGN_LEFT, "本周期 TOKEN");
    s_view.tokens = label(s_view.root, 14, 226, 212, 16, &lv_font_montserrat_14,
                          PASSPORT_LAYOUT_COLOR_PRIMARY, LV_TEXT_ALIGN_LEFT, "--");
    rect(s_view.root, 14, 251, 212, 1, PASSPORT_LAYOUT_COLOR_SEPARATOR, 0);
    s_view.source = label(s_view.root, 14, 258, 144, 18, &passport_font_14,
                          PASSPORT_LAYOUT_COLOR_SECONDARY, LV_TEXT_ALIGN_LEFT, "来源：未知");
    s_view.age = label(s_view.root, 158, 258, 68, 18, &passport_font_14,
                       PASSPORT_LAYOUT_COLOR_SECONDARY, LV_TEXT_ALIGN_RIGHT, "未同步");
    passport_codex_view_update(NULL, 0, false, 0);
    return true;
}

void passport_codex_view_destroy(void) {
    if (s_view.root) lv_obj_delete(s_view.root);
    memset(&s_view, 0, sizeof(s_view));
}

static bool window_present(const passport_quota_window_t *window) {
    return window->used_known || window->duration_min > 0 || window->reset_s > 0;
}

static void duration_text(char *out, size_t size, int minutes) {
    if (minutes <= 0) snprintf(out, size, "时长未知");
    else if (minutes % 1440 == 0) snprintf(out, size, "%d 天窗口", minutes / 1440);
    else if (minutes % 60 == 0) snprintf(out, size, "%d 小时窗口", minutes / 60);
    else snprintf(out, size, "%d 分钟窗口", minutes);
}

static void reset_text(char *out, size_t size, int64_t reset_s, int64_t now) {
    if (reset_s <= 0 || now <= 0) snprintf(out, size, "重置未知");
    else if (now >= reset_s * INT64_C(1000)) snprintf(out, size, "等待刷新");
    else {
        uint64_t seconds = (uint64_t)((reset_s * INT64_C(1000) - now + 999) / 1000);
        if (seconds >= 86400)
            snprintf(out, size, "重置 %" PRIu64 "d%" PRIu64 "h", seconds / 86400, seconds % 86400 / 3600);
        else if (seconds >= 3600)
            snprintf(out, size, "重置 %" PRIu64 "h%" PRIu64 "m", seconds / 3600, seconds % 3600 / 60);
        else if (seconds >= 60)
            snprintf(out, size, "重置 %" PRIu64 "m", (seconds + 59) / 60);
        else snprintf(out, size, "重置 %" PRIu64 "s", seconds);
    }
}

void passport_codex_view_update(const passport_codex_snapshot_t *snapshot,
                                int64_t now_utc_ms, bool tokens_known,
                                uint64_t tokens) {
    if (!s_view.root) return;
    const bool valid = passport_codex_snapshot_valid(snapshot);
    const bool observed = valid && snapshot->observed_utc_ms > 0;
    const bool time_known = now_utc_ms >= INT64_C(1704067200000);
    const bool future = observed && time_known && now_utc_ms < snapshot->observed_utc_ms;
    const bool fresh = passport_codex_snapshot_fresh(snapshot, now_utc_ms);
    bool reset_reached = false;
    if (observed && time_known && !future) {
        for (unsigned i = 0; i < 2; ++i) {
            const passport_quota_window_t *window = &snapshot->window[i];
            if (window->used_known && window->reset_s > 0 &&
                now_utc_ms >= window->reset_s * INT64_C(1000)) reset_reached = true;
        }
    }
    const bool stale = observed && time_known && !future &&
                       (now_utc_ms >= snapshot->expires_utc_ms || reset_reached);
    const bool metadata_fresh = observed && time_known && !future && !stale;
    const char *state = fresh ? "已更新" : stale ? "上次 已过期" :
                        snapshot && !valid ? "数据异常" :
                        observed && (!time_known || future) ? "上次 时间未知" :
                        observed ? "额度未知" : "未同步";
    text(s_view.state, state);
    lv_obj_set_style_text_color(s_view.state,
        lv_color_hex(fresh ? PASSPORT_LAYOUT_COLOR_ACCENT :
                     stale ? PASSPORT_LAYOUT_COLOR_WARNING : PASSPORT_LAYOUT_COLOR_SECONDARY), 0);

    unsigned count = 0;
    char buffer[80];
    if (valid) {
        for (unsigned i = 0; i < 2; ++i) {
            const passport_quota_window_t *window = &snapshot->window[i];
            if (!window_present(window)) continue;
            quota_card_t *card = &s_view.cards[count++];
            show(card->panel, true);
            duration_text(buffer, sizeof(buffer), window->duration_min);
            text(card->duration, buffer);
            const bool known = passport_codex_snapshot_display_known(snapshot, i);
            if (known) {
                const double remaining = 100.0 - (double)window->used_percent;
                const unsigned tenths = (unsigned)(remaining * 10.0 + 0.5);
                if (remaining > 0.0 && tenths == 0) snprintf(buffer, sizeof(buffer), "<0.1%%");
                else snprintf(buffer, sizeof(buffer), "%u.%u%%", tenths / 10, tenths % 10);
                text(card->value, buffer);
                unsigned width = (unsigned)(192.0 * remaining / 100.0 + 0.5);
                if (remaining > 0.0 && width == 0) width = 1;
                show(card->fill, width > 0);
                lv_obj_set_width(card->fill, width > 0 ? (int)width : 1);
            } else text(card->value, "--");
            show(card->rail, known);
            lv_obj_set_style_text_color(card->value,
                lv_color_hex(known ? PASSPORT_LAYOUT_COLOR_PRIMARY : PASSPORT_LAYOUT_COLOR_SECONDARY), 0);
            if (metadata_fresh) reset_text(buffer, sizeof(buffer), window->reset_s, now_utc_ms);
            else snprintf(buffer, sizeof(buffer), "%s", stale ? "等待同步" : "重置未知");
            text(card->reset, buffer);
        }
    }
    for (unsigned i = count; i < 2; ++i) show(s_view.cards[i].panel, false);
    show(s_view.empty_title, count == 0);
    show(s_view.empty_help, count == 0);
    if (tokens_known) snprintf(buffer, sizeof(buffer), "%" PRIu64, tokens);
    else snprintf(buffer, sizeof(buffer), "--");
    text(s_view.tokens, buffer);
    text(s_view.source, valid ? (strcmp(snapshot->source, "official_app_server") == 0 ?
         "来源：官方接口" : "来源：本机日志") : "来源：未知");
    if (!observed) snprintf(buffer, sizeof(buffer), "未同步");
    else if (!time_known || future) snprintf(buffer, sizeof(buffer), "时间未知");
    else {
        uint64_t age = (uint64_t)(now_utc_ms - snapshot->observed_utc_ms) / 1000;
        if (age >= 86400) snprintf(buffer, sizeof(buffer), "%" PRIu64 "d前", age / 86400);
        else if (age >= 3600) snprintf(buffer, sizeof(buffer), "%" PRIu64 "h前", age / 3600);
        else if (age >= 60) snprintf(buffer, sizeof(buffer), "%" PRIu64 "m前", age / 60);
        else snprintf(buffer, sizeof(buffer), "%" PRIu64 "s前", age);
    }
    text(s_view.age, buffer);
}
