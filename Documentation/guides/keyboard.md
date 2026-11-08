# Keyboard and management MCU

The evdev keyboard implementation satisfies M4 and was accepted early as
M2 on 28 October 2026: “Test is a pass.” The original specification calls
keyboard M4 and Linux SD access M2. The 29 October review found no additional
input implementation required before M5 — Framebuffer and fbcon.

This guide owns the input protocol, behavior, regression procedure and M4/M5
boundary. See [validation](../validation/README.md) for accepted evidence and
[the roadmap](../README.md#open-work) for broader follow-ups.

## M4 completion and M5 handoff

The review checked all 72 matrix bits against the pinned coprocessor header,
modifiers/chords, aggregated space switches, firmware checks, input recovery,
repeat registration and the current kernel/DTS/package integration. All 56
host tests passed, including five keyboard and three GPIO tests. The prepared
kernel passed a reverse-patch dry run. No driver, ABI or firmware changes were
needed; this was a source/host/evidence review, not a new hardware acceptance.

The accepted [capture](../validation/evidence/m2-sd/m2-sd-acceptance-console.log),
lines 807–1415, records MCU r9 input, 603 events, zero overflows, both Shift and
Alt keys, Ctrl, Meta, Fn, five EV_KEY repeat events and clear initial/final
state. SHA256:
`415d1c86618fe9738105bd18f87000fc6966834f46196ce0d9c77f222eafd8fa`.
The current [M3.1 capture](../validation/evidence/m3.1/m3.1-hw-regression.log)
confirms the same input probe/poll/repeat settings; probe success alone is not
a new full physical-key test.

M5 now provides display, backlight, VT/fbcon and a local shell; acceptance must validate
actual local text entry, punctuation, modifiers, repeat/release, shell editing
and Ctrl-C while retaining USB recovery. The [display guide](display.md) records the standard US console keymap,
TERM=linux shell and unassigned Fn/session shortcuts. CONFIG_VT is now enabled.
Input, backlight, RTC and power must share the MCU endpoint through one owner.
KEY_POWER currently emits an event only. Version 0.5.2 provides separate
[safe power commands](power.md); RTC and power-key policy remain tracked.

The physical-key, overlapping-space, electrical-fault, overflow, close/reopen,
latency/rollover and simultaneous SD/input/USB checks below remain accepted
follow-ups. They do not block starting M5 and are not newly claimed passes.
Recheck input and memory under display load during M5 acceptance. Latest host
output is `build/current-tests.log`; `build/VERIFICATION.json` records workspace
verification.

## Board and protocol

Target: Tanmatsu rev7, BSP 1.6.4, CH32V203 management MCU firmware r9.
`dependencies.lock` pins `nicolaielectronics/tanmatsu_coprocessor` 0.8.0.
The checked-in registry component's header defines the nine keyboard bytes;
its implementation defines the register addressing, repeated-start reads and
little-endian firmware identity. `tanmatsu_hardware.h` supplies GPIO9 SDA,
GPIO10 SCL, MCU address 0x5f, GPIO1 IRQ and GPIO35 volume-down.
The driver reads the actual firmware number and accepts only 9. A different
firmware is rejected with its observed number in dmesg; it is never updated.
Record the observed firmware during target validation.

The current kernel patch includes:

- `drivers/gpio/gpio-tanmatsu.c` exposes only GPIO9, GPIO10 and GPIO35 through gpiolib. Other GPIOs
  are invalid, and GPIO35 is input-only. HP GPIO at 0x500e0000, IO MUX at
  0x500e1000 and LP IO MUX at 0x5012b000 are checked against ESP-IDF v6.0.2
  P4 register/LL headers. Requested pins select digital GPIO function 1,
  input and pull-up, clear inherited per-pin IRQ/OD state and release output
  before rerouting. GPIO9/10 also select HP ownership in LP IO MUX. The
  driver uses W1TS/W1TC for output and direction; it does not reset shared
  clocks or touch SD, radio, display or USB pins. It uses the loader's live
  GPIO/IO MUX clocks; cold boot without that handoff is not supported.
- The upstream `i2c-gpio` and bit algorithm provide approximately 100 kHz,
  emulated open drain and SCL readback for clock stretching. DTS requests
  5 us half cycles and a 10 ms stretching timeout (per stretch, not a total
  transaction deadline). There is no raw `/dev/i2c-*` interface.
- `drivers/input/keyboard/tanmatsu-mcu.c` owns the MCU endpoint and registers `Tanmatsu MCU keyboard` with
  evdev, keys, repeat and headphone switch events. It performs combined
  register-address/write plus repeated-start/read transactions. Firmware
  bytes 0–1 and keys 2–10 are read together; inputs use register 15. No
  device control registers are written. Linux owns the bus after handoff;
  the loader starts no BSP input/I2C tasks.

GPIO1 is unused by the Linux driver. CLIC handoff masking leaves the MCU IRQ
unrouted. Full-state polling avoids dependence on interrupt acknowledgement
and catches released keys even after a missed IRQ. Polling starts when an
input consumer opens the device, defaults to 20 ms and is adjustable from
10–100 ms through the input device's `poll` attribute. There is no suspend or
wakeup support. Transitions shorter than a poll period may be missed; fast
rollover and observed latency require device testing.

## Input behavior and failure recovery

All 72 physical matrix bits are mapped. Three space switches become one
KEY_SPACE; it stays pressed until all three are released. Fn is KEY_FN,
Meta is KEY_LEFTMETA, and left/right Shift and Alt stay distinct. F1–F6,
arrows, punctuation and volume-up retain standard Linux keycodes. Volume-down
uses active-low GPIO35, power uses register 15 bit 2, and headphone insertion
uses bit 1. Card detect bit 0 is reserved for later storage integration.
KEY_POWER emits an event only; no shutdown policy runs.

This is an evdev device with US physical key positions, not a text generator.
Consumers choose layout/Fn behavior. There is no Fn translation or session-key
policy yet. Linux input software repeat defaults to 400 ms delay / 50 ms
period; EVIOCSREP can change it. M5 adds fbcon and a local tty1 shell alongside USB recovery.

Every poll validates firmware before reporting a sample. I2C failures, short
transfers, unexpected firmware or GPIO read errors discard the whole sample,
release all keys and switches and stop any held-key repeat. A later complete
r9 sample reports current state again, including keys still held. The first
failure and recovery are logged; consecutive failures increment `read_errors`
without flooding printk. The MCU device exposes read-only `firmware_version`
and `read_errors` attributes under `/sys/bus/i2c/devices/*-005f/`.
There is no event FIFO, MCU reset, automatic firmware update or forced bus
recovery. A permanently stuck bus requires physical reset. If initial probe
fails, no input device is created: inspect dmesg, correct the cause and reboot
or rebind the MCU driver after the bus becomes available.

`m2-input-test` automatically finds the named event device, shows initial
key/switch state and repeat settings, and captures events for 30 seconds.
Optional arguments are `/dev/input/eventN` and duration 1–3600 seconds.
It handles SYN_DROPPED by discarding through SYN_REPORT and querying current
key/switch state. It does not grab the keyboard or change repeat settings.
Capture completion is not an automatic hardware PASS.

## Target regression procedure

Install the complete current set following [installation](../../INSTALL.md). Save the USB capture with
CURRENT.json/hashes, board revision, observed MCU firmware and monitor setup.

```sh
uname -a
cat /proc/bus/input/devices
ls /dev/input
cat /sys/bus/i2c/devices/*-005f/firmware_version
cat /sys/bus/i2c/devices/*-005f/read_errors
free
m1-smoke
m2-input-test
```

Compare every physical key against EV_KEY numeric codes (Linux
`include/uapi/linux/input-event-codes.h`), checking down=1 and up=0. Test
letters, digits, punctuation, Tab/Enter/Esc, arrows, F1–F6, Fn, Meta, both
Shifts, Ctrl, both Alts and both volume buttons. Hold a letter for repeat=2
and confirm repeat stops on release. Test Shift+A, Ctrl+C and Alt chords,
multiple held keys, fast typing and all three space switches: releasing one
space switch must not release the logical key while another remains held.
Check headphone insertion/removal and short power-button press events without
holding the board's hardware power button long enough to switch it off.

Run `m2-input-test /dev/input/eventN 120` for a longer capture, substituting
the discovered event path. Compare `read_errors` and memory before/after
several runs, idle and typing. Record latency, missed keys, rollover and USB
stalls. Close/reopen capture with keys held and released. Confirm each boot
probes r9 and physical reset recovers Launcher.

Host fault injection covers failed/partial reads, unexpected firmware and
recovery while modifiers are held. Electrical disconnect/reset and evdev
queue-overflow recovery still need controlled target tests. Do not disconnect
live board internals merely to exercise the software test. Record a safe
fault-injection method before attempting hardware fault tests.

The procedure above remains the regression checklist for mapping, chords,
repeat, space-switch behavior, recovery, RAM and Launcher return. User acceptance
closes M2; observations absent from this capture remain follow-ups rather than
claims of completed tests.
M5 adds display backlight through the same MCU owner and local fbcon. RTC initialization, keyboard backlight, LEDs, battery/charger reporting and
radio control remain later integrations. Safe power
commands now share the MCU owner; the key itself does not trigger them.
