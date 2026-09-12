#include "passport_cursor_view.h"
#include "passport_assets.h"
#include "passport_layout.h"
#include <inttypes.h>
#include <stdio.h>
#include <string.h>

static struct {
    lv_obj_t *root, *plan, *reset, *state, *age;
    lv_obj_t *value[2], *remaining[2], *fill[2];
} s_view;

static void show(lv_obj_t *obj, bool visible) {
    if (visible) lv_obj_remove_flag(obj, LV_OBJ_FLAG_HIDDEN);
    else lv_obj_add_flag(obj, LV_OBJ_FLAG_HIDDEN);
}

static void text(lv_obj_t *obj, const char *value) {
    if (strcmp(lv_label_get_text(obj), value)) lv_label_set_text(obj, value);
}

static lv_obj_t *rect(lv_obj_t *parent, int x, int y, int w, int h, uint32_t color, int radius) {
    lv_obj_t *obj = lv_obj_create(parent);
    lv_obj_remove_style_all(obj);
    lv_obj_remove_flag(obj, LV_OBJ_FLAG_SCROLLABLE | LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_pos(obj, x, y);
    lv_obj_set_size(obj, w, h);
    lv_obj_set_style_bg_color(obj, lv_color_hex(color), 0);
    lv_obj_set_style_bg_opa(obj, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(obj, radius, 0);
    return obj;
}

static lv_obj_t *label(lv_obj_t *parent, int x, int y, int w, int h,
                        const lv_font_t *font, uint32_t color, lv_text_align_t align, const char *value) {
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

static void deleted(lv_event_t *event) {
    if (lv_event_get_target(event) == s_view.root) memset(&s_view, 0, sizeof(s_view));
}

bool passport_cursor_view_create(lv_obj_t *screen) {
    if (!screen || s_view.root) return false;
    s_view.root = rect(screen, 0, 42, 240, 278, PASSPORT_LAYOUT_COLOR_BACKGROUND, 0);
    lv_obj_add_event_cb(s_view.root, deleted, LV_EVENT_DELETE, NULL);
    label(s_view.root, 14, 0, 116, 25, &passport_font_20, PASSPORT_LAYOUT_COLOR_PRIMARY,
          LV_TEXT_ALIGN_LEFT, "Cursor");
    s_view.plan = label(s_view.root, 130, 5, 96, 18, &passport_font_14,
                        PASSPORT_LAYOUT_COLOR_ACCENT, LV_TEXT_ALIGN_RIGHT, "--");
    for (unsigned i = 0; i < 2; ++i) {
        lv_obj_t *card = rect(s_view.root, 14, 33 + (int)i * 89, 212, 81, PASSPORT_LAYOUT_COLOR_PANEL, 10);
        label(card, 10, 7, 145, 18, &passport_font_14, PASSPORT_LAYOUT_COLOR_SECONDARY,
              LV_TEXT_ALIGN_LEFT, i == 0 ? "Cursor 模型" : "其他模型");
        label(card, 162, 7, 40, 18, &passport_font_14, PASSPORT_LAYOUT_COLOR_SECONDARY,
              LV_TEXT_ALIGN_RIGHT, "已用");
        s_view.value[i] = label(card, 10, 29, 107, 25, &passport_font_20,
                                PASSPORT_LAYOUT_COLOR_PRIMARY, LV_TEXT_ALIGN_LEFT, "--");
        s_view.remaining[i] = label(card, 117, 34, 85, 18, &passport_font_14,
                                    PASSPORT_LAYOUT_COLOR_SECONDARY, LV_TEXT_ALIGN_RIGHT, "剩余 --");
        lv_obj_t *rail = rect(card, 10, 67, 192, 4, PASSPORT_LAYOUT_COLOR_SEPARATOR, 2);
        s_view.fill[i] = rect(rail, 0, 0, 192, 4,
                              i == 0 ? PASSPORT_LAYOUT_COLOR_ACCENT : PASSPORT_LAYOUT_COLOR_SECONDARY, 2);
    }
    s_view.reset = label(s_view.root, 14, 211, 212, 18, &passport_font_14,
                         PASSPORT_LAYOUT_COLOR_SECONDARY, LV_TEXT_ALIGN_LEFT, "重置未知");
    s_view.state = label(s_view.root, 14, 234, 212, 18, &passport_font_14,
                         PASSPORT_LAYOUT_COLOR_SECONDARY, LV_TEXT_ALIGN_LEFT, "请在 Mac 中同步");
    rect(s_view.root, 14, 257, 212, 1, PASSPORT_LAYOUT_COLOR_SEPARATOR, 0);
    label(s_view.root, 14, 259, 130, 18, &passport_font_14,
          PASSPORT_LAYOUT_COLOR_SECONDARY, LV_TEXT_ALIGN_LEFT, "Cursor App");
    s_view.age = label(s_view.root, 144, 259, 82, 18, &passport_font_14,
                       PASSPORT_LAYOUT_COLOR_SECONDARY, LV_TEXT_ALIGN_RIGHT, "未同步");
    passport_cursor_view_update(NULL, 0);
    return true;
}

void passport_cursor_view_update(const passport_cursor_snapshot_t *s, int64_t now) {
    if (!s_view.root) return;
    const bool valid = passport_cursor_snapshot_valid(s);
    const bool observed = valid && s->observed_utc_ms > 0;
    const bool clock_ok = observed && now >= s->observed_utc_ms;
    const bool expired = clock_ok && (now >= s->expires_utc_ms ||
                                      (s->reset_at_ms > 0 && now >= s->reset_at_ms));
    const bool fresh = passport_cursor_snapshot_fresh(s, now);
    text(s_view.plan, observed && !expired && s->plan[0] ? s->plan : "--");
    char buffer[80];
    for (unsigned i = 0; i < 2; ++i) {
        const bool known = fresh && s->used_known[i];
        unsigned width = 0;
        if (known) {
            snprintf(buffer, sizeof(buffer), "%.1f%%", (double)s->used_percent[i]);
            width = (unsigned)(192.0 * s->used_percent[i] / 100.0 + 0.5);
            if (s->used_percent[i] > 0 && width == 0) width = 1;
        } else strcpy(buffer, "--");
        text(s_view.value[i], buffer);
        if (known) snprintf(buffer, sizeof(buffer), "剩余 %.1f%%", 100.0 - (double)s->used_percent[i]);
        else strcpy(buffer, "剩余 --");
        text(s_view.remaining[i], buffer);
        show(s_view.fill[i], width > 0);
        lv_obj_set_width(s_view.fill[i], width > 0 ? (int)width : 1);
    }
    if (clock_ok && !expired && s->reset_at_ms > now) {
        uint64_t minutes = (uint64_t)((s->reset_at_ms - now + 59999) / 60000);
        if (minutes >= 1440) snprintf(buffer, sizeof(buffer), "重置 %" PRIu64 "d %" PRIu64 "h", minutes / 1440, minutes % 1440 / 60);
        else snprintf(buffer, sizeof(buffer), "重置 %" PRIu64 "h %" PRIu64 "m", minutes / 60, minutes % 60);
    } else snprintf(buffer, sizeof(buffer), "%s", expired ? "等待同步" : "重置未知");
    text(s_view.reset, buffer);
    text(s_view.state, fresh ? "套餐用量已更新" : expired ? "额度已过期，请同步" :
         observed && !clock_ok ? "时间未知，请同步" : observed ? "额度未知" : "请在 Mac 中同步");
    lv_obj_set_style_text_color(s_view.state, lv_color_hex(fresh ? PASSPORT_LAYOUT_COLOR_ACCENT :
        expired ? PASSPORT_LAYOUT_COLOR_WARNING : PASSPORT_LAYOUT_COLOR_SECONDARY), 0);
    if (clock_ok) {
        uint64_t seconds = (uint64_t)((now - s->observed_utc_ms) / 1000);
        if (seconds < 60) snprintf(buffer, sizeof(buffer), "%" PRIu64 "s", seconds);
        else if (seconds < 3600) snprintf(buffer, sizeof(buffer), "%" PRIu64 "m", seconds / 60);
        else snprintf(buffer, sizeof(buffer), "%" PRIu64 "h", seconds / 3600);
    } else strcpy(buffer, "未同步");
    text(s_view.age, buffer);
}

void passport_cursor_view_destroy(void) {
    if (s_view.root) lv_obj_delete(s_view.root);
    memset(&s_view, 0, sizeof(s_view));
}
