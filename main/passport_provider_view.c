#include "passport_provider_view.h"
#include "passport_assets.h"
#include "passport_layout.h"
#include <inttypes.h>
#include <stdio.h>
#include <string.h>

typedef struct {
    lv_obj_t *card;
    lv_obj_t *state;
    lv_obj_t *sessions;
    lv_obj_t *age;
} agent_row_t;
static struct {
    lv_obj_t *root;
    int page;
    lv_obj_t *state;
    lv_obj_t *metric;
    lv_obj_t *model;
    lv_obj_t *activity;
    lv_obj_t *metric_time;
    lv_obj_t *source;
    lv_obj_t *empty;
    agent_row_t rows[PASSPORT_PROVIDER_COUNT];
} s_view;

static lv_obj_t *panel(lv_obj_t *parent, int x, int y, int width, int height, uint32_t color, int radius) {
    lv_obj_t *object = lv_obj_create(parent);
    lv_obj_remove_style_all(object);
    lv_obj_remove_flag(object, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_remove_flag(object, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_pos(object, x, y);
    lv_obj_set_size(object, width, height);
    lv_obj_set_style_bg_opa(object, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(object, lv_color_hex(color), 0);
    lv_obj_set_style_radius(object, radius, 0);
    return object;
}
static lv_obj_t *label(lv_obj_t *parent, int x, int y, int width, int height,
                        const char *value, const lv_font_t *font, uint32_t color, lv_text_align_t align) {
    lv_obj_t *object = lv_label_create(parent);
    lv_obj_set_pos(object, x, y);
    lv_obj_set_size(object, width, height);
    lv_label_set_text(object, value);
    lv_label_set_long_mode(object, LV_LABEL_LONG_DOT);
    lv_obj_set_style_text_font(object, font, 0);
    lv_obj_set_style_text_color(object, lv_color_hex(color), 0);
    lv_obj_set_style_text_align(object, align, 0);
    lv_obj_set_style_text_letter_space(object, 0, 0);
    return object;
}
static void text(lv_obj_t *object, const char *value) {
    if (strcmp(lv_label_get_text(object), value)) lv_label_set_text(object, value);
}
static void deleted(lv_event_t *event) {
    if (lv_event_get_target(event) == s_view.root) memset(&s_view, 0, sizeof(s_view));
}
static bool valid_slot(const passport_provider_snapshot_t *snapshot, int provider) {
    return passport_provider_snapshot_valid(snapshot) && (int)snapshot->provider == provider;
}
static const char *state_label(const passport_provider_snapshot_t *snapshot, int64_t now) {
    if (!snapshot || snapshot->state_at_ms == 0) return "未接入";
    if (now < PASSPORT_PROVIDER_MIN_UTC_MS || now < snapshot->state_at_ms) return "时间未知";
    if (now >= snapshot->state_until_ms) return "状态过期";
    switch (snapshot->state) {
    case PASSPORT_PROVIDER_STATE_WORKING: return "工作中";
    case PASSPORT_PROVIDER_STATE_IDLE: return "空闲";
    case PASSPORT_PROVIDER_STATE_WAITING: return "等待输入";
    case PASSPORT_PROVIDER_STATE_ERROR: return "发生错误";
    default: return "状态未知";
    }
}
static uint32_t state_color(const passport_provider_snapshot_t *snapshot, int64_t now) {
    if (!snapshot || !passport_provider_state_fresh(snapshot, now)) return PASSPORT_LAYOUT_COLOR_SECONDARY;
    return snapshot->state == PASSPORT_PROVIDER_STATE_ERROR || snapshot->state == PASSPORT_PROVIDER_STATE_WAITING ?
           PASSPORT_LAYOUT_COLOR_WARNING : PASSPORT_LAYOUT_COLOR_ACCENT;
}
static void age_text(char *out, size_t size, int64_t at, int64_t now) {
    if (at == 0) snprintf(out, size, "尚无记录");
    else if (now < PASSPORT_PROVIDER_MIN_UTC_MS || now < at) snprintf(out, size, "时间未知");
    else {
        uint64_t seconds = (uint64_t)(now - at) / 1000;
        if (seconds >= 86400) snprintf(out, size, "%" PRIu64 "d前", seconds / 86400);
        else if (seconds >= 3600) snprintf(out, size, "%" PRIu64 "h前", seconds / 3600);
        else if (seconds >= 60) snprintf(out, size, "%" PRIu64 "m前", seconds / 60);
        else snprintf(out, size, "%" PRIu64 "s前", seconds);
    }
}
static const char *source_label(passport_provider_source_t source) {
    switch (source) {
    case PASSPORT_PROVIDER_SOURCE_HOOKS: return "状态事件";
    case PASSPORT_PROVIDER_SOURCE_CURSOR_CODE_TRACKING: return "代码活动记录";
    case PASSPORT_PROVIDER_SOURCE_GEMINI_CLI: return "Gemini CLI";
    case PASSPORT_PROVIDER_SOURCE_CODEX_LOCAL: return "Codex 本机日志";
    default: return "未接入";
    }
}

bool passport_provider_view_create(lv_obj_t *screen, int page) {
    if (!screen || s_view.root || page < 1 || page > 3) return false;
    s_view.page = page;
    s_view.root = panel(screen, 0, 42, 240, 278, PASSPORT_LAYOUT_COLOR_BACKGROUND, 0);
    lv_obj_add_event_cb(s_view.root, deleted, LV_EVENT_DELETE, NULL);
    label(s_view.root, 14, 0, 212, 25, page == 1 ? "Cursor" : page == 2 ? "Gemini" : "智能体",
          &passport_font_20, PASSPORT_LAYOUT_COLOR_PRIMARY, LV_TEXT_ALIGN_LEFT);
    if (page == 3) {
        const char *names[] = {"Codex", "Cursor", "Gemini"};
        for (unsigned i = 0; i < PASSPORT_PROVIDER_COUNT; ++i) {
            lv_obj_t *card = panel(s_view.root, 14, 34 + (int)i * 70, 212, 64, PASSPORT_LAYOUT_COLOR_PANEL, 10);
            s_view.rows[i].card = card;
            lv_obj_add_flag(card, LV_OBJ_FLAG_HIDDEN);
            label(card, 10, 8, 86, 18, names[i], &lv_font_montserrat_14, PASSPORT_LAYOUT_COLOR_PRIMARY, LV_TEXT_ALIGN_LEFT);
            s_view.rows[i].state = label(card, 96, 8, 106, 18, "未接入", &passport_font_14, PASSPORT_LAYOUT_COLOR_SECONDARY, LV_TEXT_ALIGN_RIGHT);
            s_view.rows[i].sessions = label(card, 10, 35, 130, 18, "未收到状态事件", &passport_font_14, PASSPORT_LAYOUT_COLOR_SECONDARY, LV_TEXT_ALIGN_LEFT);
            s_view.rows[i].age = label(card, 144, 35, 58, 18, "--", &passport_font_14, PASSPORT_LAYOUT_COLOR_SECONDARY, LV_TEXT_ALIGN_RIGHT);
        }
        s_view.empty = label(s_view.root, 14, 92, 212, 60, "在 Mac 上选择展示功能", &passport_font_14,
                            PASSPORT_LAYOUT_COLOR_SECONDARY, LV_TEXT_ALIGN_CENTER);
        label(s_view.root, 14, 242, 212, 36, "仅观察本机状态事件\n状态有效期最多 3 分钟", &passport_font_14,
              PASSPORT_LAYOUT_COLOR_SECONDARY, LV_TEXT_ALIGN_LEFT);
    } else {
        s_view.state = label(s_view.root, 126, 5, 100, 18, "未接入", &passport_font_14, PASSPORT_LAYOUT_COLOR_SECONDARY, LV_TEXT_ALIGN_RIGHT);
        lv_obj_t *card = panel(s_view.root, 14, 34, 212, 80, PASSPORT_LAYOUT_COLOR_PANEL, 10);
        label(card, 10, 7, 192, 18, page == 1 ? "本机代码活动请求" : "本周期本机 TOKEN",
              &passport_font_14, PASSPORT_LAYOUT_COLOR_SECONDARY, LV_TEXT_ALIGN_LEFT);
        s_view.metric = label(card, 10, 30, 192, 25, "未接入", &passport_font_20, PASSPORT_LAYOUT_COLOR_PRIMARY, LV_TEXT_ALIGN_LEFT);
        label(card, 10, 59, 192, 18, "范围：当前用量周期", &passport_font_14, PASSPORT_LAYOUT_COLOR_SECONDARY, LV_TEXT_ALIGN_LEFT);
        label(s_view.root, 14, 127, 44, 18, "模型", &passport_font_14, PASSPORT_LAYOUT_COLOR_SECONDARY, LV_TEXT_ALIGN_LEFT);
        s_view.model = label(s_view.root, 60, 127, 166, 18, "--", &lv_font_montserrat_14, PASSPORT_LAYOUT_COLOR_PRIMARY, LV_TEXT_ALIGN_LEFT);
        s_view.activity = label(s_view.root, 14, 155, 212, 18, "最近活动：尚无记录", &passport_font_14, PASSPORT_LAYOUT_COLOR_SECONDARY, LV_TEXT_ALIGN_LEFT);
        s_view.metric_time = label(s_view.root, 14, 181, 212, 18, "计数记录：尚无记录", &passport_font_14, PASSPORT_LAYOUT_COLOR_SECONDARY, LV_TEXT_ALIGN_LEFT);
        s_view.source = label(s_view.root, 14, 207, 212, 18, "来源：未接入", &passport_font_14, PASSPORT_LAYOUT_COLOR_SECONDARY, LV_TEXT_ALIGN_LEFT);
        panel(s_view.root, 14, 233, 212, 1, PASSPORT_LAYOUT_COLOR_SEPARATOR, 0);
        label(s_view.root, 14, 240, 212, 36, page == 1 ? "仅含本机代码活动记录\n订阅额度未接入" : "仅含本机命令行模型记录\n订阅额度未接入",
              &passport_font_14, PASSPORT_LAYOUT_COLOR_SECONDARY, LV_TEXT_ALIGN_LEFT);
    }
    return true;
}

void passport_provider_view_update(const passport_provider_snapshot_t snapshots[PASSPORT_PROVIDER_COUNT], int64_t now,
                                   uint8_t visible_features) {
    if (!s_view.root) return;
    char buffer[96], age[48];
    if (s_view.page == 3) {
        unsigned displayed = 0;
        for (unsigned i = 0; i < PASSPORT_PROVIDER_COUNT; ++i) {
            agent_row_t *row = &s_view.rows[i];
            /* Provider slots 0..2 match the durable Codex/Cursor/Gemini bits. */
            if ((visible_features & (1u << i)) == 0) {
                lv_obj_add_flag(row->card, LV_OBJ_FLAG_HIDDEN);
                continue;
            }
            lv_obj_remove_flag(row->card, LV_OBJ_FLAG_HIDDEN);
            lv_obj_set_y(row->card, 34 + (int)displayed++ * 70);
            const passport_provider_snapshot_t *s = snapshots && valid_slot(&snapshots[i], (int)i) ? &snapshots[i] : NULL;
            text(row->state, state_label(s, now));
            lv_obj_set_style_text_color(row->state, lv_color_hex(state_color(s, now)), 0);
            if (!s || !s->state_at_ms) {
                text(row->sessions, "未收到状态事件");
                text(row->age, "--");
            } else {
                if (!passport_provider_state_fresh(s, now)) snprintf(buffer, sizeof(buffer), "等待新状态事件");
                else if (s->active_sessions_known) snprintf(buffer, sizeof(buffer), "活动会话 %" PRId32, s->active_sessions);
                else snprintf(buffer, sizeof(buffer), "活动会话未知");
                text(row->sessions, buffer);
                age_text(age, sizeof(age), s->state_at_ms, now);
                text(row->age, age);
            }
        }
        if (displayed) lv_obj_add_flag(s_view.empty, LV_OBJ_FLAG_HIDDEN);
        else lv_obj_remove_flag(s_view.empty, LV_OBJ_FLAG_HIDDEN);
        return;
    }
    int provider = s_view.page == 1 ? PASSPORT_PROVIDER_CURSOR : PASSPORT_PROVIDER_GEMINI;
    const passport_provider_snapshot_t *s = snapshots && valid_slot(&snapshots[provider], provider) ? &snapshots[provider] : NULL;
    text(s_view.state, state_label(s, now));
    lv_obj_set_style_text_color(s_view.state, lv_color_hex(state_color(s, now)), 0);
    if (s && passport_provider_metric_available(s, now)) snprintf(buffer, sizeof(buffer), "%" PRIu64, s->metric_value);
    else if (!s || s->metric_status == PASSPORT_PROVIDER_METRIC_UNAVAILABLE) snprintf(buffer, sizeof(buffer), "未接入");
    else if (s->metric_status == PASSPORT_PROVIDER_METRIC_NO_RECORDS) snprintf(buffer, sizeof(buffer), "尚无记录");
    else if (s->metric_status == PASSPORT_PROVIDER_METRIC_PARTIAL) snprintf(buffer, sizeof(buffer), "记录不完整");
    else snprintf(buffer, sizeof(buffer), "时间未知");
    text(s_view.metric, buffer);
    text(s_view.model, s && s->model[0] ? s->model : "--");
    age_text(age, sizeof(age), s ? s->last_activity_ms : 0, now);
    snprintf(buffer, sizeof(buffer), "最近活动：%s", age);
    text(s_view.activity, buffer);
    age_text(age, sizeof(age), s ? s->metric_at_ms : 0, now);
    snprintf(buffer, sizeof(buffer), "计数记录：%s", age);
    text(s_view.metric_time, buffer);
    snprintf(buffer, sizeof(buffer), "来源：%s", source_label(s ? s->source : PASSPORT_PROVIDER_SOURCE_NONE));
    text(s_view.source, buffer);
}

void passport_provider_view_destroy(void) {
    if (s_view.root) lv_obj_delete(s_view.root);
    memset(&s_view, 0, sizeof(s_view));
}
