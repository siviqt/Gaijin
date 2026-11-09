# Revision 38 boot display check

Application **0.5.24 / AppFS revision 38 / Linux 6.18.35-gaijin-m5** targets
the light-blue flashes still visible during startup logging. The user
confirmed revision 37 removed HOME flicker; its later 100-line console test
was visually clean and reported frames 3113 → 3557 with zero DMA
errors/underruns. See the [scoped device record](evidence/home-storage/revision37-device.json).

## Evidence and change

The pinned Linux 6.18.35 `console_emit_next_record()` masks local interrupts
around legacy console writes on this non-RT build. The
[upstream implementation](https://github.com/torvalds/linux/blob/v6.18/kernel/printk/printk.c)
has the same behavior. A long rotated fbcon draw can therefore hold up the
display completion interrupt. Startup-only flashes are consistent with this
delay, but the existing counters do not establish the physical cause.

The framebuffer now splits fill, copy and image drawing into native rows,
each at most 960 destination bytes. It polls pending completions before and
between rows using the IRQ handler's shared completion/error service. It
preserves the caller's interrupt mask, overlapping-copy direction and bitmap
row pitch. Cache writeback also polls between its existing 4 KiB chunks.
Polling stays inactive during framebuffer registration, after a DMA fault
and during shutdown. The single-core production configuration is required
for the local-IRQ serialization used here.
Kconfig excludes SMP for this driver and packaging retains the existing
single-core production requirement. Enabling a second core would require
cross-core serialization before using this polling path.

The driver exports `polled_frames` and `max_frame_us`. The latter records the
largest interval between serviced completions since scanout started, so a
boot delay remains visible at the prompt. It includes scanout time and service
delay; it does not measure hardware-to-handler latency or prove absence of
FIFO starvation. Other long IRQ-masked operations or PSRAM contention may
still require investigation if flashes remain.

Host injection executes the shipped drawing/completion code with interrupts
masked, slow drawing and DMA completion during rendering. Its bulk-draw
negative control leaves a completion pending for a modeled 160 ms; the bounded
callbacks service completions within 16.8 ms in the same model. Pixel checks
cover COPY/XOR fills, horizontal/vertical overlapping scrolls, non-byte-aligned
monochrome glyphs and indexed-color rows. Fault and inactive-state checks
verify that polling never restarts stopped DMA. These are model results,
not device timing measurements. Builds and checks are recorded in the
[host record](evidence/home-storage/revision38-host.json).

Incremental loader builds now track launcher metadata as a CMake input.
Packaging checks the ESP application descriptor version against that metadata,
rejecting a cached older loader version before producing the installation set.

## Device procedure

1. Install the complete matching revision-38 set following
   [installation](../../INSTALL.md). Preserve `/gaijin` and its home image;
   no reset or recreation is required. Safely unmount/eject the host card.
2. Cold boot and observe HOME, startup scrolling, the SD-check pause and the
   idle prompt. Report whether any whole-screen light-blue flashes remain,
   and which phase shows them. Repeat with USB disconnected to check normal
   standalone behavior. The loader HOME diagnostic remains available through
   the [existing marker](revision37.md).
3. At the prompt run `m5-display-test` and retain its complete output. It now
   prints counters since display startup before the scroll, then updated
   polling and maximum-interval counters afterward. Record the visual result
   too. Errors/underruns must remain zero and frames must increase. A positive
   polling count is expected under load but no fixed count is required.
4. Confirm keyboard input, persistent files and a clean `shutdown`/relaunch.
   This display change does not replace the remaining
   [home-storage qualification](home-storage.md).

**Physical acceptance is pending.** Revision 33 remains the last fully
accepted hardware baseline; the revision-35/36/37 reports provide scoped
home-creation, orientation and HOME-flicker evidence.
