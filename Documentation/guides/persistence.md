# Persistent state

Implementation build: **0.5.24 / AppFS revision 38 / Linux 6.18.35-gaijin-m5**.
Automatic creation, setup and confirmed dirty-home reset are implemented; their
[device acceptance](../validation/home-storage.md) remains pending. Revision 33
is the last user-accepted hardware baseline. The
[implementation record](#proposed-first-run-storage) describes the new paths
and their dependencies. The original M3 storage contract and host tools remain
applicable except where the current behavior below extends them.

Historical M3 acceptance: application **0.3.1**, AppFS revision **13**,
Linux **6.18.35-gaijin-m3.1**.
**M3 is CLOSED — PASS, accepted by the user on 29 October 2026.** The actual
unclean image was repaired on the host and verified again on Tanmatsu. See [captured results](../validation/persistence.md).
The accepted M1 and M2 evidence remains in [validation](../validation/README.md) and its original captures.

## Storage contract

The immutable base and boot assets remain under `/apps/com.github.siviqt.gaijin/`.
User state is a separately provisioned **`/gaijin/userdata.ext2`** on the same
FAT card, outside the replaceable application directory. Packaging never includes or overwrites this live image. The current loader
creates it only when absent with no transaction artifacts, or explicitly
replaces an eligible dirty image after confirmation. Updates reuse compatible
existing data. Keep a separate backup.

The initial image is 32 MiB; supported sizes are 16–256 MiB. It uses ext2 with
1 KiB blocks, 128-byte inodes, the `GAIJIN-STATE` label and only filetype,
sparse_super and large_file features. This conservative format has 32-bit
inode timestamps (through 2038). There is no journal. The on-device clock still
starts at the Unix epoch; RTC support is separate work.

| Runtime path | Storage |
| --- | --- |
| `/` | Read-only SquashFS loop, unchanged boot transition |
| `/sd` | Read-only FAT, never remounted writable |
| `/persist` | Writable mapped ext2 image, `noatime,nosuid,nodev,errors=remount-ro` |
| `/home` | Bind of `/persist/home` |
| `/root` | Bind of `/persist/home/root` |
| `/persist/settings/prompt` | Optional ASCII prompt label, 1–32 letters/digits/underscore/dot/hyphen |
| `/etc` | Factory settings in the immutable base |
| `/run`, `/tmp`, `/usr/local` | Existing bounded RAM mounts |

The prompt preference is read as data into `/run/gaijin-prompt` and applied by
the login profile. Persisted shell code is never sourced. Other settings and
application state formats are reserved for their owning subsystem. This M3
does not introduce a writable root overlay or a general `/etc` overlay.

## Safe transitions

Current builds provide `shutdown` (Launcher), `poweroff` (device off) and
`halt` (Linux stopped). They stop other user processes and
cleanly deactivate persistence before invoking Linux power control. Use these
for normal transitions; physical reset or power loss still needs offline repair
if state was mounted. See [power commands](power.md) and the
[revision-33 acceptance](../validation/revision33.md), which includes the
persistence and power-transition regressions.

## Bounded writes with read-only FAT

A writable loop attached directly to a file on read-only FAT is not possible.
`gaijin-state-map` therefore validates the fixed image path, rejects symlinks,
verifies a read-only FAT mount and obtains every allocated block using Linux `FIGETBSZ`/`FIBMAP`.
It checks partition capacity, zero/unallocated blocks, overlap and overflow,
and limits the image to 64 physical extents. It rederives extents at each boot;
there is no stale sector-map sidecar to copy or maintain. After deriving the
allocation, it reads the two ext2 superblock sectors directly through an
aligned `O_DIRECT` raw partition read and checks the clean state, label,
geometry and supported features. It does not trust a cached FAT file header
when restarting state in the same boot.

The helper opens the exact slot-0 FAT block device through a dynamically
allocated direct-I/O loop, then creates a device-mapper linear table for only
the image extents. This extra loop avoids an exclusive device-mapper claim on
the already-mounted FAT partition. The loop is auto-cleared when its last
reference closes. The ext2 device is dynamically selected `/dev/dm-N`; the
SquashFS loop number is also dynamic. FAT retains ownership of its metadata
and directory entries throughout.

Before activating the map, the helper supplies the absolute image sectors to
the SD host's root-only `write_ranges` attribute. The allowlist is frozen until
physical reset. Re-registering the identical range list is idempotent;
replacement, widening, narrowing and added ranges are refused. It starts empty; the host rejects CMD24 outside those sectors
before MMIO. Raw erase, locking, CMD25 and vendor commands remain denied.
Linux page-sized write requests are split into bounded CMD24 sectors, just as
reads are split into CMD17. DMA uses bidirectional streaming mappings with
explicit cache ownership, TI completion for writes, byte counts, descriptor
ownership/error checks and card-busy completion within the existing deadline.
A request failure credits no partial bytes, although earlier sectors may have
reached the card. ext2 and the MMC block layer receive the error.

`/sys/block/mmcblk0/ro` now reports **0**, allowing the mapped device to open
for writes. It is the host's sector gate, rather than block-wide read-only,
that bounds writes. FAT and SquashFS must still show **ro** in `/proc/mounts`.
This is an accident guard for trusted root/NOMMU code, not a security boundary
against privileged reconfiguration or arbitrary memory writes.

Do not read/hash `userdata.ext2` through `/sd` while its map is live: the FAT
file cache is a separate read-only view and can be stale after direct writes.
Use `/persist` for live data. Never modify allocation, move the file or remove
the card while Linux runs. A corrupt FAT with cross-linked files cannot supply
a trustworthy allocation map; validate the card offline before provisioning.

## Optional provisioning on the host

Revision 37 normally creates a missing 32 MiB image. For host provisioning or
a different supported capacity, stop Linux, attach the card to the host,
retain a backup and run
from the project or extracted bundle:

```sh
python3 tools/create_state.py /path/to/SD/gaijin/userdata.ext2
# Optional initial capacity:
# python3 tools/create_state.py /path/to/SD/gaijin/userdata.ext2 --size-mib 64
```

Host prerequisites are Python 3 and e2fsprogs (`mke2fs`, `debugfs`, `e2fsck`).
The tool writes a complete allocated file, sets the versioned directory tree
and root ownership, and runs a non-modifying offline check before copying it
with exclusive creation. It refuses existing files and symlinks. It never
formats the SD card or modifies a partition table. Safely eject the host card.
Do not run this command during an application update: it is a separate,
explicit provisioning step. The installation ZIP contains the script, not a
blank state image that could overwrite existing data.

## Boot and clean shutdown

After switching to the immutable base, rcS mounts RAM `/run` and `/tmp`, then
attempts state activation. The superblock must be clean and the directory
version must match. Missing, dirty, incompatible or excessively fragmented
state produces a diagnostic and bounded RAM `/home` and `/root`; the base USB
CLI remains usable. Revision 37 can initialize a genuinely absent image in the loader, before this
Linux activation path. Filesystem repair remains an offline host operation. The superblock check is a conservative eligibility check,
not a full filesystem scan. Later ext2 errors request a read-only remount.

`cat /run/gaijin-persist` shows the active mapped device. The absence of that
file means persistence did not fully activate. If `/run/gaijin-state-map`
remains after a cleanup error, run `gaijin-persist stop` and resolve the error
before reset. Partial unmounts and failed map removal can be retried. Before physical reset or returning to Launcher:

```sh
cd /
gaijin-persist stop
```

Stop applications using persistent files first. The command synchronizes,
unmounts `/root` and `/home`, unmounts ext2 and removes its map. Busy/error
returns must be resolved before reset. A plain `sync` does not mark a mounted
ext2 image clean. Physical reset, power loss or removal while mounted can leave
the image dirty and trigger RAM fallback at the next boot. Use `shutdown` or
`poweroff` to run cleanup as part of the transition.
After a successful stop, `gaijin-persist start` can reactivate the same image
within the same boot. Its fresh superblock must still be clean and its
allocation must exactly match the frozen write gate. An active/partially
stopped map must be stopped before starting again. The saved prompt takes
effect on the next login shell. The login profile uses builtin `read`, avoiding
a BusyBox re-exec for command substitution on NOMMU. General memory
fragmentation remains a separate system constraint.

## Clean start and offline recovery

For a clean start without changing the boot assets, create an empty file named
`/gaijin/clean-start` on the host. It skips mapping and all state writes until
you remove it. Developer bootargs `gaijin.clean` do the same; `gaijin.rescue`
also skips the SD root transition. In the accepted revision-33 baseline, a missing or renamed image selects RAM
fallback. Revision 37 instead creates a missing image unless clean-start/rescue
or transaction artifacts suppress creation. Use the explicit clean-start
marker when a RAM session is wanted without creating storage. Base checksum failure continues to
use the existing initramfs rescue path.

For a dirty or corrupt image, stop Linux and work on a **copy** on the host:

```sh
cp /path/to/SD/gaijin/userdata.ext2 /path/to/backup/userdata.ext2
# Inspect the copy without modification:
e2fsck -fn /path/to/backup/userdata.ext2
# Explicit repair of the copy (interactive decisions):
e2fsck -f /path/to/backup/userdata.ext2
```

Verify the repaired copy, then with the card offline preserve the original
under another name and copy the repaired image back to the fixed path. If the
card FAT is damaged, recover/check it using host tools before trusting its
file allocation. Never automatically delete or recreate a rejected image.
For offline growth, back up, run `e2fsck -f` on the copy, extend it to an exact
MiB size within 256 MiB, run `resize2fs`, check it again, and copy it back while
Linux is stopped. The image must retain its label, supported features and
versioned directories. Keep free card space for a backup and application updates.

An incompatible format or directory schema needs host-side inspection and,
where necessary, migration into a freshly created supported image. A successful
e2fsck does not convert unsupported feature flags or application schemas.
Preserve the original, verify the replacement with `tools/check_state.py`,
and check its versioned directories before returning it to the card. Repair,
backup restoration, migration and capacity changes remain offline host work.

## Host validation

`make test` runs the actual SD DMA transfer code against independent CPU/DMA
memory views for reads and writes, range endpoints, byte/block addressing,
CRC/timeouts, DMA faults, stuck busy and failed engine halt. The request tests
cover 2–8-sector read/write splitting and address overflow. The native mapper
fixture executes the complete C helper using modeled FIBMAP, loop and DM ioctls:
contiguous/fragmented files, physical partition offsets, table geometry,
holes, overlap, excessive fragmentation, capacity errors, invalid superblocks,
gate failures and rollback. No test writes host block devices.

Pinned NOMMU hush executes the actual persistence startup/stop script with
simulated mounts: clean modes, all mount failure points, version rejection,
settings validation, sync failure, bind mounts and unmount order. An actual
host image build is checked with e2fsck and debugfs, including ownership and
refusal to overwrite existing data. The userspace verifier checks the new
bFLT helper and scripts in both cpio and SquashFS; packaging requires ext2/DM
support and the new release identity. Host models do not prove P4 write timing,
physical cache/DMA operation, card durability or power-interruption recovery.

## Device acceptance gate

1. Back up the shared card, check FAT offline, provision the state image, and
   install the complete revision-13 set. Save CURRENT.json, SHA256SUMS, card and
   board identity and a full console log. Confirm kernel `6.18.35-gaijin-m3.1`.
2. Confirm `/` SquashFS and `/sd` FAT are ro, `/persist` is ext2 rw, and `/home`
   and `/root` bind to its directories. Record `/proc/mounts`, `losetup -a`,
   memory, both BSP/Linux LBA0 fingerprints and `/run/gaijin-persist`.
3. Run `m1-smoke`, `m2-sd-test`, `m2-input-test`, then `m3-state-test prepare`.
   Run `cd /; gaijin-persist stop`, reset and launch again. Run
   `m3-state-test verify` to check content, symlink, permissions and the prompt
   setting across reboot. Repeat at least three times with clean unmounts.
   Retain existing proof between verify runs; prepare deliberately refuses to
   overwrite proof. Repeat smoke/input checks while doing state I/O and observe
   USB/input latency and memory.
4. Stop cleanly, reset to Launcher, replace only `/apps/com.github.siviqt.gaijin/` with a
   complete compatible set, and verify state/proof survives the update.
   Compare offline hashes of boot assets and unrelated card files with the
   baseline after the write tests. FAT metadata must also remain unchanged.
5. On the host add `/gaijin/clean-start`. Boot must use RAM with no state map;
   write a disposable file in RAM `/home`, reset and confirm it disappears.
   Remove the marker and confirm the original state/proof returns. Separately
   rename only the state image and confirm the same fallback, then restore it.
6. Use a **disposable copy** of the state image to test a dirty superblock and
   bad magic/unsupported features. Boot must reject them without altering the
   image and reach the base CLI. Repair a dirty test copy offline, restore it
   and confirm activation and file integrity. Save before/after hashes.
   Abrupt-reset testing, if needed, belongs on a disposable card/copy, not the
   only copy of user data. No destructive raw write test is necessary.
7. In the same boot run `cd /; gaijin-persist stop; gaijin-persist start`, then
   `m3-state-test verify`. Repeat at least three times and check `losetup -a`
   for leaked loops/maps. Exit/re-enter the login shell with the persisted
   prompt and confirm no `nommu: Allocation ... failed`/bFLT errors appear.
8. Check successful clean stop, Launcher recovery, clean offline `e2fsck -fn`,
   unchanged unrelated files and repeated successful remounts. Retain any MMC,
   ext2 or DMA errors. Do not close M3 until device evidence is accepted.

## Exact host readiness check

Use `python3 tools/check_state.py /path/to/gaijin/userdata.ext2` on the host.
It validates the supported ext2 format and requires superblock state 1 before
running read-only `e2fsck`. An image with state 0 may pass `e2fsck -fn` with no
structural errors while remaining unclean: read-only mode cannot write that
flag. Gaijin will correctly refuse that image.

Back up an offline image before repairing a copy with `e2fsck -fp`; accept only
exit 0 or 1, then rerun the exact readiness check. Preserve its size and data.
Never clear the flag manually or format an existing user image to bypass a
failed check. When installing a repaired image into an allocated FAT file,
write the same-size contents in place while the card is on the host; do not
extend or replace the image while Linux is using it.

## Proposed first-run storage

The accepted design is implemented in **0.5.24 / AppFS revision 38**. Host tests
and target builds validate software behavior; the new loader progress,
creation and reset paths still require [device acceptance](../validation/home-storage.md).
The old test image is unnecessary: the build generates a checked factory seed.

### Startup policy

| State | Revision-34 behavior |
| --- | --- |
| Home absent, no creation artifacts, eligible FAT16/FAT32 card | Loader creates a 32 MiB factory image. First Linux startup opens setup. |
| Clean supported 16–256 MiB image | Reuse its capacity, files and preferences; no seed expansion. |
| Clean image with `setup-pending`, no completed record | Reuse and resume initial preferences. |
| Older compatible image without setup records | Reuse it; `gaijin-setup` is available manually. |
| Recognized dirty Gaijin ext2 image | Preserve it, use RAM and offer host recovery or an explicitly confirmed destructive reset. |
| Incompatible/damaged image, FAT/card error or incomplete transaction | Preserve it, use RAM when the base is readable and require host inspection. |
| Clean-start/rescue | Suppress loader creation/reset and Linux persistent activation. |

`/gaijin/userdata.ext2` is the card path; Linux sees that file under `/sd`.
The existing mapper exposes `/persist`, with `/home` and `/root` bound to its
home directories. This is a file-backed filesystem; it does not repartition
the shared card. FAT and SquashFS remain ro throughout Linux runtime.

### Factory creation and capacity

`tools/build_home_seed.py` uses the host creator/checker and e2fsprogs to build
an exact-format image with root-owned directories, `gaijin-state-v1` and a
`gaijin-setup-v1` pending record. Generation is deterministic and records tool
versions, source hashes and expanded/compressed hashes in
`build/home/home-seed.json`. A zlib stream is embedded in the AppFS loader.
The [earlier feasibility experiment](../reference/first-run-storage-feasibility.json)
measured the original M3-compatible seeds; the current seed also includes the
pending-setup record.

The loader uses the pinned P4 ROM inflater, an internal-RAM 32 KiB dictionary
and bounded buffers. It writes every expanded byte and verifies SHA256 and
full file readback before publishing. It never allocates the full image in
RAM. BusyBox's bundled formatter is not used; its feature flags differ from
the supported Gaijin format.

Creation/reset requires a contiguous 32 MiB candidate plus **16 MiB free update
headroom**: at least 48 MiB free, excluding any old image that will be deleted.
Insufficient contiguous allocation can still fail despite enough total free
space. Existing qualified fragmented images retain the 64-extent reuse limit.
Different capacities and resizing remain host operations.

Known FAT dirty/error flags and inconsistent FAT-copy flags are rejected.
These are eligibility checks, not a full FAT consistency scan. The loader
never formats the card, repairs FAT, modifies partitions or writes NVS.

The loader creates `/gaijin/provision-v1` and `/gaijin/userdata.new` through
exclusive creation. It closes and checks file writes, readback, rename and
volume synchronization. Any leftover transaction/candidate on a later launch
selects host inspection, even when the final image is absent. There is no
automatic partial-transaction cleanup or erase replay. FAT metadata is not a
multi-operation power-loss transaction; arbitrary interruption can still
require host FAT recovery.

During streaming/readback, a loader-only DMA-completion interrupt refreshes
HOME continuously. Rendering coalesces percentage changes at 10 Hz, writes
only newly changed bar rows and synchronizes at most 4 KiB per cache call.
The handler is disabled/freed and DMA drained before Linux handoff. A failed
display teardown returns to Launcher instead of passing running loader DMA
into Linux. Normal display refresh stays Linux-owned. Use the read-only
[display-test marker](../validation/revision37.md) to test without resetting home.
The user confirmed revision-35 creation of the 32 MiB image and the setup
menu on 9 November 2026. Revision 36's upright progress and normal Linux
operation are user-confirmed. The user confirmed revision 37 removed HOME
flicker. Revision 38 targets the remaining boot-console flashes; see the
[boot display check](../validation/revision38.md).
Full storage qualification is
tracked in the [validation record](../validation/home-storage.md).

### Linux validation and setup

`gaijin-state-map probe` derives allocation and reads the fresh raw superblock
without registering write ranges. `map-ro` exposes a read-only loop/DM view.
The startup script checks layout and setup-schema compatibility on that view,
unmounts/removes it, and only then creates the usual writable map and binds.
A rejected image receives no writable map. This eligibility path is not fsck.

Status is recorded in `/run/gaijin-home-state`. Use:

```sh
gaijin-setup --status
gaijin-persist status
gaijin-setup
```

Initial setup runs on the preferred console before login-shell respawns. A
lock prevents competing LCD/USB setup sessions. It can save the validated
prompt label, continue with defaults, show host instructions, select a RAM
session during initial setup or shut down to Launcher. Preferences are typed
data; persisted shell code is never sourced. Changes use checked file and
directory synchronization, with setup completion committed after saving.

`/persist/settings/setup-version` and `setup-pending` are independent of both
`state-version` and AppFS revision. Updates/reinstalls do not reinitialize a
completed home. A temporary session does not erase an image already created;
changes made in RAM disappear on restart.

### FAT-blocked home creation

The SD FAT volume has its own clean-state flags, separate from the ext2 home
image. A dirty FAT boot flag can block creation when no home image exists.
Readable boot assets do not establish that FAT is safe to modify. Revision 37
logs the exact failed check and setup describes the required host action.

Shut down Gaijin, attach the card to a host and unmount its FAT partition.
For the correct partition device (replace `/dev/sdX1`, never use the system
disk), run:

```sh
sudo python3 tools/check_home_volume.py /dev/sdX1
sudo fsck.fat -n /dev/sdX1
```

The diagnostic reads only the boot sector and the first sector of each FAT;
it is not a full checker. `fsck.fat -n` leaves the filesystem unchanged.
Review its output before applying any repair. For the 9 November incident,
the offline check found only a stale boot dirty flag and its backup byte;
host `fsck.fat -a` cleared that condition, and the diagnostic then reported
`boot_state: 0x00` with matching clean FAT entries. Recovery stays offline.
Do not clear the flag directly on Tanmatsu.

A writable host mount sets the boot dirty flag during normal operation.
Always unmount/eject cleanly before moving the card. A flag that remains set
after unmount needs host inspection. If a failed older loader left
`provision-v1` or `userdata.new`, preserve them for host inspection too;
the corrected loader deliberately refuses to continue an incomplete transaction.

### Optional destructive reset of a dirty image

`gaijin-setup` offers **Erase home and recreate** only when the probe reports a
recognized dirty Gaijin image, persistence is inactive and the current boot
provided the supported reset interface. The confirmation states:

```text
This permanently deletes all home files and saved settings.
An empty 32 MiB home will replace the current image.
To keep your data, cancel and recover it on a host.
Type ERASE to continue:
```

Only exact `ERASE` starts the action. Its effect is logical deletion and a new
empty filesystem, not repair, data recovery or guaranteed secure erasure.
Incompatible formats, known damaged state, unresolved transactions and card
errors remain host cases. A dirty candidate must match the fixed path, size,
ext2 label and exact supported geometry/features; the loader rechecks both
its identity and dirty state before deletion.

The setup program executes `gaijin-power reset-home --erase-confirmed`, whose
argument is an explicit destructive acknowledgement. The controller follows
the existing checked shutdown sequence: pause respawns, terminate sessions,
release persistent mounts/maps and audit writable mounts. It obtains the card
CID, file size and a fresh raw-superblock SHA256 from the mapper only after
cleanup. An ineligible image cancels the transition and resumes init.

The request travels in AppFS 2.1.0's **supported application argument**, not in
unowned RTC bytes or a writable Linux FAT file. The loader captures the valid
AppFS descriptor into DT on normal launch. For reset, the kernel publishes
that descriptor and the bounded `ghr1` argument in the standard retained AppFS
selection, then performs RESTART2. The bootloader relaunches the same app
without a manual Launcher step. The loader consumes/clears the argument and
selection before SD I/O. Ordinary shutdown still returns to Launcher.

On relaunch the loader rechecks card/image identity, dirty state and volume
eligibility. It creates and fully verifies the 32 MiB candidate while the old
image still exists. Only then may it delete the confirmed old file and publish
the replacement. No-space, invalid identity or a now-clean image refuses the
erase. There is no delete-first fallback to free candidate space. The new
image re-enters initial setup. Interrupted publication leaves recognizable
artifacts for offline inspection and cannot replay the request against a new
home.

### Offline host recovery policy

Data-preserving recovery of dirty or incompatible images always happens with
Linux stopped and the card attached to a host. Gaijin never clears a dirty
flag, runs a checker, restores a backup or migrates a schema on Tanmatsu.
A user who wants to retain files should cancel reset and follow
[offline recovery](#clean-start-and-offline-recovery). Preserve the original,
repair or migrate a working copy, verify the exact format/clean flag and
versioned layout, then install it offline. Check FAT first when its metadata
is suspect. The next normal boot reuses the verified image.

After unclean power loss, home may remain unavailable until host recovery or
explicit destructive reset. Continue using the accepted clean shutdown path.
Incomplete creation/reset transactions always remain host-only cases.

### Implementation plan and dependency gates

| Step | Implementation | Status / remaining gate |
| --- | --- | --- |
| H0 — State contract and read-only eligibility | `common/gaijin_home.h`, mapper probe/map-ro, ro layout validation and typed setup versions | Implemented; native mapper/script regressions pass. |
| H1 — Factory seed | Host creation, e2fsck, deterministic compression/provenance and loader embedding | Implemented; independent generation and decoder round trip pass. |
| H2 — Loader creator | Portable transaction plus FatFs backend, bounded ROM decode/readback, exclusive publication and progress | Creation confirmed on revision 35, upright orientation on revision 36 and HOME flicker removed on revision 37. Lifecycle/placement checks pass; memory/interruption qualification remains. See the [display test](../validation/revision37.md). |
| H3 — Setup/status | Native `gaijin-setup`, initial startup integration, settings durability, RAM/host guidance and console lock | PTY tests pass; initial menu confirmed on revision 35. Preference durability, remaining local/USB behavior and NOMMU memory need device acceptance. |
| H4 — Confirmed dirty reset | Checked shutdown, fresh identity, supported one-shot AppFS argument and loader replacement after candidate verification | Built; confirmation, failure, layout, identity and replay models pass. Real restart/replace and interruption behavior need device acceptance. |
| H5 — Release qualification | Complete matching revision-38 loader/kernel/userspace set, ABI/package checks and device procedure | Host checks/build/package pass. Startup-flicker and remaining storage checks are pending; scoped revision-35/36/37 results do not establish full storage acceptance. |

Dependencies are **H0 → H1 → H2**, **H0 → H3**, and **H2 + H3 → H4**;
H5 qualifies the advertised features. The implementation retains the frozen
Linux SD write gate and the static RV32 bFLT ABI. It adds no target formatter,
NOMMU checker, writable root overlay, Wi-Fi, package manager or GUI dependency.

### Research sources

The implementation uses pinned ESP-IDF v6.0.2 ROM/FatFs interfaces and the
[recorded source pins](../../sources.lock.json). The
[host/device validation procedure](../validation/home-storage.md) separates
current host evidence from pending physical acceptance.

- [Production Launcher dependencies](https://github.com/Nicolai-Electronics/tanmatsu-launcher/blob/v0.2.8/main/idf_component.yml)
  pin AppFS 2.1.0. Its
  [argument layout](https://github.com/badgeteam/esp32-component-appfs/blob/v2.1.0/include/appfs.h)
  and [boot selection](https://github.com/badgeteam/esp32-component-appfs/blob/v2.1.0/bootloader_main/bootloader_start.c)
  define the supported one-shot handoff used here.
- [ESP-IDF FatFs](https://docs.espressif.com/projects/esp-idf/en/v6.0.2/esp32p4/api-reference/storage/fatfs.html),
  [f_expand](https://elm-chan.org/fsw/ff/doc/expand.html),
  [f_unlink](https://elm-chan.org/fsw/ff/doc/unlink.html) and
  [f_rename](https://elm-chan.org/fsw/ff/doc/rename.html) define file allocation,
  closed-handle requirements, collision behavior and their error paths.
- [Linux ext2](https://www.kernel.org/doc/html/latest/filesystems/ext2.html)
  documents format/feature semantics.
  [e2fsck](https://man7.org/linux/man-pages/man8/e2fsck.8.html) remains a host
  generation/recovery tool. No target checker is part of this release.
