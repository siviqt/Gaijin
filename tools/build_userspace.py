#!/usr/bin/env python3
"""Build pinned Buildroot RV32 NOMMU/uClibc BusyBox and verify the Gaijin initramfs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def toolchain_fingerprint(config):
    # Product names, packages and rootfs contents do not change the compiler ABI.
    # Retain all architecture, libc, linking and toolchain configuration lines.
    rootfs = ("BR2_PACKAGE_", "BR2_TARGET_GENERIC_", "BR2_ROOTFS_", "BR2_INIT_",
              "BR2_TARGET_ROOTFS_")
    settings = [line for line in config.splitlines()
                if line.startswith("BR2_") and not line.startswith(rootfs)]
    return hashlib.sha256(('\n'.join(sorted(settings)) + '\n').encode()).hexdigest()


def run(*args, **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def check_config(text):
    lines = text.splitlines()
    for name in ("BR2_riscv", "BR2_RISCV_32", "BR2_RISCV_ABI_ILP32", "BR2_BINFMT_FLAT",
                 "BR2_RISCV_ISA_RVM", "BR2_RISCV_ISA_RVA", "BR2_RISCV_ISA_RVC",
                 "BR2_STATIC_LIBS", "BR2_TOOLCHAIN_BUILDROOT_UCLIBC", "BR2_PTHREADS_NONE",
                 "BR2_PACKAGE_GAIJIN_ABI_CHECK", "BR2_PACKAGE_GAIJIN_STATE", "BR2_PACKAGE_GAIJIN_INPUT_TEST",
                 "BR2_TARGET_ROOTFS_SQUASHFS", "BR2_TARGET_ROOTFS_SQUASHFS_BS_32K",
                 "BR2_TARGET_ROOTFS_SQUASHFS4_GZIP"):
        if f"{name}=y" not in lines:
            raise ValueError(f"Buildroot did not retain required setting: {name}=y")
    # Kconfig can omit disabled symbols whose dependencies make them invisible.
    for name in ("BR2_RISCV_USE_MMU", "BR2_USE_MMU", "BR2_ARCH_IS_64", "BR2_RISCV_ISA_RVF",
                 "BR2_RISCV_ISA_RVD", "BR2_RISCV_ISA_RVV"):
        if f"{name}=y" in lines or f"{name}=m" in lines:
            raise ValueError(f"Buildroot enabled unsupported setting: {name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="existing clean Buildroot Git checkout at the locked commit")
    parser.add_argument("--download-dir", type=Path, default=Path(os.environ.get("BR2_DL_DIR", ROOT / "build/downloads")))
    parser.add_argument("--jobs", type=int, default=min(8, os.cpu_count() or 1))
    args = parser.parse_args()
    lock = json.loads((ROOT / "sources.lock.json").read_text())["buildroot"]
    source = (args.source or ROOT / "build/buildroot").resolve()
    output = ROOT / "build/userspace"
    phase = "source check"
    try:
        if not source.exists():
            phase = "source download"
            source.parent.mkdir(parents=True, exist_ok=True)
            run("git", "clone", "--depth", "1", "--branch", lock["tag"], lock["repository"], source)
        phase = "source check"
        commit = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
        if commit != lock["commit"]:
            parser.error(f"Buildroot commit mismatch: expected {lock['commit']}, got {commit}")
        if subprocess.check_output(["git", "-C", str(source), "status", "--porcelain", "--untracked-files=no"]):
            parser.error("Buildroot source has tracked modifications; use the clean locked revision")
        output.mkdir(parents=True, exist_ok=True)
        config = ROOT / "userspace/configs/gaijin_defconfig"
        fragment = (ROOT / "userspace/busybox.fragment").read_bytes()
        external = (ROOT / "userspace/external.mk").read_bytes()
        fingerprint = hashlib.sha256(config.read_bytes() + fragment + external).hexdigest()
        toolchain_hash = toolchain_fingerprint(config.read_text())
        stamp = output / ".gaijin-config"
        toolchain_stamp = output / ".gaijin-toolchain-config"
        previous_toolchain = toolchain_stamp.read_text() if toolchain_stamp.exists() else None
        if (output / ".config").exists() and previous_toolchain != toolchain_hash:
            parser.error("toolchain configuration changed: remove build/userspace and rebuild (BusyBox-only changes are rebuilt automatically)")
        make = ["make", "-C", source, f"O={output}", f"BR2_EXTERNAL={ROOT / 'userspace'}",
                f"BR2_DL_DIR={args.download_dir.resolve()}"]
        phase = "configuration"
        run(*make, "gaijin_defconfig")
        # Fail closed if Kconfig silently drops any ABI-critical setting.
        check_config((output / ".config").read_text())
        stamp.write_text(fingerprint)
        toolchain_stamp.write_text(toolchain_hash)
        integration = ROOT / "userspace/package/elf2flt-flags/elf2flt-flags.mk"
        busybox_patches = sorted((ROOT / "userspace/patches/busybox").glob("*.patch"))
        integration_hash = hashlib.sha256(integration.read_bytes() + fragment + external +
                                          b"".join(p.read_bytes() for p in busybox_patches)).hexdigest()
        integration_stamp = output / ".gaijin-busybox-integration"
        if not integration_stamp.exists() or integration_stamp.read_text() != integration_hash:
            # Re-extract BusyBox to apply changed patches or linker options.
            run(*make, "busybox-dirclean")
            integration_stamp.write_text(integration_hash)
        run(*make, "gaijin-abi-check-dirclean")
        run(*make, "gaijin-input-test-dirclean")
        run(*make, "gaijin-state-dirclean")
        phase = "build"
        run(*make, f"-j{args.jobs}")
        phase = "ABI/archive verification"
        elf_paths = []
        for patterns in (("busybox-*/busybox_unstripped.gdb", "busybox-*/busybox.gdb"),
                         ("gaijin-abi-check-*/gaijin-abi-check.gdb",),
                         ("gaijin-abi-check-*/m5-umode-probe.gdb",),
                         ("gaijin-input-test-*/m2-input-test.gdb",),
                         ("gaijin-state-*/gaijin-state-map.gdb",),
                         ("gaijin-state-*/gaijin-power.gdb",),
                         ("gaijin-state-*/gaijin-setup.gdb",)):
            for pattern in patterns:
                matches = sorted((output / "build").glob(pattern))
                if matches:
                    break
            if len(matches) != 1:
                parser.error(f"expected one ELF intermediate for {patterns}, got {matches}")
            elf_paths += ["--elf", matches[0]]
        run(sys.executable, ROOT / "tools/verify_userspace.py", "--archive", output / "images/rootfs.cpio.gz",
            *elf_paths, "--readelf", output / "host/bin/riscv32-buildroot-linux-uclibc-readelf",
            "--busybox-config", elf_paths[1].parent / ".config",
            "--rootfs", output / "images/rootfs.squashfs",
            "--unsquashfs", output / "host/bin/unsquashfs",
            "--report", output / "gaijin-userspace.json")
        print("Gaijin initramfs verified. Run make sdcard to package with kernel and launcher metadata.")
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        hint = ("Offline: supply --source /path/to/clean/pinned/buildroot with a populated download cache.\n"
                if phase == "source download" else "")
        parser.exit(1, f"Gaijin userspace {phase} failed: {exc}\n{hint}")


if __name__ == "__main__":
    main()
