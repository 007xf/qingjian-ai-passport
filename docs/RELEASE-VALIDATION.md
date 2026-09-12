**English** · [简体中文](RELEASE-VALIDATION.zh_CN.md)

# Release validation — 1.3.1, Mac build 10

- Build: PASS. ESP-IDF 5.5.3 build and protected layout validation; application 2,332,832 bytes within 3 MB. Native Mac compilation and signature integrity passed.
- Host tests: PASS. 284 bundled-runtime Python tests, native configuration and Cursor snapshot checks, plus the repository's C model/protocol/game/layout suite. Dino regression fails on the old implementation and passes with the fix and sanitizers.
- Portability: PASS within tested scope. The complete app was moved to a different path containing Chinese characters and spaces. With an empty HOME, an independent empty CODEX_HOME and only the system PATH, required runtime modules load; no author account or usage appears and no hooks are preinstalled. Every native executable/dependency is locally signed; no missing external libraries or author credential/config files are required.
- Device tests: the previous functional v1.3.0 build was verified on the physical badge and accepted by the user, including profile, thresholds, time/battery, USB/BLE, Cursor quota, selected-agent display and Dino crouch passage. Best score advanced past the reported failure. The v1.3.1 firmware changes first-use defaults; it was built and host-tested, without flashing/resetting a device merely for publication.
- Unverified: physical operation on Intel (unsupported), other Macs or older macOS releases; all possible third-party account types. Minimum supported target is Apple Silicon/macOS14+, while the test host runs macOS27. No Developer ID certificate or Apple notarization is claimed.

The public artifacts are generated from source, not a device flash dump. Device identity, recovery contents, local account caches, credentials, private logs and personal backups are excluded. See `release-validation.json` for the concise record and the release checksums for exact files.
