"""Exercise the patched init terminal setup on a real PTY."""
import os
from pathlib import Path
import pty
import shutil
import subprocess
import tempfile
import termios
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
