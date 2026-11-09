# Revision 37 HOME refresh test

Application **0.5.23 / AppFS revision 37 / Linux 6.18.35-gaijin-m5** replaces
loader timer polling with an owned, internal-RAM DMA-completion interrupt.
The first transfer is armed before DPI starts. Bar updates are coalesced to
10 Hz; changed native rows use aligned 128-byte cache calls, with a general
4 KiB limit. Static screen contents keep refreshing independently of drawing.
The existing framebuffer and Linux/user-home formats are retained.

The loader build, host tests, compiled ISR placement and package checks are
recorded in [the host record](evidence/home-storage/revision37-host.json).
The user subsequently confirmed HOME flicker is gone; see the
[scoped device result](evidence/home-storage/revision37-device.json). Startup
blue flashes remained, while the post-boot scrolling test was clean. The
[revision-38 check](revision38.md) targets that remaining path. Full home and
display qualification remains pending. Revision 36's user-confirmed upright
screen and stable Linux output remain scoped evidence; that build still
flickered during HOME.

## Non-destructive display test

1. Install the complete revision-37 application directory and matching AppFS
   loader using [installation](../../INSTALL.md). Preserve `/gaijin` and its
   existing home image. Existing compatible homes normally skip HOME progress.
2. On the host, create a regular file `/gaijin/display-test` on that SD card
   with exactly this one line and a final newline:

   ```text
   gaijin-display-test-v1
   ```

   For example, substituting the mounted SD root:

   ```sh
   printf 'gaijin-display-test-v1\n' > /path/to/card/gaijin/display-test
   ```

   Safely unmount/eject before moving the card. The loader opens this marker
   read-only; it does not remove the marker, reset a home or write test data.
3. Boot with a USB capture. The loader prints `HOME display test: 5s static,
   then 5s progress`, shows a static HOME screen for five seconds, then fills
   the progress bar over five seconds. Check the entire screen for light-blue
   flashes in both phases. Orientation should remain upright. It then stops
   its interrupt/DMA and continues normal boot. Ordinary home provisioning
   rules still apply afterward if the home image is absent.
4. Retain the complete line beginning **`HOME refresh irq v1:`**, followed by
   the `Gaijin home` line and Linux startup. For a successful diagnostic:

   - `frames` increases (roughly 600 in this ten-second test).
   - `underrun-observations=0`, `dma-errors=0`, `cache-errors=0`, `fault=0`.
   - `irq-released=1`; no loader handler remains before Linux.
   - `max-frame-us` is the maximum interval between completion interrupts,
     normally near 16,677 us; it is not a measured hardware-to-ISR latency.
   - `draws` is at most about 52 and `draw-sync-bytes` about 844,800; this
     excludes the descriptor's per-frame cache operations.

   Underrun observations sample/clear a sticky raw bit; zero observations
   alone do not prove the absence of every underrun. Visual behavior matters.
5. Confirm normal Linux console, input, existing persistent files and clean
   shutdown. Repeat the diagnostic and ordinary boot with/without USB.
   Remove the `display-test` marker on the host when finished; otherwise the
   diagnostic repeats each boot. Always safely unmount/eject after host edits.

## Creation and teardown qualification

On a disposable test card, exercise normal creation and readback with the
same matching release. Retain the refresh summary and verify no visible blue
flashes, no observed underruns, zero DMA/cache errors and expected factory
image hash. Preserve the original user card; a diagnostic-only run is enough
to test static/animated scanout on an existing home.

Host injection executes the shipped ISR/render/stop code with repeated frame
completion, descriptor writeback, raw underruns, DMA errors, cache failures,
interrupt allocation/enable/disable/free failures, completion during rendering
and stop, and a stuck DMA channel. Cleanup either releases the handler and
drains DMA or returns a latched error that prevents Linux handoff. The
100 ms drain bound and idempotent stop are checked. A failed interrupt free
can be retried, but its fault still prevents handoff. The DMA channel itself
is retained so clocks/register setup expected by Linux remain available.

The native single-core model and compiled placement checks cannot emulate
PSRAM bus contention, the DSI FIFO, physical interrupt latency or visual
scanout. Record device results before calling the flicker correction accepted.
