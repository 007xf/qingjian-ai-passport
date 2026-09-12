#!/bin/bash
set -euo pipefail

fail() { echo "ERROR: $*" >&2; exit 1; }

[[ "$(uname -s)" == Darwin && "$(uname -m)" == arm64 ]] || \
  fail "This build requires an Apple Silicon Mac running macOS 14 or later."
[[ -n "${QINGJIAN_RUNTIME_TEMPLATE:-}" ]] || \
  fail "Set QINGJIAN_RUNTIME_TEMPLATE to a verified release app; no runtime is downloaded automatically."
[[ -d "$QINGJIAN_RUNTIME_TEMPLATE/Contents/Resources/Runtime" ]] || \
  fail "The runtime template must be an extracted QingJian .app from a verified release."

SOURCE_DIR="$(cd -- "$(dirname -- "$0")" && pwd -P)"
REPO_DIR="$(cd -- "$SOURCE_DIR/.." && pwd -P)"
TEMPLATE_DIR="$(cd -- "$QINGJIAN_RUNTIME_TEMPLATE" && pwd -P)"
TEMPLATE_RESOURCES="$TEMPLATE_DIR/Contents/Resources"
TEMPLATE_PYTHON="$TEMPLATE_RESOURCES/Runtime/bin/python3"
[[ -x "$TEMPLATE_PYTHON" ]] || fail "The selected template has no executable bundled Python."
/usr/bin/codesign --verify --deep --strict "$TEMPLATE_DIR"
SWIFTC="$(/usr/bin/xcrun --find swiftc)"
MACOS_SDK="$(/usr/bin/xcrun --sdk macosx --show-sdk-path)"

# Use only the explicitly selected, signed template interpreter. User Python
# search paths and bytecode writes must not affect a build or its template.
template_python() {
  /usr/bin/env -u PYTHONHOME -u PYTHONPATH PYTHONNOUSERSITE=1 \
    PYTHONDONTWRITEBYTECODE=1 "$TEMPLATE_PYTHON" -B "$@"
}

template_python - "$TEMPLATE_RESOURCES" "$REPO_DIR" <<'PY'
from pathlib import Path
import hashlib
import importlib
import sys

resources, repo = map(Path, sys.argv[1:])
runtime = (resources / "Runtime").resolve()
if Path(sys.prefix).resolve() != runtime:
    raise SystemExit("Template Python resolved outside its Runtime directory")
for path in runtime.rglob("*"):
    if path.is_symlink() and (not path.resolve().is_relative_to(runtime) or not path.resolve().exists()):
        raise SystemExit("Template contains an external or broken runtime symlink")
for name in ("ssl", "sqlite3", "zlib", "PIL.Image", "PIL.ImageFont", "serial", "bleak", "objc", "Foundation", "CoreBluetooth"):
    importlib.import_module(name)
from PIL import ImageFont
montserrat = resources / "Fonts/Montserrat-Medium.ttf"
expected = "421f26b23e2be6b98373d32acd3cb2897b154d4bf0a77d26534ce476e4cbed53"
if hashlib.sha256(montserrat.read_bytes()).hexdigest() != expected:
    raise SystemExit("Template Montserrat font differs from the verified release font")
ImageFont.truetype(str(montserrat), 14)
ImageFont.truetype(str(repo / "assets/fonts/QingjianNotoSans-Regular.ttf"), 14)
if not (resources / "QingJian.icns").is_file():
    raise SystemExit("Template icon is missing")
print("Template dependencies and fonts: PASS")
PY

OUTPUT_DIR="$(template_python - "${QINGJIAN_OUTPUT_DIR:-$REPO_DIR/dist}" "$TEMPLATE_DIR" <<'PY'
from pathlib import Path
import sys

out = Path(sys.argv[1]).expanduser().resolve()
template = Path(sys.argv[2]).resolve()
target = out / "青笺.app"
if "\n" in str(out) or "\r" in str(out):
    raise SystemExit("Output directory must not contain a newline")
if target.resolve() == template or out == template or out.is_relative_to(template):
    raise SystemExit("Output must not replace or modify the runtime template")
if target.exists() or target.is_symlink():
    raise SystemExit("Output app already exists; select a new QINGJIAN_OUTPUT_DIR. Existing or running apps are never overwritten.")
print(out)
PY
)"
mkdir -p -- "$OUTPUT_DIR"
BUILD_DIR="$(mktemp -d "$OUTPUT_DIR/.qingjian-build.XXXXXX")"
cleanup() { [[ -z "${BUILD_DIR:-}" ]] || rm -rf -- "$BUILD_DIR"; }
trap cleanup EXIT

APP_DIR="$BUILD_DIR/青笺.app"
RESOURCES="$APP_DIR/Contents/Resources"
mkdir -p "$APP_DIR/Contents/MacOS" "$RESOURCES/Backend" "$RESOURCES/Images"
/usr/bin/ditto "$TEMPLATE_RESOURCES/Runtime" "$RESOURCES/Runtime"
/usr/bin/ditto "$TEMPLATE_RESOURCES/Fonts" "$RESOURCES/Fonts"
cp "$TEMPLATE_RESOURCES/QingJian.icns" "$RESOURCES/QingJian.icns"
mkdir -p "$RESOURCES/Notices"
cp "$REPO_DIR/LICENSE" "$REPO_DIR/docs/THIRD-PARTY-NOTICES.md" "$REPO_DIR/docs/THIRD-PARTY-NOTICES.zh_CN.md" "$RESOURCES/Notices/"
cp "$REPO_DIR/assets/dino/chromium/LICENSE" "$RESOURCES/Notices/Chromium-LICENSE"
cp "$REPO_DIR/assets/fonts/QingjianNotoSans-Regular.ttf" "$RESOURCES/Fonts/QingjianNotoSans-Regular.ttf"
cp "$REPO_DIR/assets/fonts/OFL.txt" "$RESOURCES/Fonts/NotoSansSC-OFL.txt"
cp "$REPO_DIR/assets/fonts/passport-supported-characters.txt" "$RESOURCES/Backend/"
cp "$REPO_DIR/assets/badge-layout.json" "$RESOURCES/badge-layout.json"
for module in bridge ble serial providers activity sources cursor; do
  cp "$REPO_DIR/tools/passport_$module.py" "$RESOURCES/Backend/"
done
cp "$REPO_DIR/tools/requirements-ble.txt" "$RESOURCES/Backend/"

# The generator also writes a C image array. Run it in a disposable source
# subset, so building the Mac app does not modify firmware source files.
ASSET_DIR="$BUILD_DIR/avatar-assets"
mkdir -p "$ASSET_DIR/tools" "$ASSET_DIR/assets/images" "$ASSET_DIR/main"
cp "$REPO_DIR/tools/generate_builtin_avatars.py" "$REPO_DIR/tools/passport_bridge.py" "$ASSET_DIR/tools/"
stage=0
for character in winter mage red; do
  cp "$REPO_DIR/assets/images/aoko-$character.png" "$ASSET_DIR/assets/images/"
  cp "$REPO_DIR/assets/images/aoko-$character.png" "$RESOURCES/Images/stage-$stage-original.png"
  stage=$((stage + 1))
done
template_python "$ASSET_DIR/tools/generate_builtin_avatars.py" --preview-directory "$RESOURCES/Images"

"$SWIFTC" -sdk "$MACOS_SDK" -parse-as-library -O -target arm64-apple-macosx14.0 \
  -module-cache-path "$BUILD_DIR/swift-module-cache" \
  -framework SwiftUI -framework AppKit \
  "$SOURCE_DIR/BadgeConfig.swift" "$SOURCE_DIR/ConnectionSettings.swift" \
  "$SOURCE_DIR/BadgeFonts.swift" "$SOURCE_DIR/BadgeLayout.swift" \
  "$SOURCE_DIR/CursorQuota.swift" "$SOURCE_DIR/PassportApp.swift" \
  -o "$APP_DIR/Contents/MacOS/青笺"
cp "$SOURCE_DIR/Info.plist" "$APP_DIR/Contents/Info.plist"

# --deep alone does not reliably cover Python extension modules or PIL/.dylibs.
# Sign every actual Mach-O file from leaves upward, then seal the outer bundle.
# Do not rewrite install names or rpaths from the verified runtime template.
template_python - "$APP_DIR" <<'PY'
from pathlib import Path
import subprocess
import sys

app = Path(sys.argv[1])
magic = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce",
         b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"}
machos = []
for path in app.rglob("*"):
    if not path.is_file() or path.is_symlink() or any(part.endswith(".dSYM") for part in path.parts):
        continue
    with path.open("rb") as stream:
        if stream.read(4) in magic:
            machos.append(path)
for path in sorted(machos, key=lambda value: (len(value.parts), str(value)), reverse=True):
    subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(path)], check=True)
    subprocess.run(["/usr/bin/codesign", "--verify", "--strict", str(path)], check=True)
subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(app)], check=True)
subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)], check=True)
print(f"Individual Mach-O signatures and app seal: PASS ({len(machos)} binaries)")
PY

/usr/bin/env -u PYTHONHOME -u PYTHONPATH PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 \
  "$RESOURCES/Runtime/bin/python3" -B - "$RESOURCES" <<'PY'
from pathlib import Path
import importlib
import sys

resources = Path(sys.argv[1])
if Path(sys.prefix).resolve() != (resources / "Runtime").resolve():
    raise SystemExit("Output Python resolved outside its Runtime directory")
for name in ("ssl", "sqlite3", "zlib", "PIL.Image", "PIL.ImageFont", "serial", "bleak", "objc", "Foundation", "CoreBluetooth"):
    importlib.import_module(name)
from PIL import Image, ImageFont
for stage in range(3):
    with Image.open(resources / "Images" / f"stage-{stage}.png") as image:
        image.load()
        if image.size != (160, 160):
            raise SystemExit("Generated avatar is not native 160 x 160")
for font in ("Montserrat-Medium.ttf", "QingjianNotoSans-Regular.ttf"):
    ImageFont.truetype(str(resources / "Fonts" / font), 14)
sys.path.insert(0, str(resources / "Backend"))
for name in ("bridge", "ble", "serial", "providers", "activity", "sources", "cursor"):
    importlib.import_module("passport_" + name)
print("Output runtime, backend, images and fonts: PASS")
PY
/usr/bin/codesign --verify --deep --strict "$APP_DIR"

# Darwin's exclusive rename avoids both replacement and the directory-nesting
# behavior of mv if another app appears at the destination during the build.
template_python - "$APP_DIR" "$OUTPUT_DIR/青笺.app" <<'PY'
import ctypes
import os
import sys

libc = ctypes.CDLL(None, use_errno=True)
rename = libc.renamex_np
rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
rename.restype = ctypes.c_int
if rename(os.fsencode(sys.argv[1]), os.fsencode(sys.argv[2]), 0x00000004) != 0:  # RENAME_EXCL, sys/stdio.h
    code = ctypes.get_errno()
    raise SystemExit("Cannot publish output app without replacement: " + os.strerror(code))
PY
echo "Built: $OUTPUT_DIR/青笺.app"
echo "Local ad-hoc build; no Developer ID signature or notarization was performed."
