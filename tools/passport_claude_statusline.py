"""Anonymous official quota observer that preserves a prior statusLine command."""
import argparse
import base64
import io
import json
import subprocess
import sys

from passport_claude import capture_statusline, MAX_JSON, WINDOWS
from passport_providers import _MetadataJSON


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--claude-home", required=True)
    parser.add_argument("--original-command-b64", required=True)
    args = parser.parse_args(argv)
    try:
        original = json.loads(base64.b64decode(args.original_command_b64, validate=True).decode("utf-8"))
        if not isinstance(original, str):
            return 1
    except (ValueError, UnicodeError):
        return 1
    raw = sys.stdin.buffer.read(MAX_JSON + 1)
    # Capture failure must never suppress the user's existing terminal display.
    if len(raw) <= MAX_JSON:
        try:
            fields = {"session_id": True, "rate_limits": {name: {"used_percentage": True, "resets_at": True} for name in WINDOWS}}
            payload = _MetadataJSON(io.StringIO(raw.decode("utf-8")), MAX_JSON).document(fields)
            capture_statusline(payload, args.state_dir, args.claude_home)
        except (OSError, ValueError, TypeError, UnicodeError):
            pass
    if original:
        # The command is the exact previous user setting, never Claude input.
        try:
            if len(raw) > MAX_JSON:
                process = subprocess.Popen(["/bin/sh", "-c", original], stdin=subprocess.PIPE)
                try:
                    process.stdin.write(raw)
                    while chunk := sys.stdin.buffer.read(65536):
                        process.stdin.write(chunk)
                    process.stdin.close()
                except BrokenPipeError:
                    pass
                return process.wait()
            return subprocess.run(["/bin/sh", "-c", original], input=raw, check=False).returncode
        except OSError:
            return 1
    print("Claude Code")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
