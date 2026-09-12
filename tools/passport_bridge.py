#!/usr/bin/env python3
"""Local AI Passport backend. JSON stdout; no model turns or account mutations.

The native editor invokes this program. Only explicit device commands open USB;
tokens/reset-cycle read token counters and keep a private local accounting cache.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
from dataclasses import asdict
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import select
import shutil
import sqlite3
import stat
import struct
import subprocess
import sys
import time
import unicodedata
import zlib

MAX_JSON = 65536
AVATAR_BYTES = 8224
AVATAR_HEADER = struct.Struct("<4sHHII")
AVATAR_RESOLUTIONS = (128, 112, 96, 80, 64, 56)
FIRST_THRESHOLD = 77_777_777
FINAL_THRESHOLD = 555_555_555
MAX_DEVICE_TOKENS = 2**53 - 1
QUOTA_CACHE_VERSION = 2
CODEX_QUOTA_TTL_MS = 180_000
FIELDS = {"name": 24, "title": 48, "intro": 120}
DEFAULT_BADGE = {"name": "苍崎青子", "title": "MISS BLUE", "intro": "如果你惹怒了我，我将会开启3技能"}
DEFAULT_STATE = Path.home() / "Library/Application Support/AI Passport"


class BridgeError(Exception):
    pass


class CodexAuthError(BridgeError):
    """Current account verification failed; no prior quota may be reused."""


def timestamp(value):
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, AttributeError):
        return None


def valid_count(value):
    return type(value) is int and 0 <= value <= 2**64 - 1


def stage_for(tokens, threshold1=FIRST_THRESHOLD, threshold2=FINAL_THRESHOLD):
    return 2 if tokens >= threshold2 else 1 if tokens >= threshold1 else 0


def thresholds(config):
    first = config.get("threshold1", FIRST_THRESHOLD)
    final = config.get("threshold2", FINAL_THRESHOLD)
    if type(first) is not int or type(final) is not int or not 0 < first < final <= MAX_DEVICE_TOKENS:
        raise BridgeError("进化阈值须为正整数，第二阶段必须高于第一阶段")
    return first, final


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        os.chmod(temporary, 0o600)
        json.dump(data, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def load_json(path, default=None):
    try:
        if path.stat().st_size > MAX_JSON:
            raise BridgeError("配置文件过大")
        with path.open(encoding="utf-8") as stream:
            return json.load(stream)
    except FileNotFoundError:
        return default


def validate_config(config):
    if not isinstance(config, dict):
        raise BridgeError("配置必须是 JSON 对象")
    result = {}
    charset_paths = (Path(__file__).resolve().parents[1] / "assets/fonts/passport-supported-characters.txt",
                     Path(__file__).with_name("passport-supported-characters.txt"))
    charset_path = next((p for p in charset_paths if p.is_file()), None)
    if charset_path is None:
        raise BridgeError("缺少设备字体清单，请更新 AI Passport 编辑器")
    supported = set(charset_path.read_text(encoding="utf-8"))
    for key, maximum in FIELDS.items():
        value = config.get(key, DEFAULT_BADGE[key])
        if not isinstance(value, str):
            raise BridgeError(f"{key} 必须是文字")
        value = unicodedata.normalize("NFC", value).strip()
        if any(unicodedata.category(c).startswith("C") for c in value):
            raise BridgeError(f"{key} 不能包含换行或控制字符")
        if len(value.encode("utf-8")) > maximum:
            raise BridgeError(f"{key} 超过 {maximum} 字节，中文通常每字 3 字节")
        if key == "name" and not value:
            raise BridgeError("姓名不能为空")
        unsupported = sorted(set(value) - supported)
        if unsupported:
            raise BridgeError(f"设备字体暂不支持：{''.join(unsupported[:8])}，请换成常用汉字或英文")
        result[key] = value
    result["feature_all"] = config.get("feature_all", False)
    result["feature_mask"] = config.get("feature_mask", 147)
    if type(result["feature_all"]) is not bool:
        raise BridgeError("feature_all 必须是布尔值")
    if type(result["feature_mask"]) is not int or not 0 <= result["feature_mask"] <= 255:
        raise BridgeError("feature_mask 必须在 0–255 之间")
    for key in ("avatar_path", "avatar_raw_path"):
        value = config.get(key)
        if value:
            if not isinstance(value, str) or not Path(value).is_absolute():
                raise BridgeError("头像必须使用本机绝对路径")
            result[key] = value
    if result.get("avatar_path") and result.get("avatar_raw_path"):
        raise BridgeError("只能提供一种头像来源")
    result["avatar_default"] = config.get("avatar_default", False)
    if type(result["avatar_default"]) is not bool:
        raise BridgeError("avatar_default 必须是布尔值")
    if result["avatar_default"] and (result.get("avatar_path") or result.get("avatar_raw_path")):
        raise BridgeError("默认青子和自定义头像不能同时选择")
    result["threshold1"], result["threshold2"] = thresholds(config)
    for key in ("avatar_paths", "avatar_raw_paths"):
        paths = config.get(key, {})
        if not isinstance(paths, dict) or any(k not in ("0", "1", "2") for k in paths):
            raise BridgeError("头像阶段只能为 0、1、2")
        result[key] = {}
        for stage, value in paths.items():
            if not isinstance(value, str) or not value or not Path(value).is_absolute():
                raise BridgeError("每个阶段的头像必须使用本机绝对路径")
            result[key][stage] = value
    if set(result["avatar_paths"]) & set(result["avatar_raw_paths"]):
        raise BridgeError("同一阶段只能提供一种头像来源")
    return result


def load_avatar_source(path):
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise BridgeError("缺少 Pillow 图像转换组件") from exc
    path = Path(path)
    if path.stat().st_size > 32 * 1024 * 1024:
        raise BridgeError("头像图片不能超过 32 MB")
    Image.MAX_IMAGE_PIXELS = 32_000_000
    with Image.open(path) as source:
        if source.width * source.height > 32_000_000:
            raise BridgeError("头像图片尺寸过大")
        return ImageOps.exif_transpose(source).convert("RGBA")


def rgb565_canvas(source, resolution=128):
    """Technical format adaptation: preserve full body, composite alpha on black."""
    from PIL import Image, ImageOps
    fitted = ImageOps.contain(source, (resolution, resolution), method=Image.Resampling.LANCZOS)
    background = Image.new("RGBA", (resolution, resolution), (0, 0, 0, 255))
    background.alpha_composite(fitted, ((resolution - fitted.width) // 2,
                                     (resolution - fitted.height) // 2))
    rgb = background.convert("RGB").tobytes()
    result = bytearray(resolution * resolution * 2)
    for i in range(resolution * resolution):
        r, g, b = rgb[i*3:i*3+3]
        value = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
        result[i*2] = value & 255
        result[i*2+1] = value >> 8
    return bytes(result)


def encode_avatar_source(source):
    capacity = AVATAR_BYTES - AVATAR_HEADER.size
    for resolution in AVATAR_RESOLUTIONS:
        raw = rgb565_canvas(source, resolution)
        compressed = zlib.compress(raw, 9)
        if len(compressed) <= capacity:
            header = AVATAR_HEADER.pack(b"APZ1", resolution, resolution, len(compressed), len(raw))
            return header + compressed + bytes(capacity - len(compressed))
    raise BridgeError("头像无法压入设备存储，请选择较简单的图片")


def decode_avatar_bytes(data):
    """Return exact 128x128 little-endian RGB565 bytes; supports old indexed16."""
    if len(data) != AVATAR_BYTES:
        raise BridgeError("头像原始数据必须为 8224 字节")
    if data[:3] == b"APZ" and data[:4] != b"APZ1":
        raise BridgeError("不支持的头像压缩版本")
    if data[:4] != b"APZ1":
        pixels = bytearray(128 * 128 * 2)
        for i in range(128 * 128):
            index = (data[32+i//2] >> (0 if i & 1 else 4)) & 15
            pixels[i*2:i*2+2] = data[index*2:index*2+2]
        return bytes(pixels)
    _, width, height, compressed_size, raw_size = AVATAR_HEADER.unpack_from(data)
    if not 56 <= width <= 128 or height != width or raw_size != width * height * 2 or \
            not 0 < compressed_size <= AVATAR_BYTES - AVATAR_HEADER.size:
        raise BridgeError("头像压缩头无效")
    end = AVATAR_HEADER.size + compressed_size
    if any(data[end:]):
        raise BridgeError("头像填充数据无效")
    decoder = zlib.decompressobj()
    try:
        raw = decoder.decompress(data[AVATAR_HEADER.size:end], raw_size + 1)
    except zlib.error as exc:
        raise BridgeError("头像压缩数据损坏") from exc
    if len(raw) != raw_size or not decoder.eof or decoder.unconsumed_tail or decoder.unused_data:
        raise BridgeError("头像解码长度或压缩流无效")
    if width == 128:
        return raw
    result = bytearray(128 * 128 * 2)
    for y in range(128):
        for x in range(128):
            source_offset = ((y * height // 128) * width + x * width // 128) * 2
            target_offset = (y * 128 + x) * 2
            result[target_offset:target_offset+2] = raw[source_offset:source_offset+2]
    return bytes(result)


def avatar_bytes(config):
    if config.get("avatar_raw_path"):
        path = Path(config["avatar_raw_path"])
        if path.stat().st_size != AVATAR_BYTES:
            raise BridgeError("头像原始数据必须为 8224 字节")
        data = path.read_bytes()
        decode_avatar_bytes(data)  # Reject malformed input before any device write.
        return data
    if not config.get("avatar_path"):
        return None
    return encode_avatar_source(load_avatar_source(config["avatar_path"]))


def rgb565_image(raw, resolution=128):
    from PIL import Image
    if resolution not in (128, 160) or len(raw) != resolution * resolution * 2:
        raise BridgeError("预览像素长度无效")
    rgb = bytearray(resolution * resolution * 3)
    for i in range(resolution * resolution):
        value = raw[i*2] | (raw[i*2+1] << 8)
        r, g, b = value >> 11, (value >> 5) & 63, value & 31
        rgb[i*3:i*3+3] = bytes(((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)))
    return Image.frombytes("RGB", (resolution, resolution), bytes(rgb))


def preview_avatar(source_path, output_path, builtin=False):
    source_path, output_path = Path(source_path), Path(output_path)
    if not source_path.is_absolute() or not output_path.is_absolute():
        raise BridgeError("预览图片须使用绝对路径")
    if source_path.resolve() == output_path.resolve():
        raise BridgeError("预览输出不能覆盖原图")
    source = load_avatar_source(source_path)
    if builtin:
        raw, resolution = rgb565_canvas(source, 160), 160
    else:
        data = encode_avatar_source(source)
        raw = decode_avatar_bytes(data)
        resolution = AVATAR_HEADER.unpack_from(data)[1]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(output_path.name + ".tmp")
    size = 160 if builtin else 128
    rgb565_image(raw, size).save(temporary, format="PNG")
    temporary.replace(output_path)
    return {"ok": True, "path": str(output_path.resolve()), "width": size, "height": size,
            "source_resolution": resolution, "format": "rgb565"}


def prepare_avatars(config, token_result, strict=True):
    """Convert before connecting; unavailable BLE artwork must not stop usage sync."""
    if config.get("avatar_path") or config.get("avatar_raw_path") or config.get("avatar_default"):
        stage = token_result.get("stage")
        if stage is None:
            if not strict:
                return {}
            raise BridgeError("当前周期用量未知，请直接为三个阶段分别选择头像")
        key = str(stage)
        for old, new in (("avatar_path", "avatar_paths"), ("avatar_raw_path", "avatar_raw_paths")):
            if config.get(old):
                config[new][key] = config.pop(old)
        if config.pop("avatar_default", False):
            config["avatar_paths"].pop(key, None)
            config["avatar_raw_paths"].pop(key, None)
    prepared = {}
    for stage in ("0", "1", "2"):
        item = {}
        if stage in config.get("avatar_paths", {}):
            item["avatar_path"] = config["avatar_paths"][stage]
        if stage in config.get("avatar_raw_paths", {}):
            item["avatar_raw_path"] = config["avatar_raw_paths"][stage]
        if item:
            try:
                prepared[stage] = avatar_bytes(item)
            except Exception:
                if strict:
                    raise
                # Presence with no bytes means a desired, presently unreadable
                # custom image. It must never be confused with a default image.
                prepared[stage] = None
    return prepared


def codex_auth_context(codex_home):
    """One-way account/profile cache binding; never retain an auth credential.

    Missing or unsupported local auth (including a Keychain-only setup) returns
    None: the official app server can still verify it live, but cache shortcuts
    must not assume it is the account that produced a previous observation.
    """
    try:
        root = Path(codex_home).expanduser().resolve()
        path = root / "auth.json"
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022 or info.st_size > MAX_JSON:
            return None
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
            raw = stream.read(MAX_JSON + 1)
        if len(raw) > MAX_JSON:
            return None
        auth = json.loads(raw)
        if not isinstance(auth, dict) or auth.get("auth_mode") not in (None, "chatgpt", "chatgptAuthTokens"):
            return None
        tokens = auth.get("tokens")
        if not isinstance(tokens, dict):
            return None
        account = tokens.get("account_id")
        access = tokens.get("access_token")
        jwt = tokens.get("id_token") or tokens.get("access_token")
        if not isinstance(account, str) or not 1 <= len(account) <= 512 or \
                not isinstance(access, str) or not 1 <= len(access) <= 32768 or \
                (auth.get("auth_mode") is None and auth.get("OPENAI_API_KEY")) or \
                not isinstance(jwt, str) or not 1 <= len(jwt) <= 32768:
            return None
        part = jwt.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        subject = claims.get("sub") if isinstance(claims, dict) else None
        if not isinstance(subject, str) or not 1 <= len(subject) <= 512:
            return None
        return hashlib.sha256(json.dumps(["qingjian-codex-auth-v1", str(root), account, subject],
                                        ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()
    except (OSError, ValueError, IndexError, RecursionError):
        return None


def find_codex_cli(cli=None):
    """Bounded discovery: explicit choice, standard App locations, then CLI."""
    if cli:
        return str(Path(cli).expanduser())
    for candidate in (Path.home() / "Applications/Codex.app/Contents/Resources/codex",
                      Path("/Applications/Codex.app/Contents/Resources/codex")):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    executable = shutil.which("codex")
    if executable:
        return executable
    for candidate in (Path("/opt/homebrew/bin/codex"), Path("/usr/local/bin/codex"),
                      Path.home() / ".local/bin/codex"):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise BridgeError("未找到 Codex，请安装到“应用程序”或安装支持 App Server 的 Codex CLI")


def app_server_quota(cli=None, deadline_seconds=12, codex_home=None):
    """Read only account/read and account/rateLimits/read; never expose identity."""
    executable = find_codex_cli(cli)
    environment = os.environ.copy()
    if codex_home is not None:
        environment["CODEX_HOME"] = str(Path(codex_home).expanduser().resolve())
    process = subprocess.Popen([executable, "app-server"], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=environment)
    deadline = time.monotonic() + deadline_seconds
    pending = bytearray()
    received = 0

    def request(number, method, params=None):
        nonlocal received
        message = {"id": number, "method": method}
        if params is not None:
            message["params"] = params
        process.stdin.write((json.dumps(message) + "\n").encode())
        process.stdin.flush()
        while time.monotonic() < deadline:
            if b"\n" not in pending:
                readable, _, _ = select.select([process.stdout], [], [], max(0, deadline - time.monotonic()))
                if not readable:
                    break
                chunk = os.read(process.stdout.fileno(), 16384)
                if not chunk:
                    raise BridgeError("Codex App Server 已结束")
                received += len(chunk)
                if received > 2_000_000:
                    raise BridgeError("Codex App Server 响应超过上限")
                pending.extend(chunk)
                continue
            line, _, tail = pending.partition(b"\n")
            pending[:] = tail
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if message.get("id") != number:
                continue
            if "error" in message:
                error = CodexAuthError if method == "account/read" else BridgeError
                raise error("Codex 账户读取暂不可用")
            return message.get("result", {})
        raise BridgeError("Codex 账户读取超时")

    try:
        request(1, "initialize", {"clientInfo": {"name": "ai_passport_local", "version": "0.1.0"}})
        process.stdin.write(b'{"method":"initialized"}\n')
        process.stdin.flush()
        account = request(2, "account/read", {"refreshToken": False}).get("account")
        if not isinstance(account, dict) or account.get("type") not in ("chatgpt", "chatgptAuthTokens"):
            raise CodexAuthError("Codex 尚未登录 ChatGPT 账户")
        identity = account.get("id") or account.get("email")
        if not isinstance(identity, str) or not identity:
            raise CodexAuthError("Codex 未提供可隔离周期的账户标识")
        account_hash = hashlib.sha256(identity.strip().lower().encode()).hexdigest()[:24]
        limits = request(3, "account/rateLimits/read")
        return {"account_hash": account_hash, "limits": limits, "observed_at": time.time(),
                "source": "official_app_server"}
    finally:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main_codex_bucket(limits):
    """Select only the core Codex bucket; never substitute another model bucket."""
    if not isinstance(limits, dict):
        return None
    buckets = limits.get("rateLimitsByLimitId") or {}
    if isinstance(buckets, dict) and "codex" in buckets:
        bucket = buckets["codex"]
    else:
        bucket = limits.get("rateLimits", limits)
    if not isinstance(bucket, dict) or bucket.get("limitId", bucket.get("limit_id", "codex")) != "codex":
        return None
    if not any(key in bucket for key in ("primary", "secondary")):
        return None
    return bucket


def normalized_codex_limits(limits):
    bucket = main_codex_bucket(limits)
    if bucket is None:
        return {}
    result = {"limitId": "codex"}
    for key in ("primary", "secondary"):
        window = bucket.get(key)
        if not isinstance(window, dict):
            result[key] = None
            continue
        used = window.get("rawUsedPercent", window.get("usedPercent", window.get("used_percent")))
        valid_used = type(used) in (int, float) and math.isfinite(used) and used >= 0
        duration = window.get("windowDurationMins", window.get("window_minutes"))
        reset = window.get("resetsAt", window.get("resets_at"))
        result[key] = {
            "usedPercent": round(min(used, 100), 4) if valid_used else None,
            "rawUsedPercent": used if valid_used else None,
            "windowDurationMins": duration if type(duration) is int and 0 < duration <= 2**31-1 else None,
            "resetsAt": reset if valid_count(reset) and 0 < reset <= MAX_DEVICE_TOKENS else None,
        }
    return result


def normalized_quota_cache(quota):
    """Keep exact primary/secondary positions and only non-sensitive quota fields."""
    return {"cache_version": QUOTA_CACHE_VERSION,
            "account_hash": quota.get("account_hash"),
            "auth_context": quota.get("auth_context"),
            "source": quota.get("source", "official_app_server"),
            "observed_at": quota.get("observed_at"),
            "limits": normalized_codex_limits(quota.get("limits"))}


def codex_quota_snapshot(quota, now=None):
    now_ms = int((time.time() if now is None else now) * 1000)
    quota = quota if isinstance(quota, dict) else {}
    source = quota.get("source")
    source = source if source in ("official_app_server", "local_log") else "local_log"
    observed = quota.get("observed_at")
    valid_observed = type(observed) in (int, float) and math.isfinite(observed) and 0 < observed <= MAX_DEVICE_TOKENS / 1000
    observed_ms = int(observed * 1000) if valid_observed else 0
    # Future source times are untrustworthy after clock rollback; never make fresh.
    clock_valid = valid_observed and observed_ms <= now_ms
    expires_ms = observed_ms + CODEX_QUOTA_TTL_MS if valid_observed else 0
    payload = {"observed_utc_ms": observed_ms, "expires_utc_ms": expires_ms, "source": source}
    # Version-1 caches retained only a weekly window and lost its original slot.
    legacy = source == "official_app_server" and quota.get("cache_version") != QUOTA_CACHE_VERSION
    limits = normalized_codex_limits(quota.get("limits")) if not legacy else {}
    raw_used = []
    for index, key in enumerate(("primary", "secondary"), 1):
        window = limits.get(key) or {}
        used = window.get("usedPercent") if clock_valid else None
        reset = window.get("resetsAt")
        if reset is not None and valid_observed:
            if reset * 1000 < observed_ms:
                used = None  # The source still described a window already ended.
            elif reset * 1000 < expires_ms:
                expires_ms = reset * 1000
        payload[f"w{index}_used_percent"] = used
        payload[f"w{index}_duration_min"] = window.get("windowDurationMins")
        payload[f"w{index}_reset_s"] = reset
        raw_used.append(window.get("rawUsedPercent"))
    payload["expires_utc_ms"] = expires_ms
    # No valid clock means no active timeframe. Zero timestamps encode unknown.
    if not clock_valid:
        payload["observed_utc_ms"] = payload["expires_utc_ms"] = 0
    ready = clock_valid and any(payload[f"w{i}_used_percent"] is not None for i in (1, 2))
    stale = not ready or now_ms >= expires_ms
    return {"payload": payload, "ready": ready, "stale": stale,
            "age_seconds": (now_ms - observed_ms) / 1000 if clock_valid else None,
            "legacy_cache": legacy, "raw_used_percent": raw_used}


def weekly_window(limits):
    bucket = main_codex_bucket(limits)
    if bucket is None:
        return None
    for key in ("primary", "secondary"):
        window = bucket.get(key)
        if not isinstance(window, dict):
            continue
        duration = window.get("windowDurationMins", window.get("window_minutes"))
        end = window.get("resetsAt", window.get("resets_at"))
        if type(duration) is int and duration == 10080 and valid_count(end):
            return {"starts_at": end - duration * 60, "resets_at": end, "duration_minutes": duration}
    return None


def counter_record_kind(prefix):
    """Inspect only bounded top-level scalar metadata, never a message body.

    A picture-bearing response can exceed the line budget without hiding a
    token event. Unknown ordering/headers stay uncertain rather than guessed.
    """
    text = prefix[:1024].decode("utf-8", "replace").lstrip()
    if not text.startswith("{"):
        return None
    decoder = json.JSONDecoder()
    position = 1
    try:
        for _ in range(8):
            while position < len(text) and text[position].isspace(): position += 1
            key, position = decoder.raw_decode(text, position)
            if not isinstance(key, str): return None
            while position < len(text) and text[position].isspace(): position += 1
            if position >= len(text) or text[position] != ":": return None
            position += 1
            while position < len(text) and text[position].isspace(): position += 1
            # Never decode payloads or nested metadata merely to find a type.
            if position >= len(text) or text[position] in "{[": return None
            value, position = decoder.raw_decode(text, position)
            if key == "type": return value if isinstance(value, str) else None
            while position < len(text) and text[position].isspace(): position += 1
            if position >= len(text) or text[position] != ",": return None
            position += 1
    except (ValueError, TypeError):
        pass
    return None


class CounterCache:
    """Incremental counter-only index; conversation bodies never enter the DB."""
    def __init__(self, state_dir, source_root=None):
        state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = state_dir / "01 usage.sqlite3"
        self.db = sqlite3.connect(path, timeout=10)
        os.chmod(path, 0o600)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, inode INTEGER,
                offset INTEGER, size INTEGER, mtime INTEGER, sid TEXT, created REAL,
                forked INTEGER, malformed INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS events(sid TEXT, at REAL, total INTEGER,
                last INTEGER, inherited INTEGER, fingerprint TEXT PRIMARY KEY);
            CREATE INDEX IF NOT EXISTS events_session_time ON events(sid,at);
            CREATE TABLE IF NOT EXISTS quota(singleton INTEGER PRIMARY KEY, at REAL, data TEXT, seen_at REAL);
        """)
        if "seen_at" not in {row[1] for row in self.db.execute("PRAGMA table_info(quota)")}:
            self.db.execute("ALTER TABLE quota ADD COLUMN seen_at REAL")
            self.db.execute("UPDATE quota SET seen_at=at")
            self.db.commit()
        if self.db.execute("PRAGMA user_version").fetchone()[0] < 1:
            # Revisit old false-positive image records; counter fingerprints
            # remain deduplicated. Do not simply clear genuine parse failures.
            self.db.execute("UPDATE files SET offset=0,malformed=0 WHERE malformed>0")
            self.db.execute("PRAGMA user_version=1")
            self.db.commit()
        self.state_dir = state_dir
        if source_root is not None:
            root = Path(source_root).expanduser().resolve()
            fingerprint = hashlib.sha256(("qingjian-codex-profile-v1\0" + str(root)).encode()).hexdigest()
            self.db.execute("CREATE TABLE IF NOT EXISTS source_profile(singleton INTEGER PRIMARY KEY, fingerprint TEXT)")
            bound = self.db.execute("SELECT fingerprint FROM source_profile WHERE singleton=1").fetchone()
            belongs = bool(bound and bound[0] == fingerprint)
            if bound is None:
                # Preserve and adopt a legacy counter only when every indexed
                # source belongs to this profile. Never clear another profile.
                roots = (root / "sessions", root / "archived_sessions")
                belongs = all(any(base in Path(path).resolve().parents for base in roots)
                              for (path,) in self.db.execute("SELECT path FROM files"))
            if not belongs:
                self.db.close()
                # Every profile gets its own counters, quota cache and cycle
                # marker, while badge configuration stays in the shared state.
                self.__init__(state_dir / "11 profiles" / fingerprint, source_root=root)
                return
            if bound is None:
                self.db.execute("INSERT INTO source_profile VALUES(1,?)", (fingerprint,))
                self.db.commit()

    def scan(self, codex_root, budget=256 * 1024 * 1024):
        paths = []
        errors = []
        for folder in ("sessions", "archived_sessions"):
            base = codex_root / folder
            if base.exists():
                paths.extend(base.rglob("*.jsonl"))
        paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        truncated_inventory = len(paths) > 20000
        paths = paths[:20000]
        partial = truncated_inventory
        newest_unscanned = 0
        scanned = 0
        newest_error = 0
        for path in paths:
            try:
                stat = path.stat()
                row = self.db.execute("SELECT inode,offset,size,mtime,sid,created,forked,malformed FROM files WHERE path=?", (str(path),)).fetchone()
                if row and row[0] == stat.st_ino and row[2:4] == (stat.st_size, stat.st_mtime_ns) and row[1] == stat.st_size:
                    continue
                if budget <= 0:
                    partial = True
                    newest_unscanned = max(newest_unscanned, stat.st_mtime)
                    continue
                offset, sid, created, forked, malformed = (row[1], row[4], row[5], row[6], row[7]) if row and row[0] == stat.st_ino and row[1] <= stat.st_size else (0, None, None, 0, 0)
                with path.open("rb") as stream:
                    stream.seek(offset)
                    while budget > 0:
                        start = stream.tell()
                        line = stream.readline(8 * 1024 * 1024)
                        if not line:
                            break
                        budget -= len(line)
                        if not line.endswith(b"\n"):
                            if len(line) >= 8 * 1024 * 1024:
                                irrelevant = counter_record_kind(line) in ("response_item", "turn_context", "compacted")
                                while line and not line.endswith(b"\n"):
                                    line = stream.readline(8 * 1024 * 1024)
                                    budget -= len(line)
                                if not line.endswith(b"\n"):
                                    stream.seek(start)
                                    partial = True
                                    break
                                if not irrelevant: malformed += 1
                                offset = stream.tell()
                                continue
                            stream.seek(start)
                            partial = True
                            break
                        offset = stream.tell()
                        if b'"session_meta"' not in line and b'"token_count"' not in line:
                            continue
                        try:
                            event = json.loads(line)
                        except (ValueError, UnicodeDecodeError):
                            malformed += 1
                            continue
                        payload = event.get("payload")
                        if not isinstance(payload, dict):
                            continue
                        if event.get("type") == "session_meta" and sid is None:
                            sid = payload.get("id")
                            if not isinstance(sid, str) or len(sid) > 100:
                                sid = None
                                continue
                            created = timestamp(payload.get("timestamp"))
                            forked = bool(payload.get("forked_from_id") or payload.get("forked_from"))
                        if event.get("type") != "event_msg" or payload.get("type") != "token_count":
                            continue
                        at = timestamp(event.get("timestamp"))
                        info = payload.get("info") or {}
                        total = (info.get("total_token_usage") or {}).get("total_tokens")
                        last = (info.get("last_token_usage") or {}).get("total_tokens")
                        if sid and at is not None and valid_count(total) and total <= 2**63-1:
                            last = last if valid_count(last) and last <= total else None
                            fingerprint = hashlib.sha256(f"{sid}|{at}|{total}|{last}".encode()).hexdigest()
                            inherited = int(bool(forked and created is not None and at < created))
                            self.db.execute("INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?)", (sid, at, total, last, inherited, fingerprint))
                        quota = payload.get("rate_limits")
                        if at and isinstance(quota, dict) and quota.get("limit_id") == "codex" and normalized_codex_limits(quota):
                            previous = self.db.execute("SELECT at,data,seen_at FROM quota WHERE singleton=1").fetchone()
                            if previous is None or at > (previous[2] or previous[0]):
                                # Keep main-account windows; never credits, identity or bodies.
                                minimal = normalized_codex_limits(quota)
                                observed = at
                                if previous and normalized_codex_limits(json.loads(previous[1])) == minimal:
                                    # Repeated token events can carry the same cached quota.
                                    # Keep its expiry while advancing ordering independently.
                                    observed = previous[0]
                                self.db.execute("INSERT OR REPLACE INTO quota(singleton,at,data,seen_at) VALUES(1,?,?,?)",
                                                (observed, json.dumps(minimal), at))
                self.db.execute("INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?,?,?,?)", (str(path), stat.st_ino, offset, stat.st_size, stat.st_mtime_ns, sid, created, int(forked), malformed))
                if offset < stat.st_size:
                    partial = True
                    newest_unscanned = max(newest_unscanned, stat.st_mtime)
                scanned += 1
            except (OSError, ValueError, sqlite3.Error) as exc:
                errors.append(type(exc).__name__)
                partial = True
                newest_error = max(newest_error, time.time())
        self.db.commit()
        malformed_mtime = self.db.execute("SELECT MAX(mtime) FROM files WHERE malformed>0").fetchone()[0]
        newest_uncertain = max(newest_error, (malformed_mtime or 0) / 1_000_000_000)
        return {"files_found": len(paths), "files_scanned": scanned, "scan_incomplete": partial,
                "newest_unscanned_mtime": newest_unscanned or None,
                "newest_uncertain_mtime": newest_uncertain or None,
                "read_errors": len(errors), "malformed_records": self.db.execute("SELECT COALESCE(SUM(malformed),0) FROM files").fetchone()[0]}

    def log_quota(self):
        row = self.db.execute("SELECT at,data FROM quota WHERE singleton=1").fetchone()
        return {"observed_at": row[0], "limits": json.loads(row[1]), "source": "local_log"} if row else None

    def totals(self, cycle_start):
        lifetime = cycle = 0
        resets = uncertain = 0
        previous = {}
        sessions = set()
        meta = {sid: (created, forked) for sid, created, forked in self.db.execute("SELECT sid,MIN(created),MAX(forked) FROM files WHERE sid IS NOT NULL GROUP BY sid")}
        for sid, at, total, last, inherited in self.db.execute("SELECT sid,at,total,last,inherited FROM events ORDER BY sid,at,total"):
            old = previous.get(sid)
            previous[sid] = total
            sessions.add(sid)
            if inherited:
                continue
            created, forked = meta.get(sid, (None, 0))
            if old is None:
                delta = total
                if forked and last is not None and total > last:
                    delta = last
                    uncertain += 1
            elif total >= old:
                delta = total - old
            else:
                # A counter restart is a new increment, never a negative use.
                delta = last if last is not None else total
                resets += 1
            lifetime += delta
            if cycle_start is not None and at >= cycle_start:
                cycle_delta = delta
                if old is None and (created is None or created < cycle_start) and last is not None and delta > last:
                    # Missing pre-boundary history cannot be assigned to this cycle.
                    cycle_delta = last
                    uncertain += 1
                cycle += cycle_delta
        return {"cycle_tokens": cycle if cycle_start is not None else None, "lifetime_tokens": lifetime,
                "session_count": len(sessions), "counter_restarts": resets, "uncertain_boundaries": uncertain,
                "last_token_event_at": self.db.execute("SELECT MAX(at) FROM events WHERE inherited=0").fetchone()[0]}


def choose_cycle(previous, quota, now, manual=False):
    result = dict(previous or {})
    window = weekly_window(quota.get("limits", {})) if quota else None
    if manual:
        result.update(cycle_started_at=now, reset_source="manual_reset_card", reset_detection="user_confirmed")
    elif window and window["starts_at"] <= now < window["resets_at"]:
        old_start = result.get("cycle_started_at")
        old_end = result.get("weekly_resets_at")
        if old_start is None or (old_end is not None and now >= old_end and window["resets_at"] > old_end):
            result.update(cycle_started_at=window["starts_at"], reset_source="weekly_window", reset_detection=quota["source"])
        # A moved deadline before its natural end is not proof of card redemption.
        elif old_end is not None and window["resets_at"] != old_end and now < old_end:
            result["reset_detection"] = "window_changed_manual_confirmation_required"
        result["weekly_resets_at"] = window["resets_at"]
        result["window_duration_minutes"] = window["duration_minutes"]
    elif result.get("weekly_resets_at") and now >= result["weekly_resets_at"]:
        # Stop showing an expired cycle; wait for a current source window.
        result.update(cycle_started_at=None, reset_source="unavailable", reset_detection="waiting_for_current_weekly_window")
    elif not result:
        result.update(cycle_started_at=None, reset_source="unavailable", reset_detection="weekly_window_unavailable")
    return result


def read_tokens(args, manual=False, config=None):
    state_dir = Path(args.state_dir).expanduser()
    codex_home = Path(args.codex_home).expanduser().resolve()
    auth_context = codex_auth_context(codex_home)
    counter = CounterCache(state_dir, source_root=codex_home)
    coverage = counter.scan(codex_home)
    quota_path = counter.state_dir / "02 quota.json"
    cached = load_json(quota_path, {})
    cached = cached if isinstance(cached, dict) else {}
    quota = None
    quota_error = None
    auth_failed = False
    cached_at = cached.get("observed_at")
    cache_age = time.time() - cached_at if type(cached_at) in (int, float) and math.isfinite(cached_at) else None
    same_context = auth_context is not None and cached.get("auth_context") == auth_context
    if same_context and cached.get("cache_version") == QUOTA_CACHE_VERSION and cache_age is not None and 0 <= cache_age < 60 and not args.refresh_quota:
        quota = cached
    elif not args.no_quota:
        try:
            live = app_server_quota(args.codex_cli, codex_home=codex_home)
            if codex_auth_context(codex_home) != auth_context:
                raise CodexAuthError("Codex 登录账户在读取期间发生变化，请重新同步")
            quota = normalized_quota_cache({**live, "auth_context": auth_context})
            atomic_json(quota_path, quota)
        except CodexAuthError as exc:
            quota_error, auth_failed = str(exc), True
        except (BridgeError, OSError) as exc:
            quota_error = str(exc)
    if codex_auth_context(codex_home) != auth_context:
        quota, same_context, auth_failed = None, False, True
        quota_error = "Codex 登录账户在读取期间发生变化，请重新同步"
    if quota is None:
        # Only a still-matching local auth/profile may reuse a previous account
        # observation. Sign-out, account switch, unreadable/missing auth, or a
        # rejected live account read makes prior quota unknown. A clean local
        # log-only installation has no asserted account attribution.
        trusted_cache = cached if same_context and not auth_failed else {}
        log = counter.log_quota() if trusted_cache or (not cached and auth_context is None and not auth_failed) else None
        quota = log if log and (not trusted_cache or log["observed_at"] > trusted_cache.get("observed_at", 0)) else trusted_cache or log
        if quota and trusted_cache.get("account_hash"):
            quota["account_hash"] = trusted_cache["account_hash"]
    account_hash = quota.get("account_hash") if quota else None
    scope_id = account_hash or "local-profile-unverified"
    cycle_path = counter.state_dir / ("03 cycle " + scope_id + ".json")
    cycle = choose_cycle(load_json(cycle_path, {}), quota, time.time(), manual)
    atomic_json(cycle_path, cycle)
    totals = counter.totals(cycle.get("cycle_started_at"))
    counter.db.close()
    tokens = totals["cycle_tokens"]
    cycle_start = cycle.get("cycle_started_at")
    coverage["cycle_scan_incomplete"] = bool(cycle_start is None or any(
        coverage[key] is not None and coverage[key] >= cycle_start
        for key in ("newest_unscanned_mtime", "newest_uncertain_mtime")))
    quota_age = max(0, time.time() - quota.get("observed_at", 0)) if quota else None
    if config is None:
        config = load_json(state_dir / "04 device config.json", {})
    first, final = thresholds(config)
    dashboard = codex_quota_snapshot(quota)
    result = {"ok": True, **totals, **cycle, "stage": stage_for(tokens, first, final) if tokens is not None and not coverage["cycle_scan_incomplete"] else None,
              "threshold1": first, "threshold2": final,
              "scope": "本机可读取的 Codex 会话与归档日志；包含输入及输出 Token（含缓存输入），不等于账单或全账户总量",
              "account_hash": account_hash, "account_verified": bool(account_hash and quota and
                    quota.get("source") == "official_app_server" and quota_age < 120),
              "account_attribution": "历史本地日志不含账户 ID，无法逐条证明属于当前登录账户",
              "quota_source": quota.get("source") if quota else None,
              "quota_observed_at": quota.get("observed_at") if quota else None,
              "quota_age_seconds": quota_age,
              "quota_error": quota_error, "coverage": coverage,
              "codex_quota": dashboard["payload"], "codex_quota_ready": dashboard["ready"],
              "codex_quota_stale": dashboard["stale"], "codex_quota_age_seconds": dashboard["age_seconds"],
              "codex_quota_legacy_cache": dashboard["legacy_cache"],
              "codex_quota_raw_used_percent": dashboard["raw_used_percent"],
              "reset_card_detection": "重置卡使用需在应用中明确确认；不会按已用百分比下降自动推断",
              "updated_at": time.time()}
    return result


class Device:
    def __init__(self, port=None):
        try:
            import serial
            from serial.tools import list_ports
        except ImportError as exc:
            raise BridgeError("缺少 pyserial 串口组件") from exc
        if port is None:
            matches = [p.device for p in list_ports.comports() if p.vid == 0x303A and p.pid == 0x1001]
            matches = sorted(set(matches))
            if len(matches) != 1:
                raise BridgeError("未找到唯一的 AI Passport USB 设备，请连接设备" if not matches else "发现多个 Passport，请明确选择串口")
            port = matches[0]
        # macOS's normal pySerial open applies DTR/RTS separately, which can
        # reset native USB C3 devices even when both were preset to False.
        if sys.platform == "darwin":
            try:
                from passport_serial import MacNoResetSerial
            except ImportError as exc:
                raise BridgeError("缺少青笺 USB 连接组件，请更新应用") from exc
            serial_class = MacNoResetSerial
        else:
            serial_class = serial.Serial
        self.serial = serial_class(port=None, baudrate=115200, timeout=0.1, write_timeout=2)
        if sys.platform != "darwin":
            self.serial.dtr = False
            self.serial.rts = False
        self.serial.port = port
        self.serial.open()
        self.port = port
        self.transport = "usb"

    def close(self):
        self.serial.close()

    def request(self, command, expected, timeout=5):
        raw = ("@AP " + command + "\n").encode("ascii")
        if len(raw) > 768:
            raise BridgeError("设备命令超过长度上限")
        self.serial.reset_input_buffer()
        self.serial.write(raw)
        self.serial.flush()
        end = time.monotonic() + timeout
        pending = bytearray()
        while time.monotonic() < end:
            chunk = self.serial.read(512)
            pending.extend(chunk)
            if len(pending) > 16384:
                raise BridgeError("设备响应超过上限")
            while b"\n" in pending:
                line, _, tail = pending.partition(b"\n")
                pending[:] = tail
                line = line.strip().decode("utf-8", "replace")
                if not line.startswith("@AP "):
                    continue
                if line.startswith("@AP ERROR") or line.endswith("_ERROR"):
                    raise BridgeError("设备拒绝操作：" + line[:180])
                if line == expected or line.startswith(expected + " "):
                    return line[len(expected):].strip()
        raise BridgeError("设备未及时确认操作")

    def status(self):
        try:
            status = json.loads(self.request("STATUS", "@AP STATUS"))
        except ValueError as exc:
            raise BridgeError("设备状态格式错误") from exc
        if not isinstance(status, dict):
            raise BridgeError("设备状态不是对象")
        return status

    def wait_fields(self, expected, timeout=10):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            status = self.status()
            if not status.get("save_busy", False) and status.get("last_save_error", 0):
                raise BridgeError("设备未能确认保存，请重新读取状态")
            if not status.get("save_busy", False) and all(status.get(key) == value for key, value in expected.items()):
                return status
            time.sleep(0.12)
        raise BridgeError("设备已接收请求，但未确认保存结果；请重新读取设备状态")


def _ble_transport_module():
    try:
        import passport_ble
        return passport_ble
    except ImportError as exc:
        raise BridgeError("缺少青笺蓝牙传输组件，请更新应用") from exc


class BLEDevice(Device):
    """Bridge-compatible synchronous facade; each session keeps one asyncio loop."""
    def __init__(self, identifier):
        module = _ble_transport_module()
        self.transport = "ble"
        self._ble = None
        try:
            self._ble = module.SyncPassportBLE(identifier)
            self._ble.connect()
            self.port = "BLE:" + self._ble.identifier
        except Exception as exc:
            if self._ble is not None:
                self._ble.close()
            message = str(exc) if isinstance(exc, module.BLETransportError) else "蓝牙连接失败，请检查设备和 macOS 蓝牙权限"
            raise BridgeError(message) from exc

    def close(self):
        if self._ble is not None:
            self._ble.close()

    def request(self, command, expected, timeout=5):
        if command.split(maxsplit=1)[0] == "AVATAR":
            raise BridgeError("自定义图片及恢复默认图片只能通过 USB 更新")
        module = _ble_transport_module()
        try:
            return self._ble.request(command, expected, timeout)
        except Exception as exc:
            message = str(exc) if isinstance(exc, module.BLETransportError) else "蓝牙传输失败，请重新读取设备状态"
            raise BridgeError(message) from exc


def scan_ble_devices(timeout=5):
    module = _ble_transport_module()
    try:
        devices = asyncio.run(module.scan_candidates(timeout))
        return {"ok": True, "devices": [asdict(device) for device in devices]}
    except Exception as exc:
        message = str(exc) if isinstance(exc, (module.BLETransportError, ValueError)) else "蓝牙扫描失败，请检查 macOS 蓝牙权限"
        raise BridgeError(message) from exc


def cycle_sync_marker(token_result, device_status):
    identifier = device_status.get("device_id")
    # Firmware identity is base MAC hex, stable across USB paths and macOS UUIDs.
    if isinstance(identifier, str):
        identifier = identifier.upper()
        if identifier.startswith("MAC:"):
            identifier = identifier[4:]
        if len(identifier) != 12 or any(char not in "0123456789ABCDEF" for char in identifier):
            identifier = None
    else:
        identifier = None
    return {"cycle_started_at": token_result.get("cycle_started_at"),
            "account_hash": token_result.get("account_hash"), "device_id": identifier}


def cycle_reset_needed(previous, current):
    if current.get("cycle_started_at") is None:
        return False
    if not isinstance(previous, dict) or previous.get("cycle_started_at") is None:
        return True
    if previous["cycle_started_at"] != current["cycle_started_at"]:
        return True
    # Migrate legacy port-based or unverified-identity markers without a fake reset.
    # Only a known, changed account/device is evidence of a different target.
    for key in ("account_hash", "device_id"):
        if previous.get(key) and current.get(key) and previous[key] != current[key]:
            return True
    return False


def avatar_sync_report(status, stage, prepared, report, outcome):
    """One custom stage is cached on the badge; other artwork stays on the Mac."""
    pending = []
    for key, avatar in prepared.items():
        matches = avatar is not None and status.get("avatar_custom") is True and \
            status.get("avatar_custom_stage") == int(key) and \
            status.get("avatar_sha256") == hashlib.sha256(avatar).hexdigest()
        if not matches:
            pending.append(int(key))
    if outcome == "usb_required" and stage is not None and stage not in pending:
        pending.append(stage)
    report.update(avatar_sync=outcome, avatar_sync_stage=stage,
                  avatar_pending_usb_stages=sorted(pending),
                  avatar_cached_stage=status.get("avatar_custom_stage") if status.get("avatar_custom") else None,
                  avatar_upload_usb_only=True)


def ensure_stage_avatar(device, stage, prepared, report=None):
    report = report if report is not None else {}
    status = device.status()
    avatar = prepared.get(str(stage))
    if getattr(device, "transport", "usb") == "ble":
        # Do not send *any* AVATAR command over BLE, including DEFAULT. A
        # different cached stage is retained for its next use by the firmware.
        if str(stage) in prepared:
            matches = avatar is not None and status.get("avatar_custom") is True and \
                status.get("avatar_custom_stage") == stage and \
                status.get("avatar_sha256") == hashlib.sha256(avatar).hexdigest()
        else:
            cached_stage = status.get("avatar_custom_stage")
            matches = not status.get("avatar_custom") or \
                (type(cached_stage) is int and cached_stage in (0, 1, 2) and cached_stage != stage)
        avatar_sync_report(status, stage, prepared, report, "up_to_date" if matches else "usb_required")
        return {}
    if avatar is None:
        if str(stage) in prepared:
            raise BridgeError("当前形态图片无法读取，请重新选择后通过 USB 上传")
        if status.get("avatar_custom"):
            device.request("AVATAR DEFAULT", "@AP AVATAR_DEFAULT_OK", timeout=12)
            status = device.wait_fields({"avatar_custom": False})
        avatar_sync_report(status, stage, prepared, report, "up_to_date")
        return {"avatar_custom": False}
    avatar_sha = hashlib.sha256(avatar).hexdigest()
    expected = {"avatar_sha256": avatar_sha, "avatar_custom": True, "avatar_custom_stage": stage}
    if all(status.get(key) == value for key, value in expected.items()):
        avatar_sync_report(status, stage, prepared, report, "up_to_date")
        return expected
    device.request(f"AVATAR BEGIN {len(avatar)} {avatar_sha} {stage}", "@AP AVATAR_BEGIN_OK")
    for offset in range(0, len(avatar), 256):
        chunk = base64.b64encode(avatar[offset:offset + 256]).decode()
        device.request(f"AVATAR DATA {offset} {chunk}", "@AP AVATAR_DATA_OK")
    device.request("AVATAR END", "@AP AVATAR_END_OK", timeout=12)
    status = device.wait_fields(expected)
    avatar_sync_report(status, stage, prepared, report, "uploaded")
    return expected


def sync_codex_dashboard(device, status, token_result):
    capabilities = status.get("capabilities")
    supported = status.get("codex_dashboard_supported") is True or \
        (isinstance(capabilities, (list, tuple)) and "codex_dashboard" in capabilities) or \
        (isinstance(capabilities, dict) and capabilities.get("codex_dashboard") is True)
    if not supported:
        token_result["codex_dashboard_sync"] = "unsupported_firmware"
        return status
    payload = token_result.get("codex_quota") or codex_quota_snapshot(None)["payload"]
    raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode()
    if len(raw) > 512:
        raise BridgeError("Codex 额度摘要超过设备协议上限")
    device.request("CODEX " + base64.b64encode(raw).decode(), "@AP CODEX_OK")
    if "codex_observed_utc_ms" in status:
        status = device.wait_fields({"codex_observed_utc_ms": payload["observed_utc_ms"]})
        token_result["codex_dashboard_sync"] = "verified"
    else:
        # Older capability-enabled development firmware may not expose an ID yet.
        status = device.status()
        token_result["codex_dashboard_sync"] = "accepted_unverified"
    return status


def collect_cursor_usage(state_dir, refresh=False):
    try:
        from passport_cursor import collect_cursor_quota
        return collect_cursor_quota(Path(state_dir).expanduser(), refresh=refresh)
    except Exception:
        return {"available": False, "status": "unavailable", "error_code": "usage_unavailable"}


def cursor_quota_payload(snapshot):
    empty = {"observed_utc_ms": 0, "expires_utc_ms": 0, "source": "cursor_app_api",
             "plan": "", "cursor_used_percent": None, "other_used_percent": None, "reset_at_ms": 0}
    if not isinstance(snapshot, dict) or snapshot.get("source") != "cursor_app_api":
        return empty
    at, until, reset = (snapshot.get(key) for key in ("observed_at_ms", "expires_at_ms", "reset_at_ms"))
    if any(type(v) is not int for v in (at, until, reset)) or not 1704067200000 <= at < 4102444800000 or \
            not at < until <= at + 300000 or not at < reset < 4102444800000:
        return empty
    percentages = [snapshot.get(key) for key in ("cursor_used_percent", "other_used_percent")]
    if any(v is not None and (type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 100) for v in percentages):
        return empty
    plan = snapshot.get("plan", "")
    if not isinstance(plan, str) or len(plan) > 23 or any(ord(c) < 32 or ord(c) > 126 for c in plan):
        plan = ""
    return {**empty, "observed_utc_ms": at, "expires_utc_ms": until, "plan": plan,
            "cursor_used_percent": percentages[0], "other_used_percent": percentages[1], "reset_at_ms": reset}


def sync_cursor_dashboard(device, status, token_result):
    if status.get("cursor_quota_supported") is not True:
        token_result["cursor_quota_sync"] = "unsupported_firmware"
        return status
    payload = cursor_quota_payload(token_result.get("cursor_quota"))
    raw = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode()
    if len(raw) > 512:
        raise BridgeError("Cursor 额度摘要超过设备协议上限")
    device.request("CURSOR " + base64.b64encode(raw).decode(), "@AP CURSOR_OK")
    status = device.wait_fields({"cursor_quota_observed_utc_ms": payload["observed_utc_ms"]})
    token_result["cursor_quota_sync"] = "verified"
    return status


def collect_provider_metadata(token_result, args):
    token_result["cursor_quota"] = collect_cursor_usage(args.state_dir, getattr(args, "refresh_quota", False)) \
        if not getattr(args, "no_quota", False) else {"available": False, "status": "unavailable"}
    try:
        from passport_providers import load_provider_snapshots
        token_result["provider_snapshots"] = load_provider_snapshots(
            token_result.get("cycle_started_at"), Path(args.state_dir).expanduser(),
            Path(args.codex_home).expanduser(), codex_usage=token_result)
    except Exception:
        # A missing/corrupt provider source must neither block the badge nor
        # leave yesterday's numbers presented as current observations.
        update = time.time_ns() // 1000
        token_result["provider_metadata_error"] = "metadata_unavailable"
        token_result["provider_snapshots"] = [{
            "provider": name, "update_id": update, "state": "unknown",
            "state_at_ms": 0, "state_until_ms": 0, "active_sessions": None,
            "metric_kind": "none", "metric_value": None, "metric_status": "unavailable",
            "metric_at_ms": 0, "last_activity_ms": 0, "model": "", "source": "none"
        } for name in ("codex", "cursor", "gemini")]
    return token_result


def sync_provider_dashboards(device, status, token_result):
    if status.get("provider_dashboard_supported") is not True:
        token_result["provider_sync"] = "unsupported_firmware"
        return status
    snapshots = token_result.get("provider_snapshots")
    if not snapshots:
        token_result["provider_sync"] = "metadata_unavailable"
        return status
    for snapshot in snapshots:
        if snapshot.get("provider") not in ("codex", "cursor", "gemini"):
            raise BridgeError("未知活动来源")
        raw = json.dumps(snapshot, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()
        command = "PROVIDER " + base64.b64encode(raw).decode()
        if len(command) + 5 > 768:
            raise BridgeError("活动摘要超过设备协议上限")
        device.request(command, "@AP PROVIDER_OK")
        field = snapshot["provider"] + "_activity_update_id"
        status = device.wait_fields({field: snapshot["update_id"]})
    token_result["provider_sync"] = "verified"
    return status


def sync_device(device, token_result, config=None, prepared=None, reset_cycle=False):
    owns_settings = config is not None
    prepared = prepared or {}
    now = dt.datetime.now().astimezone()
    offset = int(now.utcoffset().total_seconds() // 60)
    sent_ms = int(time.time() * 1000)
    started = time.monotonic()
    device.request(f"TIME {sent_ms} {offset}", "@AP TIME_OK")
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        status = device.status()
        device_time = status.get("utc_ms")
        estimated_ms = sent_ms + int((time.monotonic() - started) * 1000)
        if status.get("time_synced") is True and status.get("offset_min") == offset and \
                type(device_time) is int and abs(device_time - estimated_ms) <= 3000:
            break
        time.sleep(0.12)
    else:
        raise BridgeError("设备未确认最新时间与时区")
    if owns_settings:
        first, final = thresholds(config)
    else:
        # A new Mac has no authority to replace preferences already on the badge.
        if "threshold1" not in status or "threshold2" not in status:
            raise BridgeError("设备未返回有效成长阈值，请先读取设备或更新固件")
        first, final = thresholds(status)
    expected_thresholds = {"threshold1": first, "threshold2": final}
    if owns_settings and any(status.get(key) != value for key, value in expected_thresholds.items()):
        device.request(f"THRESHOLDS {first} {final}", "@AP THRESHOLDS_OK")
        device.wait_fields(expected_thresholds)
    cycle_tokens = token_result.get("cycle_tokens")
    known = valid_count(cycle_tokens) and cycle_tokens <= MAX_DEVICE_TOKENS and not token_result.get("coverage", {}).get("cycle_scan_incomplete", False)
    token_result.update(threshold1=first, threshold2=final,
                        stage=stage_for(cycle_tokens, first, final) if known else None)
    if known:
        if reset_cycle:
            # A newly verified epoch resets the device's once-per-cycle animation.
            device.request("TOKENS 0", "@AP TOKENS_OK")
            device.wait_fields({"tokens": 0, "tokens_known": True, "stage": 0})
        stage = stage_for(cycle_tokens, first, final)
        expected_avatar = ensure_stage_avatar(device, stage, prepared, token_result) if owns_settings else {}
        # Install art first so a threshold crossing animates into the right image.
        device.request(f"TOKENS {cycle_tokens}", "@AP TOKENS_OK")
        status = device.wait_fields({"tokens": cycle_tokens, "tokens_known": True,
                                     "stage": stage, **expected_thresholds, **expected_avatar})
    else:
        device.request("TOKENS UNKNOWN", "@AP TOKENS_OK")
        status = device.wait_fields({"tokens_known": False})
        if owns_settings:
            avatar_sync_report(status, None, prepared, token_result, "cycle_unknown")
    status = sync_codex_dashboard(device, status, token_result)
    status = sync_cursor_dashboard(device, status, token_result)
    return sync_provider_dashboards(device, status, token_result)


def upload_device(device, config, token_result, prepared=None, reset_cycle=False):
    badge = {key: config[key] for key in FIELDS}
    payload = base64.b64encode(json.dumps(badge, ensure_ascii=False, separators=(",", ":")).encode()).decode()
    device.request("BADGE " + payload, "@AP BADGE_OK")
    device.wait_fields(badge)
    device.request(f"FEATURES {int(config['feature_all'])} {config['feature_mask']}", "@AP FEATURES_OK")
    expected_features = {"feature_all": config["feature_all"]}
    if not config["feature_all"]:
        expected_features["feature_mask"] = config["feature_mask"]
    device.wait_fields(expected_features)
    status = sync_device(device, token_result, config, prepared, reset_cycle)
    expected = {**badge, **expected_features}
    if not all(status.get(key) == value for key, value in expected.items()):
        raise BridgeError("最终回读与上传内容不同")
    return status


def parser():
    parser = argparse.ArgumentParser(description="AI Passport 本地编辑器后端")
    transport = parser.add_mutually_exclusive_group()
    transport.add_argument("--port")
    transport.add_argument("--ble-id", help="用户已选择或绑定的 macOS 蓝牙 UUID")
    parser.add_argument("--state-dir", default=str(DEFAULT_STATE))
    parser.add_argument("--codex-home", default=os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    parser.add_argument("--codex-cli")
    parser.add_argument("--no-quota", action="store_true")
    parser.add_argument("--refresh-quota", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    commands.add_parser("tokens")
    commands.add_parser("sync")
    commands.add_parser("sources-status", help="只读检查桌面/CLI活动接入，不连接工牌")
    setup = commands.add_parser("sources-configure", help="保留现有设置并配置官方活动回调")
    setup.add_argument("--provider", action="append", choices=("cursor", "gemini"), required=True)
    commands.add_parser("pair", help="通过可信 USB 请求蓝牙配对窗口")
    commands.add_parser("cursor-usage", help="只读刷新 Cursor 套餐用量")
    ble_scan = commands.add_parser("ble-scan", help="只读扫描，不自动连接")
    ble_scan.add_argument("--timeout", type=float, default=5)
    upload = commands.add_parser("upload")
    upload.add_argument("--config", required=True)
    reset = commands.add_parser("reset-cycle")
    reset.add_argument("--reason", required=True, choices=["manual_reset_card"])
    screen = commands.add_parser("screen")
    screen.add_argument("state", choices=["0", "1"])
    preview = commands.add_parser("preview-avatar")
    preview.add_argument("--source", required=True)
    preview.add_argument("--output", required=True)
    preview.add_argument("--builtin", action="store_true")
    return parser


def main():
    args = parser().parse_args()
    device = None
    try:
        if args.command == "pair" and args.ble_id:
            raise BridgeError("首次蓝牙配对窗口只能通过可信 USB 请求，请先连接 USB")
        if args.command == "cursor-usage":
            result = {"ok": True, "cursor_quota": collect_cursor_usage(args.state_dir, refresh=True)}
        elif args.command in ("sources-status", "sources-configure"):
            try:
                import passport_sources
            except ImportError as exc:
                raise BridgeError("缺少数据源接入组件，请更新青笺。") from exc
            if args.command == "sources-status":
                result = passport_sources.source_status(Path(args.state_dir).expanduser(), sys.executable)
            else:
                result = passport_sources.configure_sources(Path(args.state_dir).expanduser(), sys.executable, args.provider)
            if result.get("ok") is not True:
                code = result.get("error_code", "source_setup_failed")
                messages = {
                    "hooks_disabled": "Gemini 的活动回调已在原设置中关闭，青笺保留了这个选择。",
                    "owned_hook_modified": "检测到你修改过青笺活动回调，未覆盖这些改动。请检查原配置。",
                    "concurrent_change": "配置在操作过程中发生变化，已停止；请重新检测后再试。",
                    "setup_busy": "另一次配置正在进行，请稍后重新检测。",
                    "invalid_runtime": "内置运行环境不可用，请更新或重新打开青笺。",
                    "invalid_config_json": "已有配置文件格式有误，未修改配置；请修复原设置后再试。",
                    "duplicate_config_key": "已有配置包含重复字段，未修改配置；请先检查原设置。",
                    "unsupported_hooks_version": "当前活动配置版本不兼容，已保留原配置。",
                    "rollback_incomplete": "操作中有其他配置改动，自动恢复未完全完成；原文件备份已保留。",
                    "state_outside_home": "请在正常的用户资料目录中使用数据源接入设置。",
                }
                raise BridgeError(messages.get(code, "接入配置未完成，原文件已保留；请检查配置文件权限或重新检测。"))
        elif args.command == "ble-scan":
            result = scan_ble_devices(args.timeout)
        elif args.command == "preview-avatar":
            result = preview_avatar(args.source, args.output, args.builtin)
        elif args.command in ("tokens", "reset-cycle"):
            result = read_tokens(args, manual=args.command == "reset-cycle")
            collect_provider_metadata(result, args)
        else:
            state_dir = Path(args.state_dir).expanduser()
            config_path = state_dir / "04 device config.json"
            config = None
            if args.command == "upload":
                config = validate_config(load_json(Path(args.config)))
            elif args.command == "sync":
                saved_config = load_json(config_path)
                if saved_config is not None:
                    config = validate_config(saved_config)
            # Conversion and source reads finish before opening a device connection.
            token_result = read_tokens(args, config=config) if args.command in ("sync", "upload") else None
            if token_result is not None:
                collect_provider_metadata(token_result, args)
            prepared = prepare_avatars(config, token_result, strict=not bool(args.ble_id)) if config else None
            marker_path = state_dir / "05 device sync.json"
            previous_sync = load_json(marker_path, {}) if token_result else {}
            device = BLEDevice(args.ble_id) if args.ble_id else Device(args.port)
            initial_status = device.status() if token_result else None
            cycle_marker = cycle_sync_marker(token_result, initial_status) if token_result else {}
            reset_cycle = bool(token_result and cycle_reset_needed(previous_sync, cycle_marker))
            if args.command == "status":
                status = device.status()
            elif args.command == "screen":
                device.request("SCREEN " + args.state, "@AP SCREEN_OK")
                status = device.wait_fields({"screen_on": args.state == "1"})
            elif args.command == "pair":
                device.request("PAIR", "@AP PAIR_OK", timeout=10)
                status = device.status()
            elif args.command == "sync":
                status = sync_device(device, token_result, config, prepared, reset_cycle)
            else:
                status = upload_device(device, config, token_result, prepared, reset_cycle)
                # Text/settings are verified; pending stage images remain local
                # selections until a USB session installs that active stage.
                atomic_json(config_path, config)
            if token_result and status.get("tokens_known") and not token_result.get("coverage", {}).get("cycle_scan_incomplete"):
                atomic_json(marker_path, cycle_marker)
            result = {"ok": True, "port": device.port, "transport": getattr(device, "transport", "usb"), "status": status}
            if args.command == "pair":
                result["pairing"] = "requested"
            if token_result:
                result["usage"] = token_result
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0
    except Exception as exc:
        # Never dump app-server payloads, serial buffers or local conversations.
        message = str(exc) if isinstance(exc, BridgeError) else type(exc).__name__ + ": 操作失败，请检查本机文件与连接"
        print(json.dumps({"ok": False, "error": message}, ensure_ascii=False))
        return 1
    finally:
        if device:
            device.close()


if __name__ == "__main__":
    raise SystemExit(main())
