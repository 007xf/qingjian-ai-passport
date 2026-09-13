#!/usr/bin/env bash
set -euo pipefail

mode="${1:---all}"
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
    echo "Usage: $0 [--all|--static|--firmware]" >&2
}

run_static_checks() {
    local actionlint_bin
    local test_dir

    python3 tools/check_repo.py
    python3 tools/generate_badge_layout.py --check
    python3 tests/test_passport_layout.py

    actionlint_bin="${ACTIONLINT_BIN:-}"
    if [[ -z "${actionlint_bin}" ]]; then
        actionlint_bin="$(command -v actionlint || true)"
    fi
    if [[ -z "${actionlint_bin}" || ! -x "${actionlint_bin}" ]]; then
        actionlint_bin="$(./tools/install-actionlint.sh)"
    fi
    "${actionlint_bin}" -color .github/workflows/*.yml

    test_dir="$(mktemp -d /tmp/ai-passport-host-tests.XXXXXX)"
    "${CC:-cc}" -std=c11 -Wall -Wextra -Werror -Imain \
        tests/test_ui_pixel_math.c main/ui_pixel_math.c \
        -o "${test_dir}/test_ui_pixel_math"
    "${test_dir}/test_ui_pixel_math"
    "${CC:-cc}" -std=c11 -Wall -Wextra -Werror -Imain \
        tests/test_passport_logic.c main/passport_logic.c \
        -o "${test_dir}/test_passport_logic"
    "${test_dir}/test_passport_logic"
    "${CC:-cc}" -std=c11 -Wall -Wextra -Werror -Imain \
        tests/test_passport_wire.c main/passport_wire.c \
        -o "${test_dir}/test_passport_wire"
    "${test_dir}/test_passport_wire"
    "${CC:-cc}" -std=c11 -Wall -Wextra -Werror -Imain \
        tests/test_passport_codex.c main/passport_codex_model.c \
        -o "${test_dir}/test_passport_codex"
    "${test_dir}/test_passport_codex"
    "${CC:-cc}" -std=c11 -Wall -Wextra -Werror -Imain \
        tests/test_passport_cursor_model.c main/passport_cursor_model.c \
        -o "${test_dir}/test_passport_cursor_model"
    "${test_dir}/test_passport_cursor_model"
    "${CC:-cc}" -std=c11 -Wall -Wextra -Werror -Imain \
        tests/test_passport_provider.c main/passport_provider_model.c \
        -o "${test_dir}/test_passport_provider"
    "${test_dir}/test_passport_provider"
    "${CC:-cc}" -std=c11 -Wall -Wextra -Werror -Imain \
        tests/test_dino_logic.c main/dino_logic.c -o "${test_dir}/test_dino_logic"
    "${test_dir}/test_dino_logic"
    "${PASSPORT_TEST_PYTHON:-python3}" tests/test_dino_sprites.py
    "${PASSPORT_TEST_PYTHON:-python3}" tools/generate_builtin_avatars.py --check
    "${CC:-cc}" -std=c11 -Wall -Wextra -Werror -Imain \
        tests/test_passport_avatar_codec.c main/passport_avatar_codec.c -lz \
        -o "${test_dir}/test_passport_avatar_codec"
    "${test_dir}/test_passport_avatar_codec"
    "${CC:-cc}" -std=c11 -Wall -Wextra -Werror -Itests/stubs -Icomponents/bsp/include \
        tests/test_battery_readonly.c -o "${test_dir}/test_battery_readonly"
    "${test_dir}/test_battery_readonly"
    python3 tests/test_verify_firmware.py
    "${PASSPORT_TEST_PYTHON:-python3}" tests/test_passport_bridge.py
    "${PASSPORT_TEST_PYTHON:-python3}" tests/test_passport_service.py
    "${PASSPORT_TEST_PYTHON:-python3}" tests/test_passport_ble.py
    "${PASSPORT_TEST_PYTHON:-python3}" tests/test_passport_serial.py
    "${PASSPORT_TEST_PYTHON:-python3}" tests/test_passport_activity.py
    "${PASSPORT_TEST_PYTHON:-python3}" tests/test_passport_providers.py
    "${PASSPORT_TEST_PYTHON:-python3}" tests/test_passport_sources.py
    "${PASSPORT_TEST_PYTHON:-python3}" tests/test_passport_cursor.py
    "${PASSPORT_TEST_PYTHON:-python3}" tests/test_passport_cursor_tokens.py
    rm -rf "${test_dir}"
    echo "Host tests: PASS"
}

run_firmware_checks() (
    local validation_build_dir

    if ! command -v idf.py >/dev/null 2>&1; then
        echo "ERROR: idf.py is not available; activate ESP-IDF 5.5.3 first." >&2
        return 1
    fi

    validation_build_dir="$(mktemp -d /tmp/ai-passport-firmware.XXXXXX)"
    trap 'case "${validation_build_dir}" in /tmp/ai-passport-firmware.*) rm -rf -- "${validation_build_dir}" ;; esac' EXIT

    SDKCONFIG_DEFAULTS="${repo_root}/sdkconfig.defaults" \
        idf.py -B "${validation_build_dir}" \
        -D "SDKCONFIG=${validation_build_dir}/sdkconfig" build
    idf.py -B "${validation_build_dir}" merge-bin \
        -o "${validation_build_dir}/FoloToy-AI-Passport-full.bin"
    python3 tools/verify_firmware.py "${validation_build_dir}"
    mkdir -p "${repo_root}/build"
    install -m 0644 \
        "${validation_build_dir}/FoloToy-AI-Passport-full.bin" \
        "${repo_root}/build/FoloToy-AI-Passport-full.bin"
    echo "Firmware build: PASS"
)

cd "${repo_root}"
case "${mode}" in
    --all)
        run_static_checks
        run_firmware_checks
        ;;
    --static)
        run_static_checks
        ;;
    --firmware)
        run_firmware_checks
        ;;
    *)
        usage
        exit 2
        ;;
esac
