# SD storage and root filesystem

Linux owns the native slot-0 SD host after loader handoff. The current M3.1
build mounts FAT and the SquashFS base read-only, then permits writes only to
validated persistent-state image sectors. [Persistent state](persistence.md)
owns that write gate, ext2 activation and recovery policy; this guide covers
SD/DMA ownership, base-root transition and read regression procedures.

M2 Linux SD access was accepted on 29 October 2026 with M2.8 / application
0.2.8 / AppFS revision 11 / Linux 6.18.35-gaijin-m2.8. Its original read-only
results and user-confirmed off-log recovery/integrity checks remain in
[validation](../validation/README.md). The procedures below describe the current
M3.1 build; the historical inventory retains the earlier block-wide read-only
policy. The current driver reports block writable while enforcing the bounded
sector gate. DMA must remain noncoherent so cache synchronization is performed.

## Controller and ownership

The current host is included in `linux/patches/0001-gaijin-esp32p4.patch`.
It shares IOMUX mapping with GPIO, accepts page-sized Linux requests, splits
reads into single-sector commands, and retains DMA/SG diagnostics. The pinned
ESP-IDF v6.0.2 `esp_hal_sd/esp32p4/include/hal/sdmmc_ll.h`, P4 register
headers, GPIO HAL, and BSP 1.6.4 storage source supply the hardware reference.
The MMC protocol, partition parsing, FAT, loop devices and SquashFS use the
pinned Linux 6.18.35 implementations.

| Resource | Ownership and setup |
| --- | --- |
| SDMMC 0x50083000 | Linux resets controller/FIFO/IDMAC, masks interrupts, polls command and DMA status |
| Slot 0 | GPIO39–42 D0–D3, GPIO43 CLK, GPIO44 CMD; native IOMUX function 0 |
| Clock | Re-enable SDMMC bus; PLL160M / 4 host clock, divided to at most 1 MHz card clock |
| LDO4 | Loader explicitly cycles LDO4, then leaves the software-owned 3.3 V rail on for Linux to verify and use |
| Radio slot 1 | Card clock disabled, no commands, no SDIO scan, no radio GPIO or power changes |
| MCU card detect | Unchanged; boot scan only, no hotplug or automatic reinsertion recovery |

The loader uses the pinned BSP's slot wiring and mounts FAT directly through
ESP-IDF. Before the mount, it isolates the slot pins, disables LDO4 and its
3.3 V bypass for four seconds, then restores LDO4 and lets the rail settle.
It checks the regulator register at each transition. Linux uses its own MMC
protocol stack, block device, FAT and loop filesystems.

The loader unmounts FAT and releases the IDF controller before handoff. LDO4
remains enabled. Linux owns the controller and initializes the card with the
MMC core, including initial clocks/CMD0, identification, selection and bus width.
It does not resume an IDF transaction or depend on inherited clock dividers.
It preserves USB, PSRAM and unrelated clock fields. The GPIO and IOMUX mappings are shared
with the limited keyboard GPIO provider. That provider reserves the entire
IOMUX bank; SD maps its six-register subrange without a second exclusive
reservation. The SD host/clock/LDO register ranges remain exclusive; SD touches only its six per-pin
registers and atomic output-disable bits. No broad GPIO/controller reset runs.

Linux block requests and scatterlist segments support one page (4096 bytes on
this build), with up to eight 512-byte sectors. Linux 6.18.35 rejects block
queues whose advertised maximum request or segment is smaller than a page.
The request handler splits a multi-sector read into sequential physical CMD17
reads, each at most 512 bytes through the existing buffer. SDHC/SDXC addresses
advance by one sector; SDSC byte addresses advance by 512. Physical CMD18/CMD23
and their stop sequence are not issued. A successful split read completes all
sectors; any failed sector fails the request without crediting partial bytes.
Address overflow, oversized requests, writes outside the frozen state-image
allowlist and unexpected command sequences are rejected before MMIO.
Descriptor buffers round up to four bytes;
only the requested byte count is copied to the scatterlist.
A one-second command/data deadline and bounded controller/clock-reset waits
prevent indefinite polling after timeout or removal. Read throughput and
USB/input latency must be measured before raising the 1 MHz limit. Host tests
simulate DMA memory views, cache ownership, controller status and faults; they
cannot model signal timing, physical DMA accessibility or all hardware recovery.

The host rejects writes outside the frozen state-image allowlist, erase, lock
and unknown commands before MMIO, including raw MMC ioctls. State writes use
single-sector CMD24; CMD25 remains denied. eMMC and SDIO probing are disabled.
The bounded-write design is detailed in the persistence guide. Root/NOMMU code
is trusted; this is not a security sandbox against arbitrary privileged memory
access.

## Internal DMA and cache ownership

Each host has one dedicated 512-byte data buffer and one 64-byte-aligned
64-byte descriptor. Its first 16 bytes match the pinned ESP-IDF P4 descriptor:
owner bit 31, first/last bits 3/2, chain bit 4, rounded buffer size, 32-bit data
address and null next pointer. The remaining 48 bytes isolate its cache line.
The buffer and descriptor are separately aligned and never share cache lines
with mutable host state. No user scatterlist page is used as a DMA target.

The pinned RISC-V kernel has `CONFIG_ARCH_DMA_DEFAULT_COHERENT=y`.
The SD node must declare `dma-noncoherent`: IDMAC cannot snoop CPU caches covering
PSRAM. Without that property, Linux considers the device coherent and streaming
sync calls skip the registered cache backend. The driver enforces this requirement at probe.
The driver now requires `dma_need_sync()` for the descriptor mapping, releases
both mappings and fails probe if synchronization would be skipped. Host coverage
executes the pinned OF coherence resolver with the compiled board declaration,
and tests rejection/cleanup of a coherent mapping. The DMA model still cannot
prove physical cache writeback or card transfers.

The platform device uses a 32-bit DMA mask. Linux streaming DMA mappings persist
until host removal; both the data buffer and descriptor use DMA_BIDIRECTIONAL
for the current read/write implementation. Before starting a command, explicit
CPU ownership permits setting
the buffer sentinel and descriptor; sync-for-device plus a DMA write barrier
publishes both. The bus master is reset before every data transaction, including the first
after handoff, so an old suspended descriptor chain cannot be resumed. NI/RI/TI
DMA completion enables are set, the new base is written, CTRL DMA/interface
enable precedes BMOD engine enable, then poll demand is written. CTRL global IRQ
enable remains clear, so no CPU interrupt handler is required.
No CPU FIFO port read occurs. The command/card read policy remains unchanged.

Success requires command done, card data done, DMA receive-complete status,
matching card/host byte counts and returned descriptor ownership without an
error bit. DMA is stopped/reset before sync-for-CPU, descriptor inspection or
copying bytes to the scatterlist. A one-second transaction deadline remains.
CRC, timeout, DMA bus/descriptor errors and missing host transfers fail the read.

Every exit after DMA start quiesces the engine. If a bounded reset fails, the
host is marked dead, further commands are refused, and mapped buffers stay
pinned until physical reset. Removal retries quiescence; it deliberately retains
the host allocation/mappings if hardware still cannot stop, preventing a DMA
write into freed memory. Normal removal unmaps only after stopping the engine.
Read DMA targets only dedicated kernel RAM buffers. State writes use the same
cache-ownership discipline; no arbitrary SD sector becomes writable.

## Root image and early boot

Buildroot produces the existing gzip initramfs and a SquashFS v4 base with gzip
compression and 32 KiB blocks. Kernel decompression uses one workspace and one
fragment-cache entry. The verifier checks the SquashFS header, bounded size,
and key executable/configuration bytes against the verified cpio archive.
The package contains `boot/rootfs.sqfs` and its generated SHA256 sidecar.
The 140-byte loader manifest still covers Image, DTB and initramfs; it has not
changed format. The root image is checked by Linux before mounting. Checksums
provide development integrity, not signatures or authentication.

PID 1 mounts devtmpfs/proc/sysfs, then searches slot-0 FAT partitions (and a
whole-card FAT filesystem) for `/apps/com.github.siviq.gaijin/boot`. It waits at most ten
one-second intervals for candidate devices, plus bounded driver/mount/read work.
FAT is mounted `ro,loud,nosuid,nodev,noexec`; only files beneath the project application
are read. It verifies the fixed root image checksum, obtains a free loop device
with `losetup -f`, attaches it read-only, and mounts SquashFS `ro,nodev,nosuid`.
The base release/ABI markers and init/mountpoints must match before proceeding.

FAT is moved to `/newroot/sd`, followed by proc, sysfs and devtmpfs. PID 1 then
execs `switch_root -c /dev/console /newroot /sbin/init`. The FAT file remains open
as the loop backing file and its mount remains reachable at `/sd`. Old
initramfs contents are released by switch_root. `/root`, `/home`, `/usr/local`
and `/run` each have a 1 MiB RAM cap; `/tmp` has a 4 MiB cap. The root transition
creates no userdata image, formats no filesystem and
issues no SD writes. Afterward rcS can activate provisioned persistent homes
or retain bounded RAM fallback, as described in the persistence guide.

Failures before the final exec unwind successful mount moves, unmount the base,
detach the allocated loop device and unmount FAT, then start the initramfs shell.
An unavailable/corrupt base does not reset-loop or remove user data. Passing
`gaijin.rescue` in Linux bootargs skips SD mounting (developer bootargs rebuild
required); the normal host-side missing-rootfs test below needs no rebuild.
A failure during the final switch_root exec, failed rollback, or a permanently
wedged controller still requires physical reset. Do not remove a live backing
card. There is no automatic repair or hotplug recovery in this milestone.

Updates replace only `/apps/com.github.siviq.gaijin/` and its AppFS entry. User data must
stay outside that replaceable application directory; the persistence guide
defines the current backing image and recovery policy.

## Host validation

Build all components, run `make test`, `make package` and `make verify`.
Tests compile the actual read policy, DMA helpers and transfer function against
a register/cache model with independent CPU and DMA memory views. They cover
all 1–512 byte lengths, rounded descriptors, nonzero SG offsets, mapping failures,
cache publication/acquisition, R2 response order, denied write/erase opcodes,
CRC/timeouts, DMA bus and ownership errors, missing host bytes, zero LBA0, SG
corruption, failed halt and refusing commands while memory remains DMA-owned.
The model includes inherited suspended state and rejects mutations that omit
the initial bus-master reset or noncoherent mapping check. The actual pinned NOMMU hush runs boot
fixtures for success, bad checksum, incompatible identity, missing card, failed
loop setup, failed mount moves and explicit rescue. These use simulated commands
and do not mount the host filesystem. Packaging rejects missing kernel support,
stale root-image verification, changed components and checksum inconsistencies. New regressions also execute
the actual pinned Linux request/segment-size rejection checks against the
host limits, and test every 2–8-sector request size in both address modes,
scatterlist offsets, address overflow, card-status errors and failure at each
sector.
A native probe regression loads both drivers' resource ranges from the actual
DTS, reproduces the original -EBUSY overlap, and verifies that shared mapping
succeeds while exclusive-resource errors and missing/failed maps propagate.

## Device regression capture

Install the complete current set and capture startup through the shell. Keep the
`SD reference: loader LBA0` and Linux `LBA0 dma=... sg=...` lines. The hashes
should match across all three views. On the accepted SD16G card the BSP
reference is `b700eedc`, 13 nonzero bytes, signature `55aa`, FAT start sector 63.
A different card can have a different hash; compare values from the same boot.
FNV-1a is a diagnostic fingerprint, not authentication.

Linux should report nonzero matching DMA/SG data, both card/host byte counters
512, `mmcblk0`, partitions and a verified root transition. The current block
device reports writable (`ro=0`) for bounded state access; FAT and SquashFS
remain mounted read-only. A failure before LBA0 produces one `DMA read error`
line with command, status, descriptor
and counters plus the live engine/base/current pointers captured before reset. Retain it and the full capture; do not infer success from card
identification alone. If startup reaches the SD root, run `m2-sd-test` before the
remaining acceptance checks.

The [P4 technical reference manual](https://www.espressif.com/sites/default/files/documentation/esp32-p4_technical_reference_manual_en.pdf)
describes internal DMA in sections 57.7–57.9. The pinned IDF/BSP implementation
supplies the working descriptor format and transaction reference. Linux uses
its own DMA/cache APIs; no ESP-IDF or FreeRTOS runtime runs inside the kernel.

## Target acceptance procedure

1. Install one complete set using [INSTALL.md](../../INSTALL.md). Keep a host copy
   of the SD contents and record the full console log. No formatting is needed.
2. Confirm kernel `6.18.35-gaijin-m3.1`, `tanmatsu-sd` polled IDMAC probe,
   `mmcblk0` identification, `Gaijin SD: verified read-only base`, root transition,
   ABI PASS and USB shell startup. Save `dmesg`, `cat /proc/mounts`, `losetup -a`
   and `cat /sys/block/mmcblk0/ro` (expected 0 for the current bounded-write host).
   `/` must be SquashFS on a loop device and `/sd` must be FAT; both must show `ro`.
3. Run `m1-smoke`, `m2-sd-test` and `m2-input-test`. The SD test drops read caches
   before each of three root-image hashes, then executes the ABI probe from the
   SD root. All must pass; record memory and check input/USB latency while reading.
4. Exit and confirm shell respawn; run another probe after the root transition.
   Stop applications and run `cd /; gaijin-persist stop` before each physical
   reset or moving the card to the host. Resolve any stop error first. Repeat
   at least three complete Launcher-to-Linux boots and physical resets.
   Confirm normal Launcher recovery and unrelated applications still work.
5. On the host, temporarily rename only `boot/rootfs.sqfs`. Boot must reach the
   RAM rescue shell after the bounded search, with no loop/root backing mounts.
   Restore the file. In a separate test, save the sidecar, replace its hash with
   64 zeroes, and verify checksum rejection/rescue; then restore the exact set.
   Do not alter the loader's Image/DTB/initramfs manifest for these tests.
6. Compare the card's files with the host baseline after testing; Gaijin must
   not modify unrelated files or its own SD application assets. The provisioned
   userdata image can change through normal persistence. No destructive/raw write
   test is needed on a shared card. Unexpected card-removal behavior is a
   recovery follow-up, not permission to remove a live root filesystem.

Retain logs, installed CURRENT.json/SHA256SUMS, card model/capacity, board/MCU
revision, boot/root transition observations, read times, idle memory, failures
and reset recovery. Close the SD gate only after target evidence is accepted.
