<p align="right">
  <a href="CHANGELOG.zh_CN.md">简体中文</a> · <strong>English</strong>
</p>

# Changelog

## Unreleased

- Removed Grok Bot from the Mac feature/source controls and device navigation, preserving its old protocol bit and the separate Mac app. Codex, Cursor, Gemini, Agent and Dino remain selectable; Agent rows now follow the same enabled-source choices.
- Corrected Dino bird collisions to Chromium 98's fixed body boxes instead of animated pixel masks. A middle bird's wing no longer ends a held crouch as a crash. Host regression covers all wing/duck phase pairs, full score-828 encounters, low-bird jumps and best-score preservation; the new bird behavior awaits device acceptance.

- Qingjian 1.3.0 adds source diagnostics and explicit, backed-up setup for official Cursor/Gemini CLI activity hooks, with native-app limitations clearly separated.

- Qingjian 1.2.0 adds local Cursor code-activity counts, Gemini CLI token metadata and per-provider Agent states. Official hooks persist bounded anonymous activity; a private incremental Codex event index requires real task starts before treating progress as working. Missing/incomplete/expired sources stay explicit, and the Mac checks firmware support. Existing hook settings are merged with backups; moving the app updates only an already installed runtime mapping. Threshold labels retain exact integer values.
- Cursor CLI and its real terminal hook event passed local verification. The current Gemini personal CLI request was rejected by the service and yielded no new tokens; Grok desktop still has no supported external status bridge. These limitations are not presented as fully connected providers.
- Dino now renders the original Chromium sprite atlas with reproducible conversion and BSD attribution; its appearance and controls passed user device acceptance. Bluetooth use, USB-only image upload/restoration and persistence, non-resetting USB reconnects, and protected-flash readback were verified locally. The final 1.2.0 App build, deep signature verification and 200 bundled-runtime tests passed; new-dashboard UI/device readback remains a separate acceptance step.

- Restricted custom avatar writes and default-image restoration to USB. BLE continues text/settings/time/token/quota sync and reports pending stage artwork without clearing the one-image device cache. Qingjian 1.1.1 adds offline draft saving, explicit USB-only image messages, and a macOS runtime serial adapter that avoids application DTR/RTS reset transitions.
- Added the editable Passport Badge with synchronized time, battery state, three-stage avatar evolution, configurable thresholds and triple-OK screen-off with complete wake-gesture isolation. New custom artwork uses bounded APZ1 zlib-compressed RGB565 with adaptive 56–128 pixel resolution, black-background alpha compositing and matching host pixel previews.
- Added the primary Codex quota dashboard with the source's actual zero, one or two windows, remaining-percentage conversion, source labels and a maximum 180-second source-time TTL. Cached/log-repeated snapshots do not become fresh merely by being resent; unsupported or missing values stay unknown.
- Added playable Dino with jump, held crouch, pause/resume, long-OK exit, screen-off pause and best-score persistence. The independent Coding Pet feature is cancelled, while badge-avatar evolution remains; AI Talk is deferred and other AI provider integrations remain incomplete.
- Implemented encrypted BLE NUS alongside USB for the same `@AP` protocol, an explicit 120-second pairing window from Settings or trusted USB, on-device six-digit passkey entry on the Mac, bonding and disconnect/session isolation. The Mac backend requires a selected peripheral UUID, subscribes before MTU-fragmented writes, gives first connection 60 seconds, and preserves token-cycle identity across transport changes. BLE physical acceptance is still in progress; this release does not start Wi-Fi or audio.

- Added the supplied 80-byte CW2017 profile for the specified 520 mAh cell, including content/update-flag checks, verified writes, the required restart sequence, and bounded SOC-readiness polling.

- Expanded the environment bootstrap document: added Espressif's Git service mirror (`git.espressif.com.cn`) as the preferred mainland-China route for ESP-IDF v5.5.3 and its submodules, documented submodule long-wait/timeout handling, in-place repair, and the pinned-commit shallow fetch for large submodules such as `esp32-wifi-lib`, warned about stale per-repository Jihulab `insteadOf` residue, and added the official offline release archive as a last-resort fallback (learned from `esp-mosaico/esp-mosaico-vibe`).

- Reorganized the documentation by function area with a dual entry point: the root `AGENTS.md` is now a thin router (hard constraints + task routing only) and the detailed AI workflow lives in `docs/development/ai-guide.md`; `agent-guide.md` was folded in. `docs/development/` gained a second level (`engineering/`, `ci/`, `release/`), and the `plays/` application archive and `experiences/` moved into a `docs/reference/` area with a dedicated README. Removed `docs/software-design/` (empty scaffold); folded the three `assets/{fonts,images,music}/README` leaves into the `assets/` README; flattened the six `project-completion` sub-documents into a single file; and unified each directory to a single README, eliminating every `INDEX` file and a duplicated experience index. All cross-references and bibliographic links were updated; no content was dropped.

- Removed the obsolete app/test partition at `0x700000` and its related
  bootloader, validation, and documentation requirements. The fixed protected
  `cardid` partition and its CI checks remain unchanged.
- Documented a release-title convention for multi-app releases: name tags as `v<version>-<app-name>` (e.g. `v0.1.0-voice-keychain`) so the release title carries the version and the app, and confirm the title after the release is published so a release list is scannable by app.
- Added a post-release follow-up workflow: an `issue-suggestions` skill for filing user feedback as issues against the upstream project, an `experience-pr` skill for submitting reusable development experience as a documentation PR, a `docs/experiences/` directory for per-entry experience files, and supporting `project-completion`, `file-issues`, and experience-index documents.
- Simplified the tracked repository root: moved GitHub-recognized community documents into `.github/`, moved the changelog into `docs/`, updated every reference, and added a root-document allowlist to repository checks.
- Repository-wide language policy: every maintained Markdown default `.md` file is English, Simplified Chinese uses a paired `.zh_CN.md`, and both provide language switches. Static checks reject missing peers, missing switches, and Chinese prose in English defaults.
- Phase one of the AI development workflow: streamlined task-based context routing, unified local/CI validation, added PR checks and a template, and committed the dependency lock for reproducible builds.
- PR review fixes: pinned GitHub Actions to full commit SHAs, split build/release jobs by least privilege, disabled persisted sync checkout credentials, added Feature Request and Usage Question forms, clarified private security-report fallback, and corrected stale README, CI-trigger, and branch descriptions.
- Changed commit titles, PR titles, and PR bodies from Chinese-default to English; updated the Chinese punctuation rule so it no longer applies to PR descriptions.
- Reworked `build-firmware.yml` to pass `SDKCONFIG_DEFAULTS=sdkconfig.defaults`, enable `partitions.csv`, preserve the 8 MB image header, merge a flashable `FoloToy-AI-Passport-full.bin`, publish only that artifact, and use Actions cache v5.
- Integrated upstream PR #6 to resolve PR #4 conflicts: Wi-Fi, Bluetooth LE, radio lifecycle, and low-power demos; a 3 MB factory partition; build/menu/configuration updates; hardware-guide coverage; and bilingual capability tables.
- Defined English imperative Conventional Commit formatting for both commits and PR titles.
- Removed stale sync-workflow template comments and generalized an irrelevant Redis TTL rule to cache components.
- Added Chinese punctuation, credential safety, and recoverable file-deletion conventions.
- Expanded source-comment requirements for functions, state, ownership, concurrency, timing, registers, and magic values.
- Removed AI execution instructions from product READMEs so they remain human-facing product and repository overviews.
- Added `docs/development/agent-guide.md` as the focused AI workflow guide.
- Updated `AGENTS.md`, `docs/INDEX.md`, and the development index for the agent guide.
- Documented why the root README path is reserved for fork owners and how GitHub README precedence supports it.
- Created `main-update` from the upstream-aligned baseline and combined the repository-structure, firmware-CI, and upstream-sync work.
- Corrected the merged documentation index, workflow path, project tree, and CI references.
- Moved CI documentation from software design to `docs/development/`.
- Moved fork-only documentation assets from `assets/docs/` to `docs/assets/`.
- Moved the upstream English/Chinese project READMEs under `docs/` and renamed the documentation catalog to `docs/INDEX.md`.
- Initialized `AGENTS.md`, `CLAUDE.md`, and `CHANGELOG.md`.
- Standardized the initial project README language filenames.
- Added the `docs/`, `assets/`, and `skills/` directory structure.
- Moved the upstream hardware guide into `docs/hardware-design/`.
- Standardized subdirectory README capitalization and introduced fork conventions.
- Allowed fork-owned root README and supplemental documentation content on fork `main`.
- Added and documented the fork-only supplemental-document directory.
- Moved the build CI document to its dedicated CI branch before consolidation.
- Documented clean-`main` reasons, the direct-development exception, and Actions enablement for forks.
- Split the original agent rules into contribution, development, and fork documents with a compact root index.
- Updated software-design and project README references for the new documentation structure.
- Added the documentation catalog and task-triggered routing based on the earlier repository model.
- Added bilingual contribution, code-of-conduct, security, and support documents tailored to this ESP-IDF and fork workflow.
