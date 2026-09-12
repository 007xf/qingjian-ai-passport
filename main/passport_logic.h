#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/* These IDs are durable identifiers, independent of menu positions.
 * Grok (3), Pet (5), and Talk (6) are retained for old configs
 * and protocol compatibility only; the application does not expose them. */
typedef enum {
    PASSPORT_CODEX, PASSPORT_CURSOR, PASSPORT_GEMINI, PASSPORT_GROK,
    PASSPORT_AGENT, PASSPORT_PET, PASSPORT_TALK, PASSPORT_DINO,
    PASSPORT_FEATURE_COUNT
} passport_feature_t;

#define PASSPORT_ALL_FEATURES UINT8_MAX
#define PASSPORT_CONFIG_BYTES 206
#define PASSPORT_CLICK_GAP_MS 350
#define PASSPORT_LONG_MS 800
#define PASSPORT_FIRST_THRESHOLD UINT64_C(77777777)
#define PASSPORT_FINAL_THRESHOLD UINT64_C(555555555)
#define PASSPORT_MAX_TOKENS UINT64_C(9007199254740991)
#define PASSPORT_THRESHOLDS_BYTES 20

typedef struct { uint64_t first, final; } passport_thresholds_t;
bool passport_thresholds_valid(const passport_thresholds_t *thresholds);
void passport_thresholds_encode(const passport_thresholds_t *thresholds,
                                uint8_t out[PASSPORT_THRESHOLDS_BYTES]);
bool passport_thresholds_decode(const uint8_t *data, size_t length, passport_thresholds_t *out);

typedef struct {
    bool custom;
    uint8_t custom_mask;
    uint32_t revision;
} passport_config_t;

const char *passport_feature_id(unsigned feature);
const char *passport_feature_name(unsigned feature);
uint8_t passport_visible_features(const passport_config_t *cfg, uint8_t supported);
bool passport_config_prepare(const passport_config_t *saved,
                             const passport_config_t *draft, passport_config_t *out);
void passport_config_encode(const passport_config_t *cfg, uint8_t out[PASSPORT_CONFIG_BYTES]);
bool passport_config_decode(const uint8_t *data, size_t len, passport_config_t *out);
unsigned passport_popcount(uint8_t mask);
unsigned passport_token_stage(uint64_t tokens, bool known);
unsigned passport_token_stage_with_thresholds(uint64_t tokens, bool known,
                                               const passport_thresholds_t *thresholds);
bool passport_utf8_copy(char *out, size_t capacity, const char *text);
typedef struct {
    uint64_t tokens;
    bool known;
    bool stage2_seen;
} passport_evolution_t;
bool passport_evolution_update(passport_evolution_t *state, uint64_t tokens, bool known);
bool passport_evolution_update_with_thresholds(passport_evolution_t *state, uint64_t tokens,
                                               bool known, const passport_thresholds_t *thresholds);

typedef struct {
    bool synced;
    int64_t utc_anchor_ms;
    int64_t mono_anchor_ms;
    int offset_min;
} passport_clock_t;

bool passport_clock_set(passport_clock_t *clock, int64_t utc_ms,
                        int offset_min, int64_t mono_ms);
bool passport_clock_now(const passport_clock_t *clock, int64_t mono_ms, int64_t *utc_ms);
void passport_clock_hhmm(const passport_clock_t *clock, int64_t mono_ms, char out[6]);

/* Application is the sole gesture classifier: the BSP's semantic events are
 * intentionally ignored. Raw release is required; silence is never release. */
typedef enum {
    PASSPORT_INPUT_NONE, PASSPORT_INPUT_CLICK, PASSPORT_INPUT_DOUBLE,
    PASSPORT_INPUT_LONG, PASSPORT_INPUT_WAKE, PASSPORT_INPUT_OFF
} passport_input_action_t;

typedef struct {
    passport_input_action_t action;
    int key;
    uint32_t generation;
} passport_input_result_t;

typedef struct {
    bool screen_on;
    uint8_t held_mask;
    bool barrier;
    bool barrier_needs_release;
    int64_t last_raw_ms;
    int key;
    bool held;
    bool long_sent;
    uint8_t clicks;
    uint32_t generation;
    int64_t down_ms;
    int64_t up_ms;
} passport_input_t;

void passport_input_init(passport_input_t *input);
void passport_input_set_screen(passport_input_t *input, bool on, int64_t now_ms);
void passport_input_cancel(passport_input_t *input, int64_t now_ms);
passport_input_result_t passport_input_raw(passport_input_t *input, int key,
                                          bool pressed, uint32_t generation,
                                          int64_t now_ms);
passport_input_result_t passport_input_tick(passport_input_t *input,
                                           uint32_t generation, int64_t now_ms);
