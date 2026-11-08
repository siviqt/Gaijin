# Native U-mode and persistence findings

The current source uses the native M-mode kernel / U-mode application design.
The machine-mode application workaround and isolated build variants have been
removed. The CLIC entry/return correction is in the consolidated board patch;
revision 31 adds the separate NOMMU user-access bounds patch.

## Device evidence

The first native candidate failed. The
[22:01 JTAG capture](evidence/m5/m5-native-umode-20261107.jtag.log) showed Linux
idle with MIE enabled, active CLIC level 255, and a pending timer interrupt.
Its return path cleared the IRQ-return flag before the controller could lower
that level. The host test initially modeled saved registers but missed live
controller state; that test was insufficient.

The corrected adapter completes each physical IRQ with interrupts still
masked before entering Linux C code that may schedule. It saves raw CLIC
metadata separately from Linux's syscall/signal cause, and preserves the normal
saved user/kernel privilege on final return. A model executes the actual RV32
shim instructions with P4 CSR aliases and interrupt levels; its negative
control reproduces the pending-timer stall when physical completion is omitted.
Run `tools/verify_clic.py` after building the kernel.

The [first successful capture](evidence/m5/m5-umode2-20261107.console.log) reached
the shell and passed the native ABI smoke test. The
[latest capture](evidence/m5/m5-umode2-interactive-20261107.console.log) also
passed both privileged-CSR SIGILL tests and their signal returns. The user
reports that all tested shutdown/poweroff cycles completed without hangs.
The precise cycle count was not supplied. The new font/cache mitigation still
requires a device retest.

## Why persistence remained inactive

The existing user image had ext2 state `0x0000`, not the required clean state
`0x0001`. Its SHA256 was unchanged from the host backup before the latest tests:
`[redacted user-image fingerprint]`.
The mapper correctly refused it. This was an old unclean flag, not newly
observed filesystem corruption from these power cycles.

The prior host check used `e2fsck -fn`, which found no structural errors but
could not write the clean flag. Treating its successful exit as sufficient was
an error. `tools/check_state.py` now independently checks the exact format and
clean flag before invoking read-only fsck. A regression creates this condition
and verifies that successful read-only fsck alone is rejected.

Offline `e2fsck -fp` on a copy corrected the image. Exactly nine bytes changed,
all in the primary superblock; file data and image allocation were preserved.
The repaired SHA256 is
`5a45a13633b7c80007ce8746335852f495017848b6277c0e71c3bff2fe96e4d2`.
The installation writes that repair in place only after backing up the original
and checking that its hash has not changed. Persistent-state power cycles
must now be tested with the state actually mounted.

## Blue display flicker

The display must be rearmed by the frame-complete IRQ. Large bFLT instruction
cache maintenance previously masked interrupts for an entire program-text
range; display pixel writeback also used 16 KiB sections. In addition, the
public bFLT flush entry point still called the whole-cache operation rather
than the existing ranged helper. The current source connects that public path
to the P4 helper and limits both ranged operations to
4 KiB critical sections while preserving ROM cache-operation exclusion and
ordering. A host test checks a 600 KiB load, interrupt opportunities between
chunks, and preservation of an already-disabled interrupt state.

[Espressif's P4 LCD FAQ](https://docs.espressif.com/projects/esp-faq/en/latest/software-framework/peripherals/lcd.html)
associates flashing blue displays with data starvation. The P4 bridge's reset
underflow pixel is also blue/cyan. This supports the diagnosis, but the serial
capture does not establish the frequency of hardware underruns. The mitigation
requires validation with `m5-display-test` and the frame/error/underrun counters.
Panel timings, pixel clock and RGB565 format are unchanged; flicker is not
claimed resolved.

The font changes from VGA8x16 to 10x18, giving 80 columns by 26 rows in landscape.
Revision 30 of the loader supplies the new boot argument. Reinstall the loader
or launch the new revision from the SD card; the old AppFS loader requests the
old font even with a new kernel.
