# Linux power commands

Application 0.5.19 / AppFS revision 33 uses the SD power cycle and supports
LCD and USB login shells. Use the matching loader and SD boot assets.
The user confirmed all revision-33 device checks passed on 8 November 2026,
including persistent-state and power-transition regressions.

| Command | Result after cleanup |
| --- | --- |
| `shutdown` or `shutdown now` | Reset into the AppFS Launcher |
| `poweroff` or `shutdown -P now` | Ask MCU r9 to turn the device off |
| `halt` or `shutdown -H now` | Halt Linux, leaving power on; use physical reset/power afterwards |
| `shutdown --check`, `poweroff --check`, `halt --check` | Check availability without stopping processes or changing power |

The three command names resolve to `/sbin/gaijin-power`, a native static bFLT
program. They require root. There are no force, no-sync or delayed/scheduled
options. Suspend/hibernate are not supported. The physical power key still
emits an input event; it has no automatic safe-shutdown policy, and a hardware
power cut can still leave ext2 unclean.

## Cleanup and failure behavior

The controller checks kernel support before making changes. Power-off also
checks for MCU firmware r9. A lock permits one controller at a time.

`shutdown` and `poweroff` blank the backlight before printing cleanup
messages. The controller preserves the brightness setting and restores the
backlight if the transition is cancelled after blanking.

It changes its own directory to `/`, pauses BusyBox init's respawns with
SIGTSTP, sends TERM and then KILL to other user processes, reopens `/dev/console` to survive login-shell TTY hangup, and runs
`gaijin-persist stop`. Stopping the other shells releases working directories
and open files under `/root` and `/home`. This ends all other sessions and
applications, so save your work first. Linux excludes init and the calling
controller from `kill(-1, ...)`.

The persistence stop path uses checked `syncfs()` on `/persist`, unmounts the
`/root` and `/home` binds, and unmounts `/persist`. Before removing its mapper
and loop references, it rederives the image extents and reads the ext2 header
through raw direct I/O. The expected clean flag and supported geometry must
be present on the card; a stale FAT file cache cannot satisfy this check.
A separate mount-table check rejects any remaining writable `/dev/...` mount
or ext2 filesystem. Only then does the controller invoke Linux's reboot
syscall. FAT and SquashFS remain read-only. No global `sync()` traverses the
live or auto-clearing block-device aliases: ext2 has already been flushed,
unmounted and its clean header verified.

Shutdown stages go to `/dev/kmsg` as well as the terminal, so losing a login
TTY cannot hide the last completed operation. Kernel-log access is checked
before pausing init. The trace identifies user-process termination, console
reopen, ext2 flush, each unmount, clean-header verification, map removal and
entry into the Linux transition.

A cleanup, busy-mount, unsupported-operation or syscall failure prevents the
requested power transition and resumes init with SIGCONT. No lazy or forced
unmount is used. Partial persistence cleanup remains retryable through the
existing marker files. A syscall failure after cleanup does not automatically
remount state; inspect it or run `gaijin-persist start` before continuing.

## Kernel and loader ownership

The loader clears the AppFS selection magic in retained memory on entry.
The kernel driver publishes `ready` below
`/sys/bus/platform/devices/5010800c.power-controller/`. For `shutdown`, the
driver clears the selection magic again and calls the P4 ROM
`software_reset` entry at `0x4fc00094`. The bootloader then selects Launcher.
Linux uses the same Launcher reset path for any kernel restart request.

The existing MCU driver owns power-off as well as keyboard/backlight access.
A sleepable Linux POWER_OFF_PREPARE handler verifies r9 and sends register 126,
value 1 (power-off, alarm wake disabled). Firmware r9 disables backlights/LEDs
and disconnects battery and USB input power through its PMIC. The final atomic
power-off hook sends no I2C traffic. If the MCU command fails or power remains
on, Linux stays cleanly halted and logs that the physical power button is
needed. It cannot resume sessions after entering kernel power-off.

Framebuffer shutdown disables bridge output and DMA, masks its interrupt and
cancels cache-flush work before reset/power-off. Halt runs the architecture's
halt loop without the MCU prepare command, so it leaves the device powered.
RTC clock initialization, alarm wake, power-key policy and suspend remain
separate work.

## Reboot investigation and revision 17

The revision-16 device capture proves a Gaijin reboot from RAM fallback:
`already inactive`, `Restarting system with command 'gaijin'`, ROM
`SW_SYS_RESET`, and a fresh Gaijin boot. The first attempt with persistence
mounted reportedly froze blue and needed a physical power cycle. It captured
no cleanup completion or reset marker, so the exact blocking operation is
unconfirmed. The later rejection is an unclean ext2 image; FAT and SD root
continue to mount.

Revision 17 replaces the unchecked global pre-unmount sync with checked
filesystem-specific syncfs, verifies the clean header after unmount, removes
the redundant global final sync, and makes every shutdown phase visible in
the kernel log. Host tests inject flush errors, incorrect filesystems, dirty
raw headers and interrupted/failed cleanup. The kernel/ROM reset path is
unchanged. These changes harden cleanup and provide evidence for the next
failure; they do not establish that the original hang is fixed on hardware.

## Device acceptance

Select Gaijin from the updated SD card in Launcher. If it is already cached,
use F4 Uncache before launching it, or use `make install` in BadgeLink mode
for revision 33. Capture P4 USB Serial/JTAG at 115200 8N1 with flow control
off. Expect `LDO4 disabled, bypass off` and `LDO4 restored at 3.3 V`, then
`gaijin#` prompts on the LCD and USB. The diagnostic backlight pulse has
been removed.

First run both transitions with `gaijin/clean-start` on the SD card. After that
comparison, remove the marker, restore a clean userdata image if needed and
repeat with writable persistence as follows.

1. At the Linux prompt, run `cat /run/gaijin-persist`, `shutdown --check` and
   `poweroff --check`. Proceed after confirming that persistence is mounted
   and both commands report availability. Run `m3-state-test verify` if the
   earlier persistence proof is present.
2. Append a marker with
   `printf 'launcher return\n' >> /root/gaijin-power-test.log`, then run
   `shutdown`. Expect clean-header verification, map removal, Linux
   `Restarting system`, ROM `SW_SYS_RESET` and the Launcher screen.
3. Launch Gaijin manually and read `/root/gaijin-power-test.log`. After
   confirming persistence and the marker, append a second marker with
   `printf 'power off\n' >> /root/gaijin-power-test.log`, then run
   `poweroff`. Expect the same cleanup followed by the panel and power LEDs
   going off. USB may disconnect before the last kernel message reaches the
   host. If the kernel logs `MCU power-off failed` or `still powered after MCU
   command`, record that result.
4. Turn the device on with its power button, launch Gaijin and check that both
   markers are present. Save the serial capture and note when any blue flash
   occurs relative to the loader handoff, framebuffer probe and shell prompt.

Let each command perform its own persistence cleanup. If a test stalls, save
the last visible line and note whether USB still responds and what the screen
shows. See the [persistent state guide](persistence.md) for offline recovery
after a forced power cut.

## Revision-17 follow-up and revision-18 console diagnostics

The [revision-17 capture](../validation/evidence/m5/m5-reboot-rev17.log)
records clean unmount, a successful fresh raw clean-header check, map removal,
`Restarting system`, ROM `SW_SYS_RESET`, and a second Linux boot with state
mounted successfully. The user reports a blue local display and an unresponsive
USB console after that boot reaches `starting pid 70`. This is a post-reboot
console/startup failure; the capture does not show a cleanup stall. A FAT warning
while probing the whole partitioned card precedes the successful partition mount
and does not establish FAT corruption. Offline e2fsck found no structural errors;
the later forced power-off can explain the new unclean ext2 flag.

Revision 18 disables IXON/IXOFF in both console launchers after BusyBox init's
`set_sane_term()` re-enables them. The earlier rcS setting happened before this
reset and did not survive shell launch. A real-PTY regression verifies the final
terminal attributes. This corrects a USB silence risk but is not proof of the
reported freeze's cause.

Both consoles now log `launcher`, `exec login shell`, `profile begin`,
`home entered`, and `profile complete` stages to the kernel log. Five seconds
after rcS finishes, a single RAM-only boot report records uptime, load, display
frame/error counters and task states. A profile-complete marker means the
profile returned; it does not prove interactive input works. Compare the first
and second boot reports. If the delayed report is absent, the capture has not
established whether userspace can still run. Keep logging through this interval
and try Enter on USB and the local keyboard. Revision 18 retained the existing reset and framebuffer drivers. The
both-console freeze remains a separate diagnostic issue.

## Revision-20 immediate console setup

The latest revision-19 failure ends after both console launchers start and
before their login exec markers. BusyBox `stty` uses `TCSADRAIN` when
changing flags, so boot can wait on queued terminal output. Revision 20
sets the flags in init's existing `TCSANOW` call and removes `stty` from
rcS and the console launchers. A host PTY test verifies the patched routine.
Repeat a Launcher return and manual Gaijin launch to test the change on
hardware, then proceed to MCU power-off once both consoles respond.

## Revision-22 display comparison

Revision 21 still stalled after init announced the direct USB shell.
Revision 22 changes the loader to skip display initialization and requires a
disabled framebuffer node at handoff. It reuses the revision-21 kernel, DTB,
initramfs and SD root. Both display-disabled boots reached the USB prompt
with persistence active. `shutdown` returned to Launcher v0.3.1, and the user
confirmed physical power-off after `poweroff`. See the
[device result](../validation/README.md#revision-22-display-disabled-power-tests).

## Revision-23 plain USB shell

A later cold boot on revision 22 reached `usb profile complete` and then
stalled. Revision 23 uses canonical input with job control, line editing and
persistent history disabled. The delayed boot task pulses the MCU backlight
before it opens the kernel log. A visible pulse during serial silence shows
that the task could still run and reach the MCU. An unseen pulse is inconclusive
if the backlight or MCU path is unavailable.
