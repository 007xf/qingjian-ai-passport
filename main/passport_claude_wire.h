#pragma once
#include "passport_claude_model.h"

/* Canonical flat JSON, at most 512 bytes. The optional enabled field changes
 * display preferences only on explicit upload; omission preserves the device. */
bool passport_claude_wire_parse(const unsigned char *json, size_t size,
                               passport_claude_snapshot_t *snapshot,
                               bool *enabled_present, bool *enabled);
