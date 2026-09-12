"""Explicitly selected QingJian BLE transport for macOS, using Bleak/CoreBluetooth.

No discovery or connection happens on import. Keep a session on one asyncio loop:

    async with AsyncPassportBLE(selected_macos_uuid) as device:
        status = await device.status()
        await device.request("TIME ...", "@AP TIME_OK")

The caller owns user selection and persistence of the selected UUID. Discovery
filters a name/service, not a cryptographic identity. No pairing, firmware flash,
automatic reconnect, or mutation retry is performed. NUS firmware interoperability
and macOS Bluetooth permission are separate device acceptance checks.

Primary references:
https://bleak.readthedocs.io/en/latest/backends/macos.html
https://bleak.readthedocs.io/en/latest/api/client.html
https://bleak.readthedocs.io/en/latest/api/scanner.html
https://docs.espressif.com/projects/esp-idf/en/v5.5.3/esp32c3/api-reference/bluetooth/index.html
"""
from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
from dataclasses import asdict, dataclass
import json
import math
import re
import threading
from uuid import UUID

NUS_SERVICE_UUID = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX_UUID = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NUS_TX_UUID = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"
DEVICE_NAME = re.compile(r"QingJian-[0-9A-Fa-f]{6}\Z")
MAX_COMMAND_BYTES = 1024
MAX_LINE_BYTES = 8192
MAX_NOTIFY_BYTES = 512
MAX_UNEXPECTED_LINES = 32
MAX_CANDIDATES = 32
DEFAULT_CONNECT_TIMEOUT = 60


class BLETransportError(Exception):
    """A sanitized transport or dependency error suitable for the native editor."""


class BLEConnectionClosed(BLETransportError):
    pass


class BLEProtocolError(BLETransportError):
    pass


class BLERemoteCommandError(BLETransportError):
    """The device rejected a command; the established transport remains usable."""


def _timeout(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 60:
        raise ValueError("Timeout must be between 0 and 60 seconds")
    return float(value)


def selected_identifier(value):
    if not isinstance(value, str):
        raise BLETransportError("请先选择或绑定一台青笺蓝牙设备")
    try:
        identifier = UUID(value)
    except (ValueError, AttributeError) as exc:
        raise BLETransportError("macOS 蓝牙设备须使用扫描返回的 UUID，不能使用名称或 MAC 地址") from exc
    if not identifier.int:
        raise BLETransportError("蓝牙设备 UUID 无效")
    return str(identifier).upper()


def _bleak_types():
    try:
        from bleak import BleakClient, BleakScanner
    except ImportError as exc:
        raise BLETransportError("缺少蓝牙组件，请安装随青笺分发的 Bleak 与 CoreBluetooth 依赖") from exc
    return BleakScanner, BleakClient


@dataclass(frozen=True)
class BLECandidate:
    identifier: str
    name: str
    rssi: int | None


def _candidate(device, advertisement):
    name = getattr(advertisement, "local_name", None) or getattr(device, "name", None)
    services = getattr(advertisement, "service_uuids", None) or []
    if not isinstance(name, str) or not DEVICE_NAME.fullmatch(name) or \
            NUS_SERVICE_UUID not in {str(s).lower() for s in services}:
        return None
    try:
        identifier = selected_identifier(device.address)
    except (AttributeError, BLETransportError):
        return None
    rssi = getattr(advertisement, "rssi", None)
    return BLECandidate(identifier, name, rssi if type(rssi) is int else None)


async def scan_candidates(timeout=5, *, scanner=None):
    """Read advertisements for a bounded interval. Never connect or choose a peer."""
    timeout = _timeout(timeout)
    scanner = scanner or _bleak_types()[0]
    try:
        records = await asyncio.wait_for(
            scanner.discover(timeout=timeout, return_adv=True, service_uuids=[NUS_SERVICE_UUID]),
            timeout + 2,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        raise BLETransportError("蓝牙扫描失败，请检查蓝牙开关和 macOS 对青笺的蓝牙权限") from exc
    selected = {}
    for device, advertisement in records.values():
        candidate = _candidate(device, advertisement)
        if candidate is not None:
            selected[candidate.identifier] = candidate
            if len(selected) >= MAX_CANDIDATES:
                break
    return sorted(selected.values(), key=lambda item: (item.name, item.identifier))


class LineBuffer:
    """Bounded UTF-8 line assembler; notification boundaries have no semantics."""
    def __init__(self, max_line=MAX_LINE_BYTES):
        self.max_line = max_line
        self.pending = bytearray()

    def clear(self):
        self.pending.clear()

    def feed(self, data):
        if len(data) > MAX_NOTIFY_BYTES:
            self.clear()
            raise BLEProtocolError("蓝牙通知超过长度上限")
        lines = []
        for byte in data:
            if byte == 10:
                raw = bytes(self.pending)
                self.clear()
                if raw.endswith(b"\r"):
                    raw = raw[:-1]
                try:
                    line = raw.decode("utf-8", "strict")
                except UnicodeDecodeError as exc:
                    raise BLEProtocolError("蓝牙响应包含无效文字编码") from exc
                if any(ord(char) < 32 or ord(char) == 127 for char in line):
                    raise BLEProtocolError("蓝牙响应包含无效控制字符")
                if line:
                    lines.append(line)
            else:
                if len(self.pending) >= self.max_line:
                    self.clear()
                    raise BLEProtocolError("蓝牙响应行超过长度上限")
                self.pending.append(byte)
        return lines


class AsyncPassportBLE:
    def __init__(self, identifier, *, connect_timeout=DEFAULT_CONNECT_TIMEOUT, scanner=None, client_factory=None):
        self.identifier = selected_identifier(identifier)
        self.connect_timeout = _timeout(connect_timeout)
        self._scanner = scanner
        self._factory = client_factory
        self._client = None
        self._rx = self._tx = None
        self._loop = None
        self._generation = 0
        self._notify_ready = False
        self._lines = LineBuffer()
        self._pending = None
        self._expected = None
        self._unexpected = 0
        self._fatal = None
        self._request_lock = asyncio.Lock()
        self._connection_lock = asyncio.Lock()
        self._cleanup = None

    @property
    def is_connected(self):
        return bool(self._client is not None and self._client.is_connected and self._notify_ready)

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *_):
        await self.close()

    def _check_loop(self):
        loop = asyncio.get_running_loop()
        if self._loop is not None and self._loop is not loop:
            raise BLETransportError("同一蓝牙会话必须在同一个 asyncio 事件循环中运行")
        self._loop = loop

    async def connect(self):
        self._check_loop()
        async with self._connection_lock:
            if self.is_connected:
                return
            await self._disconnect_locked()
            if self._scanner is None or self._factory is None:
                scanner, factory = _bleak_types()
                self._scanner = self._scanner or scanner
                self._factory = self._factory or factory
            self._fatal = None
            self._generation += 1
            generation = self._generation

            def matches(device, advertisement):
                candidate = _candidate(device, advertisement)
                return candidate is not None and candidate.identifier == self.identifier

            async def establish():
                device = await self._scanner.find_device_by_filter(
                    matches, timeout=self.connect_timeout, service_uuids=[NUS_SERVICE_UUID])
                if device is None:
                    raise BLETransportError("未找到已选择的青笺设备，请打开其蓝牙连接模式")
                if selected_identifier(device.address) != self.identifier:
                    raise BLETransportError("发现的设备与用户选择不一致")
                self._client = self._factory(device, services=[NUS_SERVICE_UUID], timeout=self.connect_timeout,
                    disconnected_callback=lambda client: self._schedule(self._disconnected, client, generation))
                await self._client.connect()
                service = self._client.services.get_service(NUS_SERVICE_UUID)
                if service is None:
                    raise BLEProtocolError("所选设备没有青笺传输服务")
                chars = {str(char.uuid).lower(): char for char in service.characteristics}
                self._rx, self._tx = chars.get(NUS_RX_UUID), chars.get(NUS_TX_UUID)
                if self._rx is None or "write" not in self._rx.properties or \
                        self._tx is None or "notify" not in self._tx.properties:
                    raise BLEProtocolError("设备蓝牙服务缺少可靠写入或通知特征")
                await self._client.start_notify(self._tx,
                    lambda _sender, data: self._schedule(self._notification, generation, bytes(data)))
                self._notify_ready = True

            try:
                await asyncio.wait_for(establish(), self.connect_timeout)
                if not self.is_connected:
                    raise BLEConnectionClosed("订阅通知前蓝牙连接已断开")
            except asyncio.CancelledError:
                await self._disconnect_locked()
                raise
            except Exception as exc:
                await self._disconnect_locked()
                if isinstance(exc, BLETransportError):
                    raise
                raise BLETransportError("蓝牙连接失败或超时，请检查设备和 macOS 蓝牙权限") from exc

    def _schedule(self, callback, *args):
        if self._loop is not None and not self._loop.is_closed():
            try:
                self._loop.call_soon_threadsafe(callback, *args)
            except RuntimeError:
                pass  # Loop shutdown; no state survives into another connection.

    def _fail_pending(self, error):
        if self._pending is not None and not self._pending.done():
            self._pending.set_exception(error)

    def _disconnected(self, client, generation):
        if generation != self._generation or client is not self._client:
            return
        self._generation += 1
        self._notify_ready = False
        self._lines.clear()
        self._fail_pending(BLEConnectionClosed("青笺蓝牙连接已断开；未自动重发操作"))

    def _notification(self, generation, data):
        if generation != self._generation or self._client is None:
            return
        try:
            for line in self._lines.feed(data):
                if self._pending is None or self._pending.done():
                    continue
                if line.startswith("@AP ERROR") or (line.startswith("@AP ") and line.endswith("_ERROR")):
                    self._fail_pending(BLERemoteCommandError("设备拒绝了蓝牙操作，请读取设备状态后重试"))
                elif line == self._expected or line.startswith(self._expected + " "):
                    self._pending.set_result(line[len(self._expected):].strip())
                else:
                    self._unexpected += 1
                    if self._unexpected > MAX_UNEXPECTED_LINES:
                        raise BLEProtocolError("蓝牙响应与请求不匹配")
        except BLEProtocolError as exc:
            self._fatal = exc
            self._notify_ready = False
            self._fail_pending(exc)
            if self._cleanup is None or self._cleanup.done():
                self._cleanup = self._loop.create_task(self.close())

    async def _disconnect_locked(self):
        client, self._client = self._client, None
        self._generation += 1
        self._notify_ready = False
        self._rx = self._tx = None
        self._lines.clear()
        self._fail_pending(BLEConnectionClosed("蓝牙会话已关闭"))
        if client is not None:
            try:
                await asyncio.wait_for(client.disconnect(), 5)
            except Exception:
                pass  # Preserve the original operation error while abandoning session.

    async def close(self):
        self._check_loop()
        async with self._connection_lock:
            await self._disconnect_locked()

    async def request(self, command, expected, timeout=5):
        self._check_loop()
        timeout = _timeout(timeout)
        if not isinstance(command, str) or not command or any(not 32 <= ord(c) <= 126 for c in command):
            raise BLEProtocolError("蓝牙命令必须为单行 ASCII 文本")
        if not isinstance(expected, str) or not expected.startswith("@AP ") or \
                any(not 32 <= ord(c) <= 126 for c in expected):
            raise BLEProtocolError("无效的蓝牙响应标记")
        frame = ("@AP " + command + "\n").encode("ascii")
        if len(frame) > MAX_COMMAND_BYTES:
            raise BLEProtocolError("蓝牙命令超过长度上限")
        async with self._request_lock:
            if self._fatal is not None:
                raise self._fatal
            if not self.is_connected:
                raise BLEConnectionClosed("请先连接已选择的青笺蓝牙设备")
            self._lines.clear()
            self._unexpected = 0
            self._expected = expected
            self._pending = self._loop.create_future()
            client, rx = self._client, self._rx

            async def exchange():
                mtu = getattr(client, "mtu_size", 23)
                mtu = mtu if type(mtu) is int and mtu >= 23 else 23
                chunk_size = min(mtu - 3, 244)
                for offset in range(0, len(frame), chunk_size):
                    if not self.is_connected or client is not self._client:
                        raise BLEConnectionClosed("写入过程中蓝牙连接已断开")
                    await client.write_gatt_char(rx, frame[offset:offset+chunk_size], response=True)
                return await self._pending

            try:
                return await asyncio.wait_for(exchange(), timeout)
            except BLERemoteCommandError:
                raise
            except asyncio.CancelledError:
                await self.close()
                raise
            except Exception as exc:
                await self.close()
                if isinstance(exc, BLETransportError):
                    raise
                if isinstance(exc, TimeoutError):
                    raise BLETransportError("蓝牙操作超时，已关闭会话；请读取状态确认结果后再操作") from exc
                raise BLETransportError("蓝牙传输失败，已关闭会话；操作未自动重发") from exc
            finally:
                if self._pending is not None:
                    if not self._pending.done():
                        self._pending.cancel()
                    elif not self._pending.cancelled():
                        self._pending.exception()  # Retrieve faults raised during failed writes.
                self._pending = None
                self._expected = None

    async def status(self, timeout=5):
        result = await self.request("STATUS", "@AP STATUS", timeout)
        try:
            status = json.loads(result)
        except ValueError as exc:
            await self.close()
            raise BLEProtocolError("设备状态不是有效 JSON") from exc
        if not isinstance(status, dict):
            await self.close()
            raise BLEProtocolError("设备状态不是 JSON 对象")
        return status


class SyncPassportBLE:
    """Synchronous facade with one persistent asyncio thread per connection.

    CoreBluetooth work stays on that loop. Bleak 3.0.2 dispatches native callbacks
    on its own serial dispatch queue and forwards them to the captured asyncio
    loop (CentralManagerDelegate); it does not require a new loop per request.
    This threading contract is source-checked, not a physical Bluetooth test.
    """
    def __init__(self, identifier, *, connect_timeout=DEFAULT_CONNECT_TIMEOUT, scanner=None, client_factory=None):
        self.identifier = selected_identifier(identifier)
        self.connect_timeout = _timeout(connect_timeout)
        self._options = {"connect_timeout": self.connect_timeout, "scanner": scanner,
                         "client_factory": client_factory}
        self._thread = None
        self._loop = None
        self._device = None
        self._ready = threading.Event()
        self._start_lock = threading.Lock()
        self._startup_error = None

    @property
    def is_connected(self):
        return bool(self._device is not None and self._device.is_connected)

    def _thread_main(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._device = AsyncPassportBLE(self.identifier, **self._options)
        except Exception as exc:
            self._startup_error = exc
            self._ready.set()
            self._loop.close()
            return
        self._ready.set()
        try:
            self._loop.run_forever()
        finally:
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()
            if pending:
                self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            self._loop.close()

    def _start(self):
        with self._start_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._ready.clear()
            self._startup_error = None
            self._thread = threading.Thread(target=self._thread_main, name="QingJian-BLE", daemon=True)
            self._thread.start()
            if not self._ready.wait(3) or self._startup_error:
                raise BLETransportError("无法启动蓝牙传输事件循环") from self._startup_error

    def _call(self, coroutine, timeout):
        if self._thread is threading.current_thread():
            coroutine.close()
            raise BLETransportError("不能在蓝牙回调线程内同步等待自身")
        if self._loop is None or self._loop.is_closed() or self._thread is None or not self._thread.is_alive():
            coroutine.close()
            raise BLEConnectionClosed("蓝牙后台会话已关闭")
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise BLETransportError("蓝牙后台处理超时，操作未自动重发") from exc

    def connect(self):
        self._start()
        try:
            self._call(self._device.connect(), self.connect_timeout + 7)
        except BaseException:
            self.close()
            raise

    def request(self, command, expected, timeout=5):
        timeout = _timeout(timeout)
        if self._device is None:
            raise BLEConnectionClosed("请先连接已选择的蓝牙设备")
        try:
            return self._call(self._device.request(command, expected, timeout), timeout + 7)
        except BLERemoteCommandError:
            raise
        except BaseException:
            self.close()
            raise

    def status(self, timeout=5):
        timeout = _timeout(timeout)
        if self._device is None:
            raise BLEConnectionClosed("请先连接已选择的蓝牙设备")
        try:
            return self._call(self._device.status(timeout), timeout + 7)
        except BLERemoteCommandError:
            raise
        except BaseException:
            self.close()
            raise

    def close(self):
        thread = self._thread
        if thread is None or not thread.is_alive():
            return
        try:
            self._call(self._device.close(), 7)
        except BLETransportError:
            pass
        finally:
            if self._loop is not None and not self._loop.is_closed():
                self._loop.call_soon_threadsafe(self._loop.stop)
            if thread is not threading.current_thread():
                thread.join(timeout=3)

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_):
        self.close()


async def _main_async(args):
    if args.action == "scan":
        return {"ok": True, "devices": [asdict(item) for item in await scan_candidates(args.timeout)]}
    async with AsyncPassportBLE(args.device, connect_timeout=args.timeout) as device:
        return {"ok": True, "transport": "ble", "identifier": device.identifier, "status": await device.status()}


def main():
    parser = argparse.ArgumentParser(description="青笺 BLE 传输诊断（扫描不自动连接）")
    commands = parser.add_subparsers(dest="action", required=True)
    scan = commands.add_parser("scan")
    scan.add_argument("--timeout", type=float, default=5)
    status = commands.add_parser("status")
    status.add_argument("--device", required=True, help="用户选择的 macOS 蓝牙 UUID")
    status.add_argument("--timeout", type=float, default=DEFAULT_CONNECT_TIMEOUT)
    args = parser.parse_args()
    try:
        result = asyncio.run(_main_async(args))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (BLETransportError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
