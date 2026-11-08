"""Host tests exercise the same parser/DTB code linked into the P4 loader."""
import ctypes as C
import hashlib
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from package_boot import package


class Asset(C.Structure):
    _fields_ = [("bytes", C.c_uint32), ("sha256", C.c_uint8 * 32)]


class Manifest(C.Structure):
    _fields_ = [("image", Asset), ("dtb", Asset), ("initrd", Asset)]


def image_header(file_bytes=128, memory_bytes=4096):
    header = bytearray(file_bytes)
    struct.pack_into("<QQQI", header, 8, 0, memory_bytes, 0, 2)
    header[48:60] = b"RISCV\0\0\0RSC\x05"
    return header


class BootTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.work = Path(cls.temp.name)
        sources = [ROOT / "main/boot_contract.c"]
        sources += [p for p in (ROOT / "components/libfdt").glob("*.c") if p.name != "fdt_overlay.c"]
        subprocess.run(["cc", "-shared", "-fPIC", "-Wall", "-Wextra", "-Werror", "-O1",
                        "-fsanitize=undefined", "-fno-sanitize-recover=all", "-I", str(ROOT / "main"),
                        "-I", str(ROOT / "components/libfdt"), "-o", str(cls.work / "boot.so"),
                        *map(str, sources)], check=True)
        cls.lib = C.CDLL(str(cls.work / "boot.so"))
        cls.lib.m0_parse_manifest.argtypes = [C.c_void_p, C.c_size_t, C.POINTER(Manifest)]
        cls.lib.m0_check_image.argtypes = [C.c_void_p, C.c_size_t, C.c_uint32, C.POINTER(C.c_uint32)]
        cls.lib.m0_prepare_dtb.argtypes = [C.c_void_p, C.c_size_t, C.c_uint32]
        cls.lib.m5_enable_display.argtypes = [C.c_void_p]
        subprocess.run(["cc", "-shared", "-fPIC", "-Wall", "-Wextra", "-Werror", "-O1",
                        "-DGAIJIN_USB_EARLYCON=1", "-I", str(ROOT / "main"),
                        "-I", str(ROOT / "components/libfdt"), "-o", str(cls.work / "boot-debug.so"),
                        *map(str, sources)], check=True)
        cls.debug_lib = C.CDLL(str(cls.work / "boot-debug.so"))
        cls.debug_lib.m0_prepare_dtb.argtypes = [C.c_void_p, C.c_size_t, C.c_uint32]
        cls.debug_lib.m5_enable_display.argtypes = [C.c_void_p]
        subprocess.run(["dtc", "-I", "dts", "-O", "dtb", "-o", str(cls.work / "tanmatsu.dtb"),
                        str(ROOT / "linux/tanmatsu.dts")], check=True)
        cls.dtb = (cls.work / "tanmatsu.dtb").read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def check_image(self, header, file_bytes=None):
        size = C.c_uint32()
        return self.lib.m0_check_image(bytes(header), len(header), file_bytes or len(header), C.byref(size)), size.value

    def test_image_footprint_and_format(self):
        self.assertEqual(self.check_image(image_header()), (0, 4096))
        for length in (0, 127, 0x800001, 0x100000000):
            self.assertEqual(self.check_image(image_header(memory_bytes=length))[0], -1)
        self.assertEqual(self.check_image(image_header(memory_bytes=0x800000))[0], 0)
        for offset in (8, 24, 32, 48, 56):
            broken = image_header()
            broken[offset] ^= 1
            self.assertEqual(self.check_image(broken)[0], -1)
        self.assertEqual(self.check_image(image_header()[:63])[0], -1)
        self.assertEqual(self.check_image(image_header(), 0x800001)[0], -1)

    def test_package_and_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            image = base / "Image"
            image.write_bytes(image_header())
            dtb = base / "input.dtb"
            dtb.write_bytes(self.dtb)
            for initrd in (None, base / "input.cpio"):
                if initrd:
                    initrd.write_bytes(b"070701" + bytes(128))
                package(image, dtb, initrd, base / "output")
                raw = (base / "output/manifest.bin").read_bytes()
                result = Manifest()
                self.assertEqual(self.lib.m0_parse_manifest(raw, len(raw), C.byref(result)), 0)
                self.assertEqual(result.image.bytes, 128)
                self.assertEqual(bytes(result.image.sha256), hashlib.sha256(image.read_bytes()).digest())
                self.assertEqual(result.initrd.bytes, 134 if initrd else 0)
                for offset in (0, 8, 12, 16, 20, 132, 136):
                    bad = bytearray(raw)
                    bad[offset] ^= 1
                    self.assertEqual(self.lib.m0_parse_manifest(bytes(bad), len(bad), C.byref(result)), -1)
                for bad in (raw[:-1], raw + b"\0"):
                    self.assertEqual(self.lib.m0_parse_manifest(bad, len(bad), C.byref(result)), -1)
            for offset, length in ((24, 0), (24, 0x800001), (60, 0x10000), (96, 0x1000001)):
                bad = bytearray(raw)
                struct.pack_into("<I", bad, offset, length)
                self.assertEqual(self.lib.m0_parse_manifest(bytes(bad), len(bad), C.byref(result)), -1)

    def test_dtb_patching_and_rejection(self):
        for initrd in (0, 1, 0x1000000):
            dtb = C.create_string_buffer(self.dtb, 0x10000)
            self.assertEqual(self.lib.m0_prepare_dtb(dtb, len(self.dtb), initrd), 0)
            output = self.work / "patched.dtb"
            output.write_bytes(dtb.raw)
            bootargs = subprocess.check_output(["fdtget", str(output), "/chosen", "bootargs"]).decode()
            self.assertNotIn("earlycon=", bootargs)
            self.assertIn("console=ttyUJ0,115200n8", bootargs)
            self.assertNotIn("console=tty0", bootargs)
            self.assertIn("rdinit=/init", bootargs)
            self.assertNotIn("maxcpus", bootargs)
            self.assertNotIn("keep_bootcon", bootargs)
            isa = subprocess.check_output(["fdtget", str(output), "/cpus/cpu@0", "riscv,isa"])
            self.assertEqual(isa.strip(), b"rv32imac")
            memory = subprocess.check_output(["fdtget", "-t", "x", str(output), "/memory@48000000", "reg"])
            self.assertEqual(memory.strip(), b"48000000 2000000")
            if initrd:
                end = subprocess.check_output(["fdtget", "-t", "x", str(output), "/chosen", "linux,initrd-end"])
                self.assertEqual(int(end.strip(), 16), 0x49000000 + initrd)
        for blob in (self.dtb[:-1], bytes(len(self.dtb)), self.dtb.replace(b"gaijin-v1", b"gaijin-v9")):
            dtb = C.create_string_buffer(blob, 0x10000)
            self.assertEqual(self.lib.m0_prepare_dtb(dtb, len(blob), 0), -1)
        dtb = C.create_string_buffer(self.dtb, 0x10000)
        self.assertEqual(self.lib.m0_prepare_dtb(dtb, len(self.dtb), 0x1000001), -1)

    def test_display_memory_is_reserved_and_enabled_only_after_setup(self):
        dtb = C.create_string_buffer(self.dtb, 0x10000)
        self.assertEqual(self.lib.m0_prepare_dtb(dtb, len(self.dtb), 0x1000000), 0)
        output = self.work / "display.dtb"
        output.write_bytes(dtb.raw)
        def get(node, prop):
            return subprocess.check_output(["fdtget", "-t", "x", str(output), node, prop]).strip()
        self.assertEqual(get("/reserved-memory/framebuffer@48e00000", "reg"), b"48e00000 bc000")
        self.assertNotIn(b"console=tty0", subprocess.check_output(
            ["fdtget", str(output), "/chosen", "bootargs"]))
        self.assertIn(b"disabled", subprocess.check_output(
            ["fdtget", str(output), "/soc/framebuffer@50081000", "status"]))
        self.assertEqual(self.lib.m5_enable_display(dtb), 0)
        output.write_bytes(dtb.raw)
        bootargs = subprocess.check_output(["fdtget", str(output), "/chosen", "bootargs"])
        self.assertTrue(bootargs.startswith(b"console=ttyUJ0,115200n8 console=tty0 "))
        self.assertIn(b"fbcon=rotate:1,font:10x18", bootargs)
        self.assertNotIn(b"earlycon=", bootargs)
        self.assertEqual(get("/chosen", "linux,initrd-end"), b"4a000000")
        self.assertEqual(subprocess.check_output(
            ["fdtget", str(output), "/soc/framebuffer@50081000", "status"]).strip(), b"okay")
        # The input DTB must leave framebuffer initialization to the loader.
        output.write_bytes(self.dtb)
        subprocess.run(["fdtput", "-t", "s", str(output),
                        "/soc/framebuffer@50081000", "status", "okay"], check=True)
        enabled = output.read_bytes()
        self.assertEqual(self.lib.m0_prepare_dtb(C.create_string_buffer(enabled, 0x10000), len(enabled), 0), -1)
        # Mutating the reserved range must fail before loader PSRAM writes.
        output.write_bytes(self.dtb)
        subprocess.run(["fdtput", "-t", "x", str(output),
                        "/reserved-memory/framebuffer@48e00000", "reg", "48e00000", "bb800"], check=True)
        broken = output.read_bytes()
        dtb = C.create_string_buffer(broken, 0x10000)
        self.assertEqual(self.lib.m0_prepare_dtb(dtb, len(broken), 0), -1)

    def test_built_kernel_set_when_available(self):
        boot = ROOT / "build/sd/apps/com.github.siviqt.gaijin/boot"
        if not (boot / "manifest.bin").exists():
            self.skipTest("make kernel has not run")
        raw = (boot / "manifest.bin").read_bytes()
        manifest = Manifest()
        self.assertEqual(self.lib.m0_parse_manifest(raw, len(raw), C.byref(manifest)), 0)
        for name, asset in (("Image", manifest.image), ("tanmatsu.dtb", manifest.dtb),
                            ("initramfs", manifest.initrd)):
            if asset.bytes:
                data = (boot / name).read_bytes()
                self.assertEqual(len(data), asset.bytes)
                self.assertEqual(hashlib.sha256(data).digest(), bytes(asset.sha256))
        with (boot / "Image").open("rb") as source:
            header = source.read(64)
        result, footprint = self.check_image(header, manifest.image.bytes)
        self.assertEqual(result, 0)
        self.assertLessEqual(footprint, 0x800000)
        blob = (boot / "tanmatsu.dtb").read_bytes()
        dtb = C.create_string_buffer(blob, 0x10000)
        self.assertEqual(self.lib.m0_prepare_dtb(dtb, len(blob), manifest.initrd.bytes), 0)

    def test_debug_earlycon_is_explicit_for_both_display_modes(self):
        dtb = C.create_string_buffer(self.dtb, 0x10000)
        self.assertEqual(self.debug_lib.m0_prepare_dtb(dtb, len(self.dtb), 0), 0)
        output = self.work / "debug.dtb"
        for display_enabled in (False, True):
            if display_enabled:
                self.assertEqual(self.debug_lib.m5_enable_display(dtb), 0)
            output.write_bytes(dtb.raw)
            args = subprocess.check_output(["fdtget", str(output), "/chosen", "bootargs"]).decode().strip()
            self.assertTrue(args.startswith("earlycon=esp32p4usbjtag,mmio32,0x500d2000 console=ttyUJ0,115200n8 "))
            self.assertEqual("console=tty0" in args.split(), display_enabled)

    def test_rejected_display_keeps_usb_recovery_console(self):
        dtb = C.create_string_buffer(self.dtb, 0x10000)
        self.assertEqual(self.lib.m0_prepare_dtb(dtb, len(self.dtb), 0), 0)
        output = self.work / "bad-display.dtb"
        output.write_bytes(dtb.raw)
        subprocess.run(["fdtput", "-t", "s", str(output), "/soc/framebuffer@50081000",
                        "compatible", "unsupported,panel"], check=True)
        broken = C.create_string_buffer(output.read_bytes(), 0x10000)
        self.assertEqual(self.lib.m5_enable_display(broken), -1)
        output.write_bytes(broken.raw)
        args = subprocess.check_output(["fdtget", str(output), "/chosen", "bootargs"]).decode()
        self.assertIn("console=ttyUJ0,115200n8", args)
        self.assertNotIn("console=tty0", args)
        self.assertEqual(subprocess.check_output(["fdtget", str(output),
                         "/soc/framebuffer@50081000", "status"]).strip(), b"disabled")


if __name__ == "__main__":
    unittest.main()
