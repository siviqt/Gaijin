#!/usr/bin/env python3
"""Create a bounded development SD boot set (boot contract v1). No device writes."""
import argparse
import hashlib
from pathlib import Path
import shutil
import struct

KERNEL_LIMIT = 0x800000
DTB_LIMIT = 0x10000 - 1024
INITRD_LIMIT = 0x1000000


def image_record(path, limit, minimum):
    size = path.stat().st_size
    if not minimum <= size <= limit:
        raise ValueError(f"{path}: invalid size {size}")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(65536), b""):
            digest.update(chunk)
    return struct.pack("<I", size) + digest.digest()


def package(image, dtb, initrd, output):
    with image.open("rb") as source:
        header = source.read(64)
    size = image.stat().st_size
    if (len(header) != 64 or header[48:60] != b"RISCV\0\0\0RSC\x05"
            or struct.unpack_from("<Q", header, 8)[0] != 0
            or struct.unpack_from("<Q", header, 24)[0] != 0
            or struct.unpack_from("<I", header, 32)[0] != 2
            or not size <= struct.unpack_from("<Q", header, 16)[0] <= KERNEL_LIMIT):
        raise ValueError("requires a flat RV32 Image v0.2 linked at 0x48000000")
    manifest = b"GAIJIN1\0" + struct.pack("<4I", 1, 7, 1, 0)
    manifest += image_record(image, KERNEL_LIMIT, 64)
    manifest += image_record(dtb, DTB_LIMIT, 40)
    manifest += image_record(initrd, INITRD_LIMIT, 1) if initrd else bytes(36)
    manifest += bytes(8)
    assert len(manifest) == 140
    output.mkdir(parents=True, exist_ok=True)
    # Inputs may already be in output, e.g. when adding an initrd to a kernel build.
    for src, name in ((image, "Image"), (dtb, "tanmatsu.dtb"), (initrd, "initramfs")):
        if src and src.resolve() != (output / name).resolve():
            shutil.copyfile(src, output / name)
    (output / "manifest.bin").write_bytes(manifest)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--dtb", required=True, type=Path)
    parser.add_argument("--initrd", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        package(args.image, args.dtb, args.initrd, args.output)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"{exc}\n")
