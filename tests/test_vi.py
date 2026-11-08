"""Exercise the shipped vi fix on a real PTY, including an unnamed buffer."""
import errno
import fcntl
import hashlib
import os
from pathlib import Path
import pty
import re
import select
import signal
import struct
import subprocess
import tarfile
import tempfile
import termios
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ViTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        archive = ROOT / 'build/downloads/busybox/busybox-1.37.0.tar.bz2'
        if not archive.exists():
            raise unittest.SkipTest('populate the pinned BusyBox download cache')
        assert hashlib.sha256(archive.read_bytes()).hexdigest() == '3311dff32e746499f4df0d5df04d7eb396382d7e108bb9250e7b519b837043a4'
        output = ROOT / 'build/host-vi'
        source = output / 'busybox-1.37.0'
        profile = ROOT / 'tests/fixtures/host-vi.config'
        patch = ROOT / 'userspace/patches/busybox/0002-vi-allow-unnamed-buffer.patch'
        stamp = output / '.gaijin-host-vi'
        fingerprint = hashlib.sha256(archive.read_bytes() + profile.read_bytes() + patch.read_bytes()).hexdigest()
        cls.binary = source / 'busybox'
        if cls.binary.exists() and stamp.exists() and stamp.read_text() == fingerprint:
            return
        output.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive) as tar:
            tar.extractall(output, filter='data')
        env = {key: value for key, value in os.environ.items() if key not in
               ('ARCH', 'CROSS_COMPILE', 'CC', 'HOSTCC', 'HOSTCXX', 'CFLAGS', 'CPPFLAGS', 'LDFLAGS')}
        env['KCONFIG_ALLCONFIG'] = str(profile)
        make = ['make', '-C', str(source), 'CROSS_COMPILE=', 'CC=cc', 'HOSTCC=cc']
        with (output / 'build.log').open('w') as log, patch.open('rb') as change:
            subprocess.run(['patch', '-p1'], cwd=source, stdin=change, stdout=log, stderr=subprocess.STDOUT, check=True)
            subprocess.run([*make, 'allnoconfig'], env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=60)
            config = source / '.config'
            text = config.read_text()
            for setting in profile.read_text().splitlines():
                if setting.startswith('CONFIG_'):
                    name = setting.split('=', 1)[0]
                    text = re.sub(rf'^(?:# )?{name}(?:=.*| is not set)$', setting, text, flags=re.M)
            config.write_text(text)
            subprocess.run([*make, 'oldconfig'], input='\n' * 2000, text=True,
                           env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=60)
            subprocess.run([*make, '-j4'], env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
        resolved = (source / '.config').read_text().splitlines()
        assert all(line in resolved for line in profile.read_text().splitlines() if line.startswith('CONFIG_'))
        stamp.write_text(fingerprint)

    def session(self, filename, rows, commands):
        pid, fd = pty.fork()
        if pid == 0:
            env = dict(os.environ, TERM='vt100', UBSAN_OPTIONS='halt_on_error=1:color=never')
            env.pop('EXINIT', None)
            args = [str(self.binary), 'vi'] + ([str(filename)] if filename else [])
            os.execve(str(self.binary), args, env)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, 80, 0, 0))
        output = bytearray()
        status = None
        start = time.monotonic()
        commands = list(commands)
        try:
            while time.monotonic() - start < 5:
                if commands and time.monotonic() - start >= commands[0][0]:
                    os.write(fd, commands.pop(0)[1])
                ready, _, _ = select.select([fd], [], [], .02)
                if ready:
                    try:
                        data = os.read(fd, 65536)
                        if not data:
                            break
                        output.extend(data)
                    except OSError as exc:
                        if exc.errno != errno.EIO:
                            raise
                        break
                got, observed = os.waitpid(pid, os.WNOHANG)
                if got:
                    status = observed
                    break
            if status is None:
                got, status = os.waitpid(pid, os.WNOHANG)
                if not got:
                    os.kill(pid, signal.SIGKILL)
                    _, status = os.waitpid(pid, 0)
        finally:
            os.close(fd)
        self.assertEqual(os.waitstatus_to_exitcode(status), 0, repr(output))
        self.assertNotIn(b'runtime error:', output)
        return bytes(output)

    def test_unnamed_and_named_edit_save_reopen_on_console_sizes(self):
        with tempfile.TemporaryDirectory() as directory:
            for rows in (24, 26):
                for named in (False, True):
                    with self.subTest(rows=rows, named=named):
                        path = Path(directory) / f'{rows}-{named}.txt'
                        commands = [(.2, b'iVI_OK'), (.4, b'\x1b'),
                                    (.7, f':w {path}\r'.encode()), (1., b':q!\r')]
                        self.session(path if named else None, rows, commands)
                        self.assertEqual(path.read_text(), 'VI_OK\n')
                        output = self.session(path, rows, [(.2, b':q!\r')])
                        self.assertIn(b'VI_OK', output)
