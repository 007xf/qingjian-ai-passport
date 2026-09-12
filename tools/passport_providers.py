"""Provider metadata collectors for Qingjian; source applications remain read-only.

No credentials, prompts, responses, process probes, or personal quota estimates.
Gemini JSON is streamed with a selector: unselected strings are never retained.
Cursor counts distinct code-tracking request IDs, not tokens or all API requests.
Codex accounting is supplied by the existing Bridge counter, never re-estimated.
Only the minimal Codex activity index is written, inside Qingjian's own state dir.
"""
from __future__ import annotations

import datetime as dt
import codecs
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat as stat_module
import threading
import time

MAX_INTEGER = 2**53 - 1
STATE_TTL_MS = 180_000
PROVIDERS = ("codex", "cursor", "gemini")
STATES = {"unknown", "working", "idle", "waiting", "error"}
_UPDATE_LOCK = threading.Lock()
_LAST_UPDATE = 0


def _uint(value):
    return type(value) is int and 0 <= value <= MAX_INTEGER


def _model(value):
    return value if isinstance(value, str) and len(value) <= 32 and \
        all(32 <= ord(char) <= 126 for char in value) else ""


def _milliseconds(value):
    if isinstance(value, str):
        try:
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return None
            value = parsed.timestamp()
        except (ValueError, OverflowError):
            return None
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        return None
    result = int(value * 1000)
    return result if _uint(result) else None


def _update_id():
    global _LAST_UPDATE
    with _UPDATE_LOCK:
        _LAST_UPDATE = max(time.time_ns() // 1000, _LAST_UPDATE + 1)
        if not _uint(_LAST_UPDATE):
            raise ValueError("Provider update clock exceeds protocol range")
        return _LAST_UPDATE


def _metric(kind, source="none", status="unavailable", value=None, at=0, model=""):
    return {"metric_kind": kind, "metric_value": value, "metric_status": status,
            "metric_at_ms": at, "last_activity_ms": at, "model": _model(model), "source": source}


class _MetadataJSON:
    """Bounded streaming JSON selector; excludes message contents while parsing."""
    _SPECIAL = re.compile(r'["\\\x00-\x1f]')
    _NUMBER = re.compile(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\Z')

    def __init__(self, stream, limit):
        self.stream, self.limit = stream, limit
        self.buffer, self.position, self.read_count = "", 0, 0
        self.items = 0

    def _fill(self):
        if self.position < len(self.buffer):
            return True
        self.buffer = self.stream.read(16384)
        self.position = 0
        self.read_count += len(self.buffer)
        if self.read_count > self.limit:
            raise ValueError("JSON source exceeds scan budget")
        return bool(self.buffer)

    def _peek(self):
        return self.buffer[self.position] if self._fill() else ""

    def _take(self):
        value = self._peek()
        self.position += bool(value)
        return value

    def _space(self):
        while self._peek() and self._peek() in " \t\r\n":
            self.position += 1

    def _string(self, keep):
        if self._take() != '"':
            raise ValueError("Expected JSON string")
        pieces, size = [], 0
        while self._fill():
            match = self._SPECIAL.search(self.buffer, self.position)
            end = match.start() if match else len(self.buffer)
            if keep:
                part = self.buffer[self.position:end]
                size += len(part)
                if size > 4096:
                    raise ValueError("Metadata string exceeds bound")
                pieces.append(part)
            self.position = end
            if not match:
                continue
            char = self._take()
            if char == '"':
                return json.loads('"' + "".join(pieces) + '"') if keep else None
            if char != "\\":
                raise ValueError("Unescaped JSON control character")
            escaped = self._take()
            if not escaped or escaped not in '"\\/bfnrtu':
                raise ValueError("Invalid JSON escape")
            suffix = ""
            if escaped == "u":
                suffix = "".join(self._take() for _ in range(4))
                if len(suffix) != 4 or any(c not in "0123456789abcdefABCDEF" for c in suffix):
                    raise ValueError("Invalid JSON unicode escape")
            if keep:
                pieces.append("\\" + escaped + suffix)
                size += len(suffix) + 2
        raise ValueError("Unterminated JSON string")

    def value(self, selector=False, depth=0):
        if depth > 64:
            raise ValueError("JSON nesting exceeds bound")
        self._space()
        char = self._peek()
        if char == '"':
            return self._string(selector is True)
        if char in ("{", "["):
            self._take()
            object_value = char == "{"
            close = "}" if object_value else "]"
            result = {} if object_value else []
            self._space()
            if self._peek() == close:
                self._take()
                return result if selector else None
            while True:
                self._space()
                key = self._string(True) if object_value else "*"
                self._space()
                if object_value and self._take() != ":":
                    raise ValueError("Expected JSON colon")
                selected = selector.get(key, False) if isinstance(selector, dict) else False
                value = self.value(selected, depth + 1)
                if selected:
                    self.items += 1
                    if self.items > 200_000 or (object_value and key in result):
                        raise ValueError("Duplicate or excessive metadata")
                    if object_value:
                        result[key] = value
                    else:
                        result.append(value)
                self._space()
                separator = self._take()
                if separator == close:
                    return result if selector else None
                if separator != ",":
                    raise ValueError("Expected JSON separator")
        token = []
        while self._peek() and self._peek() not in " \t\r\n,]}":
            token.append(self._take())
            if len(token) > 128:
                raise ValueError("JSON scalar exceeds bound")
        token = "".join(token)
        if token not in ("true", "false", "null") and not self._NUMBER.fullmatch(token):
            raise ValueError("Invalid JSON scalar")
        value = json.loads(token)
        return value if selector is True else None

    def document(self, selector):
        value = self.value(selector)
        self._space()
        if self._peek():
            raise ValueError("Trailing JSON content")
        return value

    def byte_offset(self):
        """Position consumed, excluding prefetched UTF-8 bytes in an index window."""
        pending = len(self.stream.decoder.getstate()[0])
        unread = len(self.buffer[self.position:].encode("utf-8"))
        return self.stream.binary.tell() - pending - unread


_GEMINI_FIELDS = {"sessionId": True, "startTime": True, "lastUpdated": True,
    "messages": {"*": {"id": True, "type": True, "timestamp": True,
                       "model": True, "tokens": {"total": True}}}}


def _cursor_metric(path, start, now, query_seconds=2):
    result = _metric("requests")
    if not path.is_file() or path.is_symlink():
        return result
    connection = None
    try:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.2)
        end = time.monotonic() + query_seconds
        connection.set_progress_handler(lambda: int(time.monotonic() > end), 1000)
        connection.execute("PRAGMA query_only=ON")
        with connection:
            row = connection.execute("""SELECT
                COUNT(DISTINCT CASE WHEN timestamp>=? AND timestamp<=? THEN requestId END),
                MAX(CASE WHEN typeof(timestamp)='integer' AND timestamp<=? THEN timestamp END),
                SUM(CASE WHEN typeof(timestamp)!='integer' OR timestamp<0 OR timestamp>?
                    OR (timestamp>=? AND (typeof(requestId)!='text' OR length(requestId)=0))
                    THEN 1 ELSE 0 END)
                FROM ai_code_hashes""", (start or 0, now, now, now, start or 0)).fetchone()
            latest = connection.execute("""SELECT model FROM ai_code_hashes
                WHERE typeof(timestamp)='integer' AND timestamp>=0 AND timestamp<=?
                ORDER BY timestamp DESC LIMIT 1""", (now,)).fetchone()
        at = row[1] if _uint(row[1]) else 0
        partial = start is None or bool(row[2]) or not _uint(row[0])
        status = "partial" if partial else "ready" if at else "no_records"
        return _metric("requests", "cursor_code_tracking", status,
                       row[0] if status == "ready" else None, at, latest[0] if latest else "")
    except (OSError, ValueError, sqlite3.Error):
        return result
    finally:
        if connection:
            connection.close()


def _gemini_metric(root, start, now, max_files=2000, byte_budget=64 * 1024 * 1024):
    result = _metric("tokens")
    tmp = root / "tmp"
    if not tmp.is_dir() or tmp.is_symlink():
        return result
    partial, inaccessible, found_model = start is None, False, False
    total, latest_at, latest_model, files_seen, last_activity, model_at = 0, 0, "", 0, 0, 0
    messages = {}
    try:
        # Path.glob suppresses directory PermissionError on recent Python;
        # explicit scandir distinguishes inaccessible data from no records.
        paths = []
        inventory = 0
        with os.scandir(tmp) as projects:
            for project in projects:
                inventory += 1
                if inventory > 20000 or len(paths) > max_files:
                    partial = True
                    break
                if project.is_symlink():
                    partial = True
                    continue
                if not project.is_dir(follow_symlinks=False):
                    continue
                try:
                    with os.scandir(Path(project.path) / "chats") as files:
                        for file in files:
                            inventory += 1
                            if inventory > 20000 or len(paths) > max_files:
                                partial = True
                                break
                            if file.name.startswith("session-") and file.name.endswith(".json"):
                                paths.append(Path(file.path))
                except FileNotFoundError:
                    continue
                except OSError:
                    partial, inaccessible = True, True
        if len(paths) > max_files:
            partial = True
        for path in paths[:max_files]:
            if path.is_symlink() or any(p.is_symlink() for p in (path.parent, path.parent.parent)):
                partial = True
                continue
            try:
                size = path.stat().st_size
                if size > byte_budget:
                    partial = True
                    continue
                byte_budget -= size
                before = path.stat()
                with path.open(encoding="utf-8") as stream:
                    metadata = _MetadataJSON(stream, size + 1).document(_GEMINI_FIELDS)
                after = path.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    partial = True
                files_seen += 1
                if not isinstance(metadata, dict) or not isinstance(metadata.get("messages"), list):
                    raise ValueError("Missing session metadata")
                sid = metadata.get("sessionId")
                for message in metadata["messages"]:
                    if not isinstance(message, dict):
                        partial = True
                        continue
                    at = _milliseconds(message.get("timestamp"))
                    if at is not None and at <= now:
                        last_activity = max(last_activity, at)
                        model = _model(message.get("model"))
                        if model and at > model_at:
                            model_at, latest_model = at, model
                    if message.get("type") != "gemini":
                        continue
                    found_model = True
                    if at is None or at > now:
                        partial = True
                        continue
                    latest_at = max(latest_at, at)
                    if start is not None and at < start:
                        continue
                    mid = message.get("id")
                    tokens = message.get("tokens")
                    count = tokens.get("total") if isinstance(tokens, dict) else None
                    if not isinstance(sid, str) or not sid or not isinstance(mid, str) or not mid or not _uint(count):
                        partial = True
                        continue
                    fingerprint = (count, at, _model(message.get("model")))
                    identity = (sid, mid)
                    if identity in messages:
                        if messages[identity] != fingerprint:
                            partial = True
                        continue
                    messages[identity] = fingerprint
                    total += count
                    if total > MAX_INTEGER:
                        partial = True
            except OSError:
                partial, inaccessible = True, True
            except (ValueError, UnicodeError, RecursionError):
                partial = True
    except OSError:
        return result
    status = "partial" if partial else "ready" if found_model else "no_records"
    if inaccessible and not files_seen:
        status = "unavailable"
    result = _metric("tokens", "gemini_cli", status, total if status == "ready" else None,
                     latest_at, latest_model)
    result["last_activity_ms"] = last_activity
    return result


def _codex_metric(usage, state_dir, start, now):
    if not isinstance(usage, dict):
        return _metric("tokens")
    count = usage.get("cycle_tokens")
    coverage = usage.get("coverage", {})
    incomplete = coverage.get("cycle_scan_incomplete", False) if isinstance(coverage, dict) else True
    supplied_start = _milliseconds(usage.get("cycle_started_at"))
    partial = bool(incomplete or (supplied_start is not None and supplied_start != start))
    at = _milliseconds(usage.get("last_token_event_at")) or 0
    path = state_dir / "01 usage.sqlite3"
    # New Bridge responses include the selected profile's authoritative source
    # time, including None for an empty profile. Never replace it with a legacy
    # default-profile database timestamp. Keep fallback for older Bridge data.
    if "last_token_event_at" not in usage and path.is_file() and not path.is_symlink():
        connection = None
        try:
            connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.2)
            row = connection.execute("SELECT MAX(at) FROM events WHERE inherited=0").fetchone()
            at = _milliseconds(row[0]) or at
        except (OSError, ValueError, sqlite3.Error):
            pass  # Timing metadata cannot invalidate the authoritative supplied count.
        finally:
            if connection:
                connection.close()
    if at > now:
        at, partial = 0, True
    status = "partial" if partial else "ready" if _uint(count) and start is not None else "unavailable"
    if status == "ready" and not at:
        status = "no_records" if count == 0 else "partial"
    return _metric("tokens", "codex_local", status, count if status == "ready" else None, at)


def _activity(state_dir, provider, now):
    result = {"state": "unknown", "state_at_ms": 0, "state_until_ms": 0,
              "active_sessions": None, "last_activity_ms": 0, "model": "", "present": False}
    root = state_dir / "07 activity"
    if not root.is_dir() or root.is_symlink():
        return result
    records, malformed = {}, False
    try:
        paths = list(itertools.islice(root.glob(provider + "-*.json"), 513))
        malformed = len(paths) > 512
        for path in paths[:512]:
            result["present"] = True
            try:
                if path.is_symlink() or path.stat().st_size > 4096:
                    raise ValueError("Unsafe hook record")
                with path.open(encoding="utf-8") as stream:
                    raw = stream.read(4097)
                if len(raw) > 4096:
                    raise ValueError("Hook record changed beyond bound")
                obj = json.loads(raw)
                sid = obj.get("session_id_hash")
                at, until = obj.get("state_at_ms"), obj.get("state_until_ms")
                state = obj.get("state")
                if obj.get("provider") != provider or not isinstance(sid, str) or \
                        not re.fullmatch(r"[0-9a-f]{64}", sid) or state not in STATES or \
                        not _uint(at) or not _uint(until) or at > now or not at < until <= at + STATE_TTL_MS:
                    raise ValueError("Invalid hook metadata")
                group = obj.get("session_group_hash", sid)
                if not isinstance(group, str) or not re.fullmatch(r"[0-9a-f]{64}", group):
                    raise ValueError("Invalid group identity")
                record = (at, until, state, group, _model(obj.get("model")))
                if sid not in records or records[sid][0] < at:
                    records[sid] = record
            except (OSError, ValueError, UnicodeError, AttributeError):
                malformed = True
    except OSError:
        return result
    return _summarize_activity(records, now, malformed, result["present"])


def _summarize_activity(records, now, malformed=False, present=True):
    result = {"state": "unknown", "state_at_ms": 0, "state_until_ms": 0,
              "active_sessions": None, "last_activity_ms": 0, "model": "", "present": present}
    if records:
        newest = max(records.values())
        result.update(last_activity_ms=newest[0], model=newest[4],
                      state_at_ms=newest[0], state_until_ms=newest[1])
    fresh = [r for r in records.values() if now < r[1]]
    stale_active = any(now >= r[1] and r[2] in ("working", "waiting", "unknown") for r in records.values())
    uncertain_count = malformed or stale_active or any(r[2] == "unknown" for r in fresh)
    priority = {"idle": 0, "working": 1, "error": 2, "waiting": 3}
    positive = [r for r in fresh if r[2] in ("working", "waiting", "error")]
    if positive:
        state = max(positive, key=lambda r: priority[r[2]])[2]
        # One independently verified positive session proves this state even
        # when another session's history is missing. Its count remains unknown.
        evidence = max((r for r in positive if r[2] == state), key=lambda r: (r[1], r[0]))
    elif fresh and not uncertain_count:
        state = "idle"
        evidence = max(fresh, key=lambda r: (r[1], r[0]))
    else:
        return result
    result.update(state=state, state_at_ms=evidence[0], state_until_ms=evidence[1],
                  active_sessions=None if uncertain_count else
                  len({r[3] for r in fresh if r[2] in ("working", "waiting")}))
    return result


class _TextWindow:
    """Decode a bounded byte window without loading its conversation text."""
    def __init__(self, binary, size):
        self.binary, self.remaining = binary, size
        self.decoder = codecs.getincrementaldecoder("utf-8")("strict")

    def read(self, size):
        raw = self.binary.read(min(size, self.remaining))
        self.remaining -= len(raw)
        return self.decoder.decode(raw, final=self.remaining == 0 or not raw)


_CODEX_FIELDS = {"type": True, "timestamp": True, "payload": {
    "type": True, "id": True, "timestamp": True, "turn_id": True}}


def _codex_activity(root, now, *, max_files=16, tail_bytes=512 * 1024,
                     byte_budget=8 * 1024 * 1024, inventory_limit=20000):
    """Only an observed start can authorize a token-count task heartbeat.

    Look at recently modified files (10 minutes), bounded inventory and tails.
    Missing start, ambiguous heartbeat, truncation, corruption or exhausted
    coverage yields unknown. Raw conversations and raw identifiers are discarded.
    """
    empty = _summarize_activity({}, now, present=False)
    if root is None:
        return empty
    candidates, partial, inventory = [], False, 0
    try:
        for base in (root / "sessions", root / "archived_sessions"):
            if not base.exists():
                continue
            if base.is_symlink():
                partial = True
                continue
            for path in itertools.islice(base.rglob("*.jsonl"), inventory_limit + 1):
                inventory += 1
                if inventory > inventory_limit:
                    partial = True
                    break
                stat = path.stat()
                if stat.st_mtime * 1000 >= now - 600000:
                    candidates.append((stat.st_mtime_ns, path, stat.st_size))
            if partial:
                break
    except OSError:
        partial = True
    if len(candidates) > max_files:
        partial = True
    events, seen = [], set()
    for _, path, size in sorted(candidates, key=lambda item: item[0], reverse=True)[:max_files]:
        try:
            if path.is_symlink() or byte_budget <= 0:
                partial = True
                continue
            with path.open("rb") as binary:
                # Real session_meta includes large base-instruction strings.
                # Select identity without retaining that text; header reads are
                # separately capped at 64 KiB and charged to the total budget.
                header_limit = min(size, 65536, byte_budget)
                header_reader = _MetadataJSON(_TextWindow(binary, header_limit), header_limit + 1)
                metadata = header_reader.value(_CODEX_FIELDS)
                byte_budget -= binary.tell()
                payload = metadata.get("payload", {})
                sid = payload.get("id")
                created = _milliseconds(payload.get("timestamp"))
                if metadata.get("type") != "session_meta" or not isinstance(sid, str) or not 1 <= len(sid) <= 100 \
                        or created is None or created > now:
                    raise ValueError("Invalid session identity")
                sid = hashlib.sha256(sid.encode()).hexdigest()
                if byte_budget <= 0:
                    partial = True
                    continue
                length = min(size, tail_bytes, byte_budget)
                begin = size - length
                binary.seek(max(0, begin - 1))
                aligned = not begin or binary.read(1) == b"\n"
                binary.seek(begin)
                if begin and not aligned:
                    # Discard the initial partial JSONL line inside the window.
                    while binary.tell() < size:
                        chunk = binary.readline(min(16384, size - binary.tell()))
                        if not chunk or chunk.endswith(b"\n"):
                            break
                available = size - binary.tell()
                byte_budget -= length
                reader = _MetadataJSON(_TextWindow(binary, available), available + 1)
                while True:
                    reader._space()
                    if not reader._peek():
                        break
                    event = reader.value(_CODEX_FIELDS)
                    if not isinstance(event, dict) or event.get("type") != "event_msg":
                        continue
                    payload = event.get("payload", {})
                    kind = payload.get("type") if isinstance(payload, dict) else None
                    if kind not in ("task_started", "task_complete", "turn_aborted", "token_count"):
                        continue
                    at = _milliseconds(event.get("timestamp"))
                    if at is None or at > now:
                        partial = True
                        continue
                    if at < created:
                        continue  # Inherited fork history is not this session.
                    turn = payload.get("turn_id")
                    if kind != "token_count" and (not isinstance(turn, str) or not 1 <= len(turn) <= 100):
                        partial = True
                        continue
                    turn = hashlib.sha256(turn.encode()).hexdigest() if isinstance(turn, str) and turn else ""
                    fingerprint = (sid, turn, kind, at)
                    if fingerprint not in seen:
                        seen.add(fingerprint)
                        events.append(fingerprint)
                binary.seek(max(0, size - 1))
                if size and binary.read(1) != b"\n":
                    partial = True
        except (OSError, ValueError, UnicodeError, AttributeError):
            partial = True
    tasks, latest_activity, recognized = {}, 0, set()
    priority = {"task_started": 0, "token_count": 1, "task_complete": 2, "turn_aborted": 2}
    for sid, turn, kind, at in sorted(events, key=lambda event: (event[3], priority[event[2]])):
        latest_activity = max(latest_activity, at)
        key = (sid, turn)
        if kind == "task_started":
            if key not in tasks:
                tasks[key] = {"at": at, "state": "working"}
                recognized.add(sid)
        elif kind == "token_count":
            active = [k for k, task in tasks.items() if k[0] == sid and task["state"] == "working"]
            target = key if turn and key in active else active[0] if not turn and len(active) == 1 else None
            if target:
                tasks[target]["at"] = at
            elif (turn and key not in tasks) or sid not in recognized or active:
                partial = True
        elif key in tasks:
            tasks[key].update(at=at, state="idle")
        else:
            partial = True  # An orphan completion is not a complete task history.
    records = {"/".join(key): (task["at"], task["at"] + STATE_TTL_MS, task["state"], key[0], "")
               for key, task in tasks.items()}
    # The diagnostic tail reader lacks per-file provenance for partial scans.
    # Only the indexed path below can safely preserve independent positive data.
    result = _summarize_activity({} if partial else records, now, partial, bool(candidates))
    result["last_activity_ms"] = max(result["last_activity_ms"], latest_activity)
    result["source"] = "codex_local"
    return result


def _activity_index_connection(state_dir):
    """Own minimal index; no raw source paths, text, identifiers or token counts."""
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = state_dir / "09 codex activity.sqlite3"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.close(fd)
    except FileExistsError:
        info = path.lstat()
        if not stat_module.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise ValueError("Unsafe activity index")
    db = sqlite3.connect(path, timeout=0.2)
    try:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            if db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchone():
                raise ValueError("Unrecognized activity index")
            db.executescript("""
                CREATE TABLE files(path_hash TEXT PRIMARY KEY, device INTEGER, inode INTEGER,
                    size INTEGER, mtime INTEGER, offset INTEGER, sid TEXT, created INTEGER,
                    history_gap INTEGER NOT NULL DEFAULT 0, anchor TEXT);
                CREATE TABLE tasks(path_hash TEXT, turn_hash TEXT, started INTEGER,
                    activity INTEGER, ended INTEGER, end_kind TEXT, superseded INTEGER,
                    PRIMARY KEY(path_hash,turn_hash));
                PRAGMA user_version=2;
            """)
        elif version == 1:
            # Rebuild activity metadata only: v1 treated omitted replacement-end
            # events as simultaneous tasks. No token counter index is touched.
            db.executescript("""
                ALTER TABLE tasks ADD COLUMN superseded INTEGER;
                DELETE FROM tasks;
                UPDATE files SET offset=0,sid=NULL,created=NULL,history_gap=0,anchor=NULL;
                PRAGMA user_version=2;
            """)
        elif version != 2:
            raise ValueError("Unsupported activity index version")
        return db
    except Exception:
        db.close()
        raise


def _index_event(db, path_hash, sid, created, gap, event, now):
    """Apply one complete, selected metadata event inside the index transaction."""
    if not isinstance(event, dict):
        return sid, created, True
    payload = event.get("payload")
    if event.get("type") == "session_meta" and sid is None:
        raw_sid = payload.get("id") if isinstance(payload, dict) else None
        created = _milliseconds(payload.get("timestamp")) if isinstance(payload, dict) else None
        if not isinstance(raw_sid, str) or not 1 <= len(raw_sid) <= 100 or created is None or created > now:
            return None, None, True
        return hashlib.sha256(raw_sid.encode()).hexdigest(), created, gap
    if event.get("type") != "event_msg" or not isinstance(payload, dict):
        return sid, created, gap
    kind = payload.get("type")
    if kind not in ("task_started", "task_complete", "turn_aborted", "token_count"):
        return sid, created, gap
    at = _milliseconds(event.get("timestamp"))
    if sid is None or created is None or at is None or at > now:
        return sid, created, True
    if at < created:
        return sid, created, gap
    raw_turn = payload.get("turn_id")
    turn = hashlib.sha256(raw_turn.encode()).hexdigest() if isinstance(raw_turn, str) and 1 <= len(raw_turn) <= 100 else None
    if raw_turn is not None and turn is None:
        return sid, created, True
    if kind != "token_count" and turn is None:
        return sid, created, True
    row = db.execute("SELECT started,activity,ended,end_kind,superseded FROM tasks WHERE path_hash=? AND turn_hash=?",
                     (path_hash, turn)).fetchone() if turn else None
    if kind == "task_started":
        if row is None:
            newest = db.execute("SELECT MAX(started) FROM tasks WHERE path_hash=?", (path_hash,)).fetchone()[0]
            if newest is not None and at < newest:
                return sid, created, True
            # Core Session::spawn_task aborts/replaces its previous task before
            # start_task (which asserts no active task). Steer does not emit a
            # new task_started. A replacement is not an observed completion.
            # https://github.com/openai/codex/blob/main/codex-rs/core/src/tasks/mod.rs
            db.execute("UPDATE tasks SET superseded=? WHERE path_hash=? AND ended IS NULL AND superseded IS NULL",
                       (at, path_hash))
            db.execute("INSERT INTO tasks VALUES(?,?,?,?,NULL,NULL,NULL)", (path_hash, turn, at, at))
            gap = False  # Explicit new start establishes this session's current task.
        elif row[2] is not None and at > row[2]:
            gap = True  # A completed task identity cannot silently start again.
    elif kind == "token_count":
        if row and (row[2] is not None or row[4] is not None):
            return sid, created, gap  # Explicit final accounting cannot revive a finished task.
        active = db.execute("SELECT turn_hash,activity FROM tasks WHERE path_hash=? AND ended IS NULL AND superseded IS NULL", (path_hash,)).fetchall()
        target = turn if row and row[2] is None else active[0][0] if turn is None and len(active) == 1 else None
        if target:
            db.execute("UPDATE tasks SET activity=MAX(activity,?) WHERE path_hash=? AND turn_hash=?", (at, path_hash, target))
        elif (turn and row is None) or active or not db.execute("SELECT 1 FROM tasks WHERE path_hash=? LIMIT 1", (path_hash,)).fetchone():
            gap = True
    elif row is None:
        gap = True
    elif at < row[0]:
        gap = True
    else:
        db.execute("UPDATE tasks SET activity=MAX(activity,?),ended=MAX(COALESCE(ended,0),?),end_kind=? WHERE path_hash=? AND turn_hash=?",
                   (at, at, kind, path_hash, turn))
    return sid, created, gap


def _codex_profile_state(state_dir, root):
    """Same normalized profile namespace as Bridge counters; no shared imports."""
    fingerprint = hashlib.sha256(("qingjian-codex-profile-v1\0" + str(Path(root).expanduser().resolve())).encode()).hexdigest()
    return Path(state_dir) / "11 profiles" / fingerprint


def _codex_index_activity(root, state_dir, now, *, byte_budget=128 * 1024 * 1024,
                          max_files=16, inventory_limit=20000):
    """Incremental task-only activity index; partial coverage always stays unknown.

    Sources are read only. A bounded first scan starts at byte zero so long tasks
    retain their observed start across subsequent scans. Only complete JSONL
    record offsets and hashed task identities persist in Qingjian's own database.
    """
    empty = _summarize_activity({}, now, present=False)
    if root is None:
        return empty
    root = Path(root).expanduser().resolve()
    candidates, inventory, partial, reset_seen = [], 0, False, False
    try:
        for base in (root / "sessions", root / "archived_sessions"):
            if not base.exists():
                continue
            if base.is_symlink():
                partial = True
                continue
            for path in itertools.islice(base.rglob("*.jsonl"), inventory_limit + 1):
                inventory += 1
                if inventory > inventory_limit:
                    partial = True
                    break
                info = path.stat()
                if info.st_mtime * 1000 >= now - 600000:
                    candidates.append((info.st_mtime_ns, path, info))
            if inventory > inventory_limit:
                break
    except OSError:
        partial = True
    if not candidates:
        return empty
    if len(candidates) > max_files:
        partial = True
    db, selected, unverified = None, [], set()
    try:
        db = _activity_index_connection(_codex_profile_state(state_dir, root))
        with db:
            for _, path, info in sorted(candidates, key=lambda item: item[0], reverse=True)[:max_files]:
                path_hash = hashlib.sha256(str(path.absolute()).encode()).hexdigest()
                selected.append(path_hash)
                row = db.execute("SELECT device,inode,size,mtime,offset,sid,created,history_gap,anchor FROM files WHERE path_hash=?", (path_hash,)).fetchone()
                offset, sid, created, gap = (row[4], row[5], row[6], bool(row[7])) if row else (0, None, None, False)
                try:
                    if path.is_symlink():
                        raise ValueError("Symlink source")
                    if byte_budget <= 512:
                        partial = True
                        unverified.add(path_hash)
                        continue
                    with path.open("rb") as binary:
                        reset = bool(row and ((row[0], row[1]) != (info.st_dev, info.st_ino)
                            or info.st_size < row[4] or (info.st_size == row[2] and info.st_mtime_ns != row[3])))
                        if row and offset and not reset:
                            length = min(256, offset)
                            if byte_budget < length:
                                partial = True
                                unverified.add(path_hash)
                                continue
                            binary.seek(offset - length)
                            anchor = binary.read(length)
                            byte_budget -= len(anchor)
                            reset = hashlib.sha256(anchor).hexdigest() != row[8]
                        if reset:
                            db.execute("DELETE FROM tasks WHERE path_hash=?", (path_hash,))
                            offset, sid, created, gap = 0, None, None, False
                            reset_seen = True
                            unverified.add(path_hash)
                        if info.st_size > offset:
                            allowance = min(info.st_size - offset, max(0, byte_budget - 256))
                            if allowance <= 0:
                                partial = True
                                unverified.add(path_hash)
                                continue
                            binary.seek(offset)
                            window = _TextWindow(binary, allowance)
                            reader = _MetadataJSON(window, allowance + 1)
                            try:
                                while True:
                                    reader._space()
                                    if not reader._peek():
                                        break
                                    event = reader.value(_CODEX_FIELDS)
                                    while reader._peek() in (" ", "\t", "\r"):
                                        reader._take()
                                    if reader._take() != "\n":
                                        raise ValueError("Incomplete JSONL record")
                                    payload = event.get("payload", {}) if isinstance(event, dict) else {}
                                    event_time = None
                                    if isinstance(event, dict) and isinstance(payload, dict):
                                        if event.get("type") == "session_meta" and sid is None:
                                            event_time = _milliseconds(payload.get("timestamp"))
                                        elif event.get("type") == "event_msg" and payload.get("type") in ("task_started", "task_complete", "turn_aborted", "token_count"):
                                            event_time = _milliseconds(event.get("timestamp"))
                                    if event_time is not None and event_time > now:
                                        # A writer may append after this scan's clock snapshot.
                                        # Retry that event next time instead of poisoning the index.
                                        raise ValueError("Event is newer than observation snapshot")
                                    sid, created, gap = _index_event(db, path_hash, sid, created, gap, event, now)
                                    offset = reader.byte_offset()
                            except (ValueError, UnicodeError, AttributeError, RecursionError):
                                partial = True
                                unverified.add(path_hash)
                            byte_budget -= allowance - window.remaining
                        if offset < info.st_size or sid is None or gap:
                            partial = True
                        length = min(256, offset)
                        # This checkpoint detects in-place truncation/regrowth,
                        # without retaining any source text in the index.
                        binary.seek(offset - length)
                        raw_anchor = binary.read(length)
                        byte_budget -= len(raw_anchor)
                        anchor = hashlib.sha256(raw_anchor).hexdigest()
                        db.execute("INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?,?,?,?,?)",
                                   (path_hash, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
                                    offset, sid, created, int(gap), anchor))
                except (OSError, ValueError, sqlite3.Error):
                    partial = True
                    unverified.add(path_hash)
            records = {}
            for path_hash in selected:
                if path_hash in unverified:
                    continue
                file_row = db.execute("SELECT sid,history_gap,offset,size FROM files WHERE path_hash=?", (path_hash,)).fetchone()
                if file_row is None or not file_row[0] or file_row[1] or file_row[2] < file_row[3]:
                    partial = True
                    continue
                for turn, at, ended in db.execute("SELECT turn_hash,activity,ended FROM tasks WHERE path_hash=? AND superseded IS NULL", (path_hash,)):
                    key = (file_row[0], turn)
                    record = (at, at + STATE_TTL_MS, "idle" if ended is not None else "working", file_row[0], "")
                    previous = records.get(key)
                    if previous is None or (at, ended is not None) > (previous[0], previous[2] == "idle"):
                        records[key] = record
            result = _summarize_activity(records, now, partial or reset_seen)
            result["source"] = "codex_local"
            return result
    except (OSError, ValueError, sqlite3.Error):
        return {**empty, "present": True, "source": "codex_local"}
    finally:
        if db:
            db.close()


def load_provider_snapshots(cycle_start, state_dir, codex_home=None, *, cursor_db=None,
                            gemini_home=None, codex_usage=None, now_ms=None,
                            max_gemini_files=2000, gemini_byte_budget=64 * 1024 * 1024):
    """Return thirteen-field, credential-free protocol packets for three providers.

    cycle_start uses UTC seconds, matching Bridge accounting. codex_usage is the
    existing read_tokens result. Codex task state uses a bounded, incremental
    metadata index in state_dir; message bodies are discarded, sources read-only.
    """
    now = int(time.time() * 1000) if now_ms is None else now_ms
    if not _uint(now):
        raise ValueError("Invalid provider observation time")
    start = _milliseconds(cycle_start)
    if start is not None and start > now:
        start = None
    state_dir = Path(state_dir).expanduser().resolve()
    codex_home = Path(codex_home).expanduser().absolute() if codex_home is not None else None
    cursor_db = Path(cursor_db).expanduser().absolute() if cursor_db is not None else \
        Path.home() / ".cursor/ai-tracking/ai-code-tracking.db"
    gemini_home = Path(gemini_home).expanduser().absolute() if gemini_home is not None else Path.home() / ".gemini"
    metrics = (_codex_metric(codex_usage, state_dir, start, now),
               _cursor_metric(cursor_db, start, now),
               _gemini_metric(gemini_home, start, now, max_gemini_files, gemini_byte_budget))
    update = _update_id()
    snapshots = []
    for provider, metric in zip(PROVIDERS, metrics):
        activity = _activity(state_dir, provider, now)
        if provider == "codex" and not activity["present"]:
            activity = _codex_index_activity(codex_home, state_dir, now)
        snapshot = {"provider": provider, "update_id": update, **metric,
                    **{k: activity[k] for k in ("state", "state_at_ms", "state_until_ms", "active_sessions")}}
        snapshot["last_activity_ms"] = max(metric["last_activity_ms"], activity["last_activity_ms"])
        if activity["last_activity_ms"] > metric["last_activity_ms"] and activity["model"]:
            snapshot["model"] = activity["model"]
        if metric["source"] == "none" and activity["present"]:
            snapshot["source"] = activity.get("source", "hooks")
        snapshots.append(snapshot)
    return snapshots
