"""Exercise the patched init terminal setup on a real PTY."""
import os
from pathlib import Path
import pty
import shutil
import subprocess
import tempfile
import termios
import time
import tty
import unittest
import test_hush_smoke

ROOT = Path(__file__).resolve().parents[1]


class ConsoleStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_hush_smoke.HushSmokeTests.setUpClass()
        shell = test_hush_smoke.HushSmokeTests.shell

        temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temp.cleanup)
        work = Path(temp.name)
        init = work / 'init/init.c'
        init.parent.mkdir()
        shutil.copyfile(shell.parent / 'init/init.c', init)
        patch = ROOT / 'userspace/patches/busybox/0001-init-disable-software-flow-control.patch'
        subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(patch)], cwd=work,
                       check=True, capture_output=True)
        previous = init.read_text()
        patch = ROOT / 'userspace/patches/busybox/0003-init-log-before-terminal-switch.patch'
        subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(patch)], cwd=work,
                       check=True, capture_output=True)
        source = init.read_text()
        start = source.index('static void set_sane_term(void)')
        end = source.index('\n}', start) + len('\n}')
        harness = work / 'termios.c'
        harness.write_text('#include <termios.h>\n#include <unistd.h>\n'
                           '#define tcsetattr_stdin_TCSANOW(t) tcsetattr(STDIN_FILENO, TCSANOW, (t))\n'
                           + source[start:end] + '\nint main(void) { set_sane_term(); return 0; }\n')
        cls.setup_terminal = work / 'termios'
        subprocess.run(['cc', '-Wall', '-Wextra', '-Werror', '-o', str(cls.setup_terminal),
                        str(harness)], check=True)

        def function(text, signature):
            start = text.index(signature)
            return text[start:text.index('\n}', start) + 2] + '\n'

        fixture = (ROOT / 'tests/fixtures/init-child-check.c').read_text()
        for name, text in (('current', source), ('previous', previous)):
            bodies = ''.join(function(text, signature) for signature in (
                'static void set_sane_term(void)', 'static int open_stdio_to_tty(',
                'static pid_t run(const struct init_action *a)'))
            cfile = work / (name + '.c')
            cfile.write_text(fixture.replace('/* ACTUAL_INIT_CODE */', bodies))
            binary = work / name
            subprocess.run(['cc', '-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror',
                            '-o', str(binary), str(cfile)], check=True)
            setattr(cls, name + '_child', binary)

    def test_init_disables_flow_control(self):
        master, slave = pty.openpty()
        try:
            attrs = termios.tcgetattr(slave)
            attrs[0] |= termios.IXON | termios.IXOFF
            termios.tcsetattr(slave, termios.TCSANOW, attrs)
            subprocess.run([str(self.setup_terminal)], stdin=slave, check=True, timeout=5)
            self.assertEqual(termios.tcgetattr(slave)[0], termios.ICRNL)
        finally:
            os.close(slave)
            os.close(master)

    def child_with_full_usb(self, binary, timeout):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        tty.setraw(slave)
        os.set_blocking(slave, False)
        # Let the PTY worker settle between fills so the later log cannot fit
        # in a transient gap while nobody reads the simulated USB endpoint.
        total = 0
        for _ in range(3):
            while True:
                try:
                    total += os.write(slave, b'x' * 1024)
                except BlockingIOError:
                    break
            time.sleep(0.02)
        self.assertGreater(total, 4096)
        os.set_blocking(slave, True)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        marker = Path(temp.name) / 'exec'
        result = subprocess.run([str(binary), os.ttyname(slave), str(marker)],
                                capture_output=True, text=True, timeout=timeout)
        return result, marker

    def test_usb_child_reaches_exec_with_full_output_queue(self):
        result, marker = self.child_with_full_usb(self.current_child, timeout=2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('starting pid', result.stderr)
        self.assertEqual(marker.read_text(), 'exec\n')

    def test_negative_control_usb_log_blocks_before_exec(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            self.child_with_full_usb(self.previous_child, timeout=0.5)
