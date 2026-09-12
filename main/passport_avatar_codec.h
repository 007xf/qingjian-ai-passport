#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
// Decode a bounded legacy palette or APZ1 RGB565 asset into 128x128 pixels.
bool passport_avatar_decode(const uint8_t *data, size_t size, uint16_t *pixels);
