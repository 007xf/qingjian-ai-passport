**English** · [简体中文](DISCLAIMER.zh_CN.md)

# Usage boundaries and disclaimer

Qingjian is an independent community project developed with AI assistance, not an official or endorsed product of FoloToy, OpenAI, Cursor, Google, TYPE-MOON or Apple. It is provided under the applicable repository and dependency licenses, without a promise of compatibility with every system, account, device revision or future external service.

## Firmware and recovery

Back up your own device and verify the file, version, checksum and compatible layout. The merged image installs at 0x0, includes the bootloader and partition table, and may overwrite application settings. The app-only image installs at 0x10000 on an already compatible layout. Do not erase the whole chip or overwrite device identity and recovery regions. Interrupted writing, wrong addresses or incompatible layouts can cause lost settings or a device that needs recovery. No claim of universal recovery or continuous commercial support is made.

## External data and privacy

Codex Tokens cover accessible local logs, not billing, subscription percentages or all devices. Cursor quota depends on an internal client interface which may change; Gemini support covers CLI records and hooks only. Missing, incomplete or expired observations are not financial evidence. Qingjian does not start paid model tasks, alter subscriptions, enable on-demand spending or redeem reset cards. Its reset-card button only records a new local counting period.

Personal settings and activity metadata remain on the user's Mac. Normal account checks contact the corresponding service using that user's existing login. Credentials are not sent to the badge or included in release packages and caches. This is not entirely offline software. Only connect accounts and devices you are authorized to use. Never publish credentials, conversation logs, private device backups or secret recovery links with a bug report.

## Artwork and licenses

The Aoko pictures are generated fan art based on Aoko Aozaki; no TYPE-MOON authorization is claimed. Noncommercial sharing does not itself grant character or trademark rights. The code license does not license third-party characters or brands. Ensure appropriate rights before reusing or commercially distributing artwork. Retain the project MIT license, Chromium BSD notices, font OFL notices and licenses bundled with the Python runtime and dependencies. Contact the maintainer through Issues about attribution or rights concerns.

## macOS distribution

This build is arm64 for Apple Silicon and targets macOS 14+. It is ad-hoc signed and has not received Developer ID signing or Apple notarization. Obtain the complete release from the verified links, check its checksum, and follow Apple's normal first-open process if applicable. Do not globally disable system protections.

These statements explain scope and known limitations; they do not waive rights that cannot lawfully be waived. The release validation record is the authority for what was actually tested.
