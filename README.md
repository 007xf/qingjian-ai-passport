**English** · [简体中文](README.zh_CN.md)

# Qingjian · A wearable badge that grows with your AI work

Qingjian combines a personal badge, three evolving character forms, AI usage dashboards and a classic Dino game. Edit your identity and pictures on your Mac, then take your badge with you.

![Concept illustration, not a device screenshot](docs/images/qingjian-cover.png)

This is an independent community project developed with AI assistance. It is not an official FoloToy, OpenAI, Cursor, Google or TYPE-MOON product.

- [Download the Mac app, firmware and checksums](https://github.com/007xf/qingjian-ai-passport/releases/tag/v1.3.1)
- [Source and issues](https://github.com/007xf/qingjian-ai-passport)
- AI Passport community: [project 328 / author review workspace](https://ai-passport.folotoy.cn/account/?project=328), revision 544, **pending moderation**; [public community index](https://ai-passport.folotoy.cn/plays/). A pending submission is not yet a public play page.
- Version: **1.3.1, Mac build 10 — community preview**.

## Compatibility and installation

The bundled Mac app targets **Apple Silicon, macOS 14 or later**. Python, image conversion, serial and Bluetooth dependencies are included. Intel Macs and other platforms are not supported by this build. Moving the bundle and using an empty user profile have been tested on the development Mac; this does not represent physical testing of every supported macOS release or account type.

The app has an ad-hoc integrity signature, **not a Developer ID signature or Apple notarization**. After verifying the download and checksum, follow [Apple's instructions](https://support.apple.com/102445) if macOS blocks the initial launch. Do not disable Gatekeeper globally. Investigate a damaged-file or malware warning instead of bypassing it blindly.

Download and unzip the complete app, move the extracted app bundle to Applications, and open it. Choose USB in Connection Settings, connect a powered-on badge using a data-capable cable, then verify the battery and time. Changes to your profile, feature selection and pictures require Upload to Badge; Save Draft only saves on that Mac. Existing badge settings and local drafts are retained.

## Firmware files: choose the correct address

This firmware targets the compatible ESP32-C3, 8 MB AI Passport layout. Back up your own device before installation and keep its private recovery link private.

- `qingjian-1.3.1-full.bin` is a **merged image for address 0x0**, including the bootloader, partition table and application. This is the community installer artifact. It may replace application settings even when chip erase is disabled.
- `qingjian-1.3.1-app.bin` is an **application-only image for address 0x10000**, for an already compatible layout. Never flash this file at address zero.
- Neither release file contains a device Flash dump, card identity or pairing secrets. Both end before the protected identity region. Never enable a whole-chip erase or overwrite the identity/recovery regions. Do not use an image on an unknown layout or modified board.
- Close serial clients before flashing and keep the cable connected. Use the [official installation tools](https://ai-passport.folotoy.cn/tools/web-flasher/) and your own [factory recovery instructions](https://ai-passport.folotoy.cn/guides/restore-factory-firmware/).

## First-launch preset and daily use

New profiles use Aoko Aozaki, the title `MISS BLUE`, the Chinese introduction shown in the product preview, and thresholds **77,777,777 / 555,555,555**. Codex, Cursor, Agents and Dino are selected; Gemini remains available but is not selected. The winter-scarf, red-dress and red-haired forms use the previously selected artwork. Initial usage is unknown until real data is read: no author account or example Token count is included.

Edit each of the three pictures and both thresholds independently. The Mac's Clear Preview uses the original picture; Device Pixels shows the actual converted artwork. Built-in art uses native 160×160 RGB565 pixels without an intermediate 128-pixel upscale. Custom pictures retain the compatible compressed codec and may have less detail.

**All picture changes, including restoring defaults, require USB.** The Mac keeps three originals, while the badge caches one custom form. Switching to another custom form requires USB synchronization. Bluetooth continues to synchronize text, settings, time, usage and activity; a Pending USB message does not mean a picture was uploaded.

While the app is running, ordinary synchronization happens about once a minute. App exit, Mac sleep and disconnection stop live updates. Saved badge content and Dino remain available offline; a cold boot needs time synchronization.

## Connect your own AI tools

**Codex:** Sign in to the local Codex app or a compatible CLI. Qingjian reads the current account's available quota windows separately from the Tokens found in this Mac's accessible logs. Tokens include input, output and cached input, with cumulative-event and inherited-fork deduplication. They are not a bill or a cross-device account total. Account changes and separate `CODEX_HOME` profiles are isolated. Growth follows the account's weekly quota reset; after actually using a reset card, confirm that event in Qingjian. Qingjian never redeems or buys a reset card.

**Cursor:** Sign in to the standard local Cursor app and choose Refresh Usage. The two bars show **used**, not remaining, percentages for Cursor Models and Other Models, plus the billing reset. The collector uses the same internal read-only service as the current Cursor client, rather than a public stable API. Upstream changes, custom user-data directories or alternative builds can require an update. No model task, plan change or on-demand spending change is made. Cache lifetime is at most five minutes; missing and expired values remain unknown. Each Mac reads its user's own login and stores no copied credentials.

For Cursor task status, use Data Sources to install the official activity hooks, run a new task, and detect it again. Configured and Real Event Received are separate states.

**Gemini:** The optional integration covers **Gemini CLI** local records and official hooks. It does not connect Gemini Mac, the website or Antigravity. The CLI must itself be able to sign in and run for that account. No records means unknown, not a fabricated zero. The personal CLI tested during development was rejected by its service, so universal Gemini connectivity is not claimed.

**Agents:** Only selected AI sources appear. Deselect Gemini and its entire row disappears. Working, idle, waiting and error labels come from observed events, expire within three minutes, and are not inferred from a running process or historical usage.

Grok Bot was removed. The separate desktop pet was canceled; voice/audio and Wi-Fi features are not part of this firmware.

## Buttons, Bluetooth and Dino

Use Up/Down to move between enabled pages and OK for the current page's action. Hold OK to return. Quickly press OK three times to turn the screen off; a complete gesture on any function key wakes it without activating another page. Screen-off is not power-off.

To pair Bluetooth, open Settings → Bluetooth Pairing on the badge, or request its two-minute pairing window over USB. Search from the Mac, explicitly choose your badge, and enter the six-digit code displayed on the badge when macOS asks. Permit Bluetooth access if requested. Pair each Mac normally; another Mac's saved peripheral identifier is not portable.

Dino uses the original Chromium classic sprites: Up jumps, holding Down crouches, release stands up, short OK starts/pauses/resumes/retries, and long OK exits. Mid/high birds can be passed while crouching; the lowest bird requires a jump. Official body collision boxes fix the wing-tip collision that previously stopped a crouched player. Best score is saved. The badge adaptation does not promise browser frame rates.

## Troubleshooting and privacy

If USB is missing, check power, a data-capable cable and competing serial clients. If usage is unknown, check the source account and recent logs, then refresh. Never erase Flash to fix a connection issue. If an integration changes upstream, report a sanitized example through Issues rather than guessing a number.

Settings, images, hashed activity metadata and caches stay under the current user's Application Support directory. Do not publish that directory, credentials, full session logs, private firmware backups or device recovery links. This release includes none of them.

See [validation](docs/RELEASE-VALIDATION.md), [build notes](macos/README.md), and [usage boundaries and disclaimer](docs/DISCLAIMER.md). Retain MIT, Chromium BSD, font OFL and runtime dependency licenses. Aoko images are generated fan art; code licenses do not grant rights to third-party characters or trademarks. The cover is a concept illustration, not a hardware photograph.

The firmware builds on [FoloToy's open AI Passport project](https://github.com/FoloToy/ai-passport). Please include version, connection type, steps and sanitized screenshots in bug reports.
