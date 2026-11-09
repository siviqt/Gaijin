"""Drive the actual native setup UI through PTYs in an isolated filesystem."""
import fcntl
import os
from pathlib import Path
import pty
import select
import shlex
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SetupUI(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        for path in ('run','persist/settings','sd/gaijin','sbin',
                     'sys/bus/platform/devices/5010800c.power-controller'):
            (self.root/path).mkdir(parents=True,exist_ok=True)
        (self.root/'run/gaijin-home-state').write_text('dirty\n')
        (self.root/'sys/bus/platform/devices/5010800c.power-controller/home_reset_ready').write_text('1\n')
        (self.root/'sd/gaijin/userdata.ext2').write_bytes(b'original-data')
        event=shlex.quote(str(self.root/'power-event'))
        power=self.root/'sbin/gaijin-power'
        power.write_text('#!/bin/sh\nprintf "%s\\n" "$@" >'+event+'\n')
        power.chmod(0o755)
        harness='''#define _GNU_SOURCE
#include <unistd.h>
#include <sys/statfs.h>
#include <string.h>
static uid_t test_uid(void) {return 0;}
static int test_fs(int fd, struct statfs *s) {(void)fd;memset(s,0,sizeof(*s));s->f_type=0xef53;return 0;}
#define geteuid test_uid
#define fstatfs test_fs
'''
        source=self.root/'ui.c'
        source.write_text(harness+(ROOT/'userspace/package/gaijin-state/setup.c').read_text().replace('#define _GNU_SOURCE\n',''))
        self.binary=self.root/'gaijin-setup'
        subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',
                        '-DGAIJIN_FSROOT="'+str(self.root)+'"',str(source),'-o',str(self.binary)],check=True)

    def run_ui(self, commands, *args):
        master,slave=pty.openpty()
        process=subprocess.Popen([str(self.binary),*args],stdin=slave,stdout=slave,stderr=slave)
        os.close(slave)
        output=bytearray()
        try:
            os.write(master,commands.encode())
            until=time.monotonic()+5
            while time.monotonic()<until:
                ready,_,_=select.select([master],[],[],0.05)
                if ready:
                    try:
                        data=os.read(master,65536)
                    except OSError:
                        break
                    if not data: break
                    output.extend(data)
                elif process.poll() is not None: break
            process.wait(timeout=1)
        finally:
            if process.poll() is None: process.kill();process.wait()
            os.close(master)
        return process.returncode,output.decode(errors='replace')

    def active(self):
        (self.root/'run/gaijin-persist').write_text('/dev/dm-0\n')
        (self.root/'run/gaijin-home-state').write_text('clean\n')
        (self.root/'persist/settings/setup-pending').write_text('gaijin-setup-v1\n')

    def test_initial_wizard_saves_typed_preferences_and_commits_completion(self):
        self.active()
        result,output=self.run_ui('1\nmy-prompt\n2\n','--initial')
        self.assertEqual(result,0,output)
        self.assertEqual((self.root/'persist/settings/prompt').read_text(),'my-prompt\n')
        self.assertEqual((self.root/'run/gaijin-prompt').read_text(),'my-prompt\n')
        self.assertEqual((self.root/'persist/settings/setup-version').read_text(),'gaijin-setup-v1\n')
        self.assertFalse((self.root/'persist/settings/setup-pending').exists())
        self.assertFalse((self.root/'power-event').exists())

    def test_prompt_is_data_and_invalid_names_are_not_saved(self):
        self.active()
        result,output=self.run_ui('1\n$(touch nope)\n2\n')
        self.assertEqual(result,0,output)
        self.assertFalse((self.root/'persist/settings/prompt').exists())

    def test_cancel_and_incompatible_images_never_request_erase(self):
        result,output=self.run_ui('4\nno\n2\n')
        self.assertEqual(result,0,output)
        self.assertIn('Erase cancelled',output)
        self.assertFalse((self.root/'power-event').exists())
        (self.root/'run/gaijin-home-state').write_text('incompatible\n')
        result,output=self.run_ui('4\nERASE\n2\n')
        self.assertEqual(result,0,output)
        self.assertNotIn('4. Erase home',output)
        self.assertFalse((self.root/'power-event').exists())
        self.assertEqual((self.root/'sd/gaijin/userdata.ext2').read_bytes(),b'original-data')

    def test_exact_confirmation_executes_only_the_safe_power_controller(self):
        result,output=self.run_ui('4\nERASE\n')
        self.assertEqual(result,0,output)
        self.assertIn('permanently deletes',output)
        self.assertEqual((self.root/'power-event').read_text(),'reset-home\n--erase-confirmed\n')
        self.assertEqual((self.root/'sd/gaijin/userdata.ext2').read_bytes(),b'original-data')

    def test_second_console_cannot_open_setup(self):
        with (self.root/'run/gaijin-setup.lock').open('w') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            result,output=self.run_ui('4\nERASE\n')
        self.assertEqual(result,1,output)
        self.assertIn('another console',output)
        self.assertFalse((self.root/'power-event').exists())

    def test_fat_failure_explains_offline_card_check_without_offering_preferences(self):
        (self.root/'run/gaijin-home-state').write_text('fat-error\n')
        result,output=self.run_ui('3\n2\n')
        self.assertEqual(result,0,output)
        self.assertIn('home creation was blocked',output)
        self.assertIn('unmounted card on a host',output)
        self.assertNotIn('1. Set prompt',output)
        self.assertNotIn('4. Erase home',output)
        self.assertFalse((self.root/'power-event').exists())

    def test_initial_fallback_reports_the_storage_failure_without_waiting_for_input(self):
        (self.root/'run/gaijin-home-state').write_text('incomplete\n')
        result,output=self.run_ui('','--initial')
        self.assertEqual(result,0,output)
        self.assertIn('Home creation was interrupted',output)
        self.assertIn('using temporary RAM',output)
        self.assertFalse((self.root/'persist/settings/setup-version').exists())
