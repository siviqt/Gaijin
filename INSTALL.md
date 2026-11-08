# Install the current Gaijin build

Current build is **M5 (Framebuffer and fbcon) / application 0.5.18 / AppFS revision 32**.
Expect **Linux 6.18.35-gaijin-m5**, framebuffer output, and login shells on
LCD `tty1` and USB `ttyUJ0`. M5 hardware acceptance is pending.

Use one complete set: the project's `build/` directory, or one extraction of
`build/gaijin-current.zip`. The instructions below use paths relative to that
set. In the project, start with `cd build`; for the ZIP, enter its extracted
folder. Do not combine files from separate directories or downloads.

1. Verify the files:

   ```sh
   sha256sum -c SHA256SUMS
   ```

2. Replace the SD card's `/apps/com.github.siviqt.gaijin/` directory with
   `sd/apps/com.github.siviqt.gaijin/` from this set. The resulting application
   directory is `/apps/com.github.siviqt.gaijin/`, containing `metadata.json`,
   `linux-loader.bin`, the Penguin `icon16.png`, `icon32.png` and `icon64.png`,
   `LICENSE` and `boot/` with Image, DTB,
   initramfs, manifest, rootfs.sqfs and rootfs.sqfs.sha256.
   The SD card need not be formatted; unrelated files can remain. Replace the full application directory and upload its loader together.
   Keep `/gaijin/userdata.ext2` outside this replaceable directory and preserve it
   during updates. To enable persistence, provision it once using
   [persistence instructions](Documentation/guides/persistence.md). Without it, the system uses RAM.
   Provisioning is explicit and never part of an application update.

   Revision 32 corrects the application identifier to `com.github.siviqt.gaijin`.
   Revision 31 incorrectly compiled `com.github.siviq.gaijin`, which fails to
   boot when installed under the correct repository directory. Replace the loader,
   initramfs and rootfs together; renaming the folder alone does not repair the
   revision-31 binaries for the correct identifier. Remove
   the old cached application entry in Launcher to avoid selecting it, then
   launch Gaijin from the SD card so Launcher copies the revision-32 executable
   into AppFS. The package declares all boot assets and requires SD installation.

3. Enter **BadgeLink mode**. Upload the loader from this same set:

   ```sh
   /path/to/tanmatsu-sdk/tools/badgelink.sh appfs upload \
     com.github.siviqt.gaijin "Gaijin" 32 \
     sd/apps/com.github.siviqt.gaijin/linux-loader.bin
   ```

   Adjust the SDK path if necessary. From the project root, `make install`
   performs a consistency check and uploads this exact staged binary.
   Override its script path with `BADGELINK=/path/to/badgelink.sh` if needed.
   Uploading replaces the project's existing AppFS entry; deleting it first
   is optional. Keep Launcher and the device's system firmware installed.
   This application does not require flashing a bootloader or partition table.

4. Switch to **P4 USB Serial/JTAG debugging mode**, open the appropriate
   `/dev/ttyACM*` port at 115200 and launch Gaijin through Launcher. Disable
   host hardware/software flow control and local echo; use one serial monitor.
   M3 Persistent State is CLOSED — PASS, accepted on 29 October 2026;
   captured results and the recovery procedure are retained in the M3 guide.
   Record `Gaijin SD reset`, `SD reference: loader LBA0` and Linux `LBA0 dma=... sg=...` lines.
   For this build, expect `Gaijin M5`, `console=ttyUJ0,115200n8`, an enabled ttyUJ0
   console, disabled early bootconsole, the startup banner and automatic ABI
   PASS. The startup banner is `Gaijin`, the prompt is `gaijin#`, and ABI
   text states validation with the pinned toolchain. The initial-console
   warning should be absent.

5. In the target shell, run:

   ```sh
   cat /proc/cmdline
   cat /proc/consoles
   stty -a
   gaijin-persist status
   m5-umode-probe
   m1-smoke
   m2-sd-test
   cat /proc/bus/input/devices
   cat /sys/bus/i2c/devices/*-005f/firmware_version
   m2-input-test
   ```

   `/proc/consoles` should list ttyUJ0. `stty -a` should show
   `-ixon -ixoff` immediately after login. BusyBox init applies these flags
   with `TCSANOW` before starting either login shell.
   `m1-smoke` should finish with PASS; `exit` should respawn the shell.
   Test Ctrl-C while `sleep 30` runs and check command editing and USB
   reconnect separately. Record any stall and whether reset was needed.

Before physical reset to Launcher, stop applications and run
`cd /; gaijin-persist stop`. The normal root is read-only SquashFS and `/sd`
remains read-only FAT. With a validated state image, `/persist` is writable ext2
and `/home` and `/root` persist. Otherwise those paths use bounded RAM and
vanish on reset. `/run`, `/tmp` and `/usr/local` remain RAM-backed. Do not remove
the card while Linux runs. Hotplug, automatic repair and formatting remain disabled.
Date still starts at the Unix epoch. The USB flow-control workaround above and
accepted keyboard behavior remain applicable.

On a storage, checksum or image compatibility failure before `switch_root`,
expect `Gaijin SD: RAM rescue shell`. Inspect `dmesg` and `/proc/mounts`.
Missing Image/DTB/initramfs still fails in the loader before Linux starts.
For a Linux rescue test, temporarily rename only `boot/rootfs.sqfs` on a host,
boot and restore it afterwards; keep the other boot assets together.
A failed final root transition needs physical reset. Nothing automatically
repairs or replaces card contents.

For M3, run [the persistent-state acceptance procedure](Documentation/guides/persistence.md)
and the existing [SD read regressions](Documentation/guides/sd-storage.md)
and retain its capture. M1 and the keyboard/management-MCU M2 were accepted on
28 October 2026; Linux SD access is CLOSED — PASS on 29 October 2026.
The current host declares SD DMA noncoherent, requires cache synchronization
at probe and retains the BSP sector reference. State writes are restricted
to the provisioned image sectors; FAT and SquashFS remain read-only.
The corrected DMA path is validated on the device. Captured runtime results and
user-confirmed off-log recovery/integrity checks are retained in the validation
record; USB timeout and longer load/card-compatibility checks remain follow-ups.
For another card or build, capture startup including both LBA0 fingerprint lines
and run `m2-sd-test`, then the remaining acceptance checks. The ABI is
`gaijin-rv32imac-ilp32-le-bflt4-static-uclibc-v1`; its executable format and
compiler calling convention are unchanged. Capture boot logs over USB and
follow the [power and boot test procedure](Documentation/guides/power.md#device-acceptance).

For normal transitions use `shutdown` (Launcher), `poweroff` (device off), or
`halt` (Linux stopped, power on). These terminate other user
processes and cleanly stop persistence first. See [power commands](Documentation/guides/power.md).

## Revision-30 validation

Reinstall or launch the updated SD application so AppFS uses loader revision
30. `/proc/cmdline` must contain `font:10x18`; the LCD should show 80x26 text.
Check that `gaijin-persist status` reports active before testing saved files.
`m5-umode-probe` must report PASS. Run `m5-display-test`, scroll `seq 1 500`,
and note blue flashes and the frame/DMA-error/underrun counters before and after.

Write a small file under `/root`, run `shutdown`, relaunch, verify the file,
then repeat with `poweroff` and physical power-on. The earlier successful power
cycles used RAM fallback and did not exercise active ext2 cleanup. Capture the
full log of this active-state test. If a freeze occurs, keep power on and use
`sudo -v` followed by `bash tools/debug/capture-frozen-p4.sh` from the source
checkout. The source-only cleanup does not remove this maintained tool.

On the host, `python3 tools/check_state.py /path/to/gaijin/userdata.ext2` checks
both the strict format and clean flag without modifying the image. A successful
`e2fsck -fn` alone does not prove that the clean flag is set. Repair only an
offline, backed-up copy as described in the persistence guide.
