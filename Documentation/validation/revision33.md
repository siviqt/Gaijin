Gaijin 0.5.19 / AppFS revision 33 removes USB-reader waits from the standalone
boot path. Linux remains 6.18.35-gaijin-m5, with the revision-31 user-access fixes
and the corrected `com.github.siviqt.gaijin` identifier from revision 32.

The reported failure was a blank or blue LCD on battery, with successful startup
requiring a computer's serial console to be open. The earlier earlycon loop
could poll one million times per character while USB was full. USB also owned
`/dev/console`, putting init and startup output behind USB backpressure.
Source analysis and host fault injection identify these software problems;
the exact stopping point of the reported device boot was not captured.

The loader now starts with USB recovery boot arguments. Successful panel setup
enables the framebuffer and then supplies:

```text
console=ttyUJ0,115200n8 console=tty0 fbcon=rotate:1,font:10x18 consoleblank=0 rdinit=/init loglevel=7 panic=0
```

The LCD is the final, preferred `/dev/console`, so init and rcS can proceed
without a USB reader. If display setup fails, the loader retains:

```text
console=ttyUJ0,115200n8 rdinit=/init loglevel=7 panic=0
```

Normal boot omits earlycon. The explicit loader debug setting
`CONFIG_GAIJIN_USB_EARLYCON` adds the early USB console, whose writer now drops
characters immediately when the FIFO is full. It does not poll, sleep or need
timekeeping. Lost diagnostic output is intentional; USB TTY delivery semantics
are unchanged. Production package verification rejects the debug loader.

Inittab starts tty1 before ttyUJ0. BusyBox init logs each child before redirecting
stdio to its terminal, preventing the NOMMU vfork parent from waiting on a full
USB child's pre-exec log write. Existing immediate terminal setup and disabled
software flow control remain in place.

Host regressions compile the actual kernel console parser and boot-contract
code for both display outcomes and explicit debug mode. Fake-MMIO tests cover
a full FIFO without a clock, connected CRLF output, a FIFO filling during a
message and attachment after dropped output. The actual patched BusyBox child
path is exercised against a saturated PTY: it reaches exec; the earlier logging
order blocks in the negative control. Packaging checks both loader console
modes and rejects stale init/startup scripts or USB-first inittab ordering.
All 107 host tests pass with no skips, including verbatim license preservation.
The rebuilt kernel also passes 60 RV32
CLIC scenarios and their negative control, actual kernel string-object tests
and native vi checks. Component hashes and results are in the
[revision-33 host report](evidence/m5/m5-rev33-host.json).

On 8 November 2026 the user confirmed: "Device validations passed all checks."
Revision-33 device validation and M5 acceptance are recorded as PASS on that
basis. The [device record](evidence/m5/m5-rev33-device.json) identifies the tested
firmware hashes and check scope. No new raw capture or measured timings were
supplied with the confirmation. Consolidating the license documents afterward
does not change these firmware payloads.

The following procedure is retained for regression testing. Install the matching release
using [installation](../../INSTALL.md), preserving `/gaijin/userdata.ext2`.
Updating the SD kernel alone does not replace the cached AppFS loader; ensure
Launcher installs revision 33 or upload the matching loader with `make install`.

1. On a freshly formatted FAT card with the repository application installed
   and no state image, cold boot on battery with USB physically disconnected.
   Record time to an upright `gaijin#` LCD prompt and verify keyboard input.
   Repeat at least three times. RAM storage without a state image is expected.
2. Check `cat /proc/cmdline` and `cat /proc/consoles` locally. Expect ttyUJ0 before
   tty0 in the command line, no earlycon argument, and tty0 as the preferred
   console. Run `gaijin-persist status`, `m1-smoke`, `m2-sd-test` and
   `m5-umode-probe`.
3. Attach USB and open its terminal after the local prompt already works. Verify
   USB login, then disconnect during USB output and check that the LCD remains
   usable. Repeat cold boots with a charger, a computer with its terminal
   closed, and a computer with the terminal open.
4. Repeat with a correctly provisioned persistent state image, verify saved
   files after a clean shutdown and reboot, and test USB recovery with display
   setup disabled in a diagnostic loader. Preserve actual image identity,
   timing and console/persistence results before accepting the fix.

The confirmed checks include the referenced [display](../guides/display.md),
[user-access](../guides/user-access.md) and [power](../guides/power.md) procedures.
LCD flicker optimization remains tracked separately from these functional checks.
