"""Exercise editing, completion, history and jobs on a real controlling PTY."""
import errno
import os
from pathlib import Path
import pty
import re
import select
import signal
import tempfile
import time
import unittest

import test_hush_smoke


class HushInteractiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_hush_smoke.HushSmokeTests.setUpClass()
        cls.shell = test_hush_smoke.HushSmokeTests.shell

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        command = self.work / 'gaijin-completion-test'
        command.write_text('#!/bin/sh\nprintf "COMPLETION_OK\\n"\n')
        command.chmod(0o755)
        self.file = self.work / 'unique-completion-file'
        self.file.write_text('FILE_COMPLETION_OK\n')
        env = dict(os.environ, HOME=str(self.work), HISTFILE=str(self.work / 'history'),
                   HISTFILESIZE='128', TERM='vt100', PS1='hush-test# ',
                   PATH=f'{self.work}:/usr/bin:/bin')
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.execve(str(self.shell), [str(self.shell), '-i'], env)
        self.addCleanup(self.close_shell)
        self.read_prompt()

    def close_shell(self):
        try:
            os.kill(self.pid, signal.SIGHUP)
            for _ in range(20):
                if os.waitpid(self.pid, os.WNOHANG)[0]:
                    return
                time.sleep(0.01)
            os.kill(self.pid, signal.SIGKILL)
            os.waitpid(self.pid, 0)
        except (ProcessLookupError, ChildProcessError):
            pass
        finally:
            os.close(self.fd)

    def read_prompt(self, expected=None):
        output = bytearray()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self.fd], [], [], max(0, deadline - time.monotonic()))
            if not ready:
                break
            try:
                chunk = os.read(self.fd, 65536)
            except OSError as exc:
                if exc.errno != errno.EIO:
                    raise
                break
            if not chunk:
                break
            output += chunk
            if output.endswith(b'hush-test# ') and (expected is None or re.search(expected, output.decode(errors='replace'))):
                return output.decode(errors='replace')
        self.fail(f'shell did not return to its prompt: {output!r}')

    def command(self, data, expected=r'\n'):
        os.write(self.fd, data)
        return self.read_prompt(expected)

    def test_backspace_delete_cursor_completion_and_history(self):
        result = self.command(b"printf 'EDIT_%s\\n' ABXY\x08\x7fCD\n", r'\r?\nEDIT_ABCD\r?\n')
        self.assertRegex(result, r'\r?\nEDIT_ABCD\r?\n')
        result = self.command(b"printf 'CURSOR_%s\\n' AC\x1b[DB\n", r'\r?\nCURSOR_ABC\r?\n')
        self.assertRegex(result, r'\r?\nCURSOR_ABC\r?\n')
        self.assertRegex(self.command(b'gaijin-comple\t\n', r'\r?\nCOMPLETION_OK\r?\n'), r'\r?\nCOMPLETION_OK\r?\n')
        result = self.command(f'cat {self.work}/unique-comp'.encode() + b'\t\n', r'\r?\nFILE_COMPLETION_OK\r?\n')
        self.assertRegex(result, r'\r?\nFILE_COMPLETION_OK\r?\n')
        history = r'\r?\nHISTORY_OK\r?\n'
        self.command(b'echo HISTORY_OK\n', history)
        self.assertRegex(self.command(b'\x1b[A\n', history), history)
        self.assertIn('echo HISTORY_OK', (self.work / 'history').read_text())

    def test_ctrl_z_bg_fg_and_ctrl_c_preserve_the_shell(self):
        os.write(self.fd, b'sleep 30\n')
        time.sleep(0.1)
        os.write(self.fd, b'\x1a')
        self.read_prompt()
        self.assertIn('Stopped', self.command(b'jobs\n', 'Stopped'))
        self.command(b'bg\n')
        os.write(self.fd, b'fg\n')
        time.sleep(0.1)
        os.write(self.fd, b'\x03')
        self.read_prompt()
        self.assertRegex(self.command(b'echo SHELL_OK\n'), r'\r?\nSHELL_OK\r?\n')
