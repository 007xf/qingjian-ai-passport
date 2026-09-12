<p align="right"><a href="README.zh_CN.md">简体中文</a> · <strong>English</strong></p>

# Passport Badge

This fork starts directly on an editable employee badge. Qingjian on the Mac manages name, title, introduction, three evolution images, two token thresholds, and optional page visibility. The device displays CW2017 battery state, synchronized local time, a Codex quota dashboard, and a playable Dino game. USB and encrypted Bluetooth LE use the same application protocol. Wi-Fi and audio are not started.

## Operation

- Connect the powered device by USB and open Qingjian on the Mac, or explicitly select a previously paired Bluetooth device. Edit and upload; success requires device readback.
- Defaults are the winter, mage, and red-haired Aoko forms. Thresholds default to 77,777,777 and 555,555,555 tokens. Both thresholds and each stage image are editable.
- Triple-click OK to switch the backlight off. A complete function-key gesture wakes the screen without acting on the page. UP/DOWN change visible pages; long OK returns. The hardware power switch remains independent.
- Each short gesture is completed after a 350 ms inter-click gap; long hold is 800 ms. Screen-off keeps the processor running and is not deep sleep.
- Time is anchored to the Mac's UTC clock and local offset through the selected transport. A cold boot is unsynchronized until the next sync; disconnecting the transport does not stop the running clock.

## Bluetooth connection

Open the 120-second pairing window from device Settings or by sending `PAIR` through trusted USB. During first pairing, the device displays a six-digit passkey; enter that value in the Mac's Bluetooth prompt. Pairing and bonding are handled by the encrypted BLE service. Opening the window or receiving `PAIR_OK` is not proof that the Mac has finished pairing.

The advertisement name is `QingJian-` followed by the last six hexadecimal MAC digits. The Mac editor scans for candidates and requires a user selection or a saved macOS peripheral UUID. A name match alone is not authentication. macOS UUIDs identify peripherals on that Mac; the firmware's `device_id` is the stable base-MAC identifier used across USB and BLE. Changing transports does not start a new token cycle or replay an evolution animation.

The NUS service is `6E400001-B5A3-F393-E0A9-E50E24DCCA9E`; RX is `6E400002-B5A3-F393-E0A9-E50E24DCCA9E` for writes, and TX is `6E400003-B5A3-F393-E0A9-E50E24DCCA9E` for notifications. The host subscribes before sending requests, splits writes to fit the negotiated MTU, and allows only one request in flight. Receive lines are bounded. Disconnects discard partial messages and transfer state, and responses are kept with their originating transport/session. Timed-out mutations are not retried automatically.

The Mac backend keeps one asyncio event loop for a whole BLE session. First connection has a 60-second deadline to allow discovery and passkey entry; the synchronous adapter allows additional disconnect-cleanup time. BLE synchronizes time, tokens, quota, text and settings. All avatar writes, including restoring built-in artwork, require USB. BLE pairing and operation have passed local user acceptance. USB image upload, readback, reboot persistence and restoration, repeated non-resetting reconnects, and protected-flash readback were also verified; new dashboard readback is a separate acceptance step.

## Codex quota dashboard

The dashboard shows the actual primary Codex quota bucket, with zero, one or two windows according to the source. It does not invent a five-hour window when the account only returns a weekly window. Each available window displays its actual duration, reset time and remaining percentage, computed as `clamp(100 - usedPercent, 0, 100)`. Missing, invalid and unavailable values stay unknown. Other model buckets, including Spark, are not substituted for the primary bucket.

The preferred source is the read-only Codex App Server quota endpoint; local token-log quota snapshots are an explicit fallback. Source and stale state remain visible. Freshness is based on the source observation time, with a maximum TTL of 180 seconds and earlier expiry at a window reset when applicable. Re-sending a cached snapshot, or seeing the same snapshot repeated in another token event, does not renew its TTL. Clock rollback, missing time and expired data cannot be shown as fresh.

Quota percentage and locally counted token activity are separate metrics. The dashboard does not change subscriptions, purchase credits or redeem reset cards.

## Local provider and Agent dashboards

Qingjian 1.2.0 adds local Cursor/Gemini counters and an Agent page with separate Codex, Cursor and Gemini activity rows. The Mac checks `provider_dashboard_supported` before synchronization. Activity and cumulative counters have separate source timestamps; working state expires after at most 180 seconds, while a cumulative counter retains its original observation time. Missing, partial, future and expired data are labelled explicitly. Process presence, login state and a historical token count are never sufficient evidence of a running task.

- Cursor counts distinct requests in its local code-tracking database. This covers code activity recorded there, not all conversations, tokens, personal quota or billing. Lifecycle state comes from [official Cursor hooks](https://cursor.com/docs/hooks). The local CLI request and a real `sessionEnd` event were verified on this Mac.
- Gemini reads token metadata from local Gemini CLI model records and receives [official CLI lifecycle hooks](https://geminicli.com/docs/hooks/reference/). It does not cover the Gemini website, app, Antigravity or all-account quota. The current personal CLI acceptance request was rejected by the service and produced no new tokens; hook installation and readable historical records do not establish full online connectivity.
- Codex activity uses a private incremental index of actual task-start, completion and abort events. Token events can refresh only an already observed active task; they cannot create a working state. Incomplete coverage cannot establish idle state or a total session count. Fresh positive task evidence may still establish working/waiting/error while the total remains unknown. Fork history, replacement runs, rotation and truncation are handled explicitly.
- Grok Bot has been removed from selectable features, device pages and source setup. Its legacy bit remains reserved so existing settings retain their durable identifiers; the separate Mac application is unchanged.

The opt-in hook receiver writes only hashed session/generation identifiers, state, timestamps and a bounded model label to private application-support storage. It never stores prompts, responses, source paths, email addresses or credentials. Installation merges existing hook arrays/settings and preserves a backup. Moving the Mac app updates an already installed runtime mapping at the next launch; it does not silently install hooks for a new user. The collector's own Codex activity database is separate from the read-only source logs.

The Agent overview follows the same feature selections as the page list: disabling Gemini, Cursor or Codex also hides that source's activity row. Enabled rows are packed without empty gaps; if no AI source is selected, the overview directs the user to the Mac feature settings. Switching back to all supported features restores all three source rows.

## Dino

The game uses the actual [Chromium 98.0.4758.55 sprite atlas](../../../assets/dino/README.md), including the dinosaur, cacti and birds, with its BSD license and reproducible conversion. The original appearance and controls passed user device acceptance. This is a device adaptation, not a browser-frame-rate promise.

UP jumps and holding DOWN crouches; releasing DOWN ends the crouch. Short OK pauses or resumes, and long OK exits the game. Triple OK switches the screen off and pauses the run; the first complete wake gesture only wakes the screen. Pause, exit and screen-off use the best-score save path rather than writing flash on every frame. Best-score persistence and button timing remain explicit regression checks for subsequent firmware changes.

Collisions use the fixed body rectangles from the bundled Chromium 98 `checkForCollision`, `Trex.collisionBoxes` and classic obstacle definitions. Animated wing tips are not damage regions. This fixes the middle-height bird intermittently ending a held crouch as a crash; middle and high birds can be passed by crouching in every animation phase, while low birds still require a jump. Host regression covers the full 60-step combined wing/duck cycle, complete encounters at score 828 and maximum speed, jumpable low birds, and preserved best scores. The updated bird behavior still requires a separate device check.

## Token cycle and evolution

Only locally available Codex token-count records are indexed, including cached input and output. Repeated cumulative events and duplicate session files are not summed twice; inherited fork events are excluded. This is not a bill, quota percentage, or guaranteed all-account total. Incomplete current-cycle coverage is reported as unknown.

The weekly window in the official main Codex quota bucket establishes cycle boundaries. A verified natural weekly rollover starts a new cycle. The current interface does not provide a reliable reset-card redemption event: after using a card, choose the explicit reset-card confirmation in the Mac app. That action changes this badge's accounting epoch only; it does not redeem or buy a card. Account changes, source failures and stale records are reported distinctly.

The final-stage animation runs on a known upward threshold crossing while the badge is visible. It does not replay merely because the device reconnects at an already-high value, USB changes to BLE, or thresholds are edited. The editor remains open to synchronize new counts over the selected transport. The separate Coding Pet feature has been cancelled; badge-avatar evolution remains available.

## Storage and hardware boundaries

Names, thresholds and feature choices use the ordinary NVS partition. The device caches one stage-tagged custom image; the Mac retains all three custom choices and transfers the active one over USB before the corresponding token update. A BLE sync never replaces or clears that cache: pending artwork is reported as `avatar_sync=usb_required`, while time, token and quota updates continue. When a different custom stage is needed, USB must install that stage; the three custom images are not all resident on the badge. The editor can save drafts offline without applying them during automatic sync.

Custom images use an 8,224-byte `APZ1` packet: a 16-byte little-endian `<4sHHII` header, zlib-compressed RGB565 pixels, and zero padding. The encoder chooses the largest fitting square resolution from 128, 112, 96, 80, 64 and 56 pixels. Decoding validates dimensions, compressed and raw lengths, and stream integrity before installation, then scales to the fixed 128 by 128 display buffer. Built-in images retain full 128 by 128 RGB565 pixels; the older indexed format is only a compatibility path.

Transparent source images are contained in a square and composited onto black without cropping the figure. The Mac pixel preview uses the same RGB565 conversion and nearest-neighbor mapping as the device. This preserves full RGB565 color rather than reducing new uploads to a 16-color palette.

NVS failures never trigger a partition erase. A failure after commit is reported as unconfirmed; a later readback or reboot can reveal an already committed change.

The installed CW2017 profile is preserved. Read-only attachment requires active mode and an existing profile flag; it does not calibrate cell accuracy. Unknown readings display a dash. No pin, partition, bootloader, identity or recovery-layout changes are required.

On an existing device, back up the complete flash, inspect its actual partition map, and write only the verified application at 0x10000 when compatible. Never perform whole-chip erase. Keep personal backups and logs outside source and community packages.

## Development

The baseline is official FoloToy main commit `f75873f1aab24ac4c0ba9394c131669f66cce650`, ESP-IDF 5.5.3, ESP32-C3, 8 MB, no PSRAM. Run `tools/validate.sh --static`, `--firmware`, and the complete gate. Host tests cover gesture isolation, visibility subsets, persistence encoding, time, evolution thresholds, token accounting, quota freshness, Dino logic, APZ1 decoding, upload readback and BLE transport behavior. Hook privacy/freshness, provider indexing and counter provenance have host coverage. Build, host tests and physical-device results must be reported separately; a later App rebuild does not inherit new dashboard acceptance automatically.

The five retained choices are Codex, Cursor, Gemini, Agent and Dino (supported mask `0x97`). Local provider dashboards are implemented within the source boundaries above; the current Gemini personal CLI rejection remains unresolved. Coding Pet is cancelled, AI Talk is deferred, and iPhone support remains future work. A selected page never implies a connected provider.

## Backend and wire protocol

`tools/passport_bridge.py` provides shared `status`, `sync`, `upload --config` and `screen 0|1` commands. Global `--port` selects USB; global `--ble-id` selects an explicitly chosen macOS UUID, and the two options are mutually exclusive. `ble-scan` only returns candidates. `pair` is USB-only and requests the pairing window. `preview-avatar --source --output` is local image processing and never connects to a device. The Mac app uses its `AI Passport/Bridge` application-support directory for verified backend configuration; standalone CLI calls must use the same `--state-dir` if they should share that configuration.

Both transports carry newline-terminated `@AP` text commands:

- `STATUS`, `TIME <utc_ms> <offset_min>` and `SCREEN <0|1>` query or update basic state.
- `BADGE <base64-json>`, `FEATURES <all> <mask>` and `THRESHOLDS <first> <final>` update editable preferences. Successful queue acknowledgement is followed by saved-state readback.
- `TOKENS <count|UNKNOWN>` updates token activity without a recurring NVS write. Confirmed cycle changes send zero before the new count; switching transport does not.
- `CODEX <base64-json>` sends a flat quota snapshot containing source, observation/expiry times and nullable fields for the two actual windows. The host checks the firmware capability before using this command.
- `PROVIDER <base64-json>` sends a bounded provider snapshot with independent activity and metric times. The host checks `provider_dashboard_supported` and confirms the provider update ID after acknowledgement. Missing coverage never produces a fabricated count or working state.
- USB-only `AVATAR BEGIN 8224 <sha256> <stage>`, consecutive `AVATAR DATA <offset> <base64>`, and `AVATAR END` install a verified custom image. USB-only `AVATAR DEFAULT` clears the custom cache and restores built-in artwork. BLE rejects every `AVATAR` command with `@AP ERROR avatar_requires_usb`; `STATUS` advertises `avatar_upload_usb_only: true`.
- `PAIR` is accepted through trusted USB to open the pairing window. It does not itself complete system pairing.

Service credentials, account email addresses and conversation contents are not transmitted to the badge. The BLE module and optional dependency pin are `tools/passport_ble.py` and `tools/requirements-ble.txt`; the bundled Mac app needs its Bluetooth usage-description permission entry and the CoreBluetooth/PyObjC runtime dependencies. Runtime USB on macOS uses `tools/passport_serial.py` to suppress application-issued DTR/RTS transitions and clear hang-up-on-close; flashing tools retain their independent reset workflow.

## Art and sources

The included Aoko avatars are generated fan illustrations based on costume references, with rounded faces and full-body transparent sources. They are not official TYPE-MOON artwork and the code license does not grant rights to the underlying character.

- [Official hardware repository](https://github.com/FoloToy/ai-passport)
- [Official Aoko character page](https://www.typemoon.com/products/mahoyo/character.html)
- [Official product and setting-book information](https://typemoon.com/products/mahoyo/product/)
- [Official FGO Aoko collaboration page](https://news.fate-go.jp/2024/mahoyo_after_night_pu/)
- [Google Noto Sans SC font source](https://github.com/google/fonts/tree/main/ofl/notosanssc), distributed under the included OFL license.

## Source setup in Qingjian

The Mac app's **Data sources** panel checks configuration integrity and activity receipt separately, then offers an explicit configure/repair action for each supported observer. Cursor uses official desktop Agent/CLI hooks. Gemini CLI hooks are labeled separately from the installed native Gemini Mac app; no native Gemini activity or token interface is currently connected. Configuration does not prove successful authentication, fresh activity, or subscription quota access.

Source diagnostics never open a device, request authentication, or run a model. Setup preserves unrelated hooks and settings, validates all selected configurations first, saves private exact-byte backups, checks for concurrent edits, and verifies writes. A failed installation restores only files still owned by that installation, preserving concurrent user edits. Existing disabled or customized observer settings are not silently overridden.

Local commands are `sources-status` and `sources-configure --provider cursor` (or `--provider gemini`). The application supplies its bundled runtime; its own connection to the badge is configured independently.

References: [Cursor hooks](https://cursor.com/docs/hooks), [Gemini native Mac app](https://gemini.google/aw/mac/?hl=en), [Gemini CLI hooks](https://geminicli.com/docs/hooks/reference/).

## Cursor account quota and native avatar detail (2026-09-13)

Cursor now reads the signed-in desktop account through the client's internal read-only GetCurrentPeriodUsage/GetPlanInfo endpoints. These are not a public stable API. The two USED percentages and billing reset stay separate from Token growth; credentials are never cached. Snapshots expire within five minutes and are isolated by account fingerprint. USB/BLE carry the normalized CURSOR snapshot. App build 9 includes the collector and displays both pools.

Unchanged Aoko source art is compiled directly to native 160x160 RGB565 in Flash. The Mac editor defaults to original-resolution previews and offers a separate exact device-pixel mode; the custom 128x128 codec remains compatible.
