#!/usr/bin/env python3
"""Build the pinned native Gaijin kernel and DTB. `make sdcard` packages userspace."""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
VERSION = "6.18.35"
SHA256 = "f78602932219125e211c5f5bfd84edcfd4ec5ce88fc944f8248413f665bef236"


def run(*args, **kwargs):
    subprocess.run([str(x) for x in args], check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tarball", type=Path)
    parser.add_argument("--cross-compile", default=os.environ.get("CROSS_COMPILE", "riscv32-linux-gnu-"))
    parser.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 1))
    args = parser.parse_args()
    # Keep the public kernel banner independent of the developer's account.
    os.environ.setdefault("KBUILD_BUILD_USER", "builder")
    os.environ.setdefault("KBUILD_BUILD_HOST", "gaijin")
    if not shutil.which(args.cross_compile + "gcc"):
        parser.error("cross compiler missing; set CROSS_COMPILE (riscv32-esp-elf- also builds this kernel)")
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    # Rebuild the prepared source only when patches change.
    inputs = sorted((ROOT / "linux/patches").glob("*.patch"))
    stamp = hashlib.sha256(b"".join(p.read_bytes() for p in inputs)).hexdigest()
    source = build / f"linux-{VERSION}"
    marker = source / ".gaijin-patches"
    if not marker.exists() or marker.read_text() != stamp:
        archive = args.tarball or build / f"linux-{VERSION}.tar.xz"
        if not archive.exists():
            urllib.request.urlretrieve(f"https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-{VERSION}.tar.xz", archive)
        digest = hashlib.sha256()
        with archive.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != SHA256:
            parser.error("Linux archive SHA256 mismatch")
        if source.exists():
            shutil.rmtree(source)
        with tarfile.open(archive) as tar:
            tar.extractall(build, filter="data")
        for patch in inputs:
            with patch.open("rb") as stream:
                run("patch", "--batch", "--forward", "-p1", cwd=source, stdin=stream)
        marker.write_text(stamp)
    output = build / "kernel"
    output.mkdir(exist_ok=True)
    shutil.copyfile(ROOT / "linux/tanmatsu_defconfig", output / ".config")
    make = ["make", "-C", source, f"O={output}", "ARCH=riscv", f"CROSS_COMPILE={args.cross_compile}"]
    run(*make, "olddefconfig")
    run(*make, f"-j{args.jobs}", "Image")
    run("dtc", "-I", "dts", "-O", "dtb", "-o", output / "tanmatsu.dtb", ROOT / "linux/tanmatsu.dts")
    print(f"Gaijin kernel and DTB: {output}\nRun make sdcard to package a complete asset set with initramfs.")


if __name__ == "__main__":
    main()
