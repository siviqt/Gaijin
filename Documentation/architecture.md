# Architecture

Gaijin boots native RV32 NOMMU Linux from a Tanmatsu AppFS loader, then gives
Linux ownership of USB, SD, display refresh and MCU input/backlight. The base and FAT stay read-only;
validated ext2 image sectors provide persistent state. The current release is
application 0.5.19 / AppFS revision 33 / Linux 6.18.35-gaijin-m5, targeting
Tanmatsu rev7, ESP-IDF v6.0.2, BSP 1.6.4, Launcher 0.2.8, Radio 3.4.0 and MCU r9.

This document owns boot, memory, console, ABI and build contracts. Detailed
[keyboard](guides/keyboard.md), [SD](guides/sd-storage.md) and
[persistence](guides/persistence.md) guides own subsystem procedures.
[Milestones](README.md#milestone-status) and [accepted results](validation/README.md)
are maintained separately.

## Loader and recovery

The AppFS application `com.github.siviqt.gaijin` enters through ESP-IDF `app_main()`.
It clears the first eight bytes of retained `rtc_retain_mem_t.custom`,
matching BSP 1.6.4's Launcher-recovery behavior. The existing Launcher and
system bootloader remain installed. Failures after entry log and reset;
physical reset remains a recovery path. The [power commands](guides/power.md)
cleanly stop persistence before Launcher return, halt or MCU
power-off. The power button still emits an input event only.

The loader uses ESP-IDF SDMMC and FatFS. Linux SD access uses a separate native
MMC host driver, with page-sized requests split into sector-sized DMA reads/writes. Writes are
restricted to the validated state-image sectors described in the M3 guide.
The loader isolates the SD pins and disables LDO4 and its bypass to discharge
the socket. It restores SD power, reads and verifies the boot assets, unmounts
SD and initializes the ST7701 display before jumping. Linux starts display DMA
and scanout. Audio and radio transport remain untouched. SD slot 0 uses GPIO39–42 D0–D3, GPIO43 CLK, GPIO44 CMD and LDO4.
Radio SDIO slot 1 is untouched. The loader does not write SD files, format,
erase NVS or change partitions. Radio and coprocessor versions printed by the
loader are targets, not runtime firmware identification.

## Boot contract v1

| Region | Start | Exclusive limit | Constraint |
| --- | --- | --- | --- |
| Populated PSRAM | 0x48000000 | 0x4a000000 | Exactly 32 MiB |
| Kernel Image + BSS | 0x48000000 | 0x48800000 | At most 8 MiB |
| DTB scratch | 0x48800000 | 0x48810000 | 64 KiB |
| Display pixels + descriptor | 0x48e00000 | 0x48ebc000 | 752 KiB reserved, outside allocatable Linux RAM |
| External initramfs | 0x49000000 | 0x4a000000 | At most 16 MiB |
| Loader code/data/stack | Flash/internal SRAM | Outside PSRAM | IDF configuration / ELF inspection |

The loader accepts an uncompressed little-endian RV32 Image header v0.2,
zero load offset and both RISC-V magics. File size must fit its declared
memory footprint; omitted BSS bytes are zeroed. Linux decompresses initramfs.
libfdt validates the DTB, contract compatible, board revision and single-core
description, then supplies memory, bootargs, initrd bounds and a reservation
for the whole DTB scratch area. C identifiers retain their `M0` milestone names. The branded DT compatible
is `tanmatsu,gaijin-v1`; manifest fields and layout remain contract v1. The
renamed magic and DT compatible require a matching loader and complete asset set.

`manifest.bin` is exactly 140 bytes: `GAIJIN1\0`; four little-endian u32 values
(format 1, board 7, contract 1, flags 0); three records of u32 size plus SHA256
(Image, DTB, initrd); eight reserved zero bytes. Invalid identifiers, sizes,
truncation/trailing data and hash mismatches are rejected. Hashes cover assets
before DTB edits. This is a development integrity check, not a signature.

The SRAM handoff disables interrupts, stalls hart1, stops the IDF stack guard,
disables/clears SYSTIMER alarms and masks CLIC slots. IDF watchdogs are disabled
by startup/configuration. It preserves PSRAM/cache/clocks and writes back and
invalidates loaded ranges through the P4 cache HAL, with data/instruction
fences. PMP entry 9 grants unlocked RWX over the reference port's 64 MiB PSRAM
aperture; DTB advertises only the populated 32 MiB. Direct entry is M-mode,
PC 0x48000000, a0=0, a1=0x48800000. There is no SBI or SMP secondary release.
Linux installs its own stacks, traps, CLIC and timer state.

Linux runs its kernel in M-mode and native bFLT applications in U-mode.
The P4 trap adapter saves raw CLIC return metadata separately from Linux's
syscall and signal bookkeeping. On physical IRQ entry it completes the
controller return with interrupts still disabled before Linux C code can
schedule another task. This prevents the handler's active interrupt level
from leaking into idle or another task. Final task returns use the ordinary
saved privilege. The U-mode CSR/SIGILL probe passed on the device.
The NOMMU board still needs a separate PMP/APM policy review for memory
isolation; privileged-instruction trapping alone is not full memory protection.

## USB console and userspace startup

The consolidated kernel patch includes the serial-core USB Serial/JTAG driver
at 0x500d2000. Its console prefix is `ttyUJ`, creating `/dev/ttyUJ0`.
A host regression compiles the actual pinned Linux `console_setup()` parser
and checks the current loader boot arguments.

Bootargs:

```text
console=ttyUJ0,115200n8 console=tty0 fbcon=rotate:1,font:10x18 consoleblank=0 rdinit=/init loglevel=7 panic=0
```

The loader initially supplies USB-only recovery arguments, then selects the LCD
as `/dev/console` after display setup succeeds. The kernel is non-SMP, so there
is no `maxcpus=1`. Production boot omits earlycon. The optional loader setting
`CONFIG_GAIJIN_USB_EARLYCON` adds best-effort early logging: a full FIFO drops
characters immediately without polling or using a clock. Release packaging
rejects that debug setting. `keep_bootcon` is absent. The driver preserves
IDF PHY/clocks/enumeration, masks controller interrupts and polls every 4 ms.
RX uses the TTY flip buffer/line discipline; TX uses serial-core queues and
handles full-packet flushes, including a terminating short packet/ZLP. Port
locking serializes TTY/console access; timer shutdown synchronizes close.
Regular console writes have a total 10 ms polling deadline; under disconnect
printk can be dropped while queued TTY writes can block under backpressure.
USB recovery under load remains an open hardware validation item.

The gzip newc initramfs includes executable `/init` and character device
`/dev/console` 5:1. `/init` mounts devtmpfs and execs BusyBox init as PID 1.
Early init mounts procfs/sysfs; rcS mounts bounded tmpfs (1 MiB `/run`, 4 MiB `/tmp`,
and 1 MiB for `/usr/local`; `/root` and `/home` each get 1 MiB RAM fallback
when persistence is unavailable),
runs the ABI probe and prints memory. Inittab respawns login hush directly on
`/dev/tty1` first and then `/dev/ttyUJ0`, each with a controlling terminal.
BusyBox logs child startup before switching stdio to the child's terminal;
the NOMMU vfork parent therefore does not depend on a USB child's log write.
USB provides kernel logs and its own shell; init and rcS use the primary LCD
when display setup succeeds. Hush supports line editing, command/file
completion, a 128-entry saved history, reverse search and job control. Init
still disables terminal software flow control immediately; the interactive
shell sets its own input mode when reading a command.
The [display guide](guides/display.md) owns panel/DMA handoff, cache flushing,
backlight control and local console limitations. Early init mounts FAT and a loop-backed SquashFS base read-only and moves
FAT, devtmpfs, procfs and sysfs into the new root before `switch_root`.
Failure before the transition falls back to initramfs. The SD driver rejects
writes outside the frozen state-image sector allowlist, including raw MMC requests. BusyBox init disables IXON/IXOFF in its immediate terminal setup, including
on respawn. The boot scripts can then start login shells without waiting
for a terminal output drain.

## Userspace ABI

ABI identifier: `gaijin-rv32imac-ilp32-le-bflt4-static-uclibc-v1`.
The kernel deliberately exposes rv32imac with CONFIG_FPU=n. The P4's F-only
hardware is not supported by this Linux floating-point context implementation,
which requires D. Effective ISA/ELF capabilities are acim; software float and
double execute through the integer ilp32 calling convention. Hardware F/D and
ilp32f/ilp32d userspace are unsupported.

Pinned Buildroot 2025.02.15 builds static RV32 NOMMU uClibc-ng binaries with
elf2flt. The runtime executable format is uncompressed bFLT v4; generic RV32
ELF distribution executables and Espressif/newlib programs are incompatible.
The toolchain is GCC 13.4.0, binutils 2.43.1, uClibc-ng 1.0.57 and elf2flt
2024.05; BusyBox is 1.37.0. Hush supports NOMMU; ash does not.

The verifier checks static ELF32 LE RISC-V intermediates, soft-float ELF flags,
ISA attributes, archive structure, console node, scripts, symlinks, resolved
BusyBox shell settings and bFLT bounds/relocations. Implied zmmul/zaamo/zalrsc
attributes require their M/A parent. Maximum executable envelope is 4 MiB;
minimum stack is 16 KiB. BusyBox requests 64 KiB and the probe 16 KiB.
Initramfs limits are 4 MiB gzip / 8 MiB unpacked; these are rejection limits,
not measured peak RAM. Current measured archive sizes are in `build/userspace/gaijin-userspace.json`.

The original MIT ABI probe checks compiler ABI macros, writable globals and
pointer relocations, software float/double, uint64 arithmetic, 256 KiB malloc,
and vfork/exec/wait. Its vfork child only execs or exits. `m1-smoke` also
launches twenty external probes, checks a pipeline and RAM-file round trip,
and prints diagnostics. It uses explicit failure handling because pinned
hush lacks `set -u` and its NOMMU errexit behavior failed the original loop.
Native cached-hush tests cover completion and injected failures.

The local elf2flt integration avoids nested quoting in Buildroot's BusyBox
recipe using separate `-Wl,-elf2flt=-r` and `-Wl,-elf2flt=-s65536` flags. A make
command-line `FLTFLAGS="-s 65536"` overrides BusyBox's smaller appended default.
Regression tests exercise the shell quoting and nested make precedence.

## Build and publication

The current source configuration is `linux/tanmatsu_defconfig` and board DTS
is `linux/tanmatsu.dts`. The board patch `linux/patches/0001-gaijin-esp32p4.patch`
contains the platform implementation, followed by the pending user-access
correction in `linux/patches/0002-esp32p4-user-access.patch`. The kernel builder
reuses a source with the matching ordered patch hash; changed patches require a fresh extraction after
verification of the pinned Linux archive SHA256.
The userspace builder verifies a clean Buildroot checkout at the pinned commit
and ABI configuration. Toolchain-setting changes require rebuilding
`build/userspace`; BusyBox-only integration changes preserve the cross
toolchain. The probe is rebuilt on each userspace build.

`make build` builds all components and calls `tools/package_current.py`.
Packaging prepares the complete application directory before replacing
`build/sd/apps/com.github.siviqt.gaijin/`, removing stale files. It validates loader
identity/bootargs, resolved kernel settings and the userspace archive report.
It writes one CURRENT.json, SHA256SUMS and gaijin-current.zip. `make install`
verifies hashes, manifest, metadata, bootargs and component/staging consistency,
then uploads the exact staged binary with the metadata revision. It does not
build a loader independently. Partial builds cannot publish Launcher-only
files into the installation tree.

Keep pinned source trees, source archives and toolchain workspaces for offline
rebuilds. Before wider redistribution, use Buildroot legal-info and retain
applicable corresponding sources, notices and static-linking/relink materials.
See THIRD_PARTY.md. Host tests do not emulate the P4 or establish USB recovery.
