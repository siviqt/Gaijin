# Home-storage validation

Application **0.5.24 / AppFS revision 38 / Linux 6.18.35-gaijin-m5** provides
automatic home creation, initial preferences and explicitly confirmed
dirty-image replacement. The user confirmed revision-35 image creation and
setup-menu display on Tanmatsu, then upright HOME output and normal Linux
operation on revision 36. The user confirmed revision 37 removed HOME flicker;
revision 38 targets the remaining startup flashes. Full storage
qualification remains pending.
Revision 33 remains the last user-confirmed hardware baseline. M1–M5's
historical acceptance is retained independently of this new feature gate.

## Host coverage

The [revision-38 host record](evidence/home-storage/revision38-host.json)
records the current kernel, loader, userspace and package checks. The
[boot display check](revision38.md) covers its bounded Linux rendering and
completion-service diagnostics; physical confirmation is pending.

The [revision-37 host record](evidence/home-storage/revision37-host.json)
records **148 tests passed, zero skipped**, rebuilt loader, compiled ISR/state
placement and verified packaging. Kernel and userspace are unchanged from
revision 36 by hash.

Revision 37 adds 16 focused checks executing the shipped loader ISR,
rendering and teardown code with injected faults, strict read-only diagnostic
marker tests and compiled internal-RAM placement checks. The
[revision-37 test procedure](revision37.md) describes the non-destructive
ten-second static/animated screen and the refresh summary. HOME visual flicker
is now user-confirmed resolved; extended qualification remains pending.

The historical [revision-36 host record](evidence/home-storage/revision36-host.json)
records **132 tests passed, zero skipped**, the rebuilt loader and verified
package. Kernel and userspace hashes match revision 35. This release changes
only loader progress orientation and release metadata/documentation.

The [revision-35 host record](evidence/home-storage/revision35-host.json)
records **132 tests passed, zero skipped**, rebuilt loader/NOMMU userspace,
real-FatFs sanitizer tests and verified packaging. The kernel is unchanged
from revision 34 and matches its recorded hash and CLIC results.

The historical [revision-34 host record](evidence/home-storage/revision34-host.json)
records **123 tests passed, zero skipped**, successful loader/kernel/NOMMU
userspace builds, all 60 RV32 CLIC scenarios and the interrupt negative control.
Package integrity and the public-source audit also pass. The record binds
these results to the component hashes; it does not claim device acceptance.

Revision 35 adds execution against **ESP-IDF 6.0.2's actual FatFs** with
dynamic file caches enabled, a sparse regular-file FAT32 disk and poisoned
stack locals. Creation, clean reuse and dirty reset must reproduce the factory
hash. Address/undefined-behavior sanitizers check memory use. A negative control
removing local `FIL` initialization must fault in `f_open` called by
`home_mark`. Hardware/FDT/display calls and the ROM inflater use host adapters;
the decompressor adapter uses independent zlib, so it does not cover ROM faults.

The actual loader FAT guard is exercised against FAT16/FAT32 sector fixtures:
boot dirty state, each FAT clean/error bit, I/O failure, mirroring, meaningful
copy mismatch and harmless reserved FAT32 high-nibble differences. The new
read-only host diagnostic is checked for unchanged input bytes. PTY coverage
also checks actionable FAT/incomplete messages and unavailable menu entries.

## Revision 34 device failure — 9 November 2026

The first user capture reported `Gaijin home template v1: fat-error`, RAM
homes and inactive persistence. The unmounted host card retained boot state
`0x01`; both FAT[1] copies were `0xffffffff`. Read-only host fsck found only
the dirty boot flag and backup difference at byte 65. After host correction,
the metadata check showed boot state `0x00` and no observations.

The next boot entered creation and panicked with a store-access fault.
Decoding the matching revision-34 ELF mapped return address `0x4000fb38` to
ESP-IDF `f_open`, `ff.c:3897` (clearing the dynamic sector cache), called from
`home_mark` at `home.c:111`. A local uninitialized `FIL.buf` supplied the bad
pointer. All three local file objects are zero-initialized in revision 35.
The real-FatFs sanitizer negative control reproduces this specific call path.
This is a failed device test, not acceptance of home creation or setup.

The actual portable transaction runs with fault-injected storage operations.
Tests cover new-image creation, unchanged existing files, no-space handling,
incomplete-file rejection, identity rechecks, failure before/after deletion
and publication, and refusal to repeat an erase after interruption. A verified
candidate must exist before deletion is called.

Native PTY tests execute the real setup program against an isolated filesystem:
initial prompt/completion storage, invalid prompt data, cancellation, ineligible
images, exact ERASE confirmation and competing-console exclusion. The mock
power program records requests; no host filesystem or device is erased.

The mapper fixture executes probe and readonly DM modes without registering
write ranges, and exercises fresh-header rejection and reset identity. The
patched power callback is run against modeled retained memory; ordinary
commands clear AppFS selection while a valid reset command publishes only the
supported AppFS 2.1.0 descriptor/valid/argument fields. The loader argument
parser is checked against independent byte layouts. Existing DMA, ABI,
console, keyboard, display, editor and shutdown regressions remain required.

Factory generation passes exact format checks and host e2fsck. Independent
generation produces identical expanded/compressed hashes. The target build
verifies all seven static bFLT executables, initramfs bounds and matching
SquashFS contents. SHA256 is tested against independent standard/hashlib
vectors. These host models do not emulate the P4 ROM inflater, physical SD,
cache/DMA timing, display scanout or power-loss durability.

## Revision 35 device result — partial pass, 9 November 2026

The user reported: “Home loading screen was upside down, but it worked.
Having 32 MiB partition image created, all 6 options displayed.” The
[scoped device record](evidence/home-storage/revision35-device.json) confirms
creation and initial menu display, including normal ROM streaming and handoff
through this boot. It does not establish that every menu action was exercised.
Preference saving, clean reuse, destructive reset and interruption checks below
remain unreported.

The loader used `(native x, native y) = (y, 799-x)`, opposite to the kernel's
accepted `fbcon=rotate:1`. Revision 36 uses `(479-y, x)`, following Linux's
clockwise framebuffer mapping. Linux panel configuration is unchanged. The
upright orientation was subsequently confirmed on revision 36; an existing
valid home is reused without showing creation progress.

## Revision 36 device result — orientation pass, HOME flicker open

The user confirmed correct HOME orientation and expected Linux behavior, but
reported constant light-blue flicker during the home operation which stops
once the console loads. The [device record](evidence/home-storage/revision36-device.json)
captures that scope. Investigation found timer-based DMA rearming and
full-frame cache maintenance on every image chunk, consistent with DSI FIFO
starvation. On-device raw underrun evidence is still needed. The
[loader refresh proposal](../reference/home-progress-flicker-proposal.md)
defines the interrupt, cache, teardown and device-validation dependencies.
Its correction was implemented in revision 37; the user confirmed HOME
flicker is gone. See the [scoped device result](evidence/home-storage/revision37-device.json).
Startup-only blue flashes remained, while `m5-display-test` scrolled cleanly
with zero DMA errors/underruns. Revision 38 adds bounded Linux drawing and
completion polling; its [device check](revision38.md) is pending.

## Remaining device procedure

Use a backed-up card for normal checks and a disposable card for interruption
tests. Install the matching revision-38 AppFS loader and all SD assets. Record
CURRENT.json and SHA256SUMS, card layout, memory and timings with each capture.

1. Start with eligible FAT16/FAT32, at least 48 MiB free and no home image or
   provisioning artifacts. Cold boot with USB disconnected. Observe HOME
   progress, then initial setup. Save a prompt, Continue, and confirm persistent
   `/root` and `/home` plus ro `/sd` and `/`. Run the existing smoke/SD/input/
   display/power checks; inspect memory and display DMA errors.
2. Write a proof file, shut down cleanly, relaunch and confirm content and
   preferences without another wizard. Repeat three times, including clean
   same-boot stop/start and an application-directory replacement. Existing
   compatible host-provisioned sizes must be reused without resizing.
3. On a disposable copy, create a dirty superblock while Linux is stopped.
   Boot must preserve the image, use RAM and expose host instructions. In
   `gaijin-setup`, cancel reset and compare offline hashes: no image change.
   Repeat with an incompatible format/schema; no reset option is offered.
4. Restore a recognized dirty test image, select Erase home and recreate,
   and type ERASE. Confirm safe automatic relaunch of the same AppFS entry,
   HOME progress, empty 32 MiB state, repeated initial setup, and unchanged
   unrelated file contents. Confirm the old home/settings were discarded.
   A following boot must not repeat deletion.
5. Test insufficient candidate space and a changed card/image identity. Reset
   must refuse deletion and retain the original. Known FAT/card errors and
   existing `provision-v1` or `userdata.new` require host inspection. Test
   clean-start/rescue suppression, including when an erase argument is present.
6. Interrupt disposable creation/reset at marker creation, streaming/readback,
   old-file deletion and publication. Leftover transaction state must select
   RAM and suppress automatic missing-image creation. Preserve artifacts and
   resolve them offline; never use the only copy of user data for these tests.
7. Back up and repair a dirty test image on the host, or migrate an incompatible
   test filesystem into a supported host-created image. Verify it offline,
   restore it, and confirm clean reuse with preserved files/settings on-device.
8. Repeat standalone boot and late USB attach/disconnect checks. Record time
   to progress/setup/shell, contiguous-allocation failures and peak memory.
   A display-progress failure must not hand a running loader interrupt/DMA stream
   into Linux. Confirm clean shutdown, Launcher return and physical power-off.

Do not mark the current build fully device-accepted until these results are captured and
accepted. [Persistent state](../guides/persistence.md) owns operation and
the implementation dependency record.
