# Display and local console

M5 implements the Tanmatsu rev7 ST7701 framebuffer, landscape fbcon and MCU
backlight control. Application 0.5.19 / AppFS revision 33 uses Linux
6.18.35-gaijin-m5 with login shells on LCD tty1 and USB ttyUJ0.
The user confirmed all revision-33 device checks passed on 8 November 2026;
see the [acceptance record](../validation/revision33.md).
The current follow-up is application 0.5.24 / AppFS revision 38; its
[startup flicker check](../validation/revision38.md) remains pending.

## Ownership and memory

M5 selects a split initialization interface: the loader resets and configures
DSI on every launch using the pinned HAL, and Linux starts scanout and owns
refresh. This changes the original specification's preference for full Linux
panel initialization. It avoids duplicating PHY/clock bring-up in this first
fbcon implementation and does not rely on a Launcher splash or inherited
pixel allocation. A future Linux modesetting driver can replace this handoff.

The loader enables LDO3 at 2.5 V, resets the panel on GPIO14 and sends the
pinned Tanmatsu ST7701 initialization sequence. DSI uses two 500 Mbit/s lanes,
a 30 MHz pixel clock, 480x800 native pixels and RGB565. It uses the existing
ESP-IDF DSI bus and DMA configuration APIs, with no PSRAM heap, DMA2D copy or
GPIO11 TE interrupt. Revision 37 temporarily owns a small internal-RAM
DMA-completion handler for HOME progress, removing it before Linux handoff.
It makes no DSI reads,
whose stock HAL wait has no absent-panel timeout. A successful command write
therefore does not prove that the panel is connected. Initialization errors
leave the DT display node disabled and preserve the USB recovery shell.

The loader reserves `0x48e00000..0x48ebc000` (752 KiB) in DT reserved-memory.
The first 768,000 bytes hold 480x800 little-endian RGB565 pixels; one 64-byte
DW-GDMA descriptor starts at `0x48ebb800`. This range lies between the DTB
scratch and external initramfs and is excluded from Linux allocation even
when display initialization fails. The boot-contract parser rejects a missing,
changed or reusable reservation before the loader writes that range.

Linux's `tanmatsu-fb` driver owns DW-GDMA channel 0, its source-24 CLIC
interrupt, and the DSI bridge output enable. At handoff, the loader leaves DMA
and bridge scanout stopped until Linux arms the descriptor, avoiding startup underruns.
Each frame completion republishes the descriptor and restarts DMA;
no ESP-IDF callback runs after handoff. Descriptor cache invalidation precedes
CPU modification and writeback. DMA errors stop refresh, mask the source and
increment `dma_errors`, allowing USB diagnosis without an interrupt retry loop.
The driver validates the reserved memory and loader descriptor before use.

Revision 38 bounds fbcon fill, copy and image operations to one native row
(at most 960 destination bytes), polling the same completion/error service
before drawing and between rows. Copy direction preserves overlapping scrolls;
mono data retains byte-padded row strides. Polling preserves the caller's IRQ
mask and is inactive before scanout starts or after a fault/shutdown. This
addresses the long IRQ-masked legacy printk writes seen in the pinned kernel;
physical confirmation is still required. `polled_frames` counts completions
serviced by polling, including between cache-writeback chunks. `max_frame_us`
records the largest interval between serviced completions since scanout started,
including boot; it is not a hardware interrupt-latency measurement.

The P4 DMA cannot snoop PSRAM caches. A Linux delayed work item writes back the
pixel buffer every 50 ms in ranges of at most 4 KiB. This gives the frame
completion interrupt a chance to re-arm scanout between ROM cache calls.
This also covers `/dev/fb0` writes. Scanout is approximately 60 Hz; CPU changes
become visible at up to 20 Hz. Single buffering can tear. No second full-size
buffer or contiguous Linux pixel allocation is needed. `/dev/fb0` supports
read/write and fixed-mode fbdev ioctls; mmap, panning, mode changes and hardware
blanking are not implemented. Use the backlight interface for darkness.

The public program-load cache flush now uses the P4 ranged helper instead
of whole-cache maintenance. Ranged instruction-cache maintenance is split
into 4 KiB IRQ-exclusive sections. This gives the frame interrupt opportunities
to rearm DMA during large bFLT loads. Blue flashes remain a hardware validation
item; run `m5-display-test` and record underrun counters under boot/scroll load.

The revision-36 user report confirms stable Linux output but constant blue
flicker during HOME creation. The loader's timer-based frame restart and
per-chunk full-frame cache flush differ from the Linux path described above.
Revision 37 replaces the poll with immediate DMA-completion rearming, starts
DMA before DPI, coalesces the bar at 10 Hz and flushes only newly changed rows.
Each cache call is at most 4 KiB; bar-row calls are 128 aligned bytes. The
interrupt and controller configuration are preserved separately: the handler
is disabled/freed before Linux while the DMA channel and clocks remain configured.
The summary reports frames, raw underrun observations, DMA/cache errors,
maximum inter-completion interval, rendering work and interrupt release.
See the [implementation/dependency record](../reference/home-progress-flicker-proposal.md)
and [revision-37 display test](../validation/revision37.md). The user confirmed
HOME flicker is gone. Blue flashes remained only during startup, with a clean
post-boot scroll diagnostic; revision 38 targets that path. See the
[device result and retest](../validation/revision38.md).

## Shell, keyboard and backlight

After successful panel setup, bootargs enable `console=ttyUJ0,115200n8` and then
`console=tty0`; the LCD becomes the preferred `/dev/console`, including storage
rescue. Failed display setup keeps USB as the only recovery console. Local tty1
login starts before USB ttyUJ0, and production boot has no USB earlycon wait.
`fbcon=rotate:1,font:10x18`
selects 90-degree landscape rotation: 800x480, 80 columns by
26 rows. `consoleblank=0` keeps the local text visible.
The first device boot showed that `rotate:3` was upside-down; Linux fbcon's
rotation convention needs `rotate:1` on this mounting. The loader's HOME
progress renderer uses the same clockwise transform: landscape `(x,y)` maps
to native `(479-y,x)`. Revision 35 created the home successfully but drew this
screen upside down; revision 36 corrects the loader transform. Existing homes
are reused without displaying creation progress.

To correct an older
running build without rebooting, use the USB shell:

```sh
echo 1 > /sys/class/graphics/fbcon/rotate_all
```

The permanent setting is supplied by the AppFS loader, so install the updated
revision-33 loader as well as copying the updated SD application files.


Revision 21 tested one direct login shell on USB ttyUJ0 with display refresh.
Revision 22 tests the same shell with the framebuffer node disabled.
F1–F6 and Fn session shortcuts remain unassigned.

Input and backlight share the existing `tanmatsu-mcu` I2C client. The backlight
class device is `/sys/class/backlight/tanmatsu`, register 11, range 0..255,
default 160. Every write checks firmware r9; partial I2C transfers fail.
The I2C adapter serializes keyboard and backlight transactions.

```sh
cat /sys/class/backlight/tanmatsu/actual_brightness
echo 80 > /sys/class/backlight/tanmatsu/brightness
echo 0 > /sys/class/backlight/tanmatsu/brightness
echo 160 > /sys/class/backlight/tanmatsu/brightness
```

`shutdown` returns to Launcher, `poweroff` switches off and `halt` stops Linux.
See the [power guide](power.md) for cleanup and device tests.
They stop applications and persistence before the transition. KEY_POWER still
emits an input event only. Before a physical reset/power cut, use
`cd /; gaijin-persist stop`. RTC/time initialization remains separate; time
still starts at the Unix epoch. See [persistent state](persistence.md).

## Hardware acceptance procedure

Install the complete matching loader/kernel/userspace set using
[installation](../../INSTALL.md), retaining a USB capture. The following
procedure remains the regression checklist after revision-33 acceptance.

1. Cold start from Launcher and repeat after physical reset. Check the M5
   loader banner, enabled tty0 and ttyUJ0, `fb0: 480x800 RGB565`, local prompt,
   upright landscape output, readable 80x26 text and all four screen edges.
   Verify red/green/blue pixel order with a framebuffer pattern, and test the
   backlight at 0, 80, 160 and 255, returning it to 160.
   Also complete the [revision-33 battery and USB checks](../validation/revision33.md)
   with no USB reader present during startup.
2. Run `m5-display-test` locally and through USB. It prints geometry and memory,
   scrolls 100 lines on tty1 and requires increasing frame count with zero
   DMA errors and zero observed DSI underruns. Its PASS covers refresh only.
   Check `/sys/class/graphics/fb0/device/{frames,dma_errors,underruns}` before
   and after longer loads. Underruns indicate insufficient scanout bandwidth;
   retain the log and reset after stopping persistence if output is corrupted.
3. Type letters, digits, all printed punctuation, Shift variants, both Alt
   keys, Ctrl and overlapping space switches. Test Backspace, repeat/release
   and Ctrl-C during `sleep 30`. Run `exit` and verify
   shell respawn. The [keyboard guide](keyboard.md) owns evdev fault checks.
4. While scrolling through USB, type locally and run `m2-sd-test` from the
   other shell. Check input read-error counters, held-key release, memory,
   display counters and USB responsiveness. Repeat `m1-smoke` and
   `m3-state-test`, then cleanly stop persistence before reset.
5. Verify USB recovery when display is unavailable and the RAM rescue shell
   when the SD root fails. Compare card-file hashes around testing as in the
   [SD guide](sd-storage.md). Do not claim a display failure was detected just
   because command writes succeeded with a physically disconnected panel.

Record actual image identity, readable orientation, brightness, typed input,
repeat/Ctrl-C, memory and simultaneous-load results. Keep implementation status
separate from hardware acceptance in [validation](../validation/README.md).

Revision 20 disables software flow control in BusyBox init's existing
`TCSANOW` setup for both terminals. The boot scripts proceed without the
output drain used by the `stty` applet. Revision 21 uses the USB profile markers and delayed framebuffer snapshot
for boot diagnosis.
