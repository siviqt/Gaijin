"""Exercise actual early-init hush script with simulated mounts (no host mounts)."""
import hashlib
import os
import re
from pathlib import Path
import subprocess
import tempfile
import unittest
import test_hush_smoke

ROOT = Path(__file__).resolve().parents[1]


class SDBootTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_hush_smoke.HushSmokeTests.setUpClass()
        cls.shell = test_hush_smoke.HushSmokeTests.shell

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.bin = self.work / "bin"
        self.bin.mkdir()
        self.log = self.work / "commands"
        self.env = dict(os.environ, PATH=f"{self.bin}:/usr/bin:/bin", LOG=str(self.log))
        for directory in ("sd/apps/com.github.siviq.gaijin/boot", "dev", "proc", "etc", "newroot/etc",
                          "newroot/sbin", "newroot/sd", "newroot/proc", "newroot/sys", "newroot/dev",
                          "newroot/run", "newroot/tmp", "newroot/root", "newroot/home",
                          "newroot/persist", "newroot/usr/local"):
            (self.work / directory).mkdir(parents=True, exist_ok=True)
        for name in ("gaijin-base", "gaijin-abi"):
            for parent in ("etc", "newroot/etc"):
                (self.work / parent / name).write_text("matching identity\n")
        init = self.work / "newroot/sbin/init"
        init.write_text("#!/bin/sh\n")
        init.chmod(0o755)
        (self.work / "dev/mmcblk0p1").touch()
        (self.work / "proc/cmdline").write_text("console=ttyUJ0\n")
        self.image = self.work / "sd/apps/com.github.siviq.gaijin/boot/rootfs.sqfs"
        self.image.write_bytes(b"simulated immutable base")
        self.checksum = self.image.with_name("rootfs.sqfs.sha256")
        self.checksum.write_text(hashlib.sha256(self.image.read_bytes()).hexdigest() + "\n")
        self.command("mount", '''echo "mount $*" >>"$LOG"
case "$*" in *"$FAIL_MOUNT"*) [ -z "$FAIL_MOUNT" ] || exit 1 ;; esac
exit 0
''')
        self.command("umount", 'echo "umount $*" >>"$LOG"\n')
        self.command("sleep", 'echo sleep >>"$LOG"\n')
        self.command("losetup", '''echo "losetup $*" >>"$LOG"
[ "$1" != -f ] || echo /dev/loop7
[ "$1" != -r ] || [ "$FAIL_LOOP" != 1 ]
''')
        self.command("switch_root", 'echo "switch_root $*" >>"$LOG"\nexit 0\n')
        script = (ROOT / "userspace/overlay/sbin/gaijin-sd-root").read_text()
        script = re.sub(r"(?<![A-Za-z0-9_/])(?:/newroot|/sd|/dev/mmcblk0|/etc/gaijin|/proc/cmdline)",
                        lambda match: str(self.work) + match[0], script)
        script = script.replace('[ -b "$sd_device" ]', '[ -f "$sd_device" ]')
        script = script.replace(f'[ -b {self.work}/dev/mmcblk0 ]', f'[ -f {self.work}/dev/mmcblk0 ]')
        script = script.replace("exec /sbin/switch_root", "exec switch_root")
        self.script = self.work / "boot"
        self.script.write_text(script)

    def command(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(0o755)

    def run_boot(self, **env):
        result = subprocess.run([str(self.shell), str(self.script)], env=dict(self.env, **env),
                                capture_output=True, text=True, timeout=5)
        self.commands = self.log.read_text() if self.log.exists() else ""
        return result

    def test_readonly_boot_moves_backing_mount_before_switch(self):
        result = self.run_boot()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("mount -t vfat -o ro,loud,nosuid,nodev,noexec", self.commands)
        self.assertIn("losetup -r /dev/loop7", self.commands)
        self.assertIn("mount -t squashfs -o ro,nodev,nosuid /dev/loop7", self.commands)
        self.assertIn(f"mount --move {self.work}/sd {self.work}/newroot/sd", self.commands)
        self.assertTrue(self.commands.rstrip().endswith(f"switch_root -c /dev/console {self.work}/newroot /sbin/init"))
        self.assertNotIn("umount", self.commands)
        self.assertNotIn("losetup -d", self.commands)

    def test_corrupt_image_falls_back_without_loop_or_transition(self):
        self.image.write_bytes(b"corrupt")
        result = self.run_boot()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checksum failed", result.stdout)
        self.assertIn("umount", self.commands)
        self.assertNotIn("losetup", self.commands)
        self.assertNotIn("switch_root", self.commands)

    def test_bad_base_identity_unmounts_before_detaching(self):
        (self.work / "newroot/etc/gaijin-base").write_text("old release")
        result = self.run_boot()
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(self.commands.index(f"umount {self.work}/newroot"),
                        self.commands.index("losetup -d /dev/loop7"))
        self.assertNotIn("switch_root", self.commands)

    def test_loop_setup_failure_does_not_detach_someone_elses_loop(self):
        result = self.run_boot(FAIL_LOOP="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("losetup -d", self.commands)
        self.assertIn("umount", self.commands)

    def test_move_failure_rolls_back_proc_and_fat(self):
        result = self.run_boot(FAIL_MOUNT=f"--move /sys {self.work}/newroot/sys")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"mount --move {self.work}/newroot/proc /proc", self.commands)
        self.assertIn(f"mount --move {self.work}/newroot/sd {self.work}/sd", self.commands)
        self.assertIn("losetup -d /dev/loop7", self.commands)
        self.assertNotIn("switch_root", self.commands)

    def test_missing_card_has_bounded_wait(self):
        (self.work / "dev/mmcblk0p1").unlink()
        result = self.run_boot()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.commands.count("sleep\n"), 10)
        self.assertNotIn("mount", self.commands)

    def test_delayed_partition_does_not_probe_its_raw_mbr_as_fat(self):
        (self.work / "dev/mmcblk0p1").unlink()
        (self.work / "dev/mmcblk0").touch()
        self.command("sleep", 'echo sleep >>"$LOG"\ntouch "$PARTITION"\n')
        result = self.run_boot(PARTITION=str(self.work / "dev/mmcblk0p1"))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.commands.count("sleep\n"), 1)
        self.assertIn(f"{self.work}/dev/mmcblk0p1 {self.work}/sd", self.commands)
        self.assertNotIn(f"{self.work}/dev/mmcblk0 {self.work}/sd", self.commands)

    def test_superfloppy_is_tried_only_after_partition_wait(self):
        (self.work / "dev/mmcblk0p1").unlink()
        (self.work / "dev/mmcblk0").touch()
        result = self.run_boot()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.commands.count("sleep\n"), 10)
        self.assertIn(f"{self.work}/dev/mmcblk0 {self.work}/sd", self.commands)

    def test_partition_without_assets_does_not_fall_back_to_raw_mbr(self):
        (self.work / "dev/mmcblk0").touch()
        self.image.unlink()
        result = self.run_boot()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(f"{self.work}/dev/mmcblk0 {self.work}/sd", self.commands)

    def test_explicit_rescue_does_not_touch_storage(self):
        (self.work / "proc/cmdline").write_text("gaijin.rescue console=ttyUJ0")
        result = self.run_boot()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.commands, "")
