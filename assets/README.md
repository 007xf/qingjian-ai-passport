<p align="right">
  <a href="README.zh_CN.md">简体中文</a> · <strong>English</strong>
</p>

# Assets

This directory stores reusable fonts, images, music, and sound effects, organized by asset type.

Keep each asset in the matching subdirectory and document its destination, naming, integration method, and source/license. Do not mix binary assets with Markdown documentation.

## Fonts

Store reusable font files and generated font sources in `fonts/`.

- Use descriptive names that include the family, weight, size, and format when relevant.
- Document the source, license, character range, conversion command, and expected destination.
- Check Flash and internal-RAM impact before adding a font; the ESP32-C3 has no PSRAM.
- Do not commit fonts whose license does not permit redistribution.

## Images

Store reusable source images and generated display assets in `images/`.

- Use descriptive names and document dimensions, pixel format, conversion steps, and destination.
- Prefer formats suitable for the 240 × 320 RGB565 display and account for Flash and internal RAM.
- Preserve editable sources where licensing permits, and record the source and license.
- Never commit device QR secrets, credentials, or personal data in images.

## Music and sound effects

Store reusable music and sound-effect sources in `music/`.

- Document the source, license, sample rate, bit depth, channels, conversion command, and destination.
- Prefer 16 kHz, 16-bit mono PCM when it matches the current BSP audio path.
- Check Flash and internal-RAM cost before embedding audio; stream or chunk long recordings.
- Do not commit media without redistribution permission.

## Passport bundled material

`badge-layout.json` is the shared device and Mac preview layout contract. It defines pixel bounds, colors, actual font line heights, alignment, overflow and captions for the 240 by 320 badge. Edit that file and run `python3 tools/generate_badge_layout.py` to regenerate `main/passport_layout.h`; the static gate rejects a stale header. The Mac app bundles this same JSON and uses the matching font outlines.

Aoko winter/mage/red source PNGs are in `images/`. The selected generated fan art has complete natural standing legs and the earlier rounded-eye faces. `main/passport_avatar_data.c` contains native 160 by 160 RGB565 display pixels generated directly from the unchanged originals by `tools/generate_builtin_avatars.py`. Built-in images render from Flash at scale 1; custom uploads keep the compatible 128 by 128 codec. See the [badge art sources](../docs/development/passport-os/README.md#art-and-sources).

`main/passport_font_14.c` and `main/passport_font_20.c` use Noto Sans SC at weight400, 2bpp, GB2312 plus printable ASCII (7,541 characters). The supported character list and OFL license are under `fonts/`.
