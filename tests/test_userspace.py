"""M1 ABI/boot packaging checks; these do not emulate P4 USB hardware."""
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from package_current import (ABI, APP_ID, bootargs, check_application, check_current,
                             check_loader, check_userspace_paths, stage_launcher, stage_application)
from build_userspace import check_config
from verify_userspace import check_busybox_config, flat_info, read_newc, resolve_path, verify, verify_elf, verify_squashfs


def flat():
    header = bytearray(80)
    header[:4] = b"bFLT"
    struct.pack_into(">9I", header, 4, 4, 64, 68, 76, 1024, 65536, 76, 1, 1)
    return bytes(header)


def newc(entries):
    data = bytearray()
    for ino, (name, mode, payload, rdev) in enumerate(entries + [("TRAILER!!!", 0, b"", (0, 0))], 1):
        fields = [ino, mode, 0, 0, 1, 0, len(payload), 0, 0, *rdev, len(name.encode()) + 1, 0]
        data += b"070701" + ''.join(f'{v:08x}' for v in fields).encode() + name.encode() + b'\0'
        data += bytes((-len(data)) % 4)
        data += payload
        data += bytes((-len(data)) % 4)
    return bytes(data)


def minimal_root():
    entries = []
    entries.append(("etc/profile", stat.S_IFREG | 0o644, (ROOT / "userspace/overlay/etc/profile").read_bytes(), (0, 0)))
    entries.append(("etc/gaijin-base", stat.S_IFREG | 0o644,
                    (ROOT / "userspace/overlay/etc/gaijin-base").read_bytes(), (0, 0)))
    entries.append(("etc/gaijin-abi", stat.S_IFREG | 0o644,
                    (ROOT / "userspace/overlay/etc/gaijin-abi").read_bytes(), (0, 0)))
    for name in ("init", "etc/init.d/rcS", "usr/bin/m1-smoke", "sbin/gaijin-sd-root", "usr/bin/m2-sd-test", "sbin/gaijin-persist", "usr/bin/m3-state-test", "usr/bin/m5-display-test"):
        entries.append((name, stat.S_IFREG | 0o755, (ROOT / "userspace/overlay" / name).read_bytes(), (0, 0)))
    entries += [("bin/sh", stat.S_IFLNK | 0o777, b"busybox", (0, 0)),
                ("sbin/init", stat.S_IFLNK | 0o777, b"../bin/busybox", (0, 0)),
                ("dev/console", stat.S_IFCHR | 0o600, b"", (5, 1)),
                ("etc/inittab", stat.S_IFREG | 0o644,
                 (ROOT / "userspace/overlay/etc/inittab").read_bytes(), (0, 0))]
    for name in ("bin/busybox", "usr/bin/gaijin-abi-check", "usr/bin/m5-umode-probe", "usr/bin/m2-input-test", "sbin/gaijin-state-map", "sbin/gaijin-power"):
        entries.append((name, stat.S_IFREG | 0o755, flat(), (0, 0)))
    for name in ("shutdown", "poweroff", "halt"):
        entries.append(("sbin/" + name, stat.S_IFLNK | 0o777, b"gaijin-power", (0, 0)))
    return entries


def elf(flags=1, machine=243):
    data = bytearray(84)
    data[:7] = b"\x7fELF\x01\x01\x01"
    struct.pack_into("<HH", data, 16, 2, machine)
    struct.pack_into("<I", data, 28, 52)
    struct.pack_into("<I", data, 36, flags)
    struct.pack_into("<HHH", data, 40, 52, 32, 1)
    struct.pack_into("<I", data, 52, 1)
    return bytes(data)


class UserspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.archive = self.work / "rootfs.cpio.gz"
        self.archive.write_bytes(gzip.compress(newc(minimal_root())))
        self.elf = self.work / "app.gdb"
        self.elf.write_bytes(elf())

    def test_buildroot_abi_configuration(self):
        config = (ROOT / "userspace/configs/gaijin_defconfig").read_text()
        check_config(config)
        check_config('\n'.join(line for line in config.splitlines() if not line.startswith('#')))
        for invalid in (config.replace("BR2_RISCV_ABI_ILP32=y", "BR2_RISCV_ABI_ILP32D=y"),
                        config + '\nBR2_USE_MMU=y', config + '\nBR2_RISCV_ISA_RVF=y'):
            with self.assertRaises(ValueError):
                check_config(invalid)
        busybox = (ROOT / "userspace/busybox.fragment").read_text()
        check_busybox_config(busybox)
        with self.assertRaises(ValueError):
            check_busybox_config(busybox.replace("CONFIG_SH_IS_HUSH=y", "CONFIG_SH_IS_ASH=y"))

    def test_elf2flt_flags_survive_busybox_shell_quoting(self):
        # BusyBox wraps both variables in double quotes before invoking make.
        # Run that shell boundary instead of merely comparing Makefile text.
        script = self.work / "capture.py"
        script.write_text('import json, os, sys\nprint(json.dumps([os.environ["CFLAGS"], sys.argv[1]]))\n')
        makefile = self.work / "flags.mk"
        makefile.write_text(
            'comma := ,\nBR2_BINFMT_FLAT = y\nPKG = BUSYBOX\n'
            'BUSYBOX_FLAT_STACKSIZE = 65536\n'
            f'include {ROOT / "userspace/package/elf2flt-flags/elf2flt-flags.mk"}\n'
            'all:\n\tCFLAGS="-Os $(ELF2FLT_FLAGS)" '
            f'{sys.executable} {script} EXTRA_LDFLAGS="$(ELF2FLT_FLAGS) -static"\n')
        output = subprocess.check_output(["make", "--no-print-directory", "-s", "-f", str(makefile)], text=True)
        cflags, ldflags = json.loads(output)
        for flags in (cflags, ldflags):
            self.assertIn("-Wl,-elf2flt=-r -Wl,-elf2flt=-s65536", flags)
        self.assertTrue(ldflags.endswith(" -static"))
        # BusyBox's own append must not shrink the requested 64 KiB stack.
        nested = self.work / "busybox.mk"
        nested.write_text('FLTFLAGS += -s 16000\nall:\n\t@printf "%s" "$(FLTFLAGS)"\n')
        with makefile.open("a") as stream:
            stream.write(f'check-stack:\n\t@$(MAKE) --no-print-directory -s -f {nested} $(BUSYBOX_MAKE_OPTS)\n')
        stack = subprocess.check_output(["make", "--no-print-directory", "-s", "-f", str(makefile), "check-stack"], text=True)
        self.assertEqual(stack, "-s 65536")

    def test_flat_header_bounds(self):
        self.assertEqual(flat_info(flat())["stack"], 65536)
        for offset, value in ((4, 3), (8, 32), (12, 1025), (20, 5 * 1024 * 1024),
                              (24, 4096), (28, 100), (32, 100), (36, 4)):
            data = bytearray(flat())
            struct.pack_into(">I", data, offset, value)
            with self.assertRaises(ValueError):
                flat_info(data)
        for data in (b"\x7fELF" + bytes(80), flat()[:-1]):
            with self.assertRaises(ValueError):
                flat_info(data)

    def test_reject_wrong_elf_abi_and_isa(self):
        with patch("verify_userspace.subprocess.check_output", return_value='Tag_RISCV_arch: "rv32i2p1_m2p0_a2p1_c2p0"'):
            self.assertIn("rv32i", verify_elf(self.elf, "readelf")["isa"])
            for value in (elf(flags=3), elf(flags=5), elf(machine=62)):
                self.elf.write_bytes(value)
                with self.assertRaises(ValueError):
                    verify_elf(self.elf, "readelf")
            dynamic = bytearray(elf())
            struct.pack_into("<I", dynamic, 52, 3)
            self.elf.write_bytes(dynamic)
            with self.assertRaisesRegex(ValueError, "static M1 ABI"):
                verify_elf(self.elf, "readelf")
        self.elf.write_bytes(elf())
        with patch("verify_userspace.subprocess.check_output", return_value='Tag_RISCV_arch: "rv32i2p1_m2p0_a2p1_c2p0_zmmul1p0_zaamo1p0_zalrsc1p0"'):
            verify_elf(self.elf, "readelf")
        for attrs in ('Tag_RISCV_arch: "rv32i2p1_m2p0_f2p2"',
                      'Tag_RISCV_arch: "rv64i2p1"', 'Tag_RISCV_arch: "rv32i2p1_zmmul1p0"', ''):
            with patch("verify_userspace.subprocess.check_output", return_value=attrs):
                with self.assertRaises(ValueError):
                    verify_elf(self.elf, "readelf")

    def test_archive_boot_requirements(self):
        with patch("verify_userspace.subprocess.check_output", return_value='Tag_RISCV_arch: "rv32i2p1_m2p0_a2p1_c2p0"'):
            report = verify(self.archive, [self.elf] * 6, "readelf")
        self.assertEqual(report["abi"], ABI)
        self.assertFalse(report["hardware_validated"])
        for missing in ("init", "bin/sh", "dev/console", "etc/inittab", "bin/busybox"):
            self.archive.write_bytes(gzip.compress(newc([e for e in minimal_root() if e[0] != missing])))
            with self.assertRaises(ValueError):
                verify(self.archive, [self.elf] * 6, "readelf")
        for name in ("/init", "../init"):
            self.archive.write_bytes(gzip.compress(newc([(name, stat.S_IFREG, b"x", (0, 0))])))
            with self.assertRaisesRegex(ValueError, "unsafe"):
                read_newc(self.archive)

    def test_retired_branding_and_obsolete_abi_text_are_rejected(self):
        retired = bytes((84, 97, 110, 79, 83))
        for name, payload in (("etc/issue", retired + b" M1"),
                              (retired.decode().lower() + "-old", b"obsolete"),
                              ("etc/extra", b"Compatibility remains provisional until validation")):
            self.archive.write_bytes(gzip.compress(newc(minimal_root() + [
                (name, stat.S_IFREG | 0o644, payload, (0, 0))])))
            with self.assertRaises(ValueError):
                verify(self.archive, [self.elf] * 6, "readelf")

    def test_branding_changes_preserve_toolchain_fingerprint(self):
        from build_userspace import toolchain_fingerprint
        config = (ROOT / "userspace/configs/gaijin_defconfig").read_text()
        reference = toolchain_fingerprint(config)
        self.assertEqual(reference, toolchain_fingerprint(
            config.replace('HOSTNAME="gaijin"', 'HOSTNAME="renamed"') +
            '\nBR2_PACKAGE_EXAMPLE=y\n'))
        self.assertNotEqual(reference, toolchain_fingerprint(
            config.replace('BR2_RISCV_ABI_ILP32=y', 'BR2_RISCV_ABI_ILP32D=y')))

    def test_merged_usr_and_symlink_loops(self):
        merged = [("bin", stat.S_IFLNK | 0o777, b"usr/bin", (0, 0)),
                  ("sbin", stat.S_IFLNK | 0o777, b"usr/sbin", (0, 0))]
        for name, mode, data, rdev in minimal_root():
            if name.startswith(("bin/", "sbin/")):
                name = "usr/" + name
            merged.append((name, mode, data, rdev))
        self.archive.write_bytes(gzip.compress(newc(merged)))
        with patch("verify_userspace.subprocess.check_output", return_value='Tag_RISCV_arch: "rv32i2p1_m2p0_a2p1_c2p0"'):
            verify(self.archive, [self.elf] * 6, "readelf")
        entries = {"bin": dict(mode=stat.S_IFLNK, data=b"bin")}
        with self.assertRaisesRegex(ValueError, "loop"):
            resolve_path(entries, "bin/sh")
        self.archive.write_bytes(gzip.compress(newc(minimal_root())[:-50]))
        with self.assertRaises(ValueError):
            read_newc(self.archive)
        self.archive.write_bytes(gzip.compress(b"x" * 8192))
        with patch("verify_userspace.MAX_UNPACKED", 4096):
            with self.assertRaisesRegex(ValueError, "unpacked"):
                read_newc(self.archive)

    def test_direct_login_on_both_consoles_without_the_pulse(self):
        entries = [entry for entry in minimal_root() if entry[0] != "usr/bin/m5-display-test"]
        self.archive.write_bytes(gzip.compress(newc(entries)))
        with self.assertRaisesRegex(ValueError, "missing archive path"):
            verify(self.archive, [self.elf] * 6, "readelf")
        for action in (b"ttyUJ0::respawn:/sbin/gaijin-console usb\ntty1::respawn:-/bin/sh",
                       b"ttyUJ0::respawn:-/bin/sh"):
            with self.subTest(action=action):
                entries = [(name, mode, data.replace(b"ttyUJ0::respawn:-/bin/sh\ntty1::respawn:-/bin/sh", action), rdev)
                           if name == "etc/inittab" else (name, mode, data, rdev)
                           for name, mode, data, rdev in minimal_root()]
                self.archive.write_bytes(gzip.compress(newc(entries)))
                with self.assertRaisesRegex(ValueError, "direct login shells"):
                    verify(self.archive, [self.elf] * 6, "readelf")
        entries = minimal_root() + [("sbin/gaijin-boot-report", stat.S_IFREG | 0o755, b"#!/bin/sh\n", (0, 0))]
        self.archive.write_bytes(gzip.compress(newc(entries)))
        with self.assertRaisesRegex(ValueError, "retired backlight diagnostic"):
            verify(self.archive, [self.elf] * 6, "readelf")

    def test_launcher_and_complete_sd_package(self):
        loader = self.work / "linux-loader.bin"
        loader.write_bytes(bootargs().encode() + b"\0Gaijin M5 contract=1\0Linux owns DMA refresh\0LDO4 disabled, bypass off\0/sd/apps/com.github.siviqt.gaijin/boot/\0")
        output = self.work / APP_ID
        stage_launcher(loader, output)
        meta = json.loads((output / "metadata.json").read_text())
        self.assertEqual(meta["application"][0]["revision"], 32)
        self.assertTrue(meta['application'][0]['external_only'])
        self.assertEqual(meta["repository_url"], "https://github.com/siviqt/Gaijin")
        self.assertEqual(meta["icon"], {f"{size}x{size}": f"icon{size}.png" for size in (16, 32, 64)})
        self.assertEqual({path.name for path in output.rglob("*.png")},
                         {"icon16.png", "icon32.png", "icon64.png"})
        for size in (16, 32, 64):
            self.assertEqual((output / f"icon{size}.png").read_bytes()[16:24],
                             struct.pack(">II", size, size))
        self.assertFalse((output / "boot/manifest.bin").exists())
        kernel = self.work / "kernel"
        kernel.mkdir()
        (kernel / ".config").write_bytes((ROOT / "linux/tanmatsu_defconfig").read_bytes())
        (kernel / "arch/riscv/boot").mkdir(parents=True)
        image = bytearray(128)
        struct.pack_into("<QQQI", image, 8, 0, 4096, 0, 2)
        image[48:60] = b"RISCV\0\0\0RSC\x05"
        (kernel / "arch/riscv/boot/Image").write_bytes(image)
        subprocess.run(["dtc", "-I", "dts", "-O", "dtb", "-o", str(kernel / "tanmatsu.dtb"),
                        str(ROOT / "linux/tanmatsu.dts")], check=True)
        (self.work / "images").mkdir()
        (self.work / "images/rootfs.cpio.gz").write_bytes(self.archive.read_bytes())
        rootfs = self.work / "images/rootfs.squashfs"
        rootfs.write_bytes(b"test squashfs fixture")
        (self.work / "gaijin-userspace.json").write_text(json.dumps(dict(abi=ABI, shell="hush", binaries={"usr/bin/m2-input-test": {}, "sbin/gaijin-state-map": {}, "sbin/gaijin-power": {}}, archive_sha256=hashlib.sha256(self.archive.read_bytes()).hexdigest(), rootfs={"sha256": hashlib.sha256(rootfs.read_bytes()).hexdigest()})))
        state = self.work / "gaijin/userdata.ext2"
        state.parent.mkdir()
        state.write_bytes(b"existing user data outside the application")
        stage_application(loader, kernel, self.work, output)
        check_application(output)
        for asset in meta['application'][0]['assets']:
            self.assertEqual(asset['source_file'], asset['target_file'])
            self.assertTrue((output / asset['source_file']).is_file(), asset)
        self.assertEqual(state.read_bytes(), b"existing user data outside the application")
        self.assertFalse((output / "userdata.ext2").exists())
        manifest = (output / "boot/manifest.bin").read_bytes()
        self.assertEqual(len(manifest), 140)
        self.assertEqual(struct.unpack_from("<I", manifest, 96)[0], self.archive.stat().st_size)
        self.assertEqual(manifest[100:132], hashlib.sha256(self.archive.read_bytes()).digest())
        self.assertEqual((output / "boot/rootfs.sqfs").read_bytes(), rootfs.read_bytes())
        self.assertEqual((output / "boot/rootfs.sqfs.sha256").read_text().strip(),
                         hashlib.sha256(rootfs.read_bytes()).hexdigest())
        saved_dtb = (kernel / "tanmatsu.dtb").read_bytes()
        subprocess.run(["fdtput", "-d", str(kernel / "tanmatsu.dtb"),
                        "/soc/mmc@50083000", "dma-noncoherent"], check=True)
        with self.assertRaisesRegex(ValueError, "must declare dma-noncoherent"):
            stage_application(loader, kernel, self.work, output)
        (kernel / "tanmatsu.dtb").write_bytes(saved_dtb)
        subprocess.run(["fdtput", "-t", "s", str(kernel / "tanmatsu.dtb"),
                        "/soc/mmc@50083000", "status", "disabled"], check=True)
        with self.assertRaisesRegex(ValueError, "SD host must be enabled"):
            stage_application(loader, kernel, self.work, output)
        (kernel / "tanmatsu.dtb").write_bytes(saved_dtb)
        saved_rootfs = rootfs.read_bytes()
        rootfs.write_bytes(b"changed image")
        with self.assertRaisesRegex(ValueError, "stale SD rootfs"):
            stage_application(loader, kernel, self.work, output)
        rootfs.write_bytes(saved_rootfs)
        saved_config = (kernel / ".config").read_text()
        for missing in ("CONFIG_EXT2_FS=y", "CONFIG_BLK_DEV_DM=y"):
            (kernel / ".config").write_text(saved_config.replace(missing, ""))
            with self.assertRaisesRegex(ValueError, "kernel missing"):
                stage_application(loader, kernel, self.work, output)
        (kernel / ".config").write_text(saved_config)
        (kernel / ".config").write_text(saved_config.replace("CONFIG_KEYBOARD_TANMATSU_MCU=y", ""))
        with self.assertRaisesRegex(ValueError, "required setting"):
            stage_application(loader, kernel, self.work, output)
        (kernel / ".config").write_text(saved_config)
        saved_report = (self.work / "gaijin-userspace.json").read_text()
        missing_input = json.loads(saved_report)
        missing_input["binaries"] = {}
        (self.work / "gaijin-userspace.json").write_text(json.dumps(missing_input))
        with self.assertRaisesRegex(ValueError, "stale userspace"):
            stage_application(loader, kernel, self.work, output)
        (self.work / "gaijin-userspace.json").write_text(saved_report)
        (self.work / "images/rootfs.cpio.gz").write_bytes(b"stale archive")
        with self.assertRaisesRegex(ValueError, "stale"):
            stage_application(loader, kernel, self.work, output)

    def test_application_directory_and_loader_share_the_published_identifier(self):
        self.assertEqual(APP_ID, "com.github.siviqt.gaijin")
        wrong_directory = self.work / "com.github.siviq.gaijin"
        with self.assertRaisesRegex(ValueError, "directory must be named"):
            check_application(wrong_directory)
        with self.assertRaisesRegex(ValueError, "directory must be named"):
            stage_application(self.work / "missing-loader", self.work, self.work, wrong_directory)
        loader = self.work / "linux-loader.bin"
        loader.write_bytes(bootargs().encode() + b"\0Gaijin M5 contract=1\0Linux owns DMA refresh\0"
                           b"LDO4 disabled, bypass off\0/sd/apps/com.github.siviq.gaijin/boot/\0")
        with self.assertRaisesRegex(ValueError, "loader is not current"):
            check_loader(loader)

    def test_userspace_rejects_the_previous_application_path(self):
        check_userspace_paths(self.archive)
        for script in ("sbin/gaijin-sd-root", "usr/bin/m2-sd-test"):
            entries = [(name, mode, payload.replace(b"com.github.siviqt.gaijin", b"com.github.siviq.gaijin")
                        if name == script else payload, rdev)
                       for name, mode, payload, rdev in minimal_root()]
            self.archive.write_bytes(gzip.compress(newc(entries)))
            with self.subTest(script=script), self.assertRaisesRegex(ValueError, "stale userspace application path"):
                check_userspace_paths(self.archive)

    def test_squashfs_memory_envelope_and_contents(self):
        image = self.work / "rootfs.sqfs"
        header = bytearray(96)
        header[:4] = b"hsqs"
        struct.pack_into("<I", header, 12, 32768)
        struct.pack_into("<H", header, 20, 1)
        struct.pack_into("<HH", header, 28, 4, 0)
        struct.pack_into("<Q", header, 40, 96)
        image.write_bytes(header)
        entries, _ = read_newc(self.archive)
        with patch("verify_userspace.subprocess.check_output",
                   side_effect=lambda command: entries[command[-1]]["data"]):
            report = verify_squashfs(image, self.archive, "unsquashfs")
            self.assertEqual(report["block_size"], 32768)
        with patch("verify_userspace.subprocess.check_output", return_value=b"wrong image"):
            with self.assertRaisesRegex(ValueError, "contents differ"):
                verify_squashfs(image, self.archive, "unsquashfs")
        for offset, value in ((12, 1048576), (20, 4), (40, 1000000)):
            bad = bytearray(header)
            struct.pack_into("<I", bad, offset, value)
            image.write_bytes(bad)
            with self.assertRaisesRegex(ValueError, "bounded SquashFS"):
                verify_squashfs(image, self.archive, "unsquashfs")

    def test_incompatible_loader_rejected_before_publication(self):
        loader = self.work / "invalid-loader.bin"
        loader.write_bytes(b"invalid loader without current boot contract\0")
        with self.assertRaisesRegex(ValueError, "loader is not current"):
            check_loader(loader)

    def test_current_set_rejects_modified_or_extra_files(self):
        source = ROOT / "build"
        if not (source / "CURRENT.json").exists():
            self.skipTest("current installation set has not been packaged")
        report = json.loads((source / "CURRENT.json").read_text())
        for name in [*report["files"], "CURRENT.json", "SHA256SUMS"]:
            destination = self.work / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, destination)
        check_current(self.work)
        loader = self.work / report["appfs_binary"]
        saved = loader.read_bytes()
        loader.write_bytes(b"wrong loader")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            check_current(self.work)
        loader.write_bytes(saved)
        (loader.parent / "obsolete.bin").write_bytes(b"old build")
        with self.assertRaisesRegex(ValueError, "extra files"):
            check_current(self.work)

    def test_built_kernel_configuration(self):
        config = ROOT / "build/kernel/.config"
        if not config.exists():
            self.skipTest("kernel has not been built")
        lines = config.read_text().splitlines()
        for setting in ("# CONFIG_FPU is not set", "# CONFIG_MMU is not set", "# CONFIG_SMP is not set",
                        "CONFIG_SERIAL_ESP32P4_USB_JTAG=y", "CONFIG_BINFMT_FLAT=y", "CONFIG_BINFMT_SCRIPT=y"):
            self.assertIn(setting, lines)

    def test_built_userspace_when_available(self):
        base = ROOT / "build/userspace"
        report_path = base / "gaijin-userspace.json"
        if not report_path.exists():
            self.skipTest("userspace has not been verified")
        saved = json.loads(report_path.read_text())
        elfs = []
        for item in saved["elf_intermediates"]:
            matches = list((ROOT / 'build/userspace/build').rglob(Path(item['path']).name))
            self.assertEqual(len(matches), 1, item['path'])
            elfs.append(matches[0])
        report = verify(base / "images/rootfs.cpio.gz", elfs,
                        str(base / "host/bin/riscv32-buildroot-linux-uclibc-readelf"),
                        elfs[0].parent / ".config")
        report["rootfs"] = verify_squashfs(base / "images/rootfs.squashfs",
                                          base / "images/rootfs.cpio.gz",
                                          base / "host/bin/unsquashfs")
        self.assertEqual(report, saved)
        self.assertEqual(report["shell"], "hush")


if __name__ == "__main__":
    unittest.main()
