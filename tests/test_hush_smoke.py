"""Execute M1 smoke checks with the pinned BusyBox hush, using host fixtures."""
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BUSYBOX_SHA256 = "3311dff32e746499f4df0d5df04d7eb396382d7e108bb9250e7b519b837043a4"


class HushSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        archive = ROOT / "build/downloads/busybox/busybox-1.37.0.tar.bz2"
        if not archive.exists():
            raise unittest.SkipTest("pinned BusyBox archive unavailable; populate the userspace download cache")
        if hashlib.sha256(archive.read_bytes()).hexdigest() != BUSYBOX_SHA256:
            raise AssertionError("host hush source hash mismatch")
        output = ROOT / "build/host-hush"
        source = output / "busybox-1.37.0"
        profile = ROOT / "tests/fixtures/host-hush.config"
        fingerprint = hashlib.sha256(profile.read_bytes() + archive.read_bytes()).hexdigest()
        marker = output / ".gaijin-host-hush"
        binary = source / "busybox"
        cls.shell = source / "hush"
        output.mkdir(parents=True, exist_ok=True)
        if not source.exists():
            with tarfile.open(archive) as tar:
                tar.extractall(output, filter="data")
        # Check this even for the cached native binary.
        target_hush = ROOT / "build/userspace/build/busybox-1.37.0/shell/hush.c"
        if target_hush.exists() and target_hush.read_bytes() != (source / "shell/hush.c").read_bytes():
            raise AssertionError("native regression shell differs from target hush source")
        resolved = (source / ".config").read_text().splitlines() if (source / ".config").exists() else []
        required = [line for line in profile.read_text().splitlines() if line.startswith("CONFIG_")]
        if (binary.exists() and marker.exists() and marker.read_text() == fingerprint
                and all(line in resolved for line in required)):
            if not cls.shell.exists():
                cls.shell.symlink_to("busybox")
            return
        env = {key: value for key, value in os.environ.items() if key not in
               ("ARCH", "CROSS_COMPILE", "CC", "HOSTCC", "HOSTCXX", "CFLAGS", "CPPFLAGS", "LDFLAGS")}
        env["KCONFIG_ALLCONFIG"] = str(profile)
        make = ["make", "-C", str(source), "CROSS_COMPILE=", "CC=cc", "HOSTCC=cc", "HOSTCXX=c++"]
        with (output / "build.log").open("w") as log:
            subprocess.run([*make, "allnoconfig"], env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=60)
            config_path = source / ".config"
            config = config_path.read_text()
            for setting in required:
                name = setting.split("=", 1)[0]
                config = re.sub(rf"^(?:# )?{name}(?:=.*| is not set)$", setting, config, flags=re.M)
            config_path.write_text(config)
            subprocess.run([*make, "oldconfig"], input="\n" * 2000, text=True,
                           env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=60)
            resolved = config_path.read_text().splitlines()
            if not all(line in resolved for line in required):
                raise AssertionError("native hush configuration dropped a required feature")
            subprocess.run([*make, "-j4"], env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
        marker.write_text(fingerprint)
        if not cls.shell.exists():
            cls.shell.symlink_to("busybox")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.bin = self.work / "bin"
        self.bin.mkdir()
        for name in ("uname", "stty", "free"):
            self.command(name, 'exit 0\n')
        self.count = self.work / "probe-count"
        self.probe = self.command("abi-probe", 'echo launch >>"$PROBE_COUNT"\n'
                                  'if [ -n "${PROBE_FAIL_AT:-}" ]; then\n'
                                  '    n=$(wc -l <"$PROBE_COUNT")\n'
                                  '    [ "$n" -ne "$PROBE_FAIL_AT" ] || exit 7\n'
                                  'fi\nexit 0\n')
        self.env = dict(os.environ, PATH=f"{self.bin}:/usr/bin:/bin", PROBE_COUNT=str(self.count))
        script = (ROOT / "userspace/overlay/usr/bin/m1-smoke").read_text()
        script = script.replace("/usr/bin/gaijin-abi-check", str(self.probe))
        for name in ("cmdline", "cpuinfo", "meminfo"):
            fixture = self.work / name
            fixture.write_text("host test fixture\n")
            script = script.replace(f"/proc/{name}", str(fixture))
        script = script.replace("/tmp/m1-smoke", str(self.work / "ram-file"))
        self.script = self.work / "m1-smoke"
        self.script.write_text(script)

    def command(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(0o755)
        return path

    def run_smoke(self, **extra_env):
        return subprocess.run([str(self.shell), str(self.script)],
                              env=dict(self.env, **extra_env), capture_output=True, text=True, timeout=15)

    def test_smoke_completes_under_hush(self):
        result = self.run_smoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("M1 smoke PASS", result.stdout)
        self.assertIn("pipeline-ok", result.stdout)
        self.assertIn("tmpfs-ok", result.stdout)
        self.assertEqual(len(self.count.read_text().splitlines()), 21)
        self.assertFalse((self.work / "ram-file").exists())

    def test_abi_failure_stops_without_reporting_pass(self):
        for failure_at in (1, 4):
            with self.subTest(failure_at=failure_at):
                self.count.unlink(missing_ok=True)
                result = self.run_smoke(PROBE_FAIL_AT=str(failure_at))
                self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
                self.assertNotIn("M1 smoke PASS", result.stdout)
                self.assertEqual(len(self.count.read_text().splitlines()), failure_at)

    def display_fixture(self):
        fb = self.work / "fb0"
        device = fb / "device"
        device.mkdir(parents=True, exist_ok=True)
        for name, value in {"name": "tanmatsu", "virtual_size": "480,800", "bits_per_pixel": "16"}.items():
            (fb / name).write_text(value + "\n")
        for name, value in {"frames": "10", "dma_errors": "0", "underruns": "0"}.items():
            (device / name).write_text(value + "\n")
        self.command("sleep", '''
case "$DISPLAY_FAILURE" in
    stalled) ;;
    read) rm "$DISPLAY_DEVICE/frames" ;;
    *) printf '20\\n' > "$DISPLAY_DEVICE/frames" ;;
esac
case "$DISPLAY_FAILURE" in
    dma) printf '1\\n' > "$DISPLAY_DEVICE/dma_errors" ;;
    underrun) printf '1\\n' > "$DISPLAY_DEVICE/underruns" ;;
esac
exit 0
''')
        script = (ROOT / "userspace/overlay/usr/bin/m5-display-test").read_text()
        script = script.replace("/sys/class/graphics/fb0", str(fb))
        script = script.replace("/dev/tty1", str(self.work / "tty1"))
        path = self.work / "m5-display-test"
        path.write_text(script)
        return path, device

    def run_display(self, failure=""):
        script, device = self.display_fixture()
        return subprocess.run([str(self.shell), str(script)], env=dict(
            self.env, DISPLAY_DEVICE=str(device), DISPLAY_FAILURE=failure),
            capture_output=True, text=True, timeout=15)

    def test_display_smoke_completes_under_pinned_nommu_hush(self):
        result = self.run_display()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("M5 refresh PASS: frames=10 -> 20", result.stdout)
        self.assertIn("M5 display load 99:", (self.work / "tty1").read_text())

    def test_display_smoke_rejects_stalls_dma_underruns_and_missing_counters(self):
        for failure in ("stalled", "dma", "underrun", "read"):
            with self.subTest(failure=failure):
                result = self.run_display(failure)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertNotIn("M5 refresh PASS", result.stdout)

    def test_display_smoke_propagates_terminal_setup_failure(self):
        self.command("stty", "exit 7\n")
        result = self.run_display()
        self.assertEqual(result.returncode, 7, result.stdout + result.stderr)
        self.assertNotIn("M5 refresh PASS", result.stdout)

    def test_hush_rejects_the_original_nounset_option(self):
        result = subprocess.run([str(self.shell), "-c", "set -eu"], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid option", result.stderr)


if __name__ == "__main__":
    unittest.main()
