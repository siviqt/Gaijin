#!/usr/bin/env python3
"""Reject M1 archives/ELF intermediates outside the RV32 soft-float contract."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import stat
import struct
import subprocess

MAX_UNPACKED = 8 * 1024 * 1024
MAX_COMPRESSED = 4 * 1024 * 1024


def read_newc(archive):
    if archive.stat().st_size > MAX_COMPRESSED:
        raise ValueError("initramfs exceeds the 4 MiB compressed M1 build envelope")
    with gzip.open(archive, "rb") as stream:
        data = stream.read(MAX_UNPACKED + 1)
    if len(data) > MAX_UNPACKED:
        raise ValueError("initramfs exceeds the 8 MiB unpacked M1 build envelope")
    entries = {}
    offset = 0
    while offset + 110 <= len(data):
        header = data[offset:offset + 110]
        if header[:6] != b"070701":
            raise ValueError("expected newc cpio")
        fields = [int(header[6 + i * 8:14 + i * 8], 16) for i in range(13)]
        ino, mode, uid, gid, nlink, mtime, size, devmaj, devmin, rmaj, rmin, namesize, check = fields
        start = offset + 110
        if not namesize or start + namesize > len(data) or data[start + namesize - 1] != 0:
            raise ValueError("truncated cpio name")
        name = data[start:start + namesize - 1].decode()
        offset = (start + namesize + 3) & ~3
        end = offset + size
        if end > len(data):
            raise ValueError("truncated cpio payload")
        if name == "TRAILER!!!":
            if any(data[end:]):
                raise ValueError("unexpected data after cpio trailer")
            return entries, len(data)
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("unsafe cpio path")
        key = str(path)
        if key in entries:
            raise ValueError(f"duplicate cpio path: {key}")
        entries[key] = dict(mode=mode, data=data[offset:end], rdev=(rmaj, rmin),
                            inode=(devmaj, devmin, ino), nlink=nlink)
        offset = (end + 3) & ~3
    raise ValueError("missing cpio trailer")


def flat_info(data):
    if len(data) < 64 or data[:4] != b"bFLT":
        raise ValueError("expected a bFLT v4 executable")
    rev, entry, data_start, data_end, bss_end, stack, reloc, count, flags = struct.unpack_from(">9I", data, 4)
    if (rev != 4 or not 64 <= entry < data_start <= data_end <= bss_end or
            reloc < data_end or reloc + 4 * count != len(data) or flags & ~3 or
            bss_end + stack > 4 * 1024 * 1024 or stack < 16384):
        raise ValueError("invalid/oversized/compressed bFLT executable or insufficient stack")
    return dict(bytes=len(data), bss_end=bss_end, stack=stack, relocations=count, flags=flags)


def resolve_path(entries, name):
    """Follow both parent and final symlinks, including merged-/usr roots."""
    name = name.lstrip("/")
    for _ in range(40):
        parts = PurePosixPath(name).parts
        for index in range(len(parts)):
            prefix = "/".join(parts[:index + 1])
            item = entries.get(prefix, {})
            if stat.S_ISLNK(item.get("mode", 0)):
                target = item["data"].decode()
                parent = PurePosixPath(prefix).parent
                rewritten = parent / target / PurePosixPath(*parts[index + 1:])
                name = posixpath.normpath("/" + str(rewritten)).lstrip("/")
                break
        else:
            if name not in entries:
                raise ValueError(f"missing archive path: {name}")
            return name, entries[name]
    raise ValueError("archive symlink loop")


def verify_elf(path, readelf):
    data = path.read_bytes()
    if (len(data) < 52 or data[:7] != b"\x7fELF\x01\x01\x01" or
            struct.unpack_from("<H", data, 18)[0] != 243 or
            struct.unpack_from("<I", data, 36)[0] & ~1):
        raise ValueError(f"{path}: requires ELF32 LE RISC-V, soft-float ABI, optional RVC only")
    phoff = struct.unpack_from("<I", data, 28)[0]
    phsize, phcount = struct.unpack_from("<HH", data, 42)
    if phsize < 32 or phoff + phsize * phcount > len(data):
        raise ValueError(f"{path}: malformed ELF program headers")
    if any(struct.unpack_from("<I", data, phoff + phsize * i)[0] in (2, 3) for i in range(phcount)):
        raise ValueError(f"{path}: dynamic/interpreted ELF is outside the static M1 ABI")
    attrs = subprocess.check_output([readelf, "-A", str(path)], text=True)
    match = re.search(r'Tag_RISCV_arch:\s*"([^"]+)"', attrs)
    if not match or not match[1].startswith("rv32i"):
        raise ValueError(f"{path}: missing RV32 ISA attribute")
    extensions = {re.sub(r"[0-9].*", "", ext) for ext in match[1][4:].split("_")}
    # New binutils spells out subsets of the existing M and A extensions.
    # These do not add instructions beyond the rv32imac execution subset.
    implied = {"zmmul": "m", "zaamo": "a", "zalrsc": "a"}
    for name in extensions:
        if name not in ("i", "m", "a", "c", "zicsr", "zifencei") and implied.get(name) not in extensions:
            raise ValueError(f"{path}: unsupported ISA extension {name}")
    return dict(path=path.name, isa=match[1], sha256=hashlib.sha256(data).hexdigest())


def check_busybox_config(text):
    lines = text.splitlines()
    for name in ("CONFIG_STATIC", "CONFIG_NOMMU", "CONFIG_INIT", "CONFIG_FEATURE_USE_INITTAB",
                 "CONFIG_FEATURE_INIT_SCTTY", "CONFIG_SH_IS_HUSH", "CONFIG_HUSH",
                 "CONFIG_HUSH_INTERACTIVE", "CONFIG_HUSH_TRAP", "CONFIG_HUSH_LOOPS",
                 "CONFIG_HUSH_JOB", "CONFIG_HUSH_SAVEHISTORY", "CONFIG_FEATURE_EDITING",
                 "CONFIG_FEATURE_EDITING_SAVEHISTORY", "CONFIG_FEATURE_TAB_COMPLETION",
                 "CONFIG_FEATURE_REVERSE_SEARCH", "CONFIG_FEATURE_EDITING_FANCY_PROMPT",
                 "CONFIG_FIND", "CONFIG_LESS", "CONFIG_VI", "CONFIG_HEXDUMP", "CONFIG_TIMEOUT",
                 "CONFIG_MOUNT", "CONFIG_FEATURE_MOUNT_FLAGS", "CONFIG_LOSETUP",
                 "CONFIG_UMOUNT", "CONFIG_SWITCH_ROOT", "CONFIG_SHA256SUM", "CONFIG_CMP", "CONFIG_AWK",
                 "CONFIG_HUSH_IF", "CONFIG_HUSH_CASE", "CONFIG_HUSH_FUNCTIONS", "CONFIG_HUSH_TEST",
                 "CONFIG_HUSH_TICK", "CONFIG_HUSH_READ", "CONFIG_FEATURE_SH_MATH", "CONFIG_HEAD", "CONFIG_FEATURE_FANCY_HEAD",
                 "CONFIG_SYNC", "CONFIG_RM", "CONFIG_TOUCH", "CONFIG_STAT", "CONFIG_FEATURE_STAT_FORMAT", "CONFIG_LN", "CONFIG_CHMOD"):
        if f"{name}=y" not in lines:
            raise ValueError(f"BusyBox did not retain required setting: {name}=y")
    if "CONFIG_SH_IS_ASH=y" in lines or "CONFIG_ASH=y" in lines:
        raise ValueError("ash is unsupported by BusyBox on NOMMU")
    for name in ("CONFIG_FEATURE_EDITING_HISTORY", "CONFIG_FEATURE_EDITING_MAX_LEN"):
        value = next((line.split('=', 1)[1] for line in lines if line.startswith(name + '=')), '0')
        if not value.isdigit() or int(value) < 128:
            raise ValueError(f"interactive shell requires {name} >= 128")


def check_release_branding(name, data):
    # Keep retired product text out of the release, including embedded binaries.
    retired = bytes((116, 97, 110, 111, 115))
    if retired in name.encode().lower() or retired in data.lower():
        raise ValueError(f"retired product branding in {name}")
    if b"compatibility remains provisional" in data.lower():
        raise ValueError(f"obsolete ABI disclaimer in {name}")


def verify(archive, elfs, readelf, busybox_config=None):
    entries, unpacked = read_newc(archive)
    for name, item in entries.items():
        check_release_branding(name, item.get("data", b""))
    abi_text = entries.get("etc/gaijin-abi", {}).get("data", b"")
    if (not abi_text.startswith(b"gaijin-rv32imac-ilp32-le-bflt4-static-uclibc-v1\n") or
            b"Validated on Tanmatsu rev7 with the pinned" not in abi_text):
        raise ValueError("missing accepted Gaijin ABI description")
    for name in ("init", "etc/init.d/rcS", "usr/bin/m1-smoke", "sbin/gaijin-sd-root", "usr/bin/m2-sd-test", "sbin/gaijin-persist", "usr/bin/m3-state-test", "usr/bin/m5-display-test"):
        _, item = resolve_path(entries, name)
        if not stat.S_ISREG(item.get("mode", 0)) or not item["mode"] & 0o111 or not item["data"].startswith(b"#!/bin/sh\n"):
            raise ValueError(f"missing executable shell script {name}")
    busybox, _ = resolve_path(entries, "bin/busybox")
    for name in ("bin/sh", "sbin/init"):
        resolved, item = resolve_path(entries, name)
        if resolved != busybox:
            raise ValueError(f"{name} must link to BusyBox")
    console = entries.get("dev/console", {})
    if not stat.S_ISCHR(console.get("mode", 0)) or console.get("rdev") != (5, 1):
        raise ValueError("initramfs must supply /dev/console c 5 1 before /init")
    inittab = entries.get("etc/inittab", {}).get("data", b"").splitlines()
    if inittab != [b"::sysinit:/etc/init.d/rcS", b"tty1::respawn:-/bin/sh", b"ttyUJ0::respawn:-/bin/sh"]:
        raise ValueError("init must start direct login shells on tty1 before ttyUJ0")
    _, startup = resolve_path(entries, "etc/init.d/rcS")
    if b"stty " in startup["data"]:
        raise ValueError("rcS must use init's immediate terminal setup")
    if b"gaijin-boot-report" in startup["data"] or any(Path(name).name == "gaijin-boot-report" for name in entries):
        raise ValueError("boot archive contains the retired backlight diagnostic")
    binaries = {}
    for name in ("bin/busybox", "usr/bin/gaijin-abi-check", "usr/bin/m5-umode-probe", "usr/bin/m2-input-test", "sbin/gaijin-state-map", "sbin/gaijin-power", "sbin/gaijin-setup"):
        _, item = resolve_path(entries, name)
        if not item.get("mode", 0) & 0o111:
            raise ValueError(f"missing executable {name}")
        payload = item.get("data", b"")
        if not payload and item.get("nlink", 0) > 1:
            payload = next((e["data"] for e in entries.values() if e["inode"] == item["inode"] and e["data"]), b"")
        binaries[name] = flat_info(payload)
    if binaries["bin/busybox"]["stack"] < 65536:
        raise ValueError("BusyBox requires the requested 64 KiB flat stack")
    power, _ = resolve_path(entries, "sbin/gaijin-power")
    for name in ("shutdown", "poweroff", "halt"):
        actual, _ = resolve_path(entries, "sbin/" + name)
        if actual != power:
            raise ValueError(f"{name} must use the safe Gaijin power controller")
    if any(Path(name).name == "reboot" for name in entries):
        raise ValueError("the root filesystem must not contain a reboot command")
    if len(elfs) != 7:
        raise ValueError("supply BusyBox, ABI-probe, privilege-probe, input-test, state-mapper, power-controller and setup ELF intermediates")
    if busybox_config:
        check_busybox_config(busybox_config.read_text())
    return dict(abi="gaijin-rv32imac-ilp32-le-bflt4-static-uclibc-v1", compressed_bytes=archive.stat().st_size,
                unpacked_bytes=unpacked, archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                binaries=binaries, elf_intermediates=[verify_elf(p, readelf) for p in elfs],
                shell="hush" if busybox_config else None, hardware_validated=False)


def verify_squashfs(image, archive, unsquashfs):
    """Bound decompressor memory and compare executable/config bytes with cpio."""
    data = image.read_bytes()
    if (len(data) < 96 or len(data) > MAX_UNPACKED or data[:4] != b"hsqs" or
            struct.unpack_from("<I", data, 12)[0] != 32768 or
            struct.unpack_from("<H", data, 20)[0] != 1 or
            struct.unpack_from("<HH", data, 28) != (4, 0) or
            not 96 <= struct.unpack_from("<Q", data, 40)[0] <= len(data)):
        raise ValueError("rootfs requires bounded SquashFS v4, gzip, 32 KiB blocks")
    entries, _ = read_newc(archive)
    for name in ("bin/busybox", "usr/bin/gaijin-abi-check", "usr/bin/m5-umode-probe", "usr/bin/m2-input-test", "sbin/gaijin-state-map", "sbin/gaijin-power", "sbin/gaijin-setup",
                 "init", "etc/init.d/rcS", "sbin/gaijin-sd-root", "usr/bin/m2-sd-test",
                 "sbin/gaijin-persist", "usr/bin/m3-state-test", "usr/bin/m5-display-test", "etc/profile",
                 "etc/inittab", "etc/gaijin-base", "etc/gaijin-abi"):
        resolved, item = resolve_path(entries, name)
        payload = item["data"]
        if not payload and item.get("nlink", 0) > 1:
            payload = next(e["data"] for e in entries.values()
                           if e["inode"] == item["inode"] and e["data"])
        actual = subprocess.check_output([str(unsquashfs), "-cat", str(image), resolved])
        if actual != payload:
            raise ValueError(f"SquashFS/initramfs contents differ: {name}")
    return dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                block_size=32768, compression="gzip")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--elf", required=True, action="append", type=Path)
    parser.add_argument("--readelf", required=True)
    parser.add_argument("--busybox-config", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--rootfs", required=True, type=Path)
    parser.add_argument("--unsquashfs", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = verify(args.archive, args.elf, args.readelf, args.busybox_config)
        result["rootfs"] = verify_squashfs(args.rootfs, args.archive, args.unsquashfs)
        args.report.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"M1 userspace verification failed: {exc}\n")


if __name__ == "__main__":
    main()
