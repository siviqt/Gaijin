"""M3 storage boundary, image format and NOMMU boot/cleanup regressions."""
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
import test_hush_smoke

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from create_state import create
from check_state import check_header


class StateImageTests(unittest.TestCase):
    def test_readonly_fsck_success_does_not_prove_clean_unmount(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory)/'userdata.ext2'
            create(image, 16)
            self.assertEqual(check_header(image), 16*1024*1024)
            with image.open('r+b') as stream:
                stream.seek(1024+58)
                stream.write(b'\0\0')
            # This is the actual failure mode: fsck finds no structural errors
            # but -n cannot persist the clean flag required by the mapper.
            result = subprocess.run(['/usr/sbin/e2fsck', '-fn', str(image)], capture_output=True)
            self.assertEqual(result.returncode, 0)
            with self.assertRaisesRegex(ValueError, 'clean flag'):
                check_header(image)
            result = subprocess.run(['/usr/sbin/e2fsck', '-fp', str(image)], capture_output=True)
            self.assertIn(result.returncode, (0, 1))
            self.assertEqual(check_header(image), 16*1024*1024)

    def test_mapper_validates_extents_and_rolls_back_failures(self):
        source = (ROOT / 'userspace/package/gaijin-state/state.c').read_text()
        fixture = (ROOT / 'tests/fixtures/host-state-map.c').read_text()
        # Preincluded headers stay outside the syscall macros; struct tags too.
        source = re.sub(r'^#(?:define _(?:GNU_SOURCE|FILE_OFFSET_BITS).*|include .*)\n', '', source, flags=re.M)
        # Function-like macros avoid rewriting struct stat tags.
        for name in ('stat', 'fstat', 'fstatfs', 'fstatvfs'):
            fixture = fixture.replace(f'#define {name} fake_{name}', f'#define {name}(...) fake_{name}(__VA_ARGS__)')
        with tempfile.TemporaryDirectory() as directory:
            c, binary = Path(directory)/'state.c', Path(directory)/'state'
            c.write_text(fixture.replace('/* STATE_SOURCE */', source))
            subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror', str(c), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True, capture_output=True, timeout=10)

    def test_offline_image_is_clean_and_existing_data_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory)/'userdata.ext2'
            create(image, 16)
            with image.open('rb') as stream:
                stream.seek(1024)
                superblock = stream.read(1024)
            self.assertEqual(image.stat().st_size, 16*1024*1024)
            self.assertEqual(superblock[56:60], b'\x53\xef\x01\x00')
            self.assertEqual(superblock[92:104], bytes(4)+b'\x02\0\0\0\x03\0\0\0')
            before = image.read_bytes()
            with self.assertRaises(ValueError):
                create(image, 32)
            self.assertEqual(image.read_bytes(), before)
            for size in (0, 15, 257):
                with self.assertRaises(ValueError):
                    create(Path(directory)/'new', size)
            marker = subprocess.check_output(['/sbin/debugfs', '-R', 'cat /state-version', str(image)], stderr=subprocess.DEVNULL)
            self.assertEqual(marker, b'gaijin-state-v1\n')
            info = subprocess.check_output(['/sbin/debugfs', '-R', 'stat /home/root', str(image)], text=True, stderr=subprocess.DEVNULL)
            self.assertRegex(info, r'User:\s+0\s+Group:\s+0')


class StateBootTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_hush_smoke.HushSmokeTests.setUpClass()
        cls.shell = test_hush_smoke.HushSmokeTests.shell

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        for name in ('bin','run','persist/home/root','persist/settings','sd/gaijin','proc'):
            (self.work/name).mkdir(parents=True, exist_ok=True)
        (self.work/'persist/state-version').write_text('gaijin-state-v1\n')
        (self.work/'proc/cmdline').write_text('console=ttyUJ0')
        self.log = self.work/'commands'
        self.env = dict(os.environ, PATH=f'{self.work}/bin:/usr/bin:/bin', LOG=str(self.log), GAIJIN_POWER_TRACE="0")
        self.command('gaijin-state-map', '''echo "map $*" >>"$LOG"
if [ "$1" = flush ]; then
    [ "$FAIL_SYNC" != 1 ] || exit 1
elif [ "$1" = verify-clean ]; then
    [ "$FAIL_VERIFY" != 1 ] || exit 1
else
    [ "$FAIL_MAP" != 1 ] || exit 1
    [ "$1" = stop ] || echo /dev/dm-3
fi
''')
        self.command('mount', '''echo "mount $*" >>"$LOG"
case "$*" in *"$FAIL_MOUNT"*) [ -z "$FAIL_MOUNT" ] || exit 1 ;; esac
''')
        self.command('umount', '''echo "umount $*" >>"$LOG"
case "$*" in *"$FAIL_UMOUNT"*) [ -z "$FAIL_UMOUNT" ] || exit 1 ;; esac
''')
        self.command('sync', 'echo sync >>"$LOG"\n[ "$FAIL_SYNC" != 1 ]\n')
        script=(ROOT/'userspace/overlay/sbin/gaijin-persist').read_text().replace('/proc/cmdline', str(self.work/'proc/cmdline'))
        script=re.sub(r'(?<![A-Za-z0-9_/])/(?:run|sd|persist|proc/cmdline|root|home)(?=[/" ;]|$)', lambda m: str(self.work)+m[0], script)
        self.script=self.work/'persist-script'
        self.script.write_text(script)

    def command(self,name,body):
        p=self.work/'bin'/name; p.write_text('#!/bin/sh\n'+body); p.chmod(0o755)

    def run_script(self, action='start', **env):
        result=subprocess.run([str(self.shell),str(self.script),action], env=dict(self.env, **env), text=True, capture_output=True, timeout=5)
        self.commands=self.log.read_text() if self.log.exists() else ''
        return result

    def test_mounts_persistent_paths_and_allowlisted_setting(self):
        (self.work/'persist/settings/prompt').write_text('mydevice\n')
        result=self.run_script()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('rw,noatime,nosuid,nodev,errors=remount-ro',self.commands)
        self.assertIn(f'bind {self.work}/persist/home {self.work}/home',self.commands)
        self.assertTrue((self.work/'run/gaijin-persist').exists())
        self.assertEqual((self.work/'run/gaijin-prompt').read_text(),'mydevice\n')
        result=self.run_script('stop')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertFalse((self.work/'run/gaijin-persist').exists())
        self.assertLess(self.commands.index('map flush\n'),self.commands.index('umount'))
        self.assertTrue(self.commands.rstrip().endswith('map stop'))

    def test_status_reports_active_ram_and_partial_cleanup_without_writes(self):
        result = self.run_script('status')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('inactive', result.stdout)
        self.assertEqual(self.commands, '')
        (self.work/'sd/gaijin/clean-start').touch()
        self.assertIn('clean-start marker', self.run_script('status').stdout)
        self.assertEqual(self.commands, '')
        (self.work/'sd/gaijin/clean-start').unlink()
        self.assertEqual(self.run_script().returncode, 0)
        self.log.unlink()
        result = self.run_script('status')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('active on /dev/dm-3', result.stdout)
        self.assertEqual(self.commands, '')
        (self.work/'run/gaijin-state-root').unlink()
        result = self.run_script('status')
        self.assertEqual(result.returncode, 1)
        self.assertIn('cleanup incomplete', result.stdout)
        self.assertEqual(self.commands, '')

    def test_clean_modes_do_not_map_or_write(self):
        for flag in ('gaijin.clean','gaijin.rescue'):
            (self.work/'proc/cmdline').write_text(flag)
            self.assertNotEqual(self.run_script().returncode,0)
            self.assertEqual(self.commands,'')
        (self.work/'proc/cmdline').write_text('')
        (self.work/'sd/gaijin/clean-start').touch()
        self.assertNotEqual(self.run_script().returncode,0)
        self.assertEqual(self.commands,'')

    def test_unavailable_mapper_and_bad_layout_keep_base_usable(self):
        self.assertNotEqual(self.run_script(FAIL_MAP='1').returncode,0)
        self.assertNotIn('mount ',self.commands)
        (self.work/'persist/state-version').write_text('incompatible')
        self.assertNotEqual(self.run_script().returncode,0)
        self.assertNotIn('bind ',self.commands)
        self.assertTrue(self.commands.rstrip().endswith('map stop'))
        self.assertFalse((self.work/'run/gaijin-persist').exists())

    def test_each_mount_failure_unwinds_without_formatting(self):
        for failure in ('-t ext2',f'bind {self.work}/persist/home {self.work}/home',f'bind {self.work}/persist/home/root {self.work}/root'):
            self.log.unlink(missing_ok=True)
            self.assertNotEqual(self.run_script(FAIL_MOUNT=failure).returncode,0)
            self.assertTrue(self.commands.rstrip().endswith('map stop'))
            self.assertFalse((self.work/'run/gaijin-persist').exists())

    def test_setting_is_data_and_never_sourced(self):
        for prompt in ('$(touch /tmp/should-never-run)', 'a'*33, '../bad', ''):
            (self.work/'persist/settings/prompt').write_text(prompt)
            result=self.run_script()
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertFalse((self.work/'run/gaijin-prompt').exists())
            self.assertEqual(self.run_script('stop').returncode,0)

    def test_syncfs_failure_does_not_unmount(self):
        self.assertEqual(self.run_script().returncode,0)
        self.assertNotEqual(self.run_script('stop',FAIL_SYNC='1').returncode,0)
        self.assertNotIn('umount',self.commands)
        self.assertTrue((self.work/'run/gaijin-persist').exists())
        self.assertIn('syncfs '+str(self.work/'persist')+' begin', self.run_script('stop',FAIL_SYNC='1').stderr)
        self.assertNotIn('map stop', self.commands)

    def test_cleanup_is_retryable_after_each_unmount_failure(self):
        for target in ('root','home','persist'):
            with self.subTest(target=target):
                self.assertEqual(self.run_script().returncode,0)
                self.log.unlink()
                self.assertNotEqual(self.run_script('stop',FAIL_UMOUNT=str(self.work/target)).returncode,0)
                self.assertNotIn('map stop',self.commands)
                self.assertTrue((self.work/'run/gaijin-state-map').exists())
                self.assertEqual(self.run_script('stop').returncode,0)
                self.assertFalse((self.work/'run/gaijin-state-map').exists())

    def test_map_removal_failure_is_retryable_after_unmount(self):
        self.assertEqual(self.run_script().returncode,0)
        self.assertNotEqual(self.run_script('stop',FAIL_MAP='1').returncode,0)
        self.assertTrue((self.work/'run/gaijin-state-map').exists())
        self.assertFalse((self.work/'run/gaijin-state-mounted').exists())
        self.assertEqual(self.run_script('stop').returncode,0)
        self.assertFalse((self.work/'run/gaijin-state-map').exists())

    def test_dirty_raw_header_after_unmount_blocks_map_removal_and_is_retryable(self):
        self.assertEqual(self.run_script().returncode, 0)
        result = self.run_script('stop', FAIL_VERIFY='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('map verify-clean', self.commands)
        self.assertNotIn('map stop', self.commands)
        self.assertTrue((self.work/'run/gaijin-state-map').exists())
        self.assertFalse((self.work/'run/gaijin-state-mounted').exists())
        self.assertEqual(self.run_script('stop').returncode, 0)
        self.assertFalse((self.work/'run/gaijin-state-map').exists())

    def test_same_boot_stop_and_restart_restore_persistent_mounts(self):
        (self.work/'persist/settings/prompt').write_text('restart-ok\n')
        for _ in range(3):
            self.assertEqual(self.run_script().returncode,0)
            self.assertEqual((self.work/'run/gaijin-prompt').read_text(),'restart-ok\n')
            self.assertEqual(self.run_script('stop').returncode,0)
            self.assertFalse((self.work/'run/gaijin-state-map').exists())

    def test_login_prompt_uses_only_builtins_under_nommu_hush(self):
        profile=(ROOT/'userspace/overlay/etc/profile').read_text()
        profile=profile.replace('/run/gaijin-prompt', str(self.work/'run/gaijin-prompt'))
        profile=profile.replace('cd /root', 'cd '+str(self.work))
        # Disable command lookup completely; prompt loading must not spawn cat
        # or re-exec hush for a command substitution on the fragmented device.
        profile=profile.replace('export PATH=/usr/bin:/usr/sbin:/bin:/sbin', "export PATH=''")
        profile_path=self.work/'profile'
        profile_path.write_text(profile)
        marker=self.work/'run/gaijin-prompt'
        for value, expected in (('m3proof\n','m3proof# '), ('bad/label\n','gaijin# '), ('x'*33+'\n','gaijin# '), ('','gaijin# ')):
            marker.write_text(value)
            result=subprocess.run([str(self.shell), '-c', '. "'+str(profile_path)+'"; printf "%s" "$PS1"'],
                                  env=self.env,text=True,capture_output=True,timeout=5)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout,expected)
            self.assertEqual(result.stderr,'')
