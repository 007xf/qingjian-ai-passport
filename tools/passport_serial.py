"""macOS pySerial adapter for the ESP32-C3's native USB Serial/JTAG port.

The C3 interprets virtual RTS=1,DTR=0 as a chip-reset request. pySerial's
normal open() first applies DTR and then RTS separately; pre-setting both to
False does not suppress those writes and can cross that reset state after
macOS has asserted the modem lines. This subclass intentionally leaves modem
line state to the OS, while preventing hang-up-on-close.

Use only for the runtime @AP protocol on macOS native USB (303A:1001), never
for esptool flashing/reset workflows or hardware-flow-control UART devices.
read/write/flush/fileno/reset_input_buffer and close remain inherited pySerial
operations. Do not restore an old HUPCL setting immediately before close.

Hardware no-reset acceptance still requires real-device repeated-open tests;
PTY tests establish the absence of application-issued modem control writes.
"""
import sys
import termios

import serial


class MacNoResetSerial(serial.Serial):
    """pySerial compatibility with no application RTS/DTR transitions on macOS."""

    def __init__(self, *args, **kwargs):
        # IOBase can finalize an object whose constructor rejected the platform
        # before SerialBase ran. Keep inherited close() safe in that path.
        self.is_open = False
        if sys.platform != "darwin":
            raise serial.SerialException("MacNoResetSerial is only for macOS native USB Serial/JTAG")
        super().__init__(*args, **kwargs)

    def _update_dtr_state(self):
        # Serial.open() and inherited dtr setters may call this. No TIOCMBIC,
        # TIOCMBIS or TIOCMSET is issued, even when the stored state is False.
        return None

    def _update_rts_state(self):
        return None

    def _reconfigure_port(self, force_update=False):
        # Hardware handshaking allows the driver to toggle the very control
        # lines that reset this fixed-function USB peripheral. B0 hangs up.
        if self._rtscts or self._dsrdtr or self._rs485_mode is not None:
            raise serial.SerialException("Hardware modem flow control is disabled for Passport USB")
        if self._baudrate == 0:
            raise serial.SerialException("B0 hang-up is disabled for Passport USB")
        super()._reconfigure_port(force_update=force_update)
        attributes = termios.tcgetattr(self.fd)
        no_hangup = attributes[2] & ~termios.HUPCL
        if attributes[2] != no_hangup:
            attributes[2] = no_hangup
            termios.tcsetattr(self.fd, termios.TCSANOW, attributes)
