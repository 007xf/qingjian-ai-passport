**English** · [简体中文](RELEASE-VALIDATION.zh_CN.md)

# Release validation — 1.4.0, Mac build 12

- Build: PASS. ESP-IDF 5.5.3 firmware build and protected-layout validation passed. The application is 2,336,816 bytes, below the 3 MB limit; the merged image is 2,402,352 bytes. Native Mac compilation and app signature-integrity checks passed.
- Host tests: PASS. 366 Python tests cover the bridge, background-service ownership and retries, independent Token cycles, Cursor whole-period totals, stale caches and existing integrations. Native configuration, connection routing, quota freshness/display and the repository's C model/protocol/game/layout tests also passed.
- Device tests: PASS within the recorded scope. The 1.4.0 application firmware was installed on the development badge; battery readings recovered and saved profile settings, feature choices, evolution thresholds and Dino best score were retained. The final Mac build connected over secured Bluetooth and read back battery and complete combined growth; the user confirmed Bluetooth connection validation on the updated version.
- Background operation: continuing synchronization after quitting the editor was observed during this update. A disconnected service was also observed releasing its idle-sleep assertion while remaining enabled to retry. These checks are separate from the user's final Bluetooth confirmation; they are not a long-duration radio-range or battery benchmark.
- Portability: PASS within the tested scope. The 1.4.0 packaged app passed relocation to another path, an empty user home with a minimal system PATH, bundled-runtime imports, nine backend-module/source correspondence checks, privacy checks and signature integrity on the development Mac. These checks do not substitute for physical installation on another Mac. Apple Silicon/macOS 14+ is the build target; normal Bluetooth/background-item authorization may be required after installing the update.
- Unverified: another physical Mac, all older supported macOS releases, all third-party account types, long-duration memory/power comparisons, measured battery-life improvement, and continuous operation during manual or closed-lid system sleep. The development host runs macOS 27. Intel and iPhone are unsupported. No Developer ID certificate or Apple notarization is claimed.

## Artifact identity

- Application-only firmware: `qingjian-1.4.0-app.bin`, address `0x10000`, 2,336,816 bytes; SHA-256 `e50a93bb54d9cb33c8bdf383ff4ed15a5d8aec3619543b97db8a86531f6997a1`.
- Merged firmware: `qingjian-1.4.0-full.bin`, address `0x0`, 2,402,352 bytes; SHA-256 `0b09d4bd4782cd8c5192258156d09aab9ae14abd7d84ba55e419976cd7186dc2`.
- Mac package: `qingjian-1.4.0-macos-arm64.zip`, 48,980,555 bytes; SHA-256 `d6892fc2e90bb11abb68d290e4ce6607fa0a8aa96c1324f2496558314cd01890`. Verify it against the release's `SHA256SUMS.txt`.

The public artifacts are generated from source, not a device Flash dump. Device identity, recovery contents, local account caches, credentials, private logs and personal backups are excluded. See [the release](https://github.com/007xf/qingjian-ai-passport/releases/tag/v1.4.0) and `release-validation.json` for the concise record. [Community play 328](https://ai-passport.folotoy.cn/plays/328/) is public for its previously approved release; a new version has its own moderation state and must not be described as approved before confirmation.

Before flashing or replacing the app, disable Background Sync as well as quitting the editor. Quitting the editor alone no longer releases the device connection. See [the background guide](qingjian-background-sync.md) and [disclaimer](DISCLAIMER.md) for the operating boundaries.
