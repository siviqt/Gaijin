# Persistence validation and recovery evidence

**M3 is CLOSED — PASS, accepted by the user on 29 October 2026.**
User statement: “This completes M3 implementations, its a pass.” The accepted
build is application 0.3.1 / AppFS revision 13 / kernel 6.18.35-gaijin-m3.1 #19.
All required M3 work is complete. Automatic on-device checking and a supported
board shutdown command remain follow-ups; host recovery and explicit clean
unmount are the accepted current behavior.

The **M3.1 focused hardware regression is PASS** on 29 October 2026. The new
capture boots Linux **6.18.35-gaijin-m3.1 #19**, completes three clean same-boot
stop/start cycles, verifies persistent state three times and respawns the shell
twice with the saved prompt and no allocation failures. Earlier captures prove
persistence across reboot and clean-start RAM fallback. The later offline repair and post-repair device capture below complete the
unclean-shutdown recovery cycle. All unrelated card files retained their hashes.

The earlier three captures use **application 0.3.0 / revision 12**, Linux
**6.18.35-gaijin-m3 #18**, on Tanmatsu rev7 / MCU r9 / SDHC SD16G. They motivated
the restart and prompt fixes described below.

The files below are retained without modifying the pasted text. The first log
contains multiple complete boots; the later logs can overlap observations, so
counts across all three are not added as independent runs.

| Capture | Observed evidence |
| --- | --- |
| [m3-hw-persistence.log](evidence/m3/m3-hw-persistence.log) | Initial missing-image RAM fallback; subsequent ext2 mounts; three M3 verify PASS lines after reboot; six M1 smoke and six M2 SD PASS lines; input captures of 889, 336 and 445 events, all with zero overflows; repeated clean stops |
| [m3-hw-clean-start-restart.log](evidence/m3/m3-hw-clean-start-restart.log) | Clean-start marker skips mapping and reaches RAM shell; next normal boot restores saved prompt/state; clean stop followed by two failed starts with errno 16 |
| [m3-hw-restart.log](evidence/m3/m3-hw-restart.log) | Persistent mount and saved file; busy `/root` unmount while shell uses it; `cd /` permits clean stop; next start fails with errno 16 |
| [m3.1-hw-regression.log](evidence/m3.1/m3.1-hw-regression.log) | Kernel #19: persistent boot, three clean stop/start and verify passes, two shell respawns, saved prompt, no allocation failures or errno 16 |

`/proc/mounts` shows FAT `/sd` and SquashFS `/` read-only, ext2 `/persist`,
`/home` and `/root` writable. BSP/Linux LBA0 fingerprints match `b700eedc`.
The state filesystem has approximately 63,461 KiB usable space (64 MiB image).
The first capture shows matching restored content, permissions, symlink and
prompt through `m3-state-test verify`. Re-running `prepare` correctly refuses
to overwrite the existing proof. No Linux SD DMA transfer error is shown.

[m3-hw-current.json](evidence/m3/m3-hw-current.json) preserves the local revision-12 staged
inventory before building the fixes. It is context for matching the release;
the console does not contain a complete device asset-hash inventory. Capture
SHA256 values are recorded in `sources.lock.json` under `m3_state.hardware_evidence`.

## Diagnoses and revision-13 changes

The original SD write-gate callback returned `-EBUSY` on every registration after
the first. A clean stop removed the map but intentionally retained the frozen
ranges. Starting again reached that callback and was rejected. Revision 13
accepts an exact re-registration and continues to reject any changed list.
There is no clear-gate operation and no expansion of writable sectors.

A same-boot restart also needs fresh state validation: FAT's cached file pages
can retain the pre-mount clean superblock. Revision 13 reads the allocated
superblock sectors directly from the partition before creating the map.
Dirty/unsupported state remains rejected even when a prior FAT view was clean.
Diagnostics now identify the failed stage instead of grouping every error as
an unspecified missing/dirty/unmappable image.

After saving `m3proof`, normal boots report two failed 602,112-byte NOMMU process
allocations while loading the login prompt. The log shows about 15 MiB free
but no 1 MiB buddy block. The profile used `$(cat /run/gaijin-prompt)`, which
requires extra NOMMU shell/BusyBox executions. Revision 13 uses builtin `read`
and retains prompt validation. A pinned NOMMU hush regression loads valid,
invalid, empty and overlong prompt values with executable lookup disabled.
The source fix removes those executions; the new #19 capture confirms that
the warnings are absent during initial login and two shell respawns. It does not establish a general solution
to NOMMU memory fragmentation.

The new capture confirms the corrected `Gaijin M3.1` DT model. Its first
handoff line still uses a `Gaijin M2` loader tag, whereas the local revision-13
loader uses `Gaijin`. Because the capture starts at handoff, the installed
AppFS loader revision cannot be verified. This does not invalidate the observed
Linux persistence/prompt results; install the complete matching application
and SD set using [installation](../../INSTALL.md). Hardware targets and boot/ABI contracts are unchanged.

## Host verification of revision 13

All **56 tests passed** after the changes. Loader, kernel/DTB and Buildroot
userspace builds completed; packaging and `make verify` passed. The tests
cover identical gate re-registration with changed lists still denied, direct
superblock reads across fragmented allocation, dirty-state rejection after
prior clean reads, repeated start/stop scripts and prompt loading with no
executable lookup. These host results are now supplemented by the focused device pass below.

## M3.1 focused hardware result

The unmodified [m3.1-hw-regression.log](evidence/m3.1/m3.1-hw-regression.log) has SHA256
`da68f093151916ddde1e95b4e6ca8457664e4fcb19590997c16f1126f73a6c23`.
[m3.1-hw-current.json](evidence/m3.1/m3.1-hw-current.json) records the local staged revision-13
inventory before this documentation refresh. The console reports the expected
kernel and DT model, but has no full device asset hashes or loader identity banner.

| Check | Observation in the new capture |
| --- | --- |
| Boot and ABI | One #19 boot, SD root switch, persistent ext2 activation, ABI PASS |
| Restart | Three clean stops and three successful starts in the same boot |
| State | Three verify PASS results for content, symlink, permissions and prompt |
| Shell lifecycle | Two exits followed by respawn and `m3proof#` prompt |
| Allocation regression | No page-allocation, NOMMU-allocation or bFLT-allocation failure shown |
| Restart regression | No errno 16 or mapping/start failure shown |
| Storage view | Final df shows one dm-0 mounted at /persist, /home and /root |

The same loop1 and dm-0 identifiers are reused. No `losetup -a` listing is
provided, so this is not a complete loop-leak audit. The final capture ends with
persistence still mounted: run `cd /; gaijin-persist stop` before physical reset.
A mistyped test subcommand prints usage as expected and is not counted as a test
failure. The 1 KiB df usage change alone is not evidence of corruption.

The focused restart/prompt regression is complete. The later recovery records
below add unclean-image rejection, offline repair and e2fsck verification,
unrelated-card-file integrity comparisons, and post-repair device proof. Earlier clean-start and missing-image captures are
retained separately. General NOMMU fragmentation and long-run/card-compatibility
work remain follow-ups, not claims established by this short capture.

## Unclean shutdown rejection

A later user-supplied [capture](evidence/m3.1/m3.1-hw-unclean-shutdown.log) follows powering
off without stopping persistence. On the next #19 boot, the fresh ext2
superblock check rejects state with errno 22 and falls back to RAM. The image
remains listed under `/sd/gaijin`; ABI, M1 smoke and M2 SD tests pass. A manual
persistence start is rejected at the same check. The shutdown history makes
an uncleared ext2 clean-state flag the likely cause; the console does not
print its raw value or establish whether filesystem structures are damaged.

That capture records rejection/RAM fallback after an actual unclean
shutdown. The host and device records below establish successful recovery
and preserved file contents. Recover using e2fsck on an offline host-side copy as described in
[persistent-state recovery](../guides/persistence.md), retaining the original image first. Do not rerun
image provisioning or clear the clean-state flag without checking the filesystem.

## Offline recovery completed on 29 October 2026

The user mounted the SD card on Debian and authorized examination and repair.
The 64 MiB image had superblock state 0 (unclean), the expected ext2 label,
inode size and feature flags. A full non-modifying e2fsck found no structural
errors: 21/16384 files and 2097/65536 blocks. An untouched, hash-verified backup
was saved before repair and is now retained once as
`backups/userdata-unclean-2026-10-29.ext2.gz` (outside generated builds).

Running `e2fsck -fp` on a working copy returned 1 (corrections made), set state
to 1 (clean), and changed only nine bytes in primary superblock block 1. Every
byte outside that 1 KiB block is unchanged, including all file contents and
inode/directory data. The version marker, proof contents, symlink metadata and
prompt were also compared directly with the original.

After verifying the working copy with `e2fsck -fn`, the nine verified byte
changes were applied to the existing SD image without resizing or reallocating
it. The file was synchronized and its hash matched the repaired copy. A second
`e2fsck -fn` on the actual SD image passed. All other 11 card files matched their
pre-repair sizes and SHA256 hashes. The compressed original and recovery metadata remain
under `backups/`; the temporary repaired-image duplicate has been removed.
Private backups are not included in the installation ZIP.

The [recovery report](evidence/m3.1/m3.1-offline-recovery.json) and
[host check/repair output](evidence/m3.1/m3.1-offline-recovery.log) retain the hashes and results.
This demonstrates host repair of the actual unclean image and unchanged
unrelated card files. The next section supplies the post-repair device remount
and proof verification. Continue stopping persistence before physical reset.

## Post-repair device verification — PASS

The [recovered-device capture](evidence/m3.1/m3.1-hw-recovered.log) confirms Linux #19
mounting persistent state after the host repair. The saved `m3proof` prompt
returns, `m3-state-test verify` passes, and `gaijin-persist stop` cleanly removes
the persistent mounts. Shell exit/respawn returns to the default prompt and df
shows only the SD FAT and immutable base. No allocation/start failure appears.
This completes the observed unclean shutdown → rejection/RAM fallback → host
repair → device remount/proof → clean stop cycle. It does not implement or
validate automatic repair on the device.

## Startup repair feasibility

Host recovery is the current implementation policy, rather than a hardware
requirement. A future on-device path could validate the image's allocation and
geometry, expose the existing bounded map while keeping ext2 unmounted, run
a validated NOMMU ext2 checker in conservative automatic mode, recheck, then
mount only on success. Complex errors or memory/time limits should retain RAM
fallback and host recovery. It must not simply clear the clean-state flag.

The pinned Buildroot e2fsprogs package depends on BR2_USE_MMU because of its
util-linux/libblkid dependency. Gaijin disables MMU and ships no ext2 checker;
this needs a tailored port/dependency configuration plus bFLT execution,
peak-RAM and interruption testing on the P4. Standard e2fsck preen mode (-p)
performs automatic repairs it classifies as safe and leaves more serious
problems for intervention. This is a design option, not an enabled feature.
