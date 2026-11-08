# Persistent state

Application **0.3.1**, AppFS revision **13**, Linux **6.18.35-gaijin-m3.1**.
**M3 is CLOSED — PASS, accepted by the user on 29 October 2026.** The actual
unclean image was repaired on the host and verified again on Tanmatsu. See [captured results](../validation/persistence.md).
The accepted M1 and M2 evidence remains in [validation](../validation/README.md) and its original captures.

## Storage contract

The immutable base and boot assets remain under `/apps/com.github.siviq.gaijin/`.
User state is a separately provisioned **`/gaijin/userdata.ext2`** on the same
FAT card, outside the replaceable application directory. Updates and packaging
never create, delete, resize, replace or include this image. Keep a separate
backup. The loader continues to read only its boot assets.

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
if state was mounted. See [power commands](power.md). The M3 acceptance above
remains historical; device acceptance of the new power paths is pending.

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

## Provision once on the host

With Linux stopped and the card attached to the host, retain a backup and run
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
CLI remains usable. No on-device formatting, initialization or filesystem
repair is attempted. The superblock check is a conservative eligibility check,
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
also skips the SD root transition. A missing or renamed image selects RAM
fallback and preserves the original image. Base checksum failure continues to
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
4. Stop cleanly, reset to Launcher, replace only `/apps/com.github.siviq.gaijin/` with a
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
