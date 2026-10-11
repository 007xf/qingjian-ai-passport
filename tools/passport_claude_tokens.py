"""Claude Code transcript counters, never account billing or a quota estimate.

Only assistant API message metadata is selected. Repeated content blocks,
stream updates, archived copies and inherited fork messages share an API ID and
are counted once. Subagent transcripts contain separate calls and are included.
An anonymous SQLite index preserves progress across bounded refreshes. File
contents are only parsed when new; changed prefixes invalidate that file's
metadata, and deleted files no longer contribute to the current count. Prefix
verification reads binary SHA256 segments without parsing or storing bodies.
The byte budget bounds new parsing, with at most one bounded line of overshoot
to prevent a small budget from starving a valid record. Verification reads are
separate, occur only when source metadata changes, and use bounded buffers.
Incomplete sources retain the last complete observation without renewing its
source time. No bodies or plaintext paths/API IDs enter either cache.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import time

from passport_providers import _MetadataJSON

MAX_TOKENS = 2**53 - 1
MAX_LINE = 8 * 1024 * 1024
MAX_FILES = 20000
TTL_MS = 180000
COMPONENTS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
SELECTOR = {"type": True, "timestamp": True, "requestId": True,
            "isApiErrorMessage": True, "message": {"id": True, "model": True,
            "usage": {name: True for name in COMPONENTS}}}


def config_directory(home=None):
    return Path(home if home is not None else os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude").expanduser().resolve()


def profile_scope(root):
    return hashlib.sha256(("qingjian-claude-profile-v1\0" + str(root)).encode()).hexdigest()


def empty(code=None):
    return {"source": "claude_code_local", "available": False, "complete": False,
            "status": "unavailable", "today_tokens": None, "lifetime_tokens": None,
            "observed_at_ms": 0, "expires_at_ms": 0, "day_start_ms": 0,
            "day_end_ms": 0, "error_code": code,
            "account_verified": False,
            "scope": "Local profile transcript records, possibly spanning historical accounts; not account billing",
            "counting": "input + output + cache creation + cache read; recorded values only"}


def _time(value):
    if not isinstance(value, str):
        return None
    try:
        value = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if value.tzinfo is None:
            return None
        result = int(value.timestamp() * 1000)
        return result if 1704067200000 <= result < 4102444800000 else None
    except (ValueError, OverflowError):
        return None


def _count(value):
    return type(value) is int and 0 <= value <= MAX_TOKENS


def _read_cache(path, scope, now):
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 16384:
            return None
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
            value = json.load(stream)
        if not isinstance(value, dict) or value.get("profile_scope") != scope or value.get("source") != "claude_code_local":
            return None
        at, until = value.get("observed_at_ms"), value.get("expires_at_ms")
        if not _count(at) or not _count(until) or not 0 < at <= now or not at < until <= at + TTL_MS or value.get("complete") is not True:
            return None
        if not all(_count(value.get(key)) for key in ("today_tokens", "lifetime_tokens", "day_start_ms", "day_end_ms")):
            return None
        if not value["day_start_ms"] <= at < value["day_end_ms"] or value["today_tokens"] > value["lifetime_tokens"]:
            return None
        return value
    except (OSError, ValueError, TypeError):
        return None


def _write_cache(path, value):
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = path.parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022 or path.is_symlink():
            return False
        descriptor, temporary = tempfile.mkstemp(prefix=".claude-tokens-", dir=path.parent)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, separators=(",", ":"), allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
        return True
    except (OSError, ValueError):
        return False
    finally:
        if temporary:
            os.unlink(temporary)


def _alias(kind, value):
    if value is None:
        return None
    if not isinstance(value, str) or not 0 < len(value) <= 256:
        raise ValueError("invalid call identity")
    return hashlib.sha256(("qingjian-claude-" + kind + "-v1\0" + value).encode()).hexdigest()


def _counter(line):
    event = _MetadataJSON(io.StringIO(line.decode("utf-8")), len(line) + 1).document(SELECTOR)
    if not isinstance(event, dict):
        raise ValueError("invalid record")
    if event.get("type") != "assistant" or event.get("isApiErrorMessage") is True:
        return None
    message = event.get("message")
    if not isinstance(message, dict):
        raise ValueError("invalid assistant")
    if message.get("model") == "<synthetic>" or message.get("usage") is None:
        return None
    usage, at = message["usage"], _time(event.get("timestamp"))
    request, message_id = _alias("request", event.get("requestId")), _alias("message", message.get("id"))
    if not isinstance(usage, dict) or at is None or not (request or message_id):
        raise ValueError("invalid counter metadata")
    parts = tuple(usage.get(name, 0) for name in COMPONENTS)
    if not all(_count(value) for value in parts) or any(name not in usage for name in COMPONENTS[:2]) or sum(parts) > MAX_TOKENS:
        raise ValueError("invalid counts")
    return (at, request, message_id, *parts)


def _record_type(prefix):
    """Inspect top-level scalar headers only, never decode a large message body."""
    text = prefix[:4096].decode("utf-8", "replace").lstrip()
    if not text.startswith("{"):
        return None
    decoder, position = json.JSONDecoder(), 1
    try:
        for _ in range(32):
            while position < len(text) and text[position].isspace(): position += 1
            key, position = decoder.raw_decode(text, position)
            if not isinstance(key, str): return None
            while position < len(text) and text[position].isspace(): position += 1
            if position >= len(text) or text[position] != ":": return None
            position += 1
            while position < len(text) and text[position].isspace(): position += 1
            if position >= len(text) or text[position] in "{[": return None
            value, position = decoder.raw_decode(text, position)
            if key == "type": return value if isinstance(value, str) else None
            while position < len(text) and text[position].isspace(): position += 1
            if position >= len(text) or text[position] != ",": return None
            position += 1
    except (ValueError, TypeError):
        pass
    return None


def _file_metadata(info):
    # ctime detects same-size edits whose mtime was deliberately preserved.
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class _Index:
    def __init__(self, state, scope):
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = state.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise OSError("unsafe state")
        path = state / ("14 Claude Index " + scope + ".sqlite3")
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_nlink != 1:
                raise OSError("unsafe index")
        finally:
            os.close(descriptor)
        self.db = sqlite3.connect(path, timeout=2)
        try:
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.executescript("""
            CREATE TABLE IF NOT EXISTS files(
                file_key TEXT PRIMARY KEY, device INTEGER, inode INTEGER,
                size INTEGER, mtime INTEGER, ctime INTEGER, offset INTEGER DEFAULT 0,
                skipping INTEGER DEFAULT 0, malformed INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS chunks(
                file_key TEXT REFERENCES files(file_key) ON DELETE CASCADE,
                begin INTEGER, end INTEGER, digest TEXT,
                PRIMARY KEY(file_key,begin));
            CREATE TABLE IF NOT EXISTS counters(
                file_key TEXT REFERENCES files(file_key) ON DELETE CASCADE,
                offset INTEGER, at INTEGER, request_alias TEXT, message_alias TEXT,
                input_tokens INTEGER, output_tokens INTEGER,
                cache_creation_input_tokens INTEGER, cache_read_input_tokens INTEGER,
                PRIMARY KEY(file_key,offset));
            """)
            if "ctime" not in {row[1] for row in self.db.execute("PRAGMA table_info(files)")}:
                self.db.execute("ALTER TABLE files ADD COLUMN ctime INTEGER DEFAULT 0")
        except sqlite3.Error:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def row(self, key):
        return self.db.execute("SELECT device,inode,size,mtime,ctime,offset,skipping,malformed FROM files WHERE file_key=?", (key,)).fetchone()

    def chunk(self, key, begin, data):
        self.db.execute("INSERT OR REPLACE INTO chunks VALUES(?,?,?,?)",
                        (key, begin, begin + len(data), hashlib.sha256(data).hexdigest()))

    def verified_prefix(self, key, stream, offset, coverage):
        position = 0
        for begin, end, expected in self.db.execute("SELECT begin,end,digest FROM chunks WHERE file_key=? ORDER BY begin", (key,)):
            if begin != position or end > offset or end <= begin:
                return False
            digest = hashlib.sha256()
            stream.seek(begin)
            remaining = end - begin
            while remaining:
                data = stream.read(min(1 << 20, remaining))
                coverage["verification_bytes"] += len(data)
                if not data:
                    return False
                digest.update(data)
                remaining -= len(data)
            if digest.hexdigest() != expected:
                return False
            position = end
        return position == offset

    def prepare(self, key, info, stream, coverage):
        metadata = _file_metadata(info)
        row = self.row(key)
        if row and row[:5] != metadata:
            append = row[:2] == metadata[:2] and info.st_size >= row[5] and self.verified_prefix(key, stream, row[5], coverage)
            if not append:
                self.db.execute("DELETE FROM files WHERE file_key=?", (key,))
                row = None
        if row is None:
            self.db.execute("INSERT INTO files(file_key,device,inode,size,mtime,ctime) VALUES(?,?,?,?,?,?)", (key, *metadata))
        else:
            self.db.execute("UPDATE files SET device=?,inode=?,size=?,mtime=?,ctime=? WHERE file_key=?", (*metadata, key))
        return self.row(key)

    def scan_file(self, key, path, info, budget, coverage):
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
            actual = os.fstat(stream.fileno())
            if _file_metadata(actual) != _file_metadata(info):
                raise OSError("source changed during inventory")
            row = self.prepare(key, info, stream, coverage)
            offset, skipping, malformed = row[5:]
            stream.seek(offset)
            while offset < info.st_size and budget > 0:
                begin = offset
                limit = min(info.st_size - offset, max(1, min(1 << 20, budget)) if skipping else MAX_LINE + 1)
                line = stream.readline(limit)
                if not line:
                    break
                budget -= len(line)
                coverage["bytes_read"] += len(line)
                if not skipping and len(line) <= MAX_LINE and not line.endswith(b"\n"):
                    # Do not commit an unfinished record; the next append retries it.
                    break
                if not skipping and len(line) > MAX_LINE:
                    kind = _record_type(line)
                    if kind is None or kind == "assistant":
                        malformed += 1
                    skipping = True
                if not skipping:
                    try:
                        counter = _counter(line)
                        if counter is not None:
                            self.db.execute("INSERT OR REPLACE INTO counters VALUES(?,?,?,?,?,?,?,?,?)", (key, begin, *counter))
                    except (ValueError, UnicodeError, TypeError, RecursionError):
                        malformed += 1
                self.chunk(key, begin, line)
                offset += len(line)
                if skipping and line.endswith(b"\n"):
                    skipping = False
            self.db.execute("UPDATE files SET offset=?,skipping=?,malformed=? WHERE file_key=?",
                            (offset, int(skipping), malformed, key))
            final = os.fstat(stream.fileno())
            if _file_metadata(final) != _file_metadata(info):
                coverage["scan_incomplete"] = True
            coverage["files_scanned"] += 1
        return budget

    def totals(self, start, end, now, coverage):
        parents = {}
        def root(key):
            parents.setdefault(key, key)
            while parents[key] != key:
                parents[key] = parents[parents[key]]
                key = parents[key]
            return key
        for request, message in self.db.execute("SELECT request_alias,message_alias FROM counters"):
            if request and message:
                parents[root(message)] = root(request)
        calls = {}
        for at, request, message, *values in self.db.execute("SELECT at,request_alias,message_alias,input_tokens,output_tokens,cache_creation_input_tokens,cache_read_input_tokens FROM counters"):
            key, parts = root(request or message), tuple(values)
            previous = calls.get(key)
            if previous:
                prior = previous[1]
                if not (all(a >= b for a, b in zip(parts, prior)) or all(a <= b for a, b in zip(parts, prior))):
                    coverage["conflicting_records"] += 1
                    continue
                parts, at = tuple(max(a, b) for a, b in zip(parts, prior)), max(at, previous[0])
            calls[key] = (at, parts)
        coverage["future_records"] = sum(at > now for at, _ in calls.values())
        total = sum(sum(parts) for _, parts in calls.values())
        today = sum(sum(parts) for at, parts in calls.values() if start <= at <= now and at < end)
        return total, today, len(calls)


def _inventory(root, coverage):
    projects, found, complete = root / "projects", {}, True
    try:
        info = projects.lstat()
    except FileNotFoundError:
        return found, complete
    if not stat.S_ISDIR(info.st_mode):
        raise OSError("unsafe projects")
    def failed(_):
        nonlocal complete
        complete = False
        coverage["read_errors"] += 1
    for directory, directories, names in os.walk(projects, followlinks=False, onerror=failed):
        for name in list(directories):
            child = Path(directory) / name
            if child.is_symlink():
                directories.remove(name)
                failed(None)
        for name in names:
            if not name.endswith(".jsonl"):
                continue
            path = Path(directory) / name
            key = hashlib.sha256(("qingjian-claude-file-v1\0" + str(path.relative_to(root))).encode()).hexdigest()
            coverage["files_found"] += 1
            try:
                info = path.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                    raise OSError("unsafe transcript")
                found[key] = (path, info)
            except OSError:
                found[key] = None  # A read failure is not proof this source was deleted.
                coverage["read_errors"] += 1
    return found, complete


def collect_claude_tokens(state_dir, home=None, now_ms=None, *, byte_budget=256 * 1024 * 1024, max_files=MAX_FILES):
    now = time.time_ns() // 1_000_000 if now_ms is None else now_ms
    if not _count(now) or not 1704067200000 <= now < 4102444800000:
        return empty("invalid_clock")
    if type(byte_budget) is not int or byte_budget < 0 or type(max_files) is not int or max_files < 0:
        return empty("invalid_budget")
    try:
        root, state = config_directory(home), Path(state_dir).expanduser().absolute()
    except (OSError, ValueError, TypeError, RuntimeError):
        return empty("invalid_profile")
    scope = profile_scope(root)
    cache_path = state / ("14 Claude Tokens " + scope + ".json")
    old = _read_cache(cache_path, scope, now)
    coverage = {"files_found": 0, "files_scanned": 0, "read_errors": 0, "malformed_records": 0,
                "bytes_read": 0, "verification_bytes": 0, "indexed_bytes": 0, "pending_files": 0,
                "conflicting_records": 0, "future_records": 0, "scan_incomplete": False}
    local = dt.datetime.fromtimestamp(now / 1000).astimezone()
    start = int(dt.datetime.combine(local.date(), dt.time()).astimezone().timestamp() * 1000)
    end = int(dt.datetime.combine(local.date() + dt.timedelta(days=1), dt.time()).astimezone().timestamp() * 1000)
    index, total, today, calls = None, 0, 0, 0
    try:
        index = _Index(state, scope)
        inventory, discovered = _inventory(root, coverage)
        if discovered:
            for (key,) in index.db.execute("SELECT file_key FROM files").fetchall():
                if key not in inventory:
                    index.db.execute("DELETE FROM files WHERE file_key=?", (key,))
        work = []
        for key, source in inventory.items():
            if source is None:
                continue
            path, info = source
            row = index.row(key)
            metadata = _file_metadata(info)
            if row is None:
                index.db.execute("INSERT INTO files(file_key,device,inode,size,mtime,ctime) VALUES(?,?,?,?,?,?)", (key, *metadata))
                row = index.row(key)
            if row[:5] != metadata or row[5] < info.st_size or row[6]:
                work.append((key, path, info))
        # max_files limits work per refresh, not the lifetime inventory. Already
        # completed sources do not prevent omitted sources from making progress.
        work.sort(key=lambda item: item[2].st_mtime_ns, reverse=True)
        for key, path, info in work[:max_files]:
            if byte_budget <= 0:
                break
            try:
                byte_budget = index.scan_file(key, path, info, byte_budget, coverage)
            except OSError:
                coverage["read_errors"] += 1
        index.db.commit()
        coverage["malformed_records"] = index.db.execute("SELECT COALESCE(SUM(malformed),0) FROM files").fetchone()[0]
        coverage["indexed_bytes"] = index.db.execute("SELECT COALESCE(SUM(offset),0) FROM files").fetchone()[0]
        pending = 0
        for key, source in inventory.items():
            if source is None:
                continue
            _, info = source
            row = index.row(key)
            if row is None or row[:5] != _file_metadata(info) or row[5] < info.st_size or row[6]:
                pending += 1
        coverage["pending_files"] = pending
        total, today, calls = index.totals(start, end, now, coverage)
    except (OSError, sqlite3.Error):
        coverage["read_errors"] += 1
    finally:
        if index is not None:
            index.close()
    coverage["scan_incomplete"] |= bool(coverage["pending_files"] or coverage["read_errors"] or
                                      coverage["malformed_records"] or coverage["conflicting_records"] or
                                      coverage["future_records"] or total > MAX_TOKENS)
    if coverage["scan_incomplete"] or not calls:
        error = "scan_incomplete" if coverage["scan_incomplete"] else "no_records"
        if old:
            return {**old, "available": False, "status": "stale", "error_code": error, "coverage": coverage}
        return {**empty(error), "profile_scope": scope, "coverage": coverage}
    value = {**empty(), "profile_scope": scope, "available": True, "complete": True, "status": "fresh",
             "today_tokens": today, "lifetime_tokens": total, "observed_at_ms": now,
             "expires_at_ms": min(now + TTL_MS, end), "day_start_ms": start, "day_end_ms": end,
             "call_count": calls, "coverage": coverage}
    if not _write_cache(cache_path, value):
        value["error_code"] = "cache_unwritable"
    return value
