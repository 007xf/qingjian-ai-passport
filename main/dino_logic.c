#include "dino_logic.h"
#include <limits.h>
#include <string.h>

#define Q8 256
#define JUMP_VELOCITY_Q8 1856
#define GRAVITY_Q8 96
#define INITIAL_SPEED 140
#define MAXIMUM_SPEED 190

static uint32_t random_next(dino_game_t *game) {
    uint32_t value = game->random_state;
    value ^= value << 13;
    value ^= value >> 17;
    value ^= value << 5;
    game->random_state = value;
    return value;
}

static void clear_keys(dino_game_t *game) {
    game->up_held = false;
    game->down_held = false;
}

static void remember_best(dino_game_t *game) {
    if (game->score > game->best_score) {
        game->best_score = game->score;
        game->best_dirty = true;
    }
}

void dino_game_init(dino_game_t *game, uint32_t seed, uint32_t best_score) {
    memset(game, 0, sizeof(*game));
    game->mode = DINO_READY;
    game->initial_seed = seed ? seed : UINT32_C(0xD1A05EED);
    game->random_state = game->initial_seed;
    game->best_score = best_score;
    game->speed_px_s = INITIAL_SPEED;
}

void dino_game_restart(dino_game_t *game) {
    remember_best(game);
    uint32_t best = game->best_score;
    uint32_t seed = game->initial_seed;
    bool dirty = game->best_dirty;
    dino_game_init(game, seed, best);
    game->best_dirty = dirty;
    game->mode = DINO_RUNNING;
    game->spawn_remaining_q8 = 80 * Q8; /* visible first obstacle arrives after a gentle lead-in */
}

void dino_game_suspend(dino_game_t *game) {
    clear_keys(game);
    remember_best(game);
    if (game->mode == DINO_RUNNING) game->mode = DINO_PAUSED;
    game->clock_ready = false;
    game->accumulator_us = 0;
}

void dino_game_action(dino_game_t *game) {
    switch (game->mode) {
    case DINO_READY:
    case DINO_GAME_OVER:
        dino_game_restart(game);
        break;
    case DINO_RUNNING:
        dino_game_suspend(game);
        break;
    case DINO_PAUSED:
        clear_keys(game);
        game->mode = DINO_RUNNING;
        game->clock_ready = false;
        game->accumulator_us = 0;
        break;
    }
}

bool dino_game_crouching(const dino_game_t *game) {
    return game->down_held && game->jump_q8 == 0;
}

int dino_game_player_top(const dino_game_t *game) {
    /* Chromium also draws ducking in a 59x47 source cell with empty top rows. */
    return DINO_GROUND_Y - DINO_PLAYER_HEIGHT - game->jump_q8 / Q8;
}

int dino_obstacle_top(const dino_obstacle_t *obstacle) {
    if (obstacle->kind == DINO_BIRD) {
        return DINO_GROUND_Y - (obstacle->flight_level == 1 ? 40 : obstacle->flight_level == 2 ? 90 : 65);
    }
    return DINO_GROUND_Y - obstacle->height;
}

dino_sprite_id_t dino_game_player_frame(const dino_game_t *game) {
    if (game->mode == DINO_GAME_OVER) return DINO_SPRITE_TREX_CRASH;
    if (game->jump_q8 || game->velocity_q8 || game->mode != DINO_RUNNING) return DINO_SPRITE_TREX_STAND;
    if (dino_game_crouching(game))
        return (game->simulation_steps * UINT64_C(2) / 15) % 2 ? DINO_SPRITE_TREX_DUCK_1 : DINO_SPRITE_TREX_DUCK_0;
    return (game->simulation_steps / 5) % 2 ? DINO_SPRITE_TREX_RUN_1 : DINO_SPRITE_TREX_RUN_0;
}

dino_sprite_id_t dino_obstacle_frame(const dino_game_t *game, const dino_obstacle_t *obstacle) {
    if (obstacle->kind == DINO_BIRD)
        return (game->simulation_steps / 10) % 2 ? DINO_SPRITE_BIRD_1 : DINO_SPRITE_BIRD_0;
    return obstacle->kind == DINO_CACTUS_TALL ? DINO_SPRITE_CACTUS_LARGE : DINO_SPRITE_CACTUS_SMALL;
}

void dino_game_key(dino_game_t *game, dino_key_t key, bool pressed) {
    if (key != DINO_KEY_UP && key != DINO_KEY_DOWN) return;
    bool *held = key == DINO_KEY_UP ? &game->up_held : &game->down_held;
    if (!pressed) { *held = false; return; }
    if (game->mode != DINO_RUNNING || *held) return;
    *held = true;
    if (key == DINO_KEY_UP && game->jump_q8 == 0 && !game->down_held)
        game->velocity_q8 = JUMP_VELOCITY_Q8;
    /* UP release only clears ownership; every jump gets its full useful arc.
     * DOWN affects the ground stance, never adds an untested midair fast-fall. */
}

bool dino_rect_overlap(dino_rect_t a, dino_rect_t b) {
    if (a.width <= 0 || a.height <= 0 || b.width <= 0 || b.height <= 0) return false;
    return a.x < b.x + b.width && a.x + a.width > b.x &&
           a.y < b.y + b.height && a.y + a.height > b.y;
}

bool dino_game_collides(const dino_game_t *game, const dino_obstacle_t *obstacle) {
    if (!obstacle->active) return false;
    /* Chromium 98 checkForCollision / Trex.collisionBoxes and classic
     * offline-sprite-definitions.js. Collision bodies deliberately do not
     * follow animated wing pixels: the middle bird must clear a ducking T-Rex
     * in both wing phases. Preserve the original one-pixel outer adjustment. */
    static const dino_rect_t running[] = {
        {22, 0, 17, 16}, {1, 18, 30, 9}, {10, 35, 14, 8},
        {1, 24, 29, 5}, {5, 30, 21, 4}, {9, 34, 15, 4}
    };
    static const dino_rect_t ducking[] = {{1, 18, 55, 25}};
    static const dino_rect_t small[] = {{0, 7, 5, 27}, {4, 0, 6, 34}, {10, 4, 7, 14}};
    static const dino_rect_t tall[] = {{0, 12, 7, 38}, {8, 0, 7, 49}, {13, 10, 10, 38}};
    static const dino_rect_t bird[] = {
        {15, 15, 16, 5}, {18, 21, 24, 6}, {2, 14, 4, 3}, {6, 10, 4, 7}, {10, 8, 6, 9}
    };
    const dino_rect_t player_outer = {DINO_PLAYER_X + 1, dino_game_player_top(game) + 1,
                                     DINO_PLAYER_WIDTH - 2, DINO_PLAYER_HEIGHT - 2};
    const dino_rect_t obstacle_outer = {obstacle->x_q8 / Q8 + 1, dino_obstacle_top(obstacle) + 1,
                                       obstacle->width - 2, obstacle->height - 2};
    if (!dino_rect_overlap(player_outer, obstacle_outer)) return false;
    const bool crouching = dino_game_crouching(game);
    const dino_rect_t *player_boxes = crouching ? ducking : running;
    const unsigned player_count = crouching ? 1 : sizeof(running) / sizeof(running[0]);
    const dino_rect_t *obstacle_boxes;
    unsigned obstacle_count;
    switch (obstacle->kind) {
    case DINO_CACTUS_SMALL: obstacle_boxes = small; obstacle_count = 3; break;
    case DINO_CACTUS_TALL: obstacle_boxes = tall; obstacle_count = 3; break;
    case DINO_BIRD: obstacle_boxes = bird; obstacle_count = 5; break;
    default: return false;
    }
    for (unsigned i = 0; i < player_count; ++i) {
        dino_rect_t a = player_boxes[i];
        a.x += player_outer.x; a.y += player_outer.y;
        for (unsigned j = 0; j < obstacle_count; ++j) {
            dino_rect_t b = obstacle_boxes[j];
            b.x += obstacle_outer.x; b.y += obstacle_outer.y;
            if (dino_rect_overlap(a, b)) return true;
        }
    }
    return false;
}

static void spawn_obstacle(dino_game_t *game) {
    for (unsigned i = 0; i < DINO_OBSTACLE_COUNT; i++) {
        dino_obstacle_t *obstacle = &game->obstacles[i];
        if (obstacle->active) continue;
        unsigned choices = game->score >= 100 ? 3 : 2;
        obstacle->kind = (dino_obstacle_kind_t)(random_next(game) % choices);
        obstacle->width = obstacle->kind == DINO_BIRD ? 46 : obstacle->kind == DINO_CACTUS_TALL ? 25 : 17;
        obstacle->height = obstacle->kind == DINO_BIRD ? 40 : obstacle->kind == DINO_CACTUS_TALL ? 50 : 35;
        obstacle->flight_level = obstacle->kind == DINO_BIRD ? (uint8_t)(random_next(game) % 3) : 0;
        obstacle->active = true;
        obstacle->x_q8 = (DINO_WORLD_WIDTH + 6) * Q8;
        /* At every speed, successive obstacles retain more than one complete
         * jump arc of separation. Difficulty increases through speed, not traps. */
        int gap = (int)game->speed_px_s * 9 / 10 + 60 + (int)(random_next(game) % 57);
        game->spawn_remaining_q8 = (obstacle->width + gap) * Q8;
        return;
    }
    game->spawn_remaining_q8 = 20 * Q8;
}

static void step(dino_game_t *game) {
    game->simulation_steps++;
    if (game->velocity_q8 != 0 || game->jump_q8 != 0) {
        game->jump_q8 += game->velocity_q8;
        game->velocity_q8 -= GRAVITY_Q8;
        if (game->jump_q8 <= 0) {
            game->jump_q8 = 0;
            game->velocity_q8 = 0;
        }
    }
    uint32_t speed = INITIAL_SPEED + game->score / 5;
    game->speed_px_s = (uint16_t)(speed > MAXIMUM_SPEED ? MAXIMUM_SPEED : speed);
    int32_t movement_q8 = (int32_t)game->speed_px_s * Q8 / 60;
    game->distance_q8 += (uint32_t)movement_q8;
    uint64_t score = game->distance_q8 / (10 * Q8);
    game->score = score > UINT32_MAX ? UINT32_MAX : (uint32_t)score;
    for (unsigned i = 0; i < DINO_OBSTACLE_COUNT; i++) {
        dino_obstacle_t *obstacle = &game->obstacles[i];
        if (!obstacle->active) continue;
        obstacle->x_q8 -= movement_q8;
        if (obstacle->x_q8 + obstacle->width * Q8 < 0) obstacle->active = false;
        else if (dino_game_collides(game, obstacle)) {
            game->mode = DINO_GAME_OVER;
            clear_keys(game);
            remember_best(game);
            return;
        }
    }
    game->spawn_remaining_q8 -= movement_q8;
    if (game->spawn_remaining_q8 <= 0) spawn_obstacle(game);
}

bool dino_game_tick(dino_game_t *game, int64_t mono_ms) {
    if (mono_ms < 0) return false;
    if (!game->clock_ready) {
        game->last_ms = mono_ms;
        game->clock_ready = true;
        return false;
    }
    if (mono_ms < game->last_ms) {
        /* A monotonic discontinuity is not permission to replay elapsed work. */
        dino_game_suspend(game);
        return true;
    }
    int64_t elapsed = mono_ms - game->last_ms;
    game->last_ms = mono_ms;
    if (game->mode != DINO_RUNNING) { game->accumulator_us = 0; return false; }
    if (elapsed > DINO_MAX_CATCHUP_MS) {
        uint64_t discarded = (uint64_t)game->discarded_ms + (uint64_t)(elapsed - DINO_MAX_CATCHUP_MS);
        game->discarded_ms = discarded > UINT32_MAX ? UINT32_MAX : (uint32_t)discarded;
        elapsed = DINO_MAX_CATCHUP_MS;
    }
    game->accumulator_us += (uint32_t)elapsed * 1000;
    bool changed = false;
    unsigned steps = 0;
    while (game->accumulator_us >= DINO_STEP_US && steps < 6 && game->mode == DINO_RUNNING) {
        game->accumulator_us -= DINO_STEP_US;
        step(game);
        steps++;
        changed = true;
    }
    return changed;
}

bool dino_game_take_best(dino_game_t *game, uint32_t *out) {
    if (!game->best_dirty || !out) return false;
    *out = game->best_score;
    game->best_dirty = false;
    return true;
}
