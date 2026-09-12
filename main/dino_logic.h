#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "dino_sprite_ids.h"

#define DINO_WORLD_WIDTH 216
#define DINO_GROUND_Y 132 /* classic 10 px bottom pad in the 142 px playfield */
#define DINO_PLAYER_X 28
#define DINO_PLAYER_WIDTH 44
#define DINO_PLAYER_HEIGHT 47
#define DINO_DUCK_WIDTH 59
#define DINO_DUCK_HEIGHT 25 /* Chromium logical height; source cell retains 47 px padding */
#define DINO_OBSTACLE_COUNT 3
#define DINO_STEP_US 16667
#define DINO_MAX_CATCHUP_MS 100

typedef enum { DINO_READY, DINO_RUNNING, DINO_PAUSED, DINO_GAME_OVER } dino_mode_t;
typedef enum { DINO_KEY_UP, DINO_KEY_DOWN } dino_key_t;
typedef enum { DINO_CACTUS_SMALL, DINO_CACTUS_TALL, DINO_BIRD } dino_obstacle_kind_t;
typedef struct { int x, y, width, height; } dino_rect_t;
typedef struct {
    bool active;
    dino_obstacle_kind_t kind;
    int32_t x_q8;
    uint8_t width, height;
    uint8_t flight_level; /* bird: 0 middle, 1 low, 2 high; classic source altitudes */
} dino_obstacle_t;

typedef struct {
    dino_mode_t mode;
    uint32_t initial_seed, random_state;
    uint32_t score, best_score;
    uint64_t distance_q8;
    uint32_t simulation_steps;
    uint16_t speed_px_s;
    int32_t jump_q8, velocity_q8;
    int32_t spawn_remaining_q8;
    bool up_held, down_held, best_dirty;
    bool clock_ready;
    int64_t last_ms;
    uint32_t accumulator_us;
    uint32_t discarded_ms;
    dino_obstacle_t obstacles[DINO_OBSTACLE_COUNT];
} dino_game_t;

void dino_game_init(dino_game_t *game, uint32_t seed, uint32_t best_score);
void dino_game_restart(dino_game_t *game);
void dino_game_action(dino_game_t *game); /* ready/start, running/pause, paused/resume, over/retry */
void dino_game_suspend(dino_game_t *game); /* pause and clear held keys; never silently resumes */
void dino_game_key(dino_game_t *game, dino_key_t key, bool pressed);
bool dino_game_tick(dino_game_t *game, int64_t mono_ms);
bool dino_game_take_best(dino_game_t *game, uint32_t *out);
bool dino_game_crouching(const dino_game_t *game);
int dino_game_player_top(const dino_game_t *game);
int dino_obstacle_top(const dino_obstacle_t *obstacle);
dino_sprite_id_t dino_game_player_frame(const dino_game_t *game);
dino_sprite_id_t dino_obstacle_frame(const dino_game_t *game, const dino_obstacle_t *obstacle);
bool dino_rect_overlap(dino_rect_t a, dino_rect_t b);
bool dino_game_collides(const dino_game_t *game, const dino_obstacle_t *obstacle);
