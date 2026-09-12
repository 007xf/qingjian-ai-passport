#include "passport_logic.h"
#include <assert.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>

static void test_clock(void) {
    passport_clock_t clock = {0};
    char text[6];
    int64_t utc = -1;
    passport_clock_hhmm(&clock, 0, text);
    assert(!strcmp(text, "--:--"));
    assert(!passport_clock_now(&clock, 0, &utc));
    assert(!passport_clock_set(&clock, 0, 480, 0));
    assert(!passport_clock_set(&clock, INT64_MAX, 480, 0));
    assert(!passport_clock_set(&clock, 1704067200000LL, 841, 0));
    assert(!passport_clock_set(&clock, 1704067200000LL, -721, 0));
    assert(!passport_clock_set(&clock, 1704067200000LL, 0, -1));
    assert(passport_clock_set(&clock, 1704067200000LL, 480, 1000));
    passport_clock_hhmm(&clock, 1000, text);
    assert(!strcmp(text, "08:00"));
    passport_clock_hhmm(&clock, 61000, text);
    assert(!strcmp(text, "08:01"));
    assert(passport_clock_now(&clock, 1111, &utc) && utc == 1704067200111LL);
    assert(!passport_clock_now(&clock, 999, &utc));
    passport_clock_hhmm(&clock, 999, text);
    assert(!strcmp(text, "--:--"));
    /* Explicit USB correction may move UTC back; monotonic runtime is intact. */
    assert(passport_clock_set(&clock, 1704067200000LL, -720, 2000));
    passport_clock_hhmm(&clock, 2000, text);
    assert(!strcmp(text, "12:00"));
    assert(passport_clock_now(&clock, 62000, &utc) && utc == 1704067260000LL);
    assert(!passport_clock_now(&clock, INT64_MAX, &utc));
}

static void test_features(void) {
    for (unsigned supported = 0; supported < 256; supported++) {
        for (unsigned selected = 0; selected < 256; selected++) {
            passport_config_t cfg = {.custom = true, .custom_mask = (uint8_t)selected, .revision = 7};
            assert(passport_visible_features(&cfg, supported) == (supported & selected));
            cfg.custom = false;
            assert(passport_visible_features(&cfg, supported) == supported);
        }
    }
    for (unsigned mask = 0; mask < 256; mask++) {
        passport_config_t cfg = {.custom = (mask & 1) != 0, .custom_mask = (uint8_t)mask, .revision = mask};
        passport_config_t decoded = {0};
        uint8_t encoded[PASSPORT_CONFIG_BYTES];
        passport_config_encode(&cfg, encoded);
        assert(passport_config_decode(encoded, sizeof(encoded), &decoded));
        assert(decoded.custom == cfg.custom && decoded.custom_mask == cfg.custom_mask && decoded.revision == cfg.revision);
        for (size_t i = 0; i < sizeof(encoded); i++) {
            encoded[i] ^= 1;
            assert(!passport_config_decode(encoded, sizeof(encoded), &decoded));
            encoded[i] ^= 1;
        }
        assert(!passport_config_decode(encoded, sizeof(encoded) - 1, &decoded));
    }
    passport_config_t saved = {.custom = true, .custom_mask = 0x81, .revision = 10};
    passport_config_t draft = {.custom = false, .custom_mask = 0xFF};
    passport_config_t next;
    assert(passport_config_prepare(&saved, &draft, &next));
    assert(!next.custom && next.custom_mask == 0x81 && next.revision == 11);
    assert(!passport_config_prepare(&next, &draft, &saved));
    assert(saved.custom_mask == 0x81 && saved.revision == 11);
    draft.custom = true;
    draft.custom_mask = 0;
    assert(passport_config_prepare(&saved, &draft, &next));
    assert(next.custom && next.custom_mask == 0 && passport_visible_features(&next, 255) == 0);
    assert(passport_popcount(255) == 8 && passport_popcount(0) == 0);
    assert(!strcmp(passport_feature_id(PASSPORT_DINO), "dino"));
    assert(passport_feature_id(99) == NULL);
}

static passport_input_result_t raw(passport_input_t *in, int key, bool press, int64_t ms) {
    return passport_input_raw(in, key, press, 1, ms);
}

static void tap(passport_input_t *in, int key, int64_t ms) {
    assert(raw(in, key, true, ms).action == PASSPORT_INPUT_NONE);
    assert(raw(in, key, false, ms + 50).action == PASSPORT_INPUT_NONE);
}

static void test_gestures(void) {
    passport_input_t in;
    passport_input_init(&in);
    tap(&in, 2, 0);
    assert(passport_input_tick(&in, 1, 399).action == PASSPORT_INPUT_NONE);
    assert(passport_input_tick(&in, 1, 400).action == PASSPORT_INPUT_CLICK);
    assert(passport_input_tick(&in, 1, 900).action == PASSPORT_INPUT_NONE);
    tap(&in, 2, 1000);
    tap(&in, 2, 1200);
    assert(passport_input_tick(&in, 1, 1599).action == PASSPORT_INPUT_NONE);
    assert(passport_input_tick(&in, 1, 1600).action == PASSPORT_INPUT_DOUBLE);
    assert(raw(&in, 2, true, 2000).action == PASSPORT_INPUT_NONE);
    assert(passport_input_tick(&in, 1, 2799).action == PASSPORT_INPUT_NONE);
    assert(passport_input_tick(&in, 1, 2800).action == PASSPORT_INPUT_LONG);
    assert(passport_input_tick(&in, 1, 4000).action == PASSPORT_INPUT_NONE);
    assert(raw(&in, 2, false, 4100).action == PASSPORT_INPUT_NONE);
    assert(passport_input_tick(&in, 1, 4500).action == PASSPORT_INPUT_NONE);
    /* No tick while held: release still classifies the long hold exactly once. */
    assert(raw(&in, 0, true, 5000).action == PASSPORT_INPUT_NONE);
    assert(raw(&in, 0, false, 5900).action == PASSPORT_INPUT_LONG);
    assert(passport_input_tick(&in, 1, 6300).action == PASSPORT_INPUT_NONE);
}

static void test_triple_wake(void) {
    passport_input_t in;
    passport_input_init(&in);
    tap(&in, 2, 0);
    tap(&in, 2, 150);
    assert(raw(&in, 2, true, 300).action == PASSPORT_INPUT_NONE);
    assert(raw(&in, 2, false, 350).action == PASSPORT_INPUT_OFF);
    assert(!in.screen_on);
    /* A fourth click and all tails belong to the sleep gesture. */
    assert(raw(&in, 2, true, 450).action == PASSPORT_INPUT_NONE);
    assert(raw(&in, 2, false, 500).action == PASSPORT_INPUT_NONE);
    assert(!in.screen_on);
    assert(passport_input_tick(&in, 1, 850).action == PASSPORT_INPUT_NONE);
    assert(raw(&in, 0, true, 900).action == PASSPORT_INPUT_WAKE);
    assert(in.screen_on);
    /* Silence must not pretend this long wake press was physically released. */
    assert(passport_input_tick(&in, 1, 9000).action == PASSPORT_INPUT_NONE);
    assert(raw(&in, 0, false, 9500).action == PASSPORT_INPUT_NONE);
    assert(raw(&in, 0, true, 9650).action == PASSPORT_INPUT_NONE);
    assert(raw(&in, 0, false, 9700).action == PASSPORT_INPUT_NONE);
    assert(passport_input_tick(&in, 1, 10050).action == PASSPORT_INPUT_NONE);
    tap(&in, 1, 10200);
    assert(passport_input_tick(&in, 1, 10600).action == PASSPORT_INPUT_CLICK);
    /* An external off command during a held key drains its real release. */
    assert(raw(&in, 2, true, 11000).action == PASSPORT_INPUT_NONE);
    passport_input_set_screen(&in, false, 11100);
    assert(passport_input_tick(&in, 1, 15000).action == PASSPORT_INPUT_NONE);
    assert(raw(&in, 2, false, 15100).action == PASSPORT_INPUT_NONE);
    assert(raw(&in, 1, true, 15600).action == PASSPORT_INPUT_WAKE);
    assert(raw(&in, 1, false, 15650).action == PASSPORT_INPUT_NONE);
    assert(passport_input_tick(&in, 1, 16000).action == PASSPORT_INPUT_NONE);
}

static void test_generation_and_loss(void) {
    passport_input_t in;
    passport_input_init(&in);
    tap(&in, 2, 0);
    assert(passport_input_tick(&in, 2, 500).action == PASSPORT_INPUT_NONE);
    assert(passport_input_tick(&in, 1, 900).action == PASSPORT_INPUT_NONE);
    assert(raw(&in, 2, true, 1000).action == PASSPORT_INPUT_NONE);
    assert(passport_input_tick(&in, 1, 1800).action == PASSPORT_INPUT_LONG);
    assert(passport_input_tick(&in, 2, 1900).action == PASSPORT_INPUT_NONE);
    assert(passport_input_raw(&in, 2, false, 1, 2000).action == PASSPORT_INPUT_NONE);
    passport_input_cancel(&in, 2100);
    assert(passport_input_tick(&in, 2, 99999).action == PASSPORT_INPUT_NONE);
    assert(in.barrier);
    assert(passport_input_raw(&in, 0, true, 2, 100000).action == PASSPORT_INPUT_NONE);
    assert(passport_input_raw(&in, 0, false, 2, 100050).action == PASSPORT_INPUT_NONE);
    assert(passport_input_tick(&in, 2, 100400).action == PASSPORT_INPUT_NONE);
    assert(!in.barrier);
    assert(passport_input_raw(&in, 0, true, 2, 101000).action == PASSPORT_INPUT_NONE);
    assert(passport_input_raw(&in, 0, true, 2, 101010).action == PASSPORT_INPUT_NONE); /* bounce */
    assert(passport_input_raw(&in, 0, false, 2, 101050).action == PASSPORT_INPUT_NONE);
    assert(passport_input_tick(&in, 2, 101400).action == PASSPORT_INPUT_CLICK);
}

static void test_badge_and_evolution(void) {
    char text[25];
    assert(passport_utf8_copy(text, sizeof(text), "你的姓名"));
    assert(!strcmp(text, "你的姓名"));
    assert(passport_utf8_copy(text, sizeof(text), ""));
    assert(!passport_utf8_copy(text, sizeof(text), "01234567890123456789012345"));
    assert(!passport_utf8_copy(text, sizeof(text), "bad\nname"));
    assert(!passport_utf8_copy(text, sizeof(text), "\xC0\x80"));
    assert(!passport_utf8_copy(text, sizeof(text), "\xED\xA0\x80"));
    assert(!passport_utf8_copy(text, sizeof(text), "\xF4\x90\x80\x80"));
    assert(!passport_utf8_copy(text, sizeof(text), "\xE4\xB8"));
    assert(passport_token_stage(PASSPORT_FIRST_THRESHOLD - 1, true) == 0);
    assert(passport_token_stage(PASSPORT_FIRST_THRESHOLD, true) == 1);
    assert(passport_token_stage(PASSPORT_FINAL_THRESHOLD - 1, true) == 1);
    assert(passport_token_stage(PASSPORT_FINAL_THRESHOLD, true) == 2);
    assert(passport_token_stage(UINT64_MAX, true) == 2);
    assert(passport_token_stage(UINT64_MAX, false) == 0);
    passport_evolution_t state = {0};
    assert(!passport_evolution_update(&state, PASSPORT_FINAL_THRESHOLD, true)); /* reconnect never replays */
    assert(!passport_evolution_update(&state, PASSPORT_FINAL_THRESHOLD - 1, true));
    assert(!passport_evolution_update(&state, PASSPORT_FINAL_THRESHOLD, true)); /* once per cycle */
    assert(!passport_evolution_update(&state, 0, true)); /* new reset cycle */
    assert(!passport_evolution_update(&state, PASSPORT_FINAL_THRESHOLD - 1, true));
    assert(passport_evolution_update(&state, PASSPORT_FINAL_THRESHOLD, true));
    assert(!passport_evolution_update(&state, PASSPORT_FINAL_THRESHOLD + 1, true));
    assert(!passport_evolution_update(&state, 0, false));
    assert(!passport_evolution_update(&state, 600000000, true));
}

static void test_configurable_thresholds(void) {
    passport_thresholds_t limits = {100, 200}, decoded = {0};
    assert(passport_thresholds_valid(&limits));
    assert(passport_token_stage_with_thresholds(99, true, &limits) == 0);
    assert(passport_token_stage_with_thresholds(100, true, &limits) == 1);
    assert(passport_token_stage_with_thresholds(199, true, &limits) == 1);
    assert(passport_token_stage_with_thresholds(200, true, &limits) == 2);
    assert(passport_token_stage_with_thresholds(200, false, &limits) == 0);
    uint8_t bytes[PASSPORT_THRESHOLDS_BYTES];
    passport_thresholds_encode(&limits, bytes);
    assert(passport_thresholds_decode(bytes, sizeof(bytes), &decoded));
    assert(decoded.first == 100 && decoded.final == 200);
    assert(!passport_thresholds_decode(bytes, sizeof(bytes) - 1, &decoded));
    bytes[0] = 0;
    assert(!passport_thresholds_decode(bytes, sizeof(bytes), &decoded));
    limits.first = 0;
    assert(!passport_thresholds_valid(&limits));
    limits.first = limits.final;
    assert(!passport_thresholds_valid(&limits));
    limits.first = 201;
    assert(!passport_thresholds_valid(&limits));
    limits.first = 1;
    limits.final = PASSPORT_MAX_TOKENS + 1;
    assert(!passport_thresholds_valid(&limits));
    limits.final = PASSPORT_MAX_TOKENS;
    assert(passport_thresholds_valid(&limits));
    passport_thresholds_encode(&limits, bytes);
    assert(passport_thresholds_decode(bytes, sizeof(bytes), &decoded));
    assert(decoded.final == PASSPORT_MAX_TOKENS);
    limits = (passport_thresholds_t) {100, 200};
    passport_evolution_t state = {0};
    assert(!passport_evolution_update_with_thresholds(&state, 199, true, &limits));
    assert(passport_evolution_update_with_thresholds(&state, 200, true, &limits));
    assert(!passport_evolution_update_with_thresholds(&state, 201, true, &limits));
}

int main(void) {
    test_clock();
    test_features();
    test_gestures();
    test_triple_wake();
    test_generation_and_loss();
    test_badge_and_evolution();
    test_configurable_thresholds();
    puts("Passport logic: PASS (clock, 65536 feature subsets, storage integrity, gestures, wake, Unicode, stages)");
    return 0;
}
