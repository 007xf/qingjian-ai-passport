<p align="right">
  <a href="README.zh_CN.md">简体中文</a> · <strong>English</strong>
</p>

# Build the QingJian Mac app

This folder contains the native editor source. The app targets Apple Silicon
(arm64) and macOS 14 or later. Intel Macs are not supported by this package.
The current release was tested on macOS 27; the minimum deployment target is
not a claim of physical testing on every earlier supported macOS version.

## Prerequisites

- An Apple Silicon Mac with the Xcode Command Line Tools and Swift compiler.
- A released QingJian app archive whose SHA-256 matches the published release
  checksum. Extract it first. This verified app supplies the bundled Python
  runtime, libraries, Montserrat font and icon.
- This complete source repository, including `assets`, `tools` and `macos`.

The script does not download, install or execute a replacement Python from the
internet. It does not use Homebrew Python or the system Python. Select the
runtime template explicitly; the script checks its code seal, runtime imports
and the expected Montserrat font hash before compiling. These checks verify
integrity and required dependencies; they do not replace verifying the release
archive against its published checksum.

## Build

From the repository root:

```bash
QINGJIAN_RUNTIME_TEMPLATE="/path/to/Qingjian.app" ./macos/build.sh
```

The default result is the app bundle under `dist/`. To select another output folder:

```bash
QINGJIAN_RUNTIME_TEMPLATE="/path/to/Qingjian.app" \
QINGJIAN_OUTPUT_DIR="/path/to/new-build" \
./macos/build.sh
```

The output app must not already exist. The script refuses to replace an
existing or running app, the selected template, or anything inside that
template. Choose a new output folder for each build. The temporary build
directory is removed on success or failure; source files and the template
are left unchanged.

The app is compiled from the Swift files and `Info.plist` in this folder.
Backend modules and the layout come from this repository. Original character
images are copied unchanged, and device previews are generated at 160 × 160
using the shared RGB565 conversion. The CJK font comes from `assets/fonts`;
other template fonts and their notices are retained. No personal drafts,
account credentials, usage history or Bluetooth pairing are copied.

Every Mach-O library and extension is signed individually before the outer
app bundle, including hidden `PIL/.dylibs` files. The script then verifies the
signatures, imports all backend dependencies from the resulting bundled
runtime, loads the fonts and checks the three generated preview dimensions.
It does not connect to a badge, log in to an account or test the graphical UI.

## Distribution and first launch

This build uses a local ad-hoc signature. It has no Developer ID signature or
Apple notarization, so it must not be described as a notarized app that opens
without Gatekeeper review after download. The script does not change
Gatekeeper, quarantine attributes or macOS privacy settings. A public signed
distribution requires a separate Developer ID signing and notarization
process.

On another Mac, the app stores its own settings under that Mac user's
`~/Library/Application Support/AI Passport` directory and reads that user's
existing supported AI app data. Installing QingJian does not sign the user
into Codex, Cursor or Gemini. The user must choose their own badge connection;
custom image uploads require USB. Source-configuration and device tests are
separate from a successful local build.
