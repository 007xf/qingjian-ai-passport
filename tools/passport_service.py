"""Private, per-user Qingjian sync owner. No TCP listener or credentials exported.

A LaunchAgent owns one persistent USB/BLE session. All editor commands enter a
bounded Unix-socket queue; automatic retries refresh current observations only,
never replay an upload whose confirmation was lost. Display sleep can continue
with an explicitly enabled process-scoped idle-sleep assertion. System sleep,
logout, an unavailable Mac, and iPhone relay remain separate capabilities.
"""
from __future__ import annotations

import argparse
from collections import OrderedDict
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import queue
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from uuid import UUID, uuid4

LABEL = "com.qingjian.passport.sync"
MAX_MESSAGE = 65536
REQUEST_TIMEOUT = 180
SYNC_INTERVAL = 60
STATUS_INTERVAL = 30
SNAPSHOT_STALE_SECONDS = 120
AWAKE_GRACE_SECONDS = 90
OWNER_IDLE_SECONDS = 2
FORWARDED = {"status", "sync", "upload", "screen", "pair", "tokens", "reset-cycle", "cursor-usage"}
DEVICE_ACTIONS = {"status", "sync", "upload", "screen", "pair"}


class ServiceError(Exception):
    pass


def error_kind(exc):
    """Classify structured causes without exporting exception text/payloads.

    A generic timeout's helpful sentence may mention Bluetooth permissions;
    that is not evidence that permission was denied or a prompt is pending.
    Bleak exposes actual denied/powered-off states through its reason enum.
    """
    chain, seen = [], set()
    cause = exc
    for _ in range(8):
        if cause is None or id(cause) in seen:
            break
        seen.add(id(cause))
        chain.append(cause)
        cause = cause.__cause__ or cause.__context__
    names = {type(item).__name__ for item in chain}
    for item in chain:
        if type(item).__name__ != "BleakBluetoothNotAvailableError":
            continue
        reason = getattr(item, "reason", None)
        name = reason if isinstance(reason, str) else getattr(reason, "name", None)
        if name in {"DENIED_BY_USER", "DENIED_BY_SYSTEM", "DENIED_BY_UNKNOWN"}:
            return "permission_denied"
        if name == "POWERED_OFF":
            return "bluetooth_off"
        if name in {"NO_BLUETOOTH", "NO_BLE_CENTRAL_ROLE"}:
            return "bluetooth_unavailable"
    # Reserved for an explicit authorization-preflight result; a timeout alone
    # must never be relabeled as pending authorization.
    if "BLEAuthorizationPending" in names:
        return "permission_pending"
    if "BLERemoteCommandError" in names:
        return "remote_rejected"
    if "BLEConnectionClosed" in names or any(isinstance(item, (ConnectionError, BrokenPipeError)) for item in chain):
        return "disconnected"
    if any(isinstance(item, TimeoutError) for item in chain):
        return "timeout"
    if "BLEProtocolError" in names:
        return "protocol_error"
    if isinstance(exc, ServiceError):
        return "invalid_config"
    return "transport_error"


def bridge_module():
    import passport_bridge
    return passport_bridge


def service_paths(state_dir):
    state = Path(state_dir).expanduser().absolute()
    # The native editor already passes Bridge. The legacy CLI default points
    # at AI Passport and is normalized only for these new service commands.
    if state.name == "AI Passport":
        state = state / "Bridge"
    folder = state / "Service"
    return {"state": state, "folder": folder, "socket": folder / "control.sock",
            "lock": folder / "owner.lock", "settings": folder / "settings.json",
            "snapshot": folder / "snapshot.json", "connection": state.parent / "connection.json"}


def private_directory(path):
    path = Path(path)
    if path.is_symlink():
        raise ServiceError("后台目录不能是符号链接")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise ServiceError("后台目录不属于当前用户")
    path.chmod(0o700)


def private_json(path, value):
    private_directory(path.parent)
    if path.is_symlink():
        raise ServiceError("后台状态文件不能是符号链接")
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_private(path, default=None):
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > MAX_MESSAGE:
            return default
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else default
    except (OSError, ValueError):
        return default


def configured_sync_interval(args):
    value = getattr(args, "sync_interval", None)
    if value is None:
        settings = read_private(service_paths(args.state_dir)["settings"], {})
        value = settings.get("sync_interval_seconds", SYNC_INTERVAL)
    if type(value) is not int or value not in (60, 3600):
        raise ServiceError("自动同步间隔只能选择每分钟或每小时")
    return value


def enabled(state_dir):
    return read_private(service_paths(state_dir)["settings"], {}).get("enabled") is True


def should_forward(args):
    if args.command not in FORWARDED:
        return False
    paths = service_paths(args.state_dir)
    if enabled(args.state_dir):
        return True
    try:
        info = paths["socket"].lstat()
        return stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid()
    except OSError:
        return False


def receive_json(connection):
    raw = bytearray()
    while b"\n" not in raw:
        chunk = connection.recv(min(4096, MAX_MESSAGE + 1 - len(raw)))
        if not chunk:
            raise ServiceError("后台连接提前关闭，操作结果未知；未自动重发")
        raw.extend(chunk)
        if len(raw) > MAX_MESSAGE:
            raise ServiceError("后台请求超过大小限制")
    line, _, remainder = raw.partition(b"\n")
    if remainder.strip():
        raise ServiceError("每次后台连接只允许一个请求")
    value = json.loads(line)
    if not isinstance(value, dict):
        raise ServiceError("后台请求格式无效")
    return value


def send_json(connection, value):
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode() + b"\n"
    if len(raw) > MAX_MESSAGE:
        raise ServiceError("后台结果超过大小限制")
    connection.sendall(raw)


def request(state_dir, payload, timeout=REQUEST_TIMEOUT):
    path = service_paths(state_dir)["socket"]
    try:
        info = path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ServiceError("后台连接权限不正确")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect(str(path))
            send_json(client, {"id": str(uuid4()), **payload})
            return receive_json(client)
    except (OSError, ValueError) as exc:
        raise ServiceError("后台服务暂不可用或操作未及时确认；未另开连接、未自动重发，请检查后台状态") from exc


def status(state_dir):
    paths = service_paths(state_dir)
    settings = read_private(paths["settings"], {})
    try:
        reply = request(state_dir, {"command": "service-status"}, timeout=2)
        reply.update(enabled=settings.get("enabled") is True, configured=type(settings.get("enabled")) is bool)
        return reply
    except ServiceError:
        previous = read_private(paths["snapshot"], {})
        return {**previous, "ok": True, "enabled": settings.get("enabled") is True,
                "configured": type(settings.get("enabled")) is bool, "running": False, "connected": False, "retrying": False,
                "awake_assertion_active": False,
                "snapshot_stale": bool(previous.get("status")),
                "keep_awake": settings.get("keep_awake") is True,
                "sync_interval_seconds": settings.get("sync_interval_seconds", SYNC_INTERVAL),
                "error": "后台服务尚未运行" if settings.get("enabled") else None}


def launch_agent_plist(args, *, python=None, script=None):
    python = str(Path(python or sys.executable).absolute())
    script = str(Path(script or Path(__file__).with_name("passport_bridge.py")).absolute())
    command = [python, "-B", script, "--state-dir", str(service_paths(args.state_dir)["state"]),
               "--codex-home", str(Path(args.codex_home).expanduser())]
    if getattr(args, "codex_cli", None):
        command.extend(["--codex-cli", args.codex_cli])
    command.extend(["service-run", "--sync-interval", str(configured_sync_interval(args))])
    if getattr(args, "keep_awake", False):
        command.append("--keep-awake")
    # A replaced app may keep the same executable paths. Include the installed
    # helper contents so the next enable upgrades a loaded old helper once,
    # while ordinary editor launches remain idempotent.
    revision = hashlib.sha256()
    backend = Path(script).parent
    for name in ("passport_service.py", "passport_bridge.py", "passport_ble.py", "passport_cursor.py",
                 "passport_cursor_tokens.py", "passport_providers.py"):
        candidate = backend / name
        revision.update(name.encode())
        if candidate.is_file():
            revision.update(candidate.read_bytes())
    return {"Label": LABEL, "ProgramArguments": command, "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False}, "ThrottleInterval": 10,
            "ProcessType": "Background", "ExitTimeOut": 20, "Umask": 0o077,
            "EnvironmentVariables": {"QINGJIAN_SERVICE_REVISION": revision.hexdigest(),
                                     "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1",
                                     "PYTHONNOUSERSITE": "1", "SSL_CERT_FILE": "/etc/ssl/cert.pem"},
            "StandardOutPath": "/dev/null", "StandardErrorPath": "/dev/null"}


def enable(args, *, home=None, runner=subprocess.run):
    paths = service_paths(args.state_dir)
    private_directory(paths["folder"])
    home = Path(home or Path.home())
    plist_path = home / "Library/LaunchAgents" / (LABEL + ".plist")
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    if plist_path.is_symlink():
        raise ServiceError("后台启动项不能是符号链接")
    payload = launch_agent_plist(args)
    if not Path(payload["ProgramArguments"][0]).is_file() or not os.access(payload["ProgramArguments"][0], os.X_OK):
        raise ServiceError("内置 Python 不可用，请修复青笺应用")
    old = plist_path.read_bytes() if plist_path.is_file() else None
    domain = f"gui/{os.getuid()}"
    try:
        existing = plistlib.loads(old) if old else None
    except (ValueError, plistlib.InvalidFileException):
        existing = None
    settings = read_private(paths["settings"], {})
    if existing == payload and settings.get("enabled") is True:
        loaded = runner(["/bin/launchctl", "print", domain + "/" + LABEL], capture_output=True, timeout=5, check=False)
        if loaded.returncode == 0:
            # The job may be starting or automatically recovering. Repeated
            # editor launches must never boot out its established connection.
            return {**status(args.state_dir), "ok": True, "configured": True, "enabled": True,
                    "keep_awake": bool(getattr(args, "keep_awake", False)),
                    "sync_interval_seconds": configured_sync_interval(args)}
    runner(["/bin/launchctl", "bootout", domain + "/" + LABEL], capture_output=True, timeout=25, check=False)
    temporary = plist_path.with_suffix(".tmp")
    try:
        with temporary.open("wb") as stream:
            os.chmod(temporary, 0o600)
            plistlib.dump(payload, stream)
        temporary.replace(plist_path)
        result = runner(["/bin/launchctl", "bootstrap", domain, str(plist_path)],
                        capture_output=True, timeout=25, check=False)
        if result.returncode:
            raise ServiceError("macOS 未能启动后台服务；请检查系统的登录项与后台活动设置")
        private_json(paths["settings"], {"enabled": True, "keep_awake": bool(getattr(args, "keep_awake", False)),
                    "sync_interval_seconds": configured_sync_interval(args)})
    except Exception:
        if old is None:
            plist_path.unlink(missing_ok=True)
        else:
            plist_path.write_bytes(old)
            runner(["/bin/launchctl", "bootstrap", domain, str(plist_path)], capture_output=True, timeout=25, check=False)
        raise
    finally:
        temporary.unlink(missing_ok=True)
    return {**status(args.state_dir), "ok": True, "configured": True, "enabled": True, "keep_awake": bool(getattr(args, "keep_awake", False)),
                    "sync_interval_seconds": configured_sync_interval(args)}


def disable(args, *, home=None, runner=subprocess.run):
    result = runner(["/bin/launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"],
                    capture_output=True, timeout=25, check=False)
    # A missing job is already stopped. A genuine permission/IO failure must not
    # be reported as disabled while a helper may still own the device.
    if result.returncode not in (0, 3, 113):
        raise ServiceError("macOS 未确认后台服务停止，请稍后重试")
    (Path(home or Path.home()) / "Library/LaunchAgents" / (LABEL + ".plist")).unlink(missing_ok=True)
    paths = service_paths(args.state_dir)
    interval = read_private(paths["settings"], {}).get("sync_interval_seconds", SYNC_INTERVAL)
    if type(interval) is not int or interval not in (60, 3600):
        interval = SYNC_INTERVAL
    private_json(paths["settings"], {"enabled": False, "keep_awake": False, "sync_interval_seconds": interval})
    return {**status(args.state_dir), "ok": True, "configured": True, "enabled": False, "running": False,
            "connected": False, "keep_awake": False, "awake_assertion_active": False, "sync_interval_seconds": interval}


def target_from_file(path):
    settings = read_private(path)
    if settings is None:
        if path.exists() or path.is_symlink():
            raise ServiceError("连接设置无法读取，请在青笺中重新保存连接选择")
        settings = {"transport": "usb"}
    transport = settings.get("transport", "usb")
    if transport == "usb":
        return ("usb", None)
    if transport != "bluetooth":
        raise ServiceError("保存的连接方式无效，请在青笺中重新选择")
    try:
        identifier = UUID(settings.get("bluetoothIdentifier", ""))
        if not identifier.int:
            raise ValueError()
    except (ValueError, TypeError, AttributeError) as exc:
        raise ServiceError("请先在青笺中选择蓝牙工牌") from exc
    return ("ble", str(identifier).upper())


class SyncOwner:
    """Sole transport owner; call execute/tick from one worker thread only."""

    def __init__(self, args, *, bridge=None, clock=time.time, monotonic=time.monotonic):
        self.args = copy.copy(args)
        self.paths = service_paths(args.state_dir)
        self.args.state_dir = str(self.paths["state"])
        self.bridge = bridge or bridge_module()
        self.clock, self.monotonic = clock, monotonic
        self.device = None
        self.target = None
        self.failures = 0
        self.next_retry = 0.0
        self.sync_interval = configured_sync_interval(args)
        self.next_refresh = 0.0
        self.next_status = 0.0
        self.last_tick_wall = self.clock()
        self.last_tick_monotonic = self.monotonic()
        self.needs_sync = True
        self.lock = threading.Lock()
        old = read_private(self.paths["snapshot"], {})
        self.snapshot = {**old, "ok": True, "enabled": True, "running": True,
                         "connected": False, "retrying": False, "next_retry_at": None,
                         "snapshot_stale": bool(old.get("status")),
                         "keep_awake": bool(getattr(args, "keep_awake", False)),
                         "sync_interval_seconds": self.sync_interval, "awake_assertion_active": False}

    def view(self):
        with self.lock:
            value = copy.deepcopy(self.snapshot)
        observed = value.get("status_observed_at")
        age = max(0.0, self.clock() - observed) if type(observed) in (int, float) else None
        value["status_age_seconds"] = age
        if value.get("status") and (not value.get("connected") or age is None or age > SNAPSHOT_STALE_SECONDS):
            value["snapshot_stale"] = True
            value["battery_stale"] = True
        return value

    def update(self, **changes):
        with self.lock:
            self.snapshot.update(changes)
            value = copy.deepcopy(self.snapshot)
            private_json(self.paths["snapshot"], value)
        return value

    def close(self):
        device, self.device = self.device, None
        if device is not None:
            try:
                device.close()
            except Exception:
                pass

    def connection(self):
        target = target_from_file(self.paths["connection"])
        if target != self.target:
            self.close()
            self.target = target
            self.next_retry, self.failures = 0.0, 0
            # Last observations belong to the previously selected badge. Do
            # not attach its battery or counters to a different device.
            old_port = self.snapshot.get("selected_target")
            if old_port is not None and old_port != list(target):
                self.update(status=None, status_observed_at=None, last_success_at=None)
        if self.device is None:
            self.device = self.bridge.BLEDevice(target[1]) if target[0] == "ble" else self.bridge.Device()
        return self.device

    def fail(self, exc):
        message = str(exc) if isinstance(exc, (ServiceError, self.bridge.BridgeError)) else "设备连接或同步暂不可用"
        kind = error_kind(exc)
        if message.startswith("设备拒绝操作"):
            kind = "remote_rejected"
        if self.device is not None and (kind == "remote_rejected" or isinstance(exc, ServiceError)):
            # A device rejection or local validation error is not a lost radio.
            self.update(error=message, error_kind=kind, retrying=False, next_retry_at=None)
            return
        self.close()
        self.failures += 1
        delay = min(60, 2 ** min(self.failures, 6))
        if kind in {"permission_denied", "permission_pending"}:
            delay = max(delay, 60)
        self.next_retry = self.monotonic() + delay
        self.update(connected=False, retrying=True, snapshot_stale=bool(self.snapshot.get("status")),
                    next_retry_at=self.clock() + delay, error=message, error_kind=kind,
                    retry_notice="后台将重连已选择的工牌；上传操作不会自动重发")

    def record(self, result, *, command=None):
        changes = {"error": None, "error_kind": None}
        if isinstance(result.get("usage"), dict):
            changes["usage"] = result["usage"]
        device_status = result.get("status")
        if isinstance(device_status, dict):
            device_status = copy.deepcopy(device_status)
            prior = self.snapshot.get("status") or {}
            if prior.get("device_id") and device_status.get("device_id") != prior.get("device_id"):
                prior = {}
                self.snapshot["battery_observed_at"] = None
            battery = device_status.get("battery_soc")
            if type(battery) in (int, float) and 0 <= battery <= 100:
                battery_at, battery_stale = self.clock(), False
            else:
                battery_at, battery_stale = self.snapshot.get("battery_observed_at"), True
                previous = prior.get("battery_soc")
                if type(previous) in (int, float) and 0 <= previous <= 100:
                    device_status["battery_soc"] = previous
            changes.update(status=device_status, status_observed_at=self.clock(),
                           last_success_at=self.clock(), connected=True, retrying=False,
                           next_retry_at=None, snapshot_stale=False,
                           battery_observed_at=battery_at, battery_stale=battery_stale,
                           port=result.get("port"), transport=result.get("transport"),
                           selected_target=list(self.target) if self.target else None)
            if command in ("sync", "upload"):
                self.failures = 0
            self.next_retry = 0.0
            self.next_status = self.monotonic() + STATUS_INTERVAL
        self.update(**changes)
        return {**result, "service": self.view()}

    def execute(self, message, *, background=False):
        action = message.get("command")
        if action not in FORWARDED:
            raise ServiceError("后台不支持此操作")
        args = copy.copy(self.args)
        args.command, args.via_service = action, False
        args.port, args.ble_id = None, None
        args.no_quota = False
        args.refresh_quota = bool(message.get("refresh_quota", False)) or (action == "sync" and not background)
        if action in DEVICE_ACTIONS:
            device = self.connection()
            args.ble_id = self.target[1] if self.target[0] == "ble" else None
            if message.get("ble_id") and message["ble_id"].upper() != args.ble_id:
                raise ServiceError("后台连接选择已改变，请保存连接设置后重试")
            if message.get("port") and message["port"] != device.port:
                raise ServiceError("后台不会自动切换到其他 USB 工牌")
            if action == "pair" and args.ble_id:
                raise ServiceError("首次配对窗口只能通过 USB 开启")
            if action == "upload":
                config = message.get("config")
                if not isinstance(config, str) or not Path(config).is_absolute():
                    raise ServiceError("上传需要本机配置文件的绝对路径")
                args.config = config
            if action == "screen":
                if message.get("state") not in ("0", "1"):
                    raise ServiceError("屏幕状态无效")
                args.state = message["state"]
            usage = copy.deepcopy(self.snapshot.get("usage")) if background else None
            result = self.bridge.execute_device_command(args, device, background=background, token_result=usage)
            return self.record(result, command=action)
        if action == "cursor-usage":
            quota = self.bridge.collect_cursor_usage(args.state_dir, refresh=True)
            current = copy.deepcopy(self.snapshot.get("usage") or {})
            current["cursor_quota"] = quota
            self.update(usage=current)
            return {"ok": True, "cursor_quota": quota, "service": self.view()}
        if action == "reset-cycle" and message.get("reason") != "manual_reset_card":
            raise ServiceError("手动重置必须注明重置卡记录")
        result = self.bridge.read_tokens(args, manual=action == "reset-cycle")
        self.bridge.collect_provider_metadata(result, args)
        self.update(usage=result, source_error=None)
        return {**result, "service": self.view()}

    def tick(self):
        now, wall = self.monotonic(), self.clock()
        # Darwin's monotonic clock may exclude system suspension. Refresh at
        # wake even if a pre-sleep deadline has not advanced yet.
        elapsed_wall = wall - self.last_tick_wall
        elapsed_monotonic = now - self.last_tick_monotonic
        self.last_tick_wall, self.last_tick_monotonic = wall, now
        if elapsed_wall - elapsed_monotonic > STATUS_INTERVAL:
            self.next_retry = self.next_status = 0.0
            self.next_refresh = max(0.0, self.next_refresh - (elapsed_wall - elapsed_monotonic))
            self.needs_sync = True
        try:
            target = target_from_file(self.paths["connection"])
            if target != self.target:
                self.close()
                self.next_retry = 0
                self.next_status = 0
            if now >= self.next_retry and (self.device is None or (now >= self.next_status and now < self.next_refresh)):
                was_disconnected = self.device is None
                self.connection()
                self.execute({"command": "status"})
                if was_disconnected:
                    self.needs_sync = True
        except Exception as exc:
            if now >= self.next_retry:
                self.fail(exc)
        if now >= self.next_refresh:
            self.next_refresh = now + self.sync_interval
            try:
                # Local source refresh remains independent of badge presence.
                self.execute({"command": "tokens"})
                self.needs_sync = True
            except Exception as exc:
                message = str(exc) if isinstance(exc, (ServiceError, self.bridge.BridgeError)) else "本机用量暂时无法刷新"
                self.update(source_error=message)
        if self.device is not None and now >= self.next_retry and self.needs_sync:
            try:
                self.needs_sync = False
                self.execute({"command": "sync"}, background=True)
            except Exception as exc:
                self.fail(exc)


class AwakeAssertionController:
    """Hold a process-owned idle-sleep assertion only while it is useful."""

    def __init__(self, keep_awake, *, clock=time.time, launcher=subprocess.Popen):
        self.keep_awake = bool(keep_awake)
        self.clock, self.launcher = clock, launcher
        self.started = clock()
        self.process = None
        self.retry_at = 0.0
        self.error = None

    def wanted(self, snapshot):
        if not self.keep_awake:
            return False
        now = self.clock()
        if snapshot.get("connected") and not snapshot.get("snapshot_stale"):
            return True
        if 0 <= now - self.started <= AWAKE_GRACE_SECONDS:
            return True
        last = snapshot.get("last_success_at")
        return type(last) in (int, float) and 0 <= now - last <= AWAKE_GRACE_SECONDS

    def close(self):
        process, self.process = self.process, None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)

    def tick(self, snapshot):
        desired = self.wanted(snapshot)
        if self.process is not None and self.process.poll() is not None:
            self.process = None
            self.error = "macOS 的保持唤醒请求已停止"
            self.retry_at = self.clock() + 30
        if not desired:
            self.close()
            self.error = None
        elif self.process is None and self.clock() >= self.retry_at:
            try:
                self.process = self.launcher(["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())],
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                if self.process.poll() is not None:
                    self.process = None
                    self.error = "macOS 未能保持唤醒"
                    self.retry_at = self.clock() + 30
                else:
                    self.error = None
            except OSError:
                self.error = "macOS 未能保持唤醒"
                self.retry_at = self.clock() + 30
        return {"awake_assertion_active": self.process is not None,
                "awake_assertion_error": self.error}


class ServiceServer:
    def __init__(self, owner):
        self.owner = owner
        self.stop = threading.Event()
        self.jobs = queue.Queue(maxsize=16)
        self.completed = OrderedDict()
        self.worker_failed = False

    def work(self):
        try:
            while not self.stop.is_set():
                try:
                    job = self.jobs.get(timeout=OWNER_IDLE_SECONDS)
                except queue.Empty:
                    if not self.stop.is_set():
                        self.owner.tick()
                    continue
                message, response, deadline = job
                if self.stop.is_set():
                    response.put({"ok": False, "error": "后台正在停止，排队操作未执行"})
                    break
                key = message["id"]
                if key in self.completed:
                    response.put(self.completed[key])
                    continue
                if time.monotonic() > deadline:
                    response.put({"ok": False, "error": "排队操作已超时，未执行"})
                    continue
                try:
                    result = self.owner.execute(message)
                except Exception as exc:
                    if message.get("command") in DEVICE_ACTIONS:
                        self.owner.fail(exc)
                    message_text = str(exc) if isinstance(exc, (ServiceError, self.owner.bridge.BridgeError)) else "后台操作失败；未自动重发"
                    result = {"ok": False, "error": message_text, "error_kind": error_kind(exc), "service": self.owner.view()}
                self.completed[key] = result
                while len(self.completed) > 128:
                    self.completed.popitem(last=False)
                response.put(result)
                if not self.stop.is_set() and (self.owner.monotonic() >= self.owner.next_status or
                                               self.owner.monotonic() >= self.owner.next_refresh):
                    self.owner.tick()
        except Exception:
            self.worker_failed = True
        finally:
            self.owner.close()
            try:
                self.owner.update(running=False, connected=False, retrying=False,
                                  snapshot_stale=bool(self.owner.snapshot.get("status")))
            except Exception:
                self.worker_failed = True

    def handle(self, client):
        with client:
            client.settimeout(REQUEST_TIMEOUT)
            try:
                message = receive_json(client)
                identifier = message.get("id")
                if not isinstance(identifier, str) or len(identifier) > 64:
                    raise ServiceError("请求编号无效")
                if message.get("command") == "service-status":
                    result = self.owner.view()
                else:
                    response = queue.Queue(maxsize=1)
                    try:
                        self.jobs.put_nowait((message, response, time.monotonic() + REQUEST_TIMEOUT - 2))
                        result = response.get(timeout=REQUEST_TIMEOUT - 1)
                    except queue.Full:
                        result = {"ok": False, "error": "后台队列已满，请稍后操作"}
                    except queue.Empty:
                        result = {"ok": False, "error": "后台尚未确认操作结果，请读取状态；操作未自动重发"}
                send_json(client, result)
            except (OSError, ValueError, ServiceError):
                # No raw protocol data or underlying exception text is logged.
                try:
                    send_json(client, {"ok": False, "error": "后台请求未完成；未自动重发"})
                except OSError:
                    pass

    def run(self):
        paths = self.owner.paths
        private_directory(paths["folder"])
        if len(os.fsencode(paths["socket"])) >= 104:
            raise ServiceError("后台数据目录路径过长，请使用默认用户资料目录")
        if paths["lock"].is_symlink():
            raise ServiceError("后台锁文件不能是符号链接")
        descriptor = os.open(paths["lock"], os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        assertion = AwakeAssertionController(getattr(self.owner.args, "keep_awake", False))
        power_status = None
        worker = None
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ServiceError("后台服务已在运行") from exc
            if paths["socket"].exists() or paths["socket"].is_symlink():
                info = paths["socket"].lstat()
                if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                    raise ServiceError("后台 socket 路径已被其他文件占用")
                paths["socket"].unlink()
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(str(paths["socket"]))
                paths["socket"].chmod(0o600)
                listener.listen(8)
                listener.settimeout(OWNER_IDLE_SECONDS)
                worker = threading.Thread(target=self.work, name="QingjianSyncOwner", daemon=True)
                worker.start()
                while not self.stop.is_set():
                    current_power = assertion.tick(self.owner.view())
                    if current_power != power_status:
                        self.owner.update(**current_power)
                        power_status = current_power
                    if self.worker_failed or not worker.is_alive():
                        raise ServiceError("后台同步线程已停止，正在等待系统自动重启")
                    try:
                        client, _ = listener.accept()
                    except socket.timeout:
                        continue
                    threading.Thread(target=self.handle, args=(client,), daemon=True).start()
        finally:
            self.stop.set()
            if worker:
                worker.join(timeout=20)
            assertion.close()
            if worker:
                self.owner.update(awake_assertion_active=False)
            # Only the lock owner may unlink the live endpoint.
            if worker:
                paths["socket"].unlink(missing_ok=True)
            os.close(descriptor)


def dispatch(args):
    action = args.command
    if action == "service-enable":
        return enable(args)
    if action == "service-disable":
        return disable(args)
    if action == "service-status":
        return status(args.state_dir)
    if action == "service-run":
        owner = SyncOwner(args)
        server = ServiceServer(owner)
        old_signals = {}
        for sig in (signal.SIGTERM, signal.SIGINT):
            old_signals[sig] = signal.signal(sig, lambda *_: server.stop.set())
        try:
            server.run()
        finally:
            for sig, previous in old_signals.items():
                signal.signal(sig, previous)
        return {"ok": True, "running": False}
    if action not in FORWARDED:
        raise ServiceError("此命令不通过后台服务执行")
    payload = {"command": action}
    for key in ("ble_id", "port", "config", "state", "reason", "refresh_quota"):
        value = getattr(args, key, None)
        if value is not None:
            payload[key] = value
    return request(args.state_dir, payload)
