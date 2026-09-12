#include "passport_logic.h"

#include <limits.h>
#include <stdio.h>
#include <string.h>

static const char *const FEATURE_IDS[PASSPORT_FEATURE_COUNT] = {
    "codex_dashboard", "cursor_dashboard", "gemini_dashboard", "grok_bot",
    "agent_dashboard", "coding_pet", "ai_talk", "dino"
};
static const char *const FEATURE_NAMES[PASSPORT_FEATURE_COUNT] = {
    "Codex", "Cursor", "Gemini", "Grok Bot", "Agent", "Coding Pet", "AI Talk", "Dino"
};
static const unsigned FEATURE_ORDER[PASSPORT_FEATURE_COUNT] = {
    PASSPORT_GROK, PASSPORT_CODEX, PASSPORT_CURSOR, PASSPORT_GEMINI,
    PASSPORT_AGENT, PASSPORT_PET, PASSPORT_DINO, PASSPORT_TALK
};

const char *passport_feature_id(unsigned feature) {
    return feature < PASSPORT_FEATURE_COUNT ? FEATURE_IDS[feature] : NULL;
}

const char *passport_feature_name(unsigned feature) {
    return feature < PASSPORT_FEATURE_COUNT ? FEATURE_NAMES[feature] : "Unknown";
}

unsigned passport_popcount(uint8_t mask) {
    unsigned count = 0;
    for (; mask; mask &= (uint8_t)(mask - 1)) count++;
    return count;
}

unsigned passport_token_stage(uint64_t tokens, bool known) {
    const passport_thresholds_t defaults = {PASSPORT_FIRST_THRESHOLD, PASSPORT_FINAL_THRESHOLD};
    return passport_token_stage_with_thresholds(tokens, known, &defaults);
}

bool passport_thresholds_valid(const passport_thresholds_t *thresholds) {
    return thresholds && thresholds->first > 0 && thresholds->first < thresholds->final &&
           thresholds->final <= PASSPORT_MAX_TOKENS;
}

void passport_thresholds_encode(const passport_thresholds_t *thresholds,
                                uint8_t out[PASSPORT_THRESHOLDS_BYTES]) {
    memcpy(out, "APT1", 4);
    for (unsigned i = 0; i < 8; i++) {
        out[4 + i] = (uint8_t)(thresholds->first >> (i * 8));
        out[12 + i] = (uint8_t)(thresholds->final >> (i * 8));
    }
}

bool passport_thresholds_decode(const uint8_t *data, size_t length, passport_thresholds_t *out) {
    if (!data || !out || length != PASSPORT_THRESHOLDS_BYTES || memcmp(data, "APT1", 4)) return false;
    passport_thresholds_t candidate = {0};
    for (unsigned i = 0; i < 8; i++) {
        candidate.first |= (uint64_t)data[4 + i] << (i * 8);
        candidate.final |= (uint64_t)data[12 + i] << (i * 8);
    }
    if (!passport_thresholds_valid(&candidate)) return false;
    *out = candidate;
    return true;
}

unsigned passport_token_stage_with_thresholds(uint64_t tokens, bool known,
                                               const passport_thresholds_t *thresholds) {
    if (!known) return 0;
    if (!passport_thresholds_valid(thresholds)) return 0;
    return tokens >= thresholds->final ? 2 : tokens >= thresholds->first ? 1 : 0;
}

bool passport_evolution_update(passport_evolution_t *state, uint64_t tokens, bool known) {
    const passport_thresholds_t defaults = {PASSPORT_FIRST_THRESHOLD, PASSPORT_FINAL_THRESHOLD};
    return passport_evolution_update_with_thresholds(state, tokens, known, &defaults);
}

bool passport_evolution_update_with_thresholds(passport_evolution_t *state, uint64_t tokens,
                                               bool known, const passport_thresholds_t *thresholds) {
    if (!passport_thresholds_valid(thresholds)) return false;
    bool transform = known && state->known && !state->stage2_seen &&
                     state->tokens < thresholds->final && tokens >= thresholds->final;
    if (known && tokens == 0) state->stage2_seen = false;
    if (known && tokens >= thresholds->final) state->stage2_seen = true;
    state->known = known;
    state->tokens = known ? tokens : 0;
    return transform;
}

bool passport_utf8_copy(char *out, size_t capacity, const char *text) {
    if (!out || !text || !capacity) return false;
    size_t len = 0;
    while (len < capacity && text[len]) len++;
    if (len == capacity) return false;
    for (size_t i = 0; i < len;) {
        uint8_t ch = (uint8_t)text[i++];
        if (ch < 0x20 || ch == 0x7F) return false;
        if (ch < 0x80) continue;
        unsigned tail;
        uint32_t cp, minimum;
        if (ch >= 0xC2 && ch <= 0xDF) { tail = 1; cp = ch & 0x1F; minimum = 0x80; }
        else if (ch >= 0xE0 && ch <= 0xEF) { tail = 2; cp = ch & 0x0F; minimum = 0x800; }
        else if (ch >= 0xF0 && ch <= 0xF4) { tail = 3; cp = ch & 7; minimum = 0x10000; }
        else return false;
        if (i + tail > len) return false;
        for (unsigned j = 0; j < tail; j++) {
            ch = (uint8_t)text[i++];
            if ((ch & 0xC0) != 0x80) return false;
            cp = (cp << 6) | (ch & 0x3F);
        }
        if (cp < minimum || cp > 0x10FFFF || (cp >= 0xD800 && cp <= 0xDFFF)) return false;
    }
    memcpy(out, text, len + 1);
    return true;
}

uint8_t passport_visible_features(const passport_config_t *cfg, uint8_t supported) {
    return cfg->custom ? cfg->custom_mask & supported : supported;
}

bool passport_config_prepare(const passport_config_t *saved,
                             const passport_config_t *draft, passport_config_t *out) {
    *out = *draft;
    if (!draft->custom) out->custom_mask = saved->custom_mask;
    if (out->custom == saved->custom && out->custom_mask == saved->custom_mask) {
        out->revision = saved->revision;
        return false;
    }
    if (saved->revision == UINT32_MAX) { *out = *saved; return false; }
    out->revision = saved->revision + 1;
    return true;
}

static uint32_t checksum(const uint8_t *data, size_t len) {
    uint32_t hash = UINT32_C(2166136261);
    for (size_t i = 0; i < len; i++) hash = (hash ^ data[i]) * UINT32_C(16777619);
    return hash;
}

static uint32_t read_u32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static void write_u32(uint8_t *p, uint32_t value) {
    for (unsigned i = 0; i < 4; i++) p[i] = (uint8_t)(value >> (i * 8));
}

void passport_config_encode(const passport_config_t *cfg, uint8_t out[PASSPORT_CONFIG_BYTES]) {
    memset(out, 0, PASSPORT_CONFIG_BYTES);
    memcpy(out, "APF1", 4);
    out[4] = cfg->custom;
    write_u32(out + 5, cfg->revision);
    for (unsigned i = 0; i < PASSPORT_FEATURE_COUNT; i++) {
        unsigned feature = FEATURE_ORDER[i];
        if (cfg->custom_mask & (1u << feature)) {
            strcpy((char *)out + 10 + out[9] * 24, FEATURE_IDS[feature]);
            out[9]++;
        }
    }
    write_u32(out + PASSPORT_CONFIG_BYTES - 4, checksum(out, PASSPORT_CONFIG_BYTES - 4));
}

bool passport_config_decode(const uint8_t *data, size_t len, passport_config_t *out) {
    if (!data || !out || len != PASSPORT_CONFIG_BYTES ||
        memcmp(data, "APF1", 4) != 0 || data[4] > 1 || data[9] > PASSPORT_FEATURE_COUNT ||
        read_u32(data + len - 4) != checksum(data, len - 4)) return false;
    passport_config_t candidate = {
        .custom = data[4] != 0, .custom_mask = 0, .revision = read_u32(data + 5)
    };
    for (unsigned i = 0; i < data[9]; i++) {
        const char *id = (const char *)data + 10 + i * 24;
        if (!memchr(id, 0, 24)) return false;
        for (unsigned feature = 0; feature < PASSPORT_FEATURE_COUNT; feature++) {
            if (strcmp(id, FEATURE_IDS[feature]) == 0)
                candidate.custom_mask |= (uint8_t)(1u << feature);
        }
    }
    *out = candidate;
    return true;
}

bool passport_clock_set(passport_clock_t *clock, int64_t utc_ms,
                        int offset_min, int64_t mono_ms) {
    /* A fresh USB sample may correct wall time backwards. It replaces the
     * anchor, never changes the monotonic clock, and does not prove live data. */
    if (utc_ms < INT64_C(1704067200000) || utc_ms >= INT64_C(4102444800000) ||
        offset_min < -720 || offset_min > 840 || mono_ms < 0) return false;
    *clock = (passport_clock_t) {true, utc_ms, mono_ms, offset_min};
    return true;
}

bool passport_clock_now(const passport_clock_t *clock, int64_t mono_ms, int64_t *utc_ms) {
    if (!clock->synced || mono_ms < clock->mono_anchor_ms ||
        mono_ms - clock->mono_anchor_ms > INT64_MAX - clock->utc_anchor_ms) return false;
    int64_t value = clock->utc_anchor_ms + mono_ms - clock->mono_anchor_ms;
    if (value >= INT64_C(4102444800000)) return false;
    if (utc_ms) *utc_ms = value;
    return true;
}

void passport_clock_hhmm(const passport_clock_t *clock, int64_t mono_ms, char out[6]) {
    int64_t utc_ms;
    if (!passport_clock_now(clock, mono_ms, &utc_ms)) {
        memcpy(out, "--:--", 6);
        return;
    }
    int minutes = (int)(((utc_ms / 60000) + clock->offset_min) % 1440);
    if (minutes < 0) minutes += 1440;
    unsigned hour = (unsigned)minutes / 60;
    unsigned minute = (unsigned)minutes % 60;
    out[0] = (char)('0' + hour / 10);
    out[1] = (char)('0' + hour % 10);
    out[2] = ':';
    out[3] = (char)('0' + minute / 10);
    out[4] = (char)('0' + minute % 10);
    out[5] = 0;
}

static passport_input_result_t no_action(void) {
    return (passport_input_result_t) {PASSPORT_INPUT_NONE, -1, 0};
}

static passport_input_result_t input_result(const passport_input_t *input,
                                            passport_input_action_t action) {
    return (passport_input_result_t) {action, input->key, input->generation};
}

static void clear_sequence(passport_input_t *input) {
    input->key = -1;
    input->held = false;
    input->long_sent = false;
    input->clicks = 0;
}

void passport_input_init(passport_input_t *input) {
    memset(input, 0, sizeof(*input));
    input->screen_on = true;
    input->key = -1;
}

void passport_input_set_screen(passport_input_t *input, bool on, int64_t now_ms) {
    input->screen_on = on;
    input->barrier = true;
    input->barrier_needs_release = input->held_mask != 0;
    input->last_raw_ms = now_ms;
    clear_sequence(input);
}

void passport_input_cancel(passport_input_t *input, int64_t now_ms) {
    input->barrier = true;
    input->barrier_needs_release = true;
    input->last_raw_ms = now_ms;
    /* A lost release cannot be inferred from a timeout. The next complete
     * physical gesture re-establishes an observed release boundary. */
    input->held_mask = 0;
    clear_sequence(input);
}

static void expire_barrier(passport_input_t *input, int64_t now_ms) {
    if (input->barrier && !input->held_mask && !input->barrier_needs_release &&
        now_ms >= input->last_raw_ms &&
        now_ms - input->last_raw_ms >= PASSPORT_CLICK_GAP_MS)
        input->barrier = false;
}

passport_input_result_t passport_input_tick(passport_input_t *input,
                                           uint32_t generation, int64_t now_ms) {
    expire_barrier(input, now_ms);
    if (input->barrier || !input->screen_on || input->key < 0) return no_action();
    if (input->generation != generation) {
        clear_sequence(input);
        return no_action();
    }
    if (input->held && !input->long_sent && now_ms >= input->down_ms &&
        now_ms - input->down_ms >= PASSPORT_LONG_MS) {
        input->long_sent = true;
        input->clicks = 0;
        return input_result(input, PASSPORT_INPUT_LONG);
    }
    if (!input->held && now_ms >= input->up_ms &&
        now_ms - input->up_ms >= PASSPORT_CLICK_GAP_MS) {
        passport_input_result_t result = input_result(input,
            input->clicks == 1 ? PASSPORT_INPUT_CLICK :
            input->clicks == 2 ? PASSPORT_INPUT_DOUBLE : PASSPORT_INPUT_NONE);
        clear_sequence(input);
        return result;
    }
    return no_action();
}

passport_input_result_t passport_input_raw(passport_input_t *input, int key,
                                          bool pressed, uint32_t generation,
                                          int64_t now_ms) {
    if (key < 0 || key > 2 || now_ms < input->last_raw_ms) return no_action();
    expire_barrier(input, now_ms);
    input->last_raw_ms = now_ms;
    uint8_t bit = (uint8_t)(1u << key);
    if (pressed) {
        if (input->held_mask & bit) return no_action(); /* duplicate PRESS */
        input->held_mask |= bit;
    } else {
        input->held_mask &= (uint8_t)~bit;
        if (!input->held_mask) input->barrier_needs_release = false;
    }
    if (input->barrier) return no_action();
    if (!input->screen_on) {
        if (!pressed) return no_action();
        passport_input_set_screen(input, true, now_ms);
        return (passport_input_result_t) {PASSPORT_INPUT_WAKE, key, generation};
    }
    if (pressed) {
        bool continues = input->key == key && !input->held && !input->long_sent &&
                         input->generation == generation &&
                         now_ms - input->up_ms < PASSPORT_CLICK_GAP_MS;
        if (!continues) clear_sequence(input);
        input->key = key;
        input->generation = generation;
        input->held = true;
        input->down_ms = now_ms;
        return no_action();
    }
    if (input->key != key || !input->held || input->generation != generation)
        return no_action();
    input->held = false;
    input->up_ms = now_ms;
    if (input->long_sent) {
        clear_sequence(input);
        return no_action();
    }
    if (now_ms - input->down_ms >= PASSPORT_LONG_MS) {
        passport_input_result_t result = input_result(input, PASSPORT_INPUT_LONG);
        clear_sequence(input);
        return result;
    }
    if (input->clicks < 3) input->clicks++;
    if (key == 2 && input->clicks == 3) {
        passport_input_result_t result = input_result(input, PASSPORT_INPUT_OFF);
        passport_input_set_screen(input, false, now_ms);
        return result;
    }
    return no_action();
}
