<p align="right"><strong>English</strong> · <a href="README.zh_CN.md">简体中文</a></p>

# Classic Chromium Dino artwork

The unmodified 1× atlas and reference source in `chromium/` come from Chromium tag `98.0.4758.55`. The upstream BSD license is included as `chromium/LICENSE`; include that file with binary redistributions of this artwork. Chromium and Google do not endorse this project.

- [Original sprite atlas](https://chromium.googlesource.com/chromium/src/+/refs/tags/98.0.4758.55/components/neterror/resources/images/default_100_percent/offline/100-offline-sprite.png)
- [Sprite coordinates](https://chromium.googlesource.com/chromium/src/+/refs/tags/98.0.4758.55/components/neterror/resources/offline-sprite-definitions.js)
- [Animation and drawing definitions](https://chromium.googlesource.com/chromium/src/+/refs/tags/98.0.4758.55/components/neterror/resources/offline.js)
- [BSD license](https://chromium.googlesource.com/chromium/src/+/refs/tags/98.0.4758.55/LICENSE)

The source atlas SHA-256 is `04d05978fdb111358073ab0524e5c1fafc0826615c206987618416b8bd8a4747`. `tools/generate_dino_sprites.py` verifies this hash and produces the sprite ID, pixel-mask and LVGL image headers in `main/`. `--check` verifies reproducibility without changing files; `--preview-dir` exports exact-pixel night previews. The converter requires Pillow.

Original RGB channels are inverted and alpha is preserved: white matte becomes opaque black, while gray body pixels become light gray. Monochrome frames use AL88; the large cactus preserves its subtly colored bottom edge in ARGB8888. Images are 4-byte aligned and stored in Flash, with no resizing, mutable canvas, or runtime full-image decode buffer. The current LVGL configuration supports both formats and uses stride alignment 1 and no image cache.

Upright frames remain 44×47. Chromium's ducking configuration calls its logical height 25, but draws a 59×47 source cell with empty upper rows; the full original cell is retained to avoid clipping its artwork. Running, ducking and bird frame timing follows the upstream 12/8/6 frames per second. Rendering is capped at 25 updates per second and does not promise browser frame-rate parity.

The 216×142 field uses a 132-pixel ground baseline, 140–190 pixels per second movement, and a bounded 60-Hz simulation. Collision uses Chromium 98 fixed body boxes, excluding animated wing tips; generated pixel masks remain only for sprite validation. Standalone host tests cover source fidelity, color/alpha, masks, animation selection, movement, jump timing, pause/retry, held-key release, persistence signals and time discontinuities.
