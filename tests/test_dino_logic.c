#include "dino_logic.h"
#include "dino_sprite_masks.h"
#include <assert.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>

static dino_game_t running(uint32_t seed) {
    dino_game_t game;
    dino_game_init(&game, seed, 0);
    dino_game_action(&game);
    game.spawn_remaining_q8 = INT32_MAX;
    return game;
}

static void advance(dino_game_t *game, int from, int until) {
    for (int stamp = from; stamp <= until; stamp += 17) dino_game_tick(game, stamp);
}

static void test_ready_pause_resume(void) {
    dino_game_t game;
    dino_game_init(&game, 123, 42);
    assert(game.mode == DINO_READY && game.best_score == 42 && game.score == 0);
    dino_game_key(&game, DINO_KEY_UP, true);
    dino_game_key(&game, DINO_KEY_DOWN, true);
    assert(!game.up_held && !game.down_held && !game.velocity_q8);
    dino_game_action(&game);
    assert(game.mode == DINO_RUNNING);
    dino_game_key(&game, DINO_KEY_DOWN, true);
    assert(dino_game_crouching(&game));
    dino_game_action(&game);
    assert(game.mode == DINO_PAUSED && !game.down_held && !game.up_held);
    uint64_t before = game.distance_q8;
    dino_game_tick(&game, 0);
    dino_game_tick(&game, 50000);
    assert(game.distance_q8 == before && game.mode == DINO_PAUSED);
    dino_game_action(&game);
    assert(game.mode == DINO_RUNNING && !game.clock_ready);
    dino_game_tick(&game, 100000);
    assert(game.distance_q8 == before); /* no replay of paused time */
    dino_game_tick(&game, 100017);
    assert(game.distance_q8 > before);
}

static void test_jump_land_and_press_ownership(void) {
    dino_game_t game = running(1);
    dino_game_key(&game, DINO_KEY_UP, true);
    assert(game.velocity_q8 > 0 && game.up_held);
    int32_t velocity = game.velocity_q8;
    dino_game_tick(&game, 0);
    dino_game_tick(&game, 17);
    assert(game.jump_q8 > 0 && game.velocity_q8 < velocity);
    velocity = game.velocity_q8;
    dino_game_key(&game, DINO_KEY_UP, true);
    assert(game.velocity_q8 == velocity); /* duplicate PRESS cannot restart jump */
    int32_t highest = game.jump_q8;
    for (int stamp = 34; stamp < 1000; stamp += 17) {
        dino_game_tick(&game, stamp);
        if (game.jump_q8 > highest) highest = game.jump_q8;
        assert(game.jump_q8 >= 0);
    }
    assert(highest > 60 * 256 && highest < 90 * 256);
    assert(game.jump_q8 == 0 && game.velocity_q8 == 0 && game.up_held);
    dino_game_key(&game, DINO_KEY_UP, true);
    assert(game.velocity_q8 == 0); /* holding does not auto-hop after landing */
    dino_game_key(&game, DINO_KEY_UP, false);
    dino_game_key(&game, DINO_KEY_UP, true);
    assert(game.velocity_q8 > 0);
    dino_game_key(&game, DINO_KEY_UP, false);
    assert(game.velocity_q8 > 0); /* short taps retain the full useful arc */
}

static void test_crouch_and_suspend(void) {
    dino_game_t game = running(2);
    int standing_top = dino_game_player_top(&game);
    dino_game_key(&game, DINO_KEY_DOWN, true);
    assert(dino_game_crouching(&game));
    assert(dino_game_player_top(&game) == standing_top);
    dino_game_key(&game, DINO_KEY_UP, true);
    assert(!game.velocity_q8); /* no simultaneous-key assumption */
    dino_game_key(&game, DINO_KEY_DOWN, false);
    assert(!dino_game_crouching(&game));
    dino_game_key(&game, DINO_KEY_UP, false);
    dino_game_key(&game, DINO_KEY_UP, true);
    advance(&game, 0, 100);
    dino_game_key(&game, DINO_KEY_DOWN, true);
    assert(game.down_held && !dino_game_crouching(&game)); /* airborne DOWN is not a false ground pose */
    dino_game_suspend(&game);
    assert(game.mode == DINO_PAUSED && !game.up_held && !game.down_held);
    dino_game_key(&game, DINO_KEY_DOWN, false);
    assert(!game.down_held);
}

static void test_collision_boxes(void) {
    assert(!dino_rect_overlap((dino_rect_t){0, 0, 10, 10}, (dino_rect_t){10, 0, 2, 2}));
    assert(dino_rect_overlap((dino_rect_t){0, 0, 10, 10}, (dino_rect_t){9, 9, 2, 2}));
    assert(!dino_rect_overlap((dino_rect_t){0, 0, 0, 10}, (dino_rect_t){0, 0, 2, 2}));
    dino_game_t game = running(3);
    dino_obstacle_t cactus = {.active = true, .kind = DINO_CACTUS_TALL,
                             .x_q8 = (DINO_PLAYER_X + 10) * 256, .width = 25, .height = 50};
    assert(dino_game_collides(&game, &cactus));
    cactus.x_q8 = 150 * 256;
    assert(!dino_game_collides(&game, &cactus));
    cactus.x_q8 = (DINO_PLAYER_X + 10) * 256;
    game.jump_q8 = 60 * 256;
    assert(!dino_game_collides(&game, &cactus));
    game.jump_q8 = 0;
    dino_game_key(&game, DINO_KEY_DOWN, true);
    assert(dino_game_collides(&game, &cactus)); /* crouching is not invulnerability */
    dino_obstacle_t bird = {.active = true, .kind = DINO_BIRD,
                           .x_q8 = (DINO_PLAYER_X + 8) * 256, .width = 46, .height = 40};
    assert(!dino_game_collides(&game, &bird));
    dino_game_key(&game, DINO_KEY_DOWN, false);
    assert(dino_game_collides(&game, &bird));
    bird.active = false;
    assert(!dino_game_collides(&game, &bird));
}

static void test_jump_clears_tall_obstacle(void) {
    dino_game_t game = running(4);
    game.obstacles[0] = (dino_obstacle_t){.active = true, .kind = DINO_CACTUS_TALL,
                                       .x_q8 = 70 * 256, .width = 25, .height = 50};
    dino_game_key(&game, DINO_KEY_UP, true);
    dino_game_key(&game, DINO_KEY_UP, false);
    advance(&game, 0, 1000);
    assert(game.mode == DINO_RUNNING && game.jump_q8 == 0);
    assert(game.score > 0);
}

static void test_game_over_restart_and_best(void) {
    dino_game_t game = running(5);
    game.score = 123;
    game.distance_q8 = 123 * 10 * 256;
    game.best_score = 9;
    game.up_held = true;
    game.obstacles[0] = (dino_obstacle_t){.active = true, .kind = DINO_CACTUS_TALL,
                                       .x_q8 = (DINO_PLAYER_X + 10) * 256, .width = 25, .height = 50};
    dino_game_tick(&game, 0);
    dino_game_tick(&game, 17);
    assert(game.mode == DINO_GAME_OVER && !game.up_held && !game.down_held);
    assert(game.best_score == 123);
    uint32_t best = 0;
    assert(dino_game_take_best(&game, &best) && best == 123);
    assert(!dino_game_take_best(&game, &best));
    dino_game_action(&game);
    assert(game.mode == DINO_RUNNING && game.score == 0 && game.best_score == 123);
    assert(!game.jump_q8 && !game.velocity_q8 && !game.up_held && !game.down_held);
    assert(game.random_state == game.initial_seed);
    for (unsigned i = 0; i < DINO_OBSTACLE_COUNT; i++) assert(!game.obstacles[i].active);
    game.score = 124;
    dino_game_suspend(&game);
    assert(dino_game_take_best(&game, &best) && best == 124); /* exiting can preserve an earned record */
}

static void test_bounded_timing_and_rollback(void) {
    dino_game_t game = running(6);
    assert(!dino_game_tick(&game, -1));
    dino_game_tick(&game, 0);
    dino_game_tick(&game, 10000);
    assert(game.simulation_steps <= 6);
    assert(game.discarded_ms == 9900);
    unsigned before = game.simulation_steps;
    dino_game_tick(&game, 20000);
    assert(game.simulation_steps - before <= 6);
    dino_game_key(&game, DINO_KEY_DOWN, true);
    assert(dino_game_tick(&game, 19999));
    assert(game.mode == DINO_PAUSED && !game.down_held);
    game = running(7);
    dino_game_tick(&game, 0);
    dino_game_tick(&game, INT64_MAX);
    assert(game.simulation_steps <= 6 && game.discarded_ms == UINT32_MAX);
}

static void test_determinism_and_fair_generation(void) {
    dino_game_t a, b;
    dino_game_init(&a, 77, 0);
    dino_game_init(&b, 77, 0);
    dino_game_action(&a);
    dino_game_action(&b);
    for (int stamp = 0; stamp <= 2500; stamp += 10) dino_game_tick(&a, stamp);
    for (int stamp = 0; stamp <= 2500; stamp += 20) dino_game_tick(&b, stamp);
    assert(a.simulation_steps == b.simulation_steps && a.score == b.score);
    assert(a.random_state == b.random_state);
    assert(!memcmp(a.obstacles, b.obstacles, sizeof(a.obstacles)));
    dino_game_t game;
    dino_game_init(&game, 99, 0);
    dino_game_action(&game);
    unsigned spawned = 0;
    bool saw_bird = false;
    for (int stamp = 0; stamp < 90000; stamp += 20) {
        bool was_active[DINO_OBSTACLE_COUNT];
        for (unsigned i = 0; i < DINO_OBSTACLE_COUNT; i++) {
            if (game.obstacles[i].x_q8 < 90 * 256) game.obstacles[i].active = false; /* remove only for generation inspection */
            was_active[i] = game.obstacles[i].active;
        }
        dino_game_tick(&game, stamp);
        assert(game.mode == DINO_RUNNING && game.speed_px_s <= 190);
        for (unsigned i = 0; i < DINO_OBSTACLE_COUNT; i++) {
            if (!was_active[i] && game.obstacles[i].active) {
                spawned++;
                assert(game.spawn_remaining_q8 >= ((int)game.speed_px_s * 9 / 10 + 60 + game.obstacles[i].width) * 256 - 4 * 256);
                if (game.obstacles[i].kind == DINO_BIRD) { assert(game.score >= 100); saw_bird = true; }
            }
        }
    }
    assert(spawned > 20 && saw_bird && game.speed_px_s == 190);
}

static void test_original_frames(void) {
    dino_game_t game = running(8);
    assert(dino_sprite_masks[DINO_SPRITE_TREX_STAND].width == 44);
    assert(dino_sprite_masks[DINO_SPRITE_TREX_STAND].height == 47);
    assert(dino_sprite_masks[DINO_SPRITE_TREX_DUCK_0].width == 59);
    assert(DINO_DUCK_HEIGHT == 25);
    assert(dino_game_player_frame(&game) == DINO_SPRITE_TREX_RUN_0);
    game.simulation_steps = 5;
    assert(dino_game_player_frame(&game) == DINO_SPRITE_TREX_RUN_1);
    game.down_held = true; game.simulation_steps = 0;
    assert(dino_game_player_frame(&game) == DINO_SPRITE_TREX_DUCK_0);
    game.simulation_steps = 8;
    assert(dino_game_player_frame(&game) == DINO_SPRITE_TREX_DUCK_1);
    game.jump_q8 = 1;
    assert(dino_game_player_frame(&game) == DINO_SPRITE_TREX_STAND);
    game.mode = DINO_GAME_OVER;
    assert(dino_game_player_frame(&game) == DINO_SPRITE_TREX_CRASH);
}

static void test_jump_timings_at_all_speeds(void) {
    for (unsigned score = 0; score <= 250; score += 50) {
        for (unsigned kind = 0; kind < 2; ++kind) {
            unsigned safe_starts = 0;
            for (int x = 60; x <= 180; ++x) {
                dino_game_t game = running(9);
                game.score = score; game.distance_q8 = score * 10 * 256;
                game.obstacles[0] = (dino_obstacle_t){.active = true, .kind = kind, .x_q8 = x * 256,
                    .width = kind == DINO_CACTUS_TALL ? 25 : 17, .height = kind == DINO_CACTUS_TALL ? 50 : 35};
                dino_game_key(&game, DINO_KEY_UP, true);
                dino_game_key(&game, DINO_KEY_UP, false);
                advance(&game, 0, 1800);
                if (game.mode == DINO_RUNNING) ++safe_starts;
            }
            assert(safe_starts >= 12); /* at least 63ms at max speed, usually much wider */
        }
    }
}

/* Duck animation repeats in 15 steps, wings in 20: sixty covers every pair.
 * Test the whole horizontal encounter, not one favorable frame/position. */
static void test_bird_body_collisions_all_animation_phases(void) {
    for (unsigned phase = 0; phase < 60; ++phase) {
        for (unsigned level = 0; level < 3; ++level) {
            for (unsigned duck = 0; duck < 2; ++duck) {
                dino_game_t game = running(10);
                game.simulation_steps = phase; game.down_held = duck;
                unsigned collisions = 0;
                for (int x = -46; x <= 100; ++x) {
                    dino_obstacle_t bird = {.active = true, .kind = DINO_BIRD,
                        .x_q8 = x * 256, .width = 46, .height = 40, .flight_level = level};
                    collisions += dino_game_collides(&game, &bird);
                }
                if (level == 2 || (level == 0 && duck)) assert(collisions == 0);
                else assert(collisions > 0);
            }
        }
    }
}

static void test_held_duck_clears_middle_bird_at_score_828(void) {
    for (unsigned phase = 0; phase < 60; ++phase) {
        for (unsigned level = 0; level < 3; level += 2) {
            for (int x = 90; x <= 180; x += 15) {
                dino_game_t game = running(11);
                game.simulation_steps = phase;
                game.score = game.best_score = 828;
                game.distance_q8 = 828 * 10 * 256;
                game.obstacles[0] = (dino_obstacle_t){.active = true, .kind = DINO_BIRD,
                    .x_q8 = x * 256, .width = 46, .height = 40, .flight_level = level};
                dino_game_key(&game, DINO_KEY_DOWN, true);
                for (int stamp = 0; stamp < 1600; stamp += 17) {
                    dino_game_tick(&game, stamp);
                    assert(game.mode == DINO_RUNNING && game.down_held && dino_game_crouching(&game));
                    dino_sprite_id_t frame = dino_game_player_frame(&game);
                    assert(frame == DINO_SPRITE_TREX_DUCK_0 || frame == DINO_SPRITE_TREX_DUCK_1);
                }
                assert(!game.obstacles[0].active && game.best_score >= 828);
                dino_game_key(&game, DINO_KEY_DOWN, false);
                assert(!dino_game_crouching(&game));
            }
        }
    }
}

static void test_low_bird_requires_jump(void) {
    dino_game_t duck = running(12);
    duck.score = duck.best_score = 828; duck.distance_q8 = 828 * 10 * 256;
    duck.obstacles[0] = (dino_obstacle_t){.active = true, .kind = DINO_BIRD,
        .x_q8 = 140 * 256, .width = 46, .height = 40, .flight_level = 1};
    dino_game_key(&duck, DINO_KEY_DOWN, true);
    advance(&duck, 0, 1600);
    assert(duck.mode == DINO_GAME_OVER && duck.best_score >= 828);
    for (unsigned phase = 0; phase < 20; ++phase) {
        unsigned safe_starts = 0;
        for (int x = 60; x <= 180; ++x) {
            dino_game_t jump = running(13);
            jump.score = 828; jump.distance_q8 = 828 * 10 * 256;
            jump.simulation_steps = phase;
            jump.obstacles[0] = (dino_obstacle_t){.active = true, .kind = DINO_BIRD,
                .x_q8 = x * 256, .width = 46, .height = 40, .flight_level = 1};
            dino_game_key(&jump, DINO_KEY_UP, true);
            dino_game_key(&jump, DINO_KEY_UP, false);
            advance(&jump, 0, 1800);
            if (jump.mode == DINO_RUNNING) ++safe_starts;
        }
        assert(safe_starts >= 12);
    }
}

int main(void) {
    test_ready_pause_resume();
    test_jump_land_and_press_ownership();
    test_crouch_and_suspend();
    test_collision_boxes();
    test_jump_clears_tall_obstacle();
    test_game_over_restart_and_best();
    test_bounded_timing_and_rollback();
    test_determinism_and_fair_generation();
    test_original_frames();
    test_bird_body_collisions_all_animation_phases();
    test_held_duck_clears_middle_bird_at_score_828();
    test_low_bird_requires_jump();
    test_jump_timings_at_all_speeds();
    puts("Dino logic: PASS (original body collisions, all 60 bird/duck animation phases, score-828 held duck, low-bird jump, pause, retry, best, timing, fair spawns)");
    return 0;
}
