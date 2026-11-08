#!/usr/bin/env python3
"""Publish, check and install the single current Gaijin application set."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import zipfile
from package_boot import package
from verify_userspace import check_release_branding, read_newc, resolve_path

ROOT = Path(__file__).resolve().parents[1]
ABI = "gaijin-rv32imac-ilp32-le-bflt4-static-uclibc-v1"
APP_ID = re.search(r'^#define GAIJIN_APP_ID "([^"]+)"',
                   (ROOT / "main/boot_contract.h").read_text(), re.M)[1]
APP = Path("sd/apps") / APP_ID
ARCHIVE = "gaijin-current.zip"
REPOSITORY_URL = "https://github.com/siviqt/Gaijin"
ICON_SIZES = (16, 32, 64)
APP_ICONS = tuple(f"icon{size}.png" for size in ICON_SIZES)
APP_ASSETS = ("README.md", "LICENSE", "boot/manifest.bin", "boot/Image", "boot/tanmatsu.dtb",
              "boot/initramfs", "boot/rootfs.sqfs", "boot/rootfs.sqfs.sha256",
              "boot/userspace-abi.json", "THIRD_PARTY.md") + APP_ICONS


def bootargs(usb_only=False):
    name = "M0_USB_BOOTARGS" if usb_only else "M0_BOOTARGS"
    return re.search(r'^#define ' + name + r' "([^"]+)"',
                     (ROOT / "main/boot_contract.h").read_text(), re.M)[1]


def check_loader(loader):
    data = loader.read_bytes()
    check_release_branding(loader.name, data)
    if b"earlycon=esp32p4usbjtag," in data:
        raise ValueError("release loader must disable CONFIG_GAIJIN_USB_EARLYCON; run make loader")
    if (any(bootargs(usb_only).encode() + b"\0" not in data for usb_only in (False, True)) or
            b"Gaijin M5 contract=1" not in data or
            b"Linux owns DMA refresh" not in data or b"LDO4 disabled, bypass off" not in data or
            b"tanmatsu,appfs-boot-selection" in data or
            f"/sd/apps/{APP_ID}/boot/".encode() not in data):
        raise ValueError("loader is not current: expected framebuffer setup and SD reset; run make loader")


def check_userspace_paths(archive):
    entries, _ = read_newc(archive)
    for name in ("init", "etc/inittab", "etc/init.d/rcS", "sbin/gaijin-sd-root", "usr/bin/m2-sd-test"):
        _, item = resolve_path(entries, name)
        if name in ("sbin/gaijin-sd-root", "usr/bin/m2-sd-test"):
            paths = set(re.findall(rb"/sd/apps/([^/\s]+)/boot", item["data"]))
            if paths != {APP_ID.encode()}:
                raise ValueError(f"stale userspace application path in {name}; run make userspace")
        if item["data"] != (ROOT / "userspace/overlay" / name).read_bytes():
            raise ValueError(f"stale userspace startup script {name}; run make userspace")


def file_record(path):
    data = path.read_bytes()
    return dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())


def launcher_metadata():
    metadata = json.loads((ROOT / "launcher/metadata.json").read_text())
    if metadata.get("repository_url") != REPOSITORY_URL:
        raise ValueError(f"metadata must declare repository_url: {REPOSITORY_URL}")
    if metadata.get("icon") != {f"{size}x{size}": f"icon{size}.png" for size in ICON_SIZES}:
        raise ValueError("metadata must use the three Penguin icons at the application root")
    app = metadata["application"][0]
    expected = dict(targets=["tanmatsu"], type="appfs", executable="linux-loader.bin", revision=33,
                    external_only=True, assets=[dict(source_file=name, target_file=name) for name in APP_ASSETS])
    if app != expected:
        raise ValueError("metadata must match Tanmatsu Launcher 0.2.8 AppFS installation")
    return metadata


def stage_launcher(loader, output):
    """Stage only application metadata/assets; never invent a kernel manifest."""
    launcher_metadata()
    output.mkdir(parents=True, exist_ok=True)
    for source, name in ((ROOT / "launcher/metadata.json", "metadata.json"),
                         (ROOT / "launcher/README.md", "README.md"),
                         (ROOT / "LICENSE", "LICENSE"), (ROOT / "THIRD_PARTY.md", "THIRD_PARTY.md"),
                         (loader, "linux-loader.bin")):
        shutil.copyfile(source, output / name)
    for name in APP_ICONS:
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "launcher" / name, target)


def stage_application(loader, kernel, userspace, output):
    if output.name != APP_ID:
        raise ValueError(f"application directory must be named {APP_ID}")
    check_loader(loader)
    archive = userspace / "images/rootfs.cpio.gz"
    rootfs = userspace / "images/rootfs.squashfs"
    report = json.loads((userspace / "gaijin-userspace.json").read_text())
    if (report.get("abi") != ABI or report.get("shell") != "hush" or
            not {"usr/bin/m2-input-test", "sbin/gaijin-state-map", "sbin/gaijin-power"} <= report.get("binaries", {}).keys() or
            report.get("archive_sha256") != hashlib.sha256(archive.read_bytes()).hexdigest()):
        raise ValueError("missing/stale userspace verification; run make userspace")
    if report.get("rootfs", {}).get("sha256") != file_record(rootfs)["sha256"]:
        raise ValueError("missing/stale SD rootfs verification; run make userspace")
    check_userspace_paths(archive)
    config = (kernel / ".config").read_text().splitlines()
    for setting in ("# CONFIG_MMU is not set", "# CONFIG_SMP is not set", "# CONFIG_FPU is not set",
                    "CONFIG_SERIAL_ESP32P4_USB_JTAG=y", "CONFIG_BINFMT_FLAT=y", "CONFIG_BINFMT_SCRIPT=y",
                    'CONFIG_LOCALVERSION="-gaijin-m5"', "CONFIG_INPUT_EVDEV=y",
                    "CONFIG_POWER_RESET_TANMATSU=y", "CONFIG_FB_TANMATSU=y", "CONFIG_FRAMEBUFFER_CONSOLE=y", "CONFIG_VT_CONSOLE=y",
                    "CONFIG_FRAMEBUFFER_CONSOLE_ROTATION=y", "CONFIG_FONT_8x16=y", "CONFIG_FONT_10x18=y",
                    "CONFIG_BACKLIGHT_CLASS_DEVICE=y", "CONFIG_FB_DEVICE=y",
                    "CONFIG_KEYBOARD_TANMATSU_MCU=y", "CONFIG_GPIO_TANMATSU_M2=y", "CONFIG_I2C_GPIO=y",
                    "CONFIG_BLOCK=y", "CONFIG_BLK_DEV_LOOP=y", "CONFIG_MMC=y", "CONFIG_MMC_BLOCK=y",
                    "CONFIG_MMC_TANMATSU_SD=y", "CONFIG_MSDOS_PARTITION=y", "CONFIG_VFAT_FS=y",
                    "CONFIG_NLS_CODEPAGE_437=y", "CONFIG_NLS_ASCII=y", "CONFIG_SQUASHFS=y",
                    "CONFIG_EXT2_FS=y", "CONFIG_BLK_DEV_DM=y", "CONFIG_SQUASHFS_ZLIB=y", "CONFIG_SQUASHFS_DECOMP_SINGLE=y"):
        if setting not in config:
            raise ValueError(f"kernel missing required setting: {setting}")
    properties = subprocess.check_output(
        ["fdtget", "-p", str(kernel / "tanmatsu.dtb"), "/soc/mmc@50083000"], text=True).splitlines()
    if "dma-noncoherent" not in properties or "dma-coherent" in properties:
        raise ValueError("SD DTB must declare dma-noncoherent for IDMAC cache sync")
    if "status" in properties and subprocess.check_output(
            ["fdtget", str(kernel / "tanmatsu.dtb"), "/soc/mmc@50083000", "status"], text=True).strip() != "okay":
        raise ValueError("SD host must be enabled in the current DTB")
    display_checks = (
        ("/soc/power-controller@5010800c", "compatible", "s", "tanmatsu,rev7-power"),
        ("/soc/power-controller@5010800c", "reg", "x", "5010800c 100"),
        ("/reserved-memory/framebuffer@48e00000", "reg", "x", "48e00000 bc000"),
        ("/soc/framebuffer@50081000", "compatible", "s", "tanmatsu,rev7-fb"),
        ("/soc/framebuffer@50081000", "status", "s", "disabled"),
        ("/soc/framebuffer@50081000", "reg", "x", "50081000 1000 500a0800 100"),
        ("/soc/framebuffer@50081000", "interrupts-extended", "x", None),
    )
    for node, prop, kind, expected in display_checks:
        value = subprocess.check_output(
            ["fdtget", "-t", kind, str(kernel / "tanmatsu.dtb"), node, prop], text=True).strip()
        if expected is not None and value != expected:
            raise ValueError(f"DTB has incompatible M5 display {prop}")
    # Prepare the entire directory, then replace it. No old files survive.
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix="app-stage-") as temp:
        stage = Path(temp) / APP_ID
        stage_launcher(loader, stage)
        package(kernel / "arch/riscv/boot/Image", kernel / "tanmatsu.dtb", archive, stage / "boot")
        shutil.copyfile(userspace / "gaijin-userspace.json", stage / "boot/userspace-abi.json")
        shutil.copyfile(rootfs, stage / "boot/rootfs.sqfs")
        (stage / "boot/rootfs.sqfs.sha256").write_text(file_record(rootfs)["sha256"] + "\n")
        check_application(stage)
        previous = Path(temp) / "previous"
        if output.exists():
            output.rename(previous)
        try:
            stage.rename(output)
        except OSError:
            if previous.exists():
                previous.rename(output)
            raise


def check_application(app):
    """Check the installed directory too: valid hashes cannot fix a wrong slug."""
    if app.name != APP_ID:
        raise ValueError(f"application directory must be named {APP_ID}")
    metadata = json.loads((app / "metadata.json").read_text())
    if metadata != launcher_metadata():
        raise ValueError("application metadata differs from source; run make package")
    for name in (*APP_ASSETS, "linux-loader.bin"):
        if not (app / name).is_file():
            raise ValueError(f"application asset missing: {name}")
    if any((app / name).exists() for name in ("LICENSES", "licenses")):
        raise ValueError("application must use the consolidated THIRD_PARTY.md; remove separate license directories")
    if (app / "THIRD_PARTY.md").read_bytes() != (ROOT / "THIRD_PARTY.md").read_bytes():
        raise ValueError("application third-party notices differ from source; run make package")
    if {path.relative_to(app).as_posix() for path in app.rglob("*.png")} != set(APP_ICONS):
        raise ValueError("application must contain only the three Penguin PNGs")
    check_loader(app / "linux-loader.bin")
    check_userspace_paths(app / "boot/initramfs")
    # Check the boot manifest independently of CURRENT.json.
    manifest = b"GAIJIN1\0" + struct.pack("<4I", 1, 7, 1, 0)
    for name in ("Image", "tanmatsu.dtb", "initramfs"):
        record = file_record(app / "boot" / name)
        manifest += struct.pack("<I", record["bytes"]) + bytes.fromhex(record["sha256"])
    manifest += bytes(8)
    if (app / "boot/manifest.bin").read_bytes() != manifest:
        raise ValueError("boot manifest does not match the current set")
    rootfs_hash = file_record(app / "boot/rootfs.sqfs")["sha256"]
    if (app / "boot/rootfs.sqfs.sha256").read_text() != rootfs_hash + "\n":
        raise ValueError("SD rootfs checksum does not match the current set")
    userspace = json.loads((app / "boot/userspace-abi.json").read_text())
    if (userspace.get("archive_sha256") != file_record(app / "boot/initramfs")["sha256"] or
            userspace.get("rootfs", {}).get("sha256") != rootfs_hash):
        raise ValueError("userspace verification does not match the installed files")
    return metadata


def check_current(build):
    report = json.loads((build / "CURRENT.json").read_text())
    if (report.get("format") != 1 or report.get("bootargs") != bootargs() or
            report.get("usb_recovery_bootargs") != bootargs(usb_only=True) or
            report.get("application_id") != APP_ID):
        raise ValueError("current build record is incompatible; run make package")
    files = report["files"]
    staged = {p.relative_to(build).as_posix() for p in (build / "sd").rglob("*") if p.is_file()}
    if staged != {name for name in files if name.startswith("sd/")}:
        raise ValueError("SD set is incomplete or contains extra files; run make package")
    for name, expected in files.items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("unsafe build record path")
        if file_record(build / relative) != expected:
            raise ValueError(f"current build checksum mismatch: {name}; run make package")
    app = build / APP
    metadata = check_application(app)
    if (report.get("appfs_binary") != str(APP / "linux-loader.bin") or
            report.get("appfs_revision") != metadata["application"][0]["revision"] or
            report.get("version") != metadata["version"]):
        raise ValueError("build record and application metadata disagree")
    # A separately rebuilt component must be packaged before it can be installed.
    for source, staged_name in (("loader/linux-loader.bin", "linux-loader.bin"),
                                ("kernel/arch/riscv/boot/Image", "boot/Image"),
                                ("kernel/tanmatsu.dtb", "boot/tanmatsu.dtb"),
                                ("userspace/images/rootfs.cpio.gz", "boot/initramfs"),
                                ("userspace/images/rootfs.squashfs", "boot/rootfs.sqfs")):
        if (build / source).exists() and file_record(build / source) != file_record(app / staged_name):
            raise ValueError(f"component changed since packaging: {source}; run make package")
    checksums = ''.join(f"{record['sha256']}  {name}\n" for name, record in sorted(files.items()))
    checksums += f"{hashlib.sha256((build / 'CURRENT.json').read_bytes()).hexdigest()}  CURRENT.json\n"
    if (build / "SHA256SUMS").read_text() != checksums:
        raise ValueError("SHA256SUMS does not match the current build")
    return report


def release_files():
    """One inventory for copying and hashing the public release documents."""
    files = {
        "README.md": ROOT / "README.md",
        "INSTALL.md": ROOT / "INSTALL.md",
        "sources.lock.json": ROOT / "sources.lock.json",
        "THIRD_PARTY.md": ROOT / "THIRD_PARTY.md",
        "launcher/ICONS.md": ROOT / "launcher/ICONS.md",
        "launcher/icon-prompts.json": ROOT / "launcher/icon-prompts.json",
        "tools/create_state.py": ROOT / "tools/create_state.py",
        "tools/check_state.py": ROOT / "tools/check_state.py",
    }
    # Documentation is curated source/evidence. Private userdata backups live
    # outside it and are never discovered by this release allowlist.
    for source in sorted((ROOT / "Documentation").rglob("*")):
        relative = source.relative_to(ROOT / "Documentation")
        if (source.is_file() and not source.is_symlink() and
                not any(part.startswith(".") for part in relative.parts) and
                source.suffix in (".md", ".txt", ".json", ".log", ".docx")):
            files[source.relative_to(ROOT).as_posix()] = source
    return files


def publish(build):
    stage_application(build / "loader/linux-loader.bin", build / "kernel", build / "userspace", build / APP)
    documents = release_files()
    # These are generated release mirrors. Replacing them removes stale files
    # without touching the canonical sources, dependency caches or backups.
    for name in ("Documentation", "tools", "licenses"):
        directory = build / name
        if directory.exists():
            shutil.rmtree(directory)
    (build / "INSTALL.txt").unlink(missing_ok=True)
    for name, source in documents.items():
        destination = build / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    paths = [p for p in (build / "sd").rglob("*") if p.is_file()]
    paths += [build / name for name in documents]
    files = {p.relative_to(build).as_posix(): file_record(p) for p in sorted(paths)}
    metadata = json.loads((build / APP / "metadata.json").read_text())
    report = dict(format=1, version=metadata["version"], appfs_revision=metadata["application"][0]["revision"],
                  application_id=APP_ID,
                  kernel_release=(build / "kernel/include/config/kernel.release").read_text().strip(),
                  abi=ABI, bootargs=bootargs(), usb_recovery_bootargs=bootargs(usb_only=True),
                  sd_root="sd", appfs_binary=str(APP / "linux-loader.bin"),
                  verification="Host integrity record; hardware results are in Documentation/validation/README.md",
                  files=files)
    (build / "CURRENT.json").write_text(json.dumps(report, indent=2) + "\n")
    checksums = ''.join(f"{record['sha256']}  {name}\n" for name, record in sorted(files.items()))
    checksums += f"{hashlib.sha256((build / 'CURRENT.json').read_bytes()).hexdigest()}  CURRENT.json\n"
    (build / "SHA256SUMS").write_text(checksums)
    check_current(build)
    # Always replace this one archive; no versioned release directories.
    with tempfile.TemporaryDirectory(dir=build, prefix="package-") as temp:
        archive = Path(temp) / ARCHIVE
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for name in sorted([*files, "CURRENT.json", "SHA256SUMS"]):
                bundle.write(build / name, name)
        archive.replace(build / ARCHIVE)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--check", action="store_true", help="verify the complete staged set without rebuilding")
    action.add_argument("--check-app", type=Path, help="verify a copied application and its directory name")
    action.add_argument("--install", action="store_true", help="verify and upload the staged AppFS binary")
    parser.add_argument("--badgelink", default="../../tanmatsu-sdk/tools/badgelink.sh")
    args = parser.parse_args()
    try:
        if args.check_app:
            metadata = check_application(args.check_app)
            print(f"Application verified: {args.check_app} ({metadata['version']}, revision {metadata['application'][0]['revision']})")
            return
        build = ROOT / "build"
        if args.check or args.install:
            report = check_current(build)
            print(f"Current set verified: {build / APP}", flush=True)
            if args.install:
                metadata = json.loads((build / APP / "metadata.json").read_text())
                subprocess.run([args.badgelink, "appfs", "upload", APP_ID, metadata["name"],
                                str(report["appfs_revision"]), str(build / APP / "linux-loader.bin")], check=True)
        else:
            publish(build)
            print(f"Current SD root: {build / 'sd'}\nDownload bundle: {build / ARCHIVE}")
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Current build operation failed: {exc}\n")


if __name__ == "__main__":
    main()
