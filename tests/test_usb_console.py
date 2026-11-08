"""Fault-inject the real optional early console without USB or a running clock."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class USBEarlyConsoleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        serial = ROOT / 'build/linux-6.18.35/drivers/tty/serial/serial_core.c'
        if not serial.exists():
            raise unittest.SkipTest('pinned Linux source is not prepared')
        source = serial.read_text()
        start = source.index('void uart_console_write(')
        uart_write = source[start:source.index('\n}', start) + 2]
        patch = (ROOT / 'linux/patches/0001-gaijin-esp32p4.patch').read_text()
        start = patch.index('+#define ESP32P4_USB_JTAG_EP1_REG')
        end = patch.index('+static int __init esp32p4_usb_jtag_earlycon_setup(', start)
        early_console = ''.join(line[1:] + '\n' for line in patch[start:end].splitlines()
                                if line.startswith('+'))
        prepared = (serial.parent / 'esp32_uart.c').read_text()
        for signature in ('static void esp32p4_usb_jtag_putchar(',
                          'static void esp32p4_usb_jtag_earlycon_write('):
            begin = early_console.index(signature)
            body = early_console[begin:early_console.index('\n}', begin) + 2]
            if body not in prepared:
                raise AssertionError('prepared kernel USB console differs from the current patch')
        fixture = (ROOT / 'tests/fixtures/usb-console-check.c').read_text()
        fixture = fixture.replace('/* ACTUAL_CONSOLE_CODE */', uart_write + '\n' + early_console)
        temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temp.cleanup)
        work = Path(temp.name)
        cfile = work / 'console.c'
        cfile.write_text(fixture)
        cls.binary = work / 'console'
        subprocess.run(['cc', '-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror',
                        '-o', str(cls.binary), str(cfile)], check=True)

    def test_full_fifo_does_not_poll_or_need_a_clock(self):
        subprocess.run([str(self.binary), 'full'], check=True, timeout=2)

    def test_connected_console_preserves_crlf(self):
        subprocess.run([str(self.binary), 'reading'], check=True, timeout=2)

    def test_later_attachment_resumes_output(self):
        subprocess.run([str(self.binary), 'reconnect'], check=True, timeout=2)

    def test_fifo_filling_mid_message_drops_remaining_output(self):
        subprocess.run([str(self.binary), 'fills'], check=True, timeout=2)
