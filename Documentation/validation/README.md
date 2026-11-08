# Build and hardware validation

This record owns milestone acceptance and the limits of retained evidence.
The user confirmed all revision-33 device checks passed on 8 November 2026.
M5 is closed PASS; see [the current acceptance record](revision33.md) and
[device confirmation](evidence/m5/m5-rev33-device.json). Older pending results
below describe their named historical builds.
Use [the roadmap](../README.md#milestone-status) for current status and next
work, and subsystem guides for operating procedures. Public captures and historical
inventories are at the [locations below](#evidence-locations). Their observations
are preserved, with [personal and device identifiers redacted](evidence/README.md).

## M1 USB console and ABI acceptance

M1 is CLOSED — PASS, accepted by the user on 28 October 2026. The accepted
record confirms console handover, initramfs startup, software float/double ABI
execution, three completed smoke runs and shell respawn. USB write-timeout/stall
and target IXON/IXOFF settings remain accepted follow-ups. Later M2 and M3
captures provide regression evidence, not independent replacements for that
original acceptance. See [installation](../../INSTALL.md) for the current
flow-control workaround.

## M4 Keyboard completion review — COMPLETE through accepted M2

The [29 October 2026 review](../guides/keyboard.md#m4-completion-and-m5-handoff) confirms that the earlier accepted
M2 keyboard implementation satisfies the original M4 input scope. No additional
keyboard implementation is required before M5. The existing accepted capture
contains modifiers, repeat and 603 events with zero overflows and clear final
state. All 56 host tests and staged package verification pass again; the current
prepared kernel also passes a reverse-patch dry run. No new hardware capture
or acceptance is claimed. Local CLI typing is an M5 integration check once
display and fbcon exist. Electrical-fault, exhaustive physical mapping and
stress follow-ups remain recorded in the keyboard guide.

## M5 implementation and early device captures

Application **0.5.0 / AppFS revision 14 / Linux 6.18.35-gaijin-m5** adds loader
panel initialization, reserved display memory, native DMA refresh, RGB565
fbdev/rotated fbcon, tty1 login hush and the shared MCU backlight interface.
Host fault-injection covers repeated frame restart, descriptor cache ownership,
DMA stop-on-error, pixel flushes/underrun accounting, fixed mode/palette,
firmware-gated backlight writes and partial I2C failures. Loader/DT tests cover
reservation validation and enabling the display only after setup.

Loader, kernel and NOMMU userspace cross-builds pass. The
[first device boot](evidence/m5/m5-first-boot.log) confirms kernel #21,
100x30 fbcon, SD root, persistence, ABI, M1 smoke and M2 SD checks. The user
reported upside-down output; `m5-display-test` failed because hush rejects
`set -eu`. Version 0.5.1 / AppFS revision 15 changes rotation to 1 and uses
explicit diagnostic error checks, with native hush regression coverage.
Corrected orientation and the diagnostic still require a device retest.
The [saved inventory](evidence/m5/m5-first-boot-current.json) identifies the
workstation bundle; the capture does not independently establish the installed
AppFS revision. No complete M5 hardware acceptance is claimed.
Follow the [display acceptance procedure](../guides/display.md#hardware-acceptance-procedure)
and retain a capture before closing M5. Software power/reset/RTC remain tracked.

The [7 November interrupt-level investigation](native-umode.md) records the
native U-mode fix now incorporated into the current source. The device passed
the privileged-CSR/signal probe and the user reports successful shutdown and
poweroff cycles without hangs. Persistence was inactive because an unchanged
unclean ext2 flag remained; offline repair corrects it for the next test.
Revision 30 adds 10x18 console text and bounded exec-cache maintenance.
Active persistence and the new display changes still need device validation.

The user reported 15 successful launches with zero freezes on
`6.18.35-gaijin-mret-mmode-test`, the revision 28 LCD-only initramfs profile.
`shutdown` and `poweroff` showed a brief blue flash. The SD card's kernel
and manifest matched the test build and its userdata checksum matched the
pre-test checksum. Validation with Linux SD access and USB console enabled
is pending.

The follow-up test with a 50 ms wait in the kernel's shutdown notifier
showed several flashes and partial frames as shutdown began. The delay was
removed and the power controller blanks before cleanup output. The user
reported much less flicker, with brief flashes during typing and at power
transitions. Display flicker remains open as a separate issue.

Revision 29 restores the normal M5 LCD and USB shells, SD root and persistent
state. It incorporates the machine-mode trap workaround, bounded framebuffer
cache writeback and early shutdown blanking. The startup pulse and boot-trace
profile were retired. Hardware validation of the combined image is pending.

## Power commands and second state recovery — device transitions pending

Version **0.5.2 / AppFS revision 16** adds `reboot`, `shutdown`, `poweroff` and
`halt`, persistence cleanup, native P4 ROM reset/AppFS selection and MCU r9
power-off hooks. Host fault tests exercise action mapping, ordering, no-op
availability checks, unsupported/force options, cleanup failures, leftover
writable mounts, init resumption, retained selection and firmware/transfer
failures. No restart, halt or physical power-off on the device is claimed.
See the [device procedure](../guides/power.md#device-acceptance).

The [orientation retest](evidence/m5/m5-orientation-refresh.log) confirms the
user's correct orientation and `M5 refresh PASS`: frames 6757 → 6972, zero DMA
errors/underruns. After an unclean reset, state is rejected and RAM fallback
works. One local-shell exit/restart is logged; its cause is not established.

The inserted SD's 64 MiB userdata image was backed up and repaired offline.
Preen corrected only eight bytes within the primary superblock; all bytes
outside it and all 11 unrelated card files are unchanged. Full e2fsck on the
repaired card image passes. The [repair report](evidence/m5/m5-state-recovery.json)
and [fsck log](evidence/m5/m5-state-recovery.log) retain results; the private
compressed original stays under `backups/`. Device remount verification after
this repair remains pending. The partition table was not changed.

## Revision-16 reboot report and revision-17 cleanup changes

The user reported a blue-screen freeze after invoking reboot with state
mounted and recovered by physical power cycling. The
[Linux/serial excerpt](evidence/m5/m5-reboot-active-excerpt.log) contains the
active state mounts and display refresh PASS, followed by serial timeout and
disconnection. It does not capture the command or any power-cleanup/reset
marker. The unmodified combined log, including later Launcher output, is
retained privately; the public excerpt omits Launcher Wi-Fi details.

The [second capture](evidence/m5/m5-reboot-ram.log) shows dirty-state rejection,
RAM fallback, `already inactive`, the kernel Gaijin restart message, ROM
SW_SYS_RESET and a fresh Gaijin boot. This establishes reboot from RAM
fallback, not successful cleanup/reboot with writable persistence. The
[local revision-16 inventory](evidence/m5/m5-reboot-rev16-current.json) is retained.

The inserted userdata image was unclean but passed a forced structural check.
After a hash-verified backup, preen changed eight bytes only in its primary
superblock. A full check on the repaired card image passes; all bytes outside
that superblock and all 11 unrelated card files match before repair. See the
[repair report](evidence/m5/m5-reboot-state-recovery.json) and
[fsck output](evidence/m5/m5-reboot-state-recovery.log). The partition table is
unchanged. This precedes the separate managed application update on the card.

Version **0.5.3 / AppFS revision 17** uses checked ext2-only syncfs, clean-header
readback before mapper removal, no global final sync, and kernel-log shutdown
stages. The exact initial hang is unproven; active-state reboot and persistence
across the new reboot need device retesting. The kernel/ROM reset is unchanged.

## Accepted milestone result — M3 Persistent State CLOSED — PASS

Accepted by the user on **29 October 2026**: “This completes M3 implementations,
its a pass.” The accepted implementation is application **0.3.1 / AppFS revision
13**, kernel **6.18.35-gaijin-m3.1 #19**. The record covers reboot persistence,
selected settings, clean-start fallback, repeated stop/start, shell regression,
actual unclean-shutdown rejection and the complete host-repair/device-recovery
cycle. There are no remaining M3 implementation or acceptance blockers.

Automatic startup repair, board-aware shutdown, installation/resize UX and the
existing USB/stress follow-ups remain future work. They do not reopen M3.

### Hardware evidence

On 29 October 2026, Linux **6.18.35-gaijin-m3.1 #19** completed three clean
same-boot persistence stop/start cycles, three state-verification passes and two
shell respawns with the saved prompt. No allocation failures or errno 16 appear
in the new [capture](evidence/m3.1/m3.1-hw-regression.log). See [persistence evidence](persistence.md)
for its hash, preserved local inventory, earlier reboot/clean-start evidence
and the limits of the capture.

The restart and prompt regression gates pass. The later
[unclean-shutdown capture](evidence/m3.1/m3.1-hw-unclean-shutdown.log) demonstrates rejection
and working RAM fallback. The actual image was repaired and verified on the
host, with all unrelated card-file hashes unchanged. The
[post-repair device capture](evidence/m3.1/m3.1-hw-recovered.log) now confirms persistent
mount, saved prompt, state verify PASS and clean stop. The recovery cycle is
complete; see [the detailed record](persistence.md). Automatic on-device fsck
is not implemented. Captures start at a legacy-tagged loader handoff, so the
installed AppFS revision is unverified; kernel and DT identify M3.1.

Revision-13 host verification: 56 tests passed; loader, kernel/DTB and userspace
builds completed; packaging and `make verify` passed. The accepted M2
capture/inventory below refer to the older read-only build.

## Accepted milestone result — M2 Linux SD CLOSED — PASS

Validated on **29 October 2026**, application **0.2.8 / AppFS revision 11**,
Linux **6.18.35-gaijin-m2.8 #16**, Tanmatsu rev7 / MCU r9, SDHC SD16G 14.5 GiB.
The final transcript is retained verbatim in
[m2-sd-acceptance-console.log](evidence/m2-sd/m2-sd-acceptance-console.log), SHA256
`415d1c86618fe9738105bd18f87000fc6966834f46196ce0d9c77f222eafd8fa`. The original tested host inventory is
[m2-sd-acceptance-current.json](evidence/m2-sd/m2-sd-acceptance-current.json). Runtime identity
and printed asset sizes match that set; no full device asset-hash listing is
present. The first successful boot segment repeats the earlier seventh capture
and is counted once within this transcript.

| Check | Evidence in final capture |
| --- | --- |
| Repeated SD boot | Four successful Linux boot segments; all find partition p1 and switch to SD root |
| Sector data | All four BSP/DMA/SG fingerprints match b700eedc; signature 55aa; both counters 512 |
| Read-only storage | FAT /sd and SquashFS / mounts are ro; /sys/block/mmcblk0/ro prints 1 |
| Repeated reads/execution | Five M2 SD PASS results: 15 cache-dropped root-image SHA256 checks and ABI runs |
| Userspace regression | Five completed M1 smoke passes, including 20 external execs per run |
| Input regression | 603 events, zero overflows, final key/switch state clear; physical key comparison remains manual |
| Shell lifecycle | Two exits followed by shell respawn; date/pwd and Ctrl-C work in the last shell |
| Launcher return | Three intervening Launcher startups, Wi-Fi connection and subsequent Gaijin launches |
| Linux SD errors | No DMA/MMC/FAT I/O error, timeout, oops or panic shown |
| Memory | SD-test ending MemAvailable 22996–23000 KiB; no progressive loss visible in these samples |

The user supplied this as the complete tests and then confirmed that the
missing-root rescue, bad-checksum rescue and before/after SD file-hash checks
were performed outside the capture: “Yes, I confirm they were done outside this
tests.” Those checks are recorded as **user-confirmed completion**, not as
independently observed console output. Their commands/results are not in the
attachment. With that confirmation and the captured passes, the SD milestone is
closed. Persistence and hotplug remain outside its scope.

One earlier M1 smoke invocation at transcript lines 434–452 stops after the ABI
probe and five host serial write-timeout warnings, before a PASS line. It is
not counted as a pass. Five later smoke invocations complete. The cause and
intent of the subsequent reset cannot be established from the capture or the
off-log-test confirmation. Keep the existing USB/flow-control issue as an
accepted follow-up; do not characterize this transcript as warning-free.

Other retained observations: Launcher SDIO radio retries and management-MCU I2C
timeouts occur before recovery and Wi-Fi connection. They are in Launcher
startup, not Linux slot-0 transfer errors; their cause is not established here.
The monitor's flashed/built application checksum warning during Launcher boot
is distinct from the Gaijin rootfs checksum-rejection test. BSP LDO power-cycle
warnings and duplicate Linux console handover lines remain present.
The keyboard capture and SD helper run sequentially, so simultaneous input/USB
latency under sustained reads is not quantified. Long stress runs, other card
models and performance tuning remain follow-ups rather than closure blockers.

The accepted M2 capture and inventory remain unchanged. Current M3 binaries
require installation as a complete new set; they have not inherited M2 hardware acceptance.

## Current source and host verification

`linux/patches/0001-gaijin-esp32p4.patch` contains the board implementation;
`linux/tanmatsu_defconfig` and `linux/tanmatsu.dts` are the only supported board
configuration and DTS. The board patch includes platform, USB, keyboard/MCU and
bounded-write SD support. The prepared Linux source passes a reverse-patch
check, and its patch marker matches the source lock. The November 8
[user-access correction](../guides/user-access.md) is a second ordered patch;
its new complete image and device validation remain pending.

The earlier M3 host suite had **59 tests**, covering loader bounds and handoff,
console selection, ABI/packaging, keyboard failures, SD DMA/cache ownership,
request splitting/write ranges, SD rescue, state mapping/restart, offline-image
creation, NOMMU prompt loading, recursive public-document packaging, source/bundle
links and live evidence locations/hashes. `build/current-tests.log` and
`build/VERIFICATION.json` record the latest workspace verification; the report
compares every staged SD file with its pre-consolidation hash and verifies
all moved raw evidence/reference files against their original bytes.
`make verify` and ZIP integrity also pass. This documentation/housekeeping
refresh leaves the accepted application/boot files unchanged and requires no
firmware reinstall.

The private original userdata recovery image is retained once as a verified
gzip under `backups/`, separate from generated builds and excluded from the
installation archive. See [workspace layout](../development.md).

## Evidence locations

The [revision-33 validation](revision33.md) describes the standalone boot and
console changes, host regressions and user-confirmed PASS for the device checks.
The earlier [revision-32 validation](revision32.md) records the correction to
`com.github.siviqt.gaijin`, copied-installation checks and remaining device tests.

The historical [revision-31 validation](revision31.md) records 93 passing host
tests with no skips, complete package checks, native editor tests and RV32
user-access/CLIC checks. Its new on-device editor tests remain pending.

| Tested release | Captures and inventories |
| --- | --- |
| M2.8 / application 0.2.8 / revision 11 | [SD and keyboard capture](evidence/m2-sd/m2-sd-acceptance-console.log); [tested inventory](evidence/m2-sd/m2-sd-acceptance-current.json) |
| M3 / application 0.3.0 / revision 12 | [Persistence](evidence/m3/m3-hw-persistence.log); [clean-start/restart](evidence/m3/m3-hw-clean-start-restart.log); [restart](evidence/m3/m3-hw-restart.log); [tested inventory](evidence/m3/m3-hw-current.json) |
| M3.1 / application 0.3.1 / revision 13 | [Regression](evidence/m3.1/m3.1-hw-regression.log); [unclean shutdown](evidence/m3.1/m3.1-hw-unclean-shutdown.log); [recovered device](evidence/m3.1/m3.1-hw-recovered.log); [tested inventory](evidence/m3.1/m3.1-hw-current.json); [recovery report](evidence/m3.1/m3.1-offline-recovery.json); [host repair output](evidence/m3.1/m3.1-offline-recovery.log) |

These files moved from the formerly flat `Documentation/` directory; their
basenames and hardware observations are preserved. Public copies have been
redacted, and their checksum references updated. Current links and
[sources.lock.json](../../sources.lock.json) use the locations above. Paths
inside captured JSON inventories/recovery reports remain as recorded, including
old document names and `INSTALL.txt`. They describe the tested historical set,
not the current layout. Resolve an old raw-evidence basename using this table;
current guides are listed in the documentation index. Never rewrite a captured
inventory to make it resemble a new release.


## Revision-17 reboot completed; post-boot consoles unresponsive

[m5-reboot-rev17.log](evidence/m5/m5-reboot-rev17.log) records successful active-state
cleanup, raw clean-header verification, reset and a fresh Linux boot that mounts
persistence. The user reports that USB and the blue local screen are then
unresponsive. The trace ends at serial shell launch, so shell readiness is not
established. The whole-card FAT probe fails before the partition mount succeeds.

The [offline inspection and repair](evidence/m5/m5-reboot-rev17-state-recovery.json)
found an unclean flag but no structural errors. Eight primary-superblock bytes
changed; user data and every other card file stayed unchanged during repair.
The successful second mount and later forced power-off explain why the image
can be unclean without showing failed reboot cleanup.

Application 0.5.4 / AppFS revision 18 moves software-flow-control setup after
BusyBox init resets termios, and adds launcher/profile markers plus a delayed
read-only boot snapshot. This is a tested termios correction and diagnostic
build, not a verified fix for both consoles freezing. Device validation remains
pending. See the [power guide](../guides/power.md) for trace interpretation.

## Revision-22 display-disabled power tests

On 5 November 2026, Gaijin 0.5.8 / AppFS revision 22 booted twice to an
interactive USB shell with display initialization and DMA disabled. Both
boots mounted persistent ext2 state. `shutdown` completed its raw clean-state
check and map removal, then returned to AppFS Launcher v0.3.1. The manual
Gaijin relaunch mounted state again. `poweroff` completed the same cleanup
and the serial link disconnected at the power transition. The user confirmed
the device switched off. [Console capture excerpt](evidence/m5/m5-rev22-power-excerpt.log)

The result covers the two power commands on the display-disabled build.
Boot stability with display enabled remains open.
