#!/usr/bin/env python3
"""Deterministic BLE transport host tests. Never load CoreBluetooth or scan radio."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest

spec = importlib.util.spec_from_file_location("passport_ble", Path(__file__).parents[1] / "tools/passport_ble.py")
ble = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ble
spec.loader.exec_module(ble)

CHOSEN = "12345678-1234-4234-8234-123456789ABC"
OTHER = "23456789-1234-4234-8234-123456789ABC"


def record(identifier=CHOSEN, name="QingJian-A1B2C3", services=None):
    device = SimpleNamespace(address=identifier, name=name)
    advertisement = SimpleNamespace(local_name=name, rssi=-55,
        service_uuids=[ble.NUS_SERVICE_UUID] if services is None else services)
    return device, advertisement


class FakeScanner:
    def __init__(self, records=None):
        self.records = records if records is not None else [record()]
        self.calls = []

    async def discover(self, **kwargs):
        self.calls.append(("discover", kwargs))
        return {str(index): item for index, item in enumerate(self.records)}

    async def find_device_by_filter(self, filterfunc, **kwargs):
        self.calls.append(("find", kwargs))
        for device, advertisement in self.records:
            if filterfunc(device, advertisement):
                return device
        return None


class FakeClient:
    def __init__(self, device, services=None, disconnected_callback=None, timeout=30):
        self.device = device
        self.service_filter = services
        self.disconnected_callback = disconnected_callback
        self.constructor_timeout = timeout
        self.is_connected = False
        self.mtu_size = 23
        self.callback = None
        self.writes = []
        self.frames = []
        self.events = []
        self.buffer = bytearray()
        self.write_event = asyncio.Event()
        self.response = b"@AP PING_OK\n"
        self.reply_delay = 0
        self.write_exception = None
        self.write_hangs = False
        self.response_handler = None
        self.rx = SimpleNamespace(uuid=ble.NUS_RX_UUID, properties=["write"])
        self.tx = SimpleNamespace(uuid=ble.NUS_TX_UUID, properties=["notify"])
        self.service = SimpleNamespace(characteristics=[self.rx, self.tx])
        self.services = SimpleNamespace(get_service=lambda uuid: self.service if uuid == ble.NUS_SERVICE_UUID else None)

    async def connect(self):
        self.events.append("connect")
        self.is_connected = True

    async def disconnect(self):
        self.events.append("disconnect")
        self.is_connected = False
        if self.disconnected_callback:
            self.disconnected_callback(self)

    async def start_notify(self, characteristic, callback):
        assert characteristic is self.tx
        self.events.append("notify")
        self.callback = callback

    async def write_gatt_char(self, characteristic, data, response):
        assert self.callback is not None, "must subscribe before writes"
        assert characteristic is self.rx
        assert response is True
        self.events.append("write")
        self.writes.append(bytes(data))
        self.write_event.set()
        if self.write_exception:
            raise self.write_exception
        if self.write_hangs:
            await asyncio.Event().wait()
        self.buffer.extend(data)
        if self.buffer.endswith(b"\n"):
            frame = bytes(self.buffer)
            self.frames.append(frame)
            self.buffer.clear()
            reply = self.response_handler(frame) if self.response_handler else self.response
            if reply is not None:
                if self.reply_delay:
                    asyncio.get_running_loop().call_later(self.reply_delay, self.emit, reply)
                else:
                    self.emit(reply)

    def emit(self, data):
        if isinstance(data, list):
            for part in data:
                self.callback(self.tx, bytearray(part))
        else:
            self.callback(self.tx, bytearray(data))


class Factory:
    def __init__(self, configure=None):
        self.clients = []
        self.configure = configure

    def __call__(self, *args, **kwargs):
        client = FakeClient(*args, **kwargs)
        if self.configure:
            self.configure(client)
        self.clients.append(client)
        return client


class FramingTests(unittest.TestCase):
    def test_split_utf8_and_multiple_crlf_lines(self):
        lines = ble.LineBuffer()
        content = '@AP STATUS {"name":"青笺"}\r\n@AP TIME_OK\n'.encode()
        output = []
        for byte in content:
            output.extend(lines.feed(bytes([byte])))
        self.assertEqual(output, ['@AP STATUS {"name":"青笺"}', "@AP TIME_OK"])
        self.assertEqual(len(lines.pending), 0)

    def test_line_overflow_clears_bounded_buffer(self):
        lines = ble.LineBuffer(max_line=16)
        with self.assertRaises(ble.BLEProtocolError):
            lines.feed(b"x" * 17)
        self.assertEqual(len(lines.pending), 0)

    def test_invalid_bytes_and_notifications_rejected(self):
        for data in (b"\xff\n", b"a\x00b\n", b"x" * 513):
            with self.subTest(data=data[:10]), self.assertRaises(ble.BLEProtocolError):
                ble.LineBuffer().feed(data)

    def test_selection_requires_nonzero_macos_uuid(self):
        for value in (None, "", "QingJian-A1B2C3", "AA:BB:CC:DD:EE:FF", "00000000-0000-0000-0000-000000000000"):
            with self.subTest(value=value), self.assertRaises(ble.BLETransportError):
                ble.AsyncPassportBLE(value)
        self.assertEqual(ble.selected_identifier(CHOSEN.lower()), CHOSEN)


class BLETests(unittest.IsolatedAsyncioTestCase):
    async def make_device(self, configure=None, records=None):
        self.factory = Factory(configure)
        self.scanner = FakeScanner(records)
        device = ble.AsyncPassportBLE(CHOSEN, scanner=self.scanner, client_factory=self.factory)
        await device.connect()
        self.addAsyncCleanup(device.close)
        return device, self.factory.clients[-1]

    async def test_scan_filters_candidates_without_constructing_client(self):
        scanner = FakeScanner([record(), record(OTHER, "Other-A1B2C3"),
                               record(OTHER, services=[]), record(CHOSEN.lower())])
        result = await ble.scan_candidates(scanner=scanner)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].identifier, CHOSEN)
        self.assertEqual(scanner.calls[0][1]["service_uuids"], [ble.NUS_SERVICE_UUID])
        self.assertTrue(scanner.calls[0][1]["return_adv"])

    async def test_different_advertised_device_is_never_connected(self):
        factory = Factory()
        device = ble.AsyncPassportBLE(CHOSEN, scanner=FakeScanner([record(OTHER)]), client_factory=factory)
        with self.assertRaises(ble.BLETransportError):
            await device.connect()
        self.assertEqual(factory.clients, [])

    async def test_missing_service_or_properties_closes_connection_before_write(self):
        for configure in (lambda c: setattr(c, "service", None),
                          lambda c: setattr(c.rx, "properties", ["write-without-response"]),
                          lambda c: setattr(c.tx, "properties", [])):
            factory = Factory(configure)
            device = ble.AsyncPassportBLE(CHOSEN, scanner=FakeScanner(), client_factory=factory)
            with self.assertRaises(ble.BLEProtocolError):
                await device.connect()
            self.assertFalse(factory.clients[-1].is_connected)
            self.assertEqual(factory.clients[-1].writes, [])

    async def test_notify_precedes_mtu_split_write_and_preserves_frame(self):
        device, client = await self.make_device()
        command = "PING " + "A" * 400
        self.assertEqual(await device.request(command, "@AP PING_OK"), "")
        self.assertEqual(b"".join(client.writes), ("@AP " + command + "\n").encode())
        self.assertTrue(all(len(part) <= 20 for part in client.writes))
        self.assertLess(client.events.index("notify"), client.events.index("write"))

    async def test_first_pairing_gets_sixty_seconds_including_backend_connect(self):
        device, client = await self.make_device()
        self.assertEqual(device.connect_timeout, 60)
        self.assertEqual(client.constructor_timeout, 60)

    async def test_larger_mtu_still_bounds_write_chunk(self):
        device, client = await self.make_device(lambda c: setattr(c, "mtu_size", 517))
        await device.request("PING " + "A" * 900, "@AP PING_OK")
        self.assertEqual(max(map(len, client.writes)), 244)

    async def test_status_fragments_join_utf8_before_json_parse(self):
        payload = '@AP STATUS {"name":"青笺","connected":true}\n'.encode()
        parts = [payload[i:i+3] for i in range(0, len(payload), 3)]
        device, _ = await self.make_device(lambda c: setattr(c, "response", parts))
        self.assertEqual(await device.status(), {"name": "青笺", "connected": True})

    async def test_timeout_disconnects_and_does_not_retry(self):
        device, client = await self.make_device(lambda c: setattr(c, "response", None))
        with self.assertRaises(ble.BLETransportError):
            await device.request("PING", "@AP PING_OK", timeout=.02)
        self.assertFalse(client.is_connected)
        self.assertEqual(client.frames, [b"@AP PING\n"])
        with self.assertRaises(ble.BLEConnectionClosed):
            await device.request("PING", "@AP PING_OK")

    async def test_write_timeout_is_in_same_request_deadline(self):
        device, client = await self.make_device(lambda c: setattr(c, "write_hangs", True))
        with self.assertRaises(ble.BLETransportError):
            await device.request("PING", "@AP PING_OK", timeout=.02)
        self.assertFalse(client.is_connected)

    async def test_disconnect_rejects_pending_request(self):
        device, client = await self.make_device(lambda c: setattr(c, "response", None))
        pending = asyncio.create_task(device.request("PING", "@AP PING_OK"))
        await client.write_event.wait()
        await client.disconnect()
        with self.assertRaises(ble.BLEConnectionClosed):
            await pending
        self.assertFalse(device.is_connected)

    async def test_cancellation_disconnects(self):
        device, client = await self.make_device(lambda c: setattr(c, "response", None))
        pending = asyncio.create_task(device.request("PING", "@AP PING_OK"))
        await client.write_event.wait()
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertFalse(client.is_connected)

    async def test_write_error_disconnects(self):
        device, client = await self.make_device(lambda c: setattr(c, "write_exception", RuntimeError("radio error")))
        with self.assertRaises(ble.BLETransportError):
            await device.request("PING", "@AP PING_OK")
        self.assertFalse(client.is_connected)

    async def test_commands_are_serialized(self):
        def configure(client):
            client.reply_delay = .01
            client.response_handler = lambda frame: b"@AP " + frame[4:].strip() + b"_OK\n"
        device, client = await self.make_device(configure)
        self.assertEqual(await asyncio.gather(device.request("ONE", "@AP ONE_OK"), device.request("TWO", "@AP TWO_OK")), ["", ""])
        self.assertEqual(client.frames, [b"@AP ONE\n", b"@AP TWO\n"])

    async def test_old_connection_notifications_cannot_complete_new_request(self):
        device, old = await self.make_device(lambda c: setattr(c, "response", None))
        old_callback = old.callback
        await device.close()
        await device.connect()
        current = self.factory.clients[-1]
        pending = asyncio.create_task(device.request("PING", "@AP PING_OK"))
        await current.write_event.wait()
        old_callback(old.tx, bytearray(b"@AP PING_OK old\n"))
        await asyncio.sleep(.01)
        self.assertFalse(pending.done())
        current.emit(b"@AP PING_OK new\n")
        self.assertEqual(await pending, "new")

    async def test_application_error_does_not_force_disconnect(self):
        device, client = await self.make_device(lambda c: setattr(c, "response", b"@AP PING_ERROR\n"))
        with self.assertRaises(ble.BLERemoteCommandError):
            await device.request("PING", "@AP PING_OK")
        self.assertTrue(device.is_connected)
        client.response = b"@AP PING_OK\n"
        self.assertEqual(await device.request("PING", "@AP PING_OK"), "")

    async def test_response_flood_and_invalid_json_close_session(self):
        for reply in (b"noise\n" * 33, b"x" * 513, b"@AP STATUS []\n", b"@AP STATUS invalid\n"):
            device, client = await self.make_device(lambda c, reply=reply: setattr(c, "response", reply))
            with self.assertRaises(ble.BLEProtocolError):
                await device.status()
            self.assertFalse(client.is_connected)

    async def test_command_injection_and_size_rejected_before_write(self):
        device, client = await self.make_device()
        for command in ("PING\nSCREEN 0", "PING\r", "", "中文", "x" * 1024):
            with self.subTest(command=command[:20]), self.assertRaises(ble.BLEProtocolError):
                await device.request(command, "@AP PING_OK")
        self.assertEqual(client.writes, [])


class SyncBLETests(unittest.TestCase):
    def test_constructor_is_inert_and_session_uses_one_background_loop(self):
        loops, threads = [], []

        class LoopClient(FakeClient):
            async def connect(self):
                loops.append(asyncio.get_running_loop())
                threads.append(threading.get_ident())
                await super().connect()

            async def write_gatt_char(self, *args, **kwargs):
                loops.append(asyncio.get_running_loop())
                threads.append(threading.get_ident())
                await super().write_gatt_char(*args, **kwargs)

        made = []

        def factory(*args, **kwargs):
            client = LoopClient(*args, **kwargs)
            made.append(client)
            return client

        scanner = FakeScanner()
        device = ble.SyncPassportBLE(CHOSEN, scanner=scanner, client_factory=factory)
        self.assertIsNone(device._thread)
        self.assertEqual(scanner.calls, [])
        self.assertEqual(made, [])
        with device:
            self.assertEqual(device.request("PING", "@AP PING_OK"), "")
            self.assertEqual(device.request("PING", "@AP PING_OK"), "")
        self.assertEqual(len({id(loop) for loop in loops}), 1)
        self.assertEqual(len(set(threads)), 1)
        self.assertNotEqual(threads[0], threading.get_ident())
        self.assertFalse(device._thread.is_alive())
        self.assertFalse(made[0].is_connected)

    def test_timeout_stops_thread_without_retry(self):
        factory = Factory(lambda client: setattr(client, "response", None))
        device = ble.SyncPassportBLE(CHOSEN, scanner=FakeScanner(), client_factory=factory)
        device.connect()
        with self.assertRaises(ble.BLETransportError):
            device.request("PING", "@AP PING_OK", timeout=.02)
        self.assertFalse(device._thread.is_alive())
        self.assertEqual(factory.clients[0].frames, [b"@AP PING\n"])
        self.assertEqual(len(factory.clients), 1)

    def test_failed_selection_stops_background_loop(self):
        factory = Factory()
        device = ble.SyncPassportBLE(CHOSEN, scanner=FakeScanner([record(OTHER)]), client_factory=factory)
        with self.assertRaises(ble.BLETransportError):
            device.connect()
        self.assertFalse(device._thread.is_alive())
        self.assertEqual(factory.clients, [])


if __name__ == "__main__":
    unittest.main()
