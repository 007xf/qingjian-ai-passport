**English** · [简体中文](DISCLAIMER.zh_CN.md)

# Usage boundaries and disclaimer

Qingjian is an independent community project developed with AI assistance, not an official or endorsed product of FoloToy, OpenAI, Cursor, Google, TYPE-MOON or Apple. It is provided under the applicable repository and dependency licenses, without a promise of compatibility with every system, account, device revision or future external service.

## Firmware and recovery

Back up your own device and verify the file, version, checksum and compatible layout. The merged image installs at 0x0, includes the bootloader and partition table, and may overwrite application settings. The app-only image installs at 0x10000 on an already compatible layout. Do not erase the whole chip or overwrite device identity and recovery regions. Interrupted writing, wrong addresses or incompatible layouts can cause lost settings or a device that needs recovery. No claim of universal recovery or continuous commercial support is made.

Disable Background Sync before flashing or replacing the Mac app, then quit the editor and other device clients. The background service continues after the editor quits, so closing the window alone does not release the connection. Battery recovery is restricted to the supported gauge and a verified blank cold-start profile using the known 520 mAh parameters; existing nonempty parameters are preserved. It is not a battery calibration procedure or support for a replacement cell with different requirements.

## External data and privacy

Codex Tokens cover accessible local logs, not billing, subscription percentages or all devices. Cursor Tokens come from the signed-in account's actual billing-month aggregate, including input, output, cache write and cache read. Growth adds each source's own current-cycle count; it does not convert percentages into Tokens or money. Repeated cache use can contribute Tokens in separate requests. A confirmed period reset can reduce growth and change the avatar stage. Codex's reset-card button only starts a new local Codex counting period; it does not reset Cursor's month.

Updating complete combined growth and the badge stage requires both Codex and Cursor Token sources to be complete and current. With only one connected source, individual usage remains available but combined growth waits; the missing source is not treated as zero.

Cursor quota and Token totals depend on an internal client interface which may change; Gemini support covers CLI records and hooks only. Last valid percentages may remain visible after expiry, explicitly marked with their original observation time. Retaining a reading does not make it current or complete. Missing, incomplete and expired observations are not financial evidence. Qingjian does not start paid model tasks, alter subscriptions, enable on-demand spending or redeem reset cards.

Personal settings and activity metadata remain on the user's Mac. Normal account checks contact the corresponding service using that user's existing login. Credentials are not sent to the badge or included in release packages and caches. This is not entirely offline software. Only connect accounts and devices you are authorized to use. Never publish credentials, conversation logs, private device backups or secret recovery links with a bug report.

Background Sync runs in the signed-in user's session after explicit setup and can continue after the editor quits. Disable it in the app when ongoing reads or the device connection are no longer wanted. This release has no iPhone relay or cloud forwarding service.

## Connection and energy use

Automatic reconnect is best-effort, subject to distance, radio conditions, pairing, macOS permissions and the Mac's power state. A missing upload acknowledgement is not an instruction to replay the upload automatically. Check the badge result before manually retrying.

The optional display-off setting temporarily prevents automatic idle system sleep while useful for the connected badge. It releases that request after a 90-second disconnection grace period or when Background Sync is disabled. It can increase Mac power use even with minute/hourly refresh and quiet UI updates. It does not guarantee connection during manual or closed-lid sleep, shutdown or logout. Long-duration battery-life improvement has not been measured; no energy-saving percentage or AirPods-style handoff guarantee is made.

## Artwork and licenses

The Aoko pictures are generated fan art based on Aoko Aozaki; no TYPE-MOON authorization is claimed. Noncommercial sharing does not itself grant character or trademark rights. The code license does not license third-party characters or brands. Ensure appropriate rights before reusing or commercially distributing artwork. Retain the project MIT license, Chromium BSD notices, font OFL notices and licenses bundled with the Python runtime and dependencies. Contact the maintainer through Issues about attribution or rights concerns.

## macOS distribution

This build is arm64 for Apple Silicon and targets macOS 14+. It is ad-hoc signed and has not received Developer ID signing or Apple notarization. Obtain the complete release from the verified links, check its checksum, and follow Apple's normal first-open process if applicable. Do not globally disable system protections.

An update may require normal Bluetooth or background-item authorization again. Keep the complete app in a stable location such as Applications, and verify background status after replacement. Tests on the development Mac do not establish compatibility with every other Mac, supported system release or account configuration.

These statements explain scope and known limitations; they do not waive rights that cannot lawfully be waived. The release validation record is the authority for what was actually tested.
