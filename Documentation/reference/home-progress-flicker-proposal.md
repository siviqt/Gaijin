# HOME progress flicker: investigation and implementation

9 November 2026. Investigated on application 0.5.22 / AppFS revision 36;
implemented in application 0.5.23 / AppFS revision 37 after user approval.
Kernel and userspace binaries are unchanged; physical flicker acceptance is
pending. See the [revision-37 diagnostic procedure](../validation/revision37.md).

## Observed result

The user confirmed upright HOME output and normal Linux operation, with
constant light-blue flicker during the home operation and no flicker after
the console loads. This supersedes the pending orientation observation in
the revision-36 host record. It does not complete storage durability/reset
qualification. See the [scoped device record](../validation/evidence/home-storage/revision36-device.json).

## Diagnosis

**DSI FIFO starvation is the strongest explanation.** The framebuffer remains
allocated; the display bridge needs a continuous stream of its pixels.
We have not captured the loader's raw underrun state on the physical device,
so attribution of every blue flash remains an inference from the code,
Espressif's documented driver behavior and the user's phase-specific report.

There were two concrete weaknesses in the revision-36 loader:

1. `main/display.c:138` rearms a one-frame DMA descriptor by polling from an
   `esp_timer` task every 16,000 us. A busy channel is skipped until the next
   callback. The configured 30 MHz clock, horizontal total 590 and vertical
   total 848 imply approximately 59.96 Hz / 16.677 ms per panel frame, with
   only 0.944 ms of nominal vertical blanking. Task polling has no guaranteed
   relationship to DMA completion; the delay before rearming can approach a
   timer period plus scheduling/cache delays. A shorter poll period would
   reduce some gaps without guaranteeing continuous delivery.
2. `main/display.c:155` redraws the bar and synchronizes the entire 768,000-byte
   framebuffer for every 32 KiB image chunk. Creation and full readback each
   process 1,024 chunks: about 2,048 full-frame sync calls, even when the
   integer percentage is unchanged. They request about 1.46 GiB of cache
   maintenance ranges, not necessarily that much external-memory write
   traffic. The pinned build has chunked C2M cache operations disabled;
   ESP-IDF holds a critical section around each full writeback. This adds
   interrupt blocking as well as PSRAM traffic.

Linux uses a materially different path: `tanmatsu_fb_irq` rearms on DMA
completion and `tanmatsu_fb_flush` limits cache maintenance to 4 KiB calls.
This agrees with the observed transition to stable output at the console.
The current loader also enables DPI before arming its first transfer; that
can introduce an initial starvation interval, but does not alone explain
the continuous flicker.

Espressif's **v6.0.2** DPI driver immediately restarts DMA in its completion
callback and identifies blue display output as an underrun symptom. Its
first-start sequence arms DMA before enabling DPI. Primary sources:

- [Pinned DPI implementation](https://github.com/espressif/esp-idf/blob/v6.0.2/components/esp_lcd/dsi/esp_lcd_panel_dpi.c)
- [Pinned cache-maintenance implementation](https://github.com/espressif/esp-idf/blob/v6.0.2/components/esp_mm/esp_cache_msync.c)
- [Pinned DW-GDMA interrupt and descriptor implementation](https://github.com/espressif/esp-idf/blob/v6.0.2/components/esp_driver_dma/src/dw_gdma.c)

## Implemented correction in revision 37

1. Replace the periodic scanout timer with a small, explicitly owned
   **DMA-completion interrupt**. Keep its handler and state in internal RAM.
   Rebuild/publish the 64-byte descriptor and restart the next frame
   immediately. Stop on DMA errors; do not log or perform filesystem work
   inside the handler. Prime the first transfer before enabling DPI.
2. Decouple rendering from scanout. Retain the current framebuffer and
   orientation. Draw static HOME text/border once; skip repeated percentages;
   cap visible updates around 10 Hz while always allowing the final 100%.
   Update the bar's changed region, align cache ranges to the P4's 64-byte
   lines and limit each cache operation to at most 4 KiB. Keep frame refresh
   running when the image/content is unchanged.
3. Make start/stop/error paths explicit. Mask the interrupt, prevent rearming,
   stop DPI/DMA, drain with the existing bounded timeout, clear pending
   events, and disable/free the loader interrupt before handoff. Preserve the
   DMA configuration and descriptor contract expected by Linux. Cleanup must
   be idempotent and cover allocation, cache and DMA failures. A failed
   drain must retain the existing return-to-Launcher behavior.
4. Add bounded diagnostics: completed frames, DMA errors, underrun
   observations and maximum inter-completion interval. Read/clear the raw
   bridge underrun bit; it is sticky, so label the counter as observations,
   not an exact count of all hardware underruns. Print one summary from task
   context after stopping, rather than per-frame logging.

An owned `esp_intr_alloc` handle with the pinned LL register operations is
appropriate here: it can be disabled/freed without deleting the SDK DMA
channel and potentially changing clocks needed by Linux. Using the full BSP
display driver would also introduce its framebuffer allocation/ownership
model. The existing reserved buffer is sufficient for this progress UI;
another full framebuffer is not required to correct delivery gaps.

## Production dependencies and acceptance gates

| Gate | Dependency and scope |
| --- | --- |
| P0 — Confirm measurements | Revision-36 visible flicker is user-confirmed; no raw baseline counter capture exists. Revision 37 adds counters and the display-only test for device measurement. User-image policy is unchanged. |
| P1 — Loader lifecycle | Implemented: owned interrupt, internal-RAM ISR/state, fresh descriptor and checked cleanup. Host fault injection and built ISR placement pass. The timer-only/no-display-ISR assumption is replaced. |
| P2 — Bounded rendering | Implemented: 10 Hz percentage coalescing, aligned 128-byte dirty-row calls, 4 KiB general limit. Initialization flush runs before scanout. Host rendering/bounds checks pass. |
| P3 — Handoff regression | Host models pass stopped/cleared source, freed handler, repeated stop, late completion, cleanup retry and 100 ms timeout. Reserved framebuffer, DMA setup, kernel and SD gate are retained. Physical handoff regression remains. |
| P4 — Device qualification | Hold a static progress screen, exercise full creation/readback on a disposable test card, then repeat handoff and Linux display/input/power checks. Require no visible blue flashes, no observed underruns during the test, zero DMA errors, expected image hashes and clean shutdown. |

P1–P3 software checks pass; P0's raw measurement and P4's physical acceptance
remain for the user test. Host fault injection executes repeated frame
completion, cache/DMA failure, stop racing with completion and rollback after
partial setup. Hardware tests are still required for timing and PSRAM
contention. The display-only diagnostic tests this without deleting an
existing user home.

This change stays in the AppFS loader and its tests/documentation. Linux,
userspace ABI, factory ext2 format, image contents and partition layout need
no change. Use the existing display timings first; bandwidth/clock changes
should depend on residual measured underruns after correcting delivery and
cache handling.
