"""Only local pseudo-terminals are opened. Never enumerates or opens USB ports."""
import errno
import importlib.util
import os
from pathlib import Path
import pty
import select
import sys
import termios
import unittest
from unittest import mock

try:
    import serial
    import serial.serialposix
except ImportError:
    serial = None

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == "darwin" and serial is not None,
                     "macOS and pyserial are required for the native-USB adapter tests")
class MacNoResetSerialTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("passport_serial_under_test", ROOT / "tools/passport_serial.py")
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.master, self.slave = pty.openpty()
        self.path = os.ttyname(self.slave)
        self.port = None
        self.modem_calls = []
        original_ioctl = serial.serialposix.fcntl.ioctl
        forbidden = {termios.TIOCMBIC, termios.TIOCMBIS, termios.TIOCMSET}

        def audit_ioctl(fd, request, *args, **kwargs):
            if request in forbidden:
                self.modem_calls.append(request)
                # Simulate a USB driver accepting the operation, but never
                # issue it to the PTY. Raising ENOTTY here would make pySerial
                # skip its second write and hide the real-device sequence.
                return 0
            return original_ioctl(fd, request, *args, **kwargs)

        self.audit = mock.patch.object(serial.serialposix.fcntl, "ioctl", side_effect=audit_ioctl)
        self.audit.start()

    def tearDown(self):
        if self.port:
            self.port.close()
        self.audit.stop()
        os.close(self.master)
        os.close(self.slave)

    def open_passive(self):
        self.port = self.module.MacNoResetSerial(port=None, baudrate=115200,
                                               timeout=0.05, write_timeout=0.5)
        self.port.dtr = False
        self.port.rts = False
        self.port.port = self.path
        self.port.open()
        return self.port

    def set_hupcl(self):
        flags = termios.tcgetattr(self.slave)
        flags[2] |= termios.HUPCL
        termios.tcsetattr(self.slave, termios.TCSANOW, flags)
        self.assertTrue(termios.tcgetattr(self.slave)[2] & termios.HUPCL)

    def test_original_pyserial_false_states_still_issue_two_modem_writes(self):
        self.port = serial.Serial(port=None, baudrate=115200, timeout=0.05)
        self.port.dtr = False
        self.port.rts = False
        self.port.port = self.path
        self.port.open()
        self.assertEqual(self.modem_calls, [termios.TIOCMBIC, termios.TIOCMBIC])

    def test_open_close_never_issue_modem_control_and_leave_hupcl_clear(self):
        self.set_hupcl()
        port = self.open_passive()
        self.assertFalse(termios.tcgetattr(port.fileno())[2] & termios.HUPCL)
        port.close()
        self.assertFalse(termios.tcgetattr(self.slave)[2] & termios.HUPCL)
        self.assertEqual(self.modem_calls, [])

    def test_repeated_open_close_preserves_flag_without_extra_modem_writes(self):
        port = self.open_passive()
        for _ in range(10):
            port.close()
            port.open()
            self.assertFalse(termios.tcgetattr(port.fileno())[2] & termios.HUPCL)
        self.assertEqual(self.modem_calls, [])

    def test_read_write_flush_and_fd_remain_pyserial_compatible(self):
        port = self.open_passive()
        inbound = b"@AP STATUS {\"time_synced\":true}\n"
        os.write(self.master, inbound)
        self.assertEqual(port.read(len(inbound)), inbound)
        outbound = b"@AP STATUS\n"
        self.assertEqual(port.write(outbound), len(outbound))
        ready, _, _ = select.select([self.master], [], [], 1)
        self.assertTrue(ready)
        self.assertEqual(os.read(self.master, 1024), outbound)
        # Darwin PTY TIOCDRAIN can wait indefinitely without a real UART. Byte
        # I/O above is native; here verify that unchanged pySerial flush still
        # dispatches to tcdrain on the correct descriptor, without modem writes.
        with mock.patch.object(serial.serialposix.termios, "tcdrain") as drain:
            port.flush()
            drain.assert_called_once_with(port.fileno())
        self.assertEqual(port.fileno(), port.fd)
        self.assertEqual(self.modem_calls, [])

    def test_input_reset_remains_compatible(self):
        port = self.open_passive()
        os.write(self.master, b"old bytes")
        ready, _, _ = select.select([port.fileno()], [], [], 1)
        self.assertTrue(ready)
        port.reset_input_buffer()
        self.assertEqual(port.read(64), b"")
        self.assertEqual(self.modem_calls, [])

    def test_dtr_rts_setters_are_inert_even_after_open(self):
        port = self.open_passive()
        for value in (True, False, True):
            port.dtr = value
            port.rts = value
        self.assertEqual(self.modem_calls, [])

    def test_reconfiguration_clears_external_hupcl_without_modem_writes(self):
        port = self.open_passive()
        self.set_hupcl()
        port.baudrate = 115200
        self.assertFalse(termios.tcgetattr(port.fileno())[2] & termios.HUPCL)
        self.assertEqual(self.modem_calls, [])

    def test_hardware_flow_control_and_b0_are_rejected(self):
        for kwargs in ({"rtscts": True}, {"dsrdtr": True}, {"baudrate": 0}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(serial.SerialException):
                    self.module.MacNoResetSerial(self.path, **kwargs)
        self.assertEqual(self.modem_calls, [])

    def test_non_macos_use_is_rejected_before_open(self):
        with mock.patch.object(self.module.sys, "platform", "linux"):
            with mock.patch.object(serial.serialposix.os, "open") as opening:
                with self.assertRaises(serial.SerialException):
                    self.module.MacNoResetSerial(self.path)
                opening.assert_not_called()


if __name__ == "__main__":
    unittest.main()
