#include "passport_claude_view.h"
#include "passport_assets.h"
#include "passport_layout.h"
#include <inttypes.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

static struct {
    lv_obj_t *root, *tokens[2], *token_state, *quota[2], *quota_state[2], *reset[2], *fill[2], *source;
} s_view;

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

static lv_obj_t *label(lv_obj_t *parent, int x, int y, int width, int height,
                       const lv_font_t *font, uint32_t color, const char *text) {
    lv_obj_t *obj = lv_label_create(parent);
    lv_obj_set_pos(obj, x, y);
    lv_obj_set_size(obj, width, height);
    lv_obj_set_style_text_font(obj, font, 0);
    lv_obj_set_style_text_color(obj, lv_color_hex(color), 0);
    lv_obj_set_style_text_letter_space(obj, 0, 0);
    lv_label_set_long_mode(obj, LV_LABEL_LONG_DOT);
    lv_label_set_text(obj, text);
    return obj;
}

static void text(lv_obj_t *obj, const char *value) {
    if (strcmp(lv_label_get_text(obj), value)) lv_label_set_text(obj, value);
}

static void deleted(lv_event_t *event) {
    if (lv_event_get_target(event) == s_view.root) memset(&s_view, 0, sizeof(s_view));
}

bool passport_claude_view_create(lv_obj_t *screen) {
    if (!screen || s_view.root) return false;
    s_view.root = rect(screen, 0, 42, 240, 278, PASSPORT_LAYOUT_COLOR_BACKGROUND, 0);
    lv_obj_add_event_cb(s_view.root, deleted, LV_EVENT_DELETE, NULL);
    label(s_view.root, 14, 0, 212, 25, &passport_font_20, PASSPORT_LAYOUT_COLOR_PRIMARY, "Claude Code");
    lv_obj_t *tokens = rect(s_view.root, 14, 31, 212, 77, PASSPORT_LAYOUT_COLOR_PANEL, 10);
    label(tokens, 10, 5, 95, 18, &passport_font_14, PASSPORT_LAYOUT_COLOR_SECONDARY, "Day Tokens");
    label(tokens, 115, 5, 87, 18, &passport_font_14, PASSPORT_LAYOUT_COLOR_SECONDARY, "History Token");
    for (unsigned i = 0; i < 2; ++i)
        s_view.tokens[i] = label(tokens, 10 + (int)i * 105, 26, 97, 25,
                                &passport_font_20, PASSPORT_LAYOUT_COLOR_PRIMARY, "--");
    s_view.token_state = label(tokens, 10, 55, 192, 18, &passport_font_14,
                               PASSPORT_LAYOUT_COLOR_SECONDARY, "Unknown");
    for (unsigned i = 0; i < 2; ++i) {
        lv_obj_t *card = rect(s_view.root, 14, 116 + (int)i * 72, 212, 64, PASSPORT_LAYOUT_COLOR_PANEL, 10);
        label(card, 10, 4, 104, 18, &passport_font_14, PASSPORT_LAYOUT_COLOR_SECONDARY,
              i == 0 ? "5h quota used" : "7d quota used");
        s_view.quota_state[i] = label(card, 145, 4, 57, 18, &passport_font_14,
                                     PASSPORT_LAYOUT_COLOR_SECONDARY, "Unknown");
        s_view.quota[i] = label(card, 10, 24, 95, 25, &passport_font_20,
                               PASSPORT_LAYOUT_COLOR_PRIMARY, "--");
        s_view.reset[i] = label(card, 105, 31, 97, 18, &passport_font_14,
                               PASSPORT_LAYOUT_COLOR_SECONDARY, "Reset --");
        lv_obj_t *rail = rect(card, 10, 56, 192, 3, PASSPORT_LAYOUT_COLOR_SEPARATOR, 2);
        s_view.fill[i] = rect(rail, 0, 0, 1, 3, PASSPORT_LAYOUT_COLOR_ACCENT, 2);
    }
    s_view.source = label(s_view.root, 14, 259, 212, 18, &passport_font_14,
                           PASSPORT_LAYOUT_COLOR_SECONDARY, "Claude Code / this Mac");
    passport_claude_view_update(NULL, 0);
    return true;
}

static void token_text(char out[40], uint64_t value) {
    if (value >= UINT64_C(1000000000000000)) snprintf(out, 40, "%.2fP", (double)value / 1000000000000000.0);
    else if (value >= UINT64_C(1000000000000)) snprintf(out, 40, "%.2fT", (double)value / 1000000000000.0);
    else if (value >= UINT64_C(1000000000)) snprintf(out, 40, "%.2fB", (double)value / 1000000000.0);
    else if (value >= UINT64_C(1000000)) snprintf(out, 40, "%.2fM", (double)value / 1000000.0);
    else if (value >= UINT64_C(10000)) snprintf(out, 40, "%.1fK", (double)value / 1000.0);
    else snprintf(out, 40, "%" PRIu64, value);
}

static void observation_time(int64_t utc_ms, char out[18]) {
    time_t seconds = (time_t)(utc_ms / 1000);
    struct tm date;
    if (!gmtime_r(&seconds, &date) || !strftime(out, 18, "%m-%d %H:%MZ", &date))
        strcpy(out, "Time unknown");
}

void passport_claude_view_update(const passport_claude_snapshot_t *s, int64_t now) {
    if (!s_view.root) return;
    char buffer[40];
    bool tokens_known = false;
    bool tokens_fresh = passport_claude_tokens_fresh(s, now);
    for (unsigned i = 0; i < 2; ++i) {
        bool known = passport_claude_tokens_display_known(s, i);
        tokens_known |= known;
        if (known) token_text(buffer, i == 0 ? s->today_tokens : s->lifetime_tokens);
        else strcpy(buffer, "--");
        text(s_view.tokens[i], buffer);
    }
    char observed[18];
    if (tokens_known) {
        observation_time(s->observed_utc_ms, observed);
        snprintf(buffer, sizeof(buffer), "%s %s", !tokens_fresh ? "Last" :
                 s->tokens_complete ? "Read" : "Partial", observed);
    } else strcpy(buffer, "Unknown");
    text(s_view.token_state, buffer);
    for (unsigned i = 0; i < 2; ++i) {
        bool known = passport_claude_quota_display_known(s, i);
        bool fresh = passport_claude_quota_fresh(s, i, now);
        if (known) snprintf(buffer, sizeof(buffer), "%.1f%%", (double)s->quota_used_percent[i]);
        else strcpy(buffer, "--");
        text(s_view.quota[i], buffer);
        text(s_view.quota_state[i], known ? fresh ? "Fresh" : "Last" : "Unknown");
        unsigned width = known ? (unsigned)(192.0 * s->quota_used_percent[i] / 100.0 + 0.5) : 0;
        if (known && s->quota_used_percent[i] > 0 && width == 0) width = 1;
        lv_obj_set_width(s_view.fill[i], width ? (int)width : 1);
        if (width) lv_obj_remove_flag(s_view.fill[i], LV_OBJ_FLAG_HIDDEN);
        else lv_obj_add_flag(s_view.fill[i], LV_OBJ_FLAG_HIDDEN);
        lv_obj_set_style_bg_color(s_view.fill[i], lv_color_hex(fresh ?
            PASSPORT_LAYOUT_COLOR_ACCENT : PASSPORT_LAYOUT_COLOR_SECONDARY), 0);
        if (fresh && s->quota_reset_ms[i] > now) {
            uint64_t minutes = (uint64_t)((s->quota_reset_ms[i] - now + 59999) / 60000);
            if (minutes >= 1440) snprintf(buffer, sizeof(buffer), "Reset %" PRIu64 "d %" PRIu64 "h", minutes / 1440, minutes % 1440 / 60);
            else snprintf(buffer, sizeof(buffer), "Reset %" PRIu64 "h %" PRIu64 "m", minutes / 60, minutes % 60);
        } else strcpy(buffer, "Reset --");
        text(s_view.reset[i], buffer);
    }
    if (passport_claude_snapshot_valid(s) && s->quota_observed_utc_ms > 0) {
        observation_time(s->quota_observed_utc_ms, observed);
        snprintf(buffer, sizeof(buffer), "Quota read %s", observed);
    } else strcpy(buffer, "Claude Code / this Mac");
    text(s_view.source, buffer);
}

void passport_claude_view_destroy(void) {
    if (s_view.root) lv_obj_delete(s_view.root);
    memset(&s_view, 0, sizeof(s_view));
}
