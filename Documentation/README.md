# Gaijin documentation

Use this index to find the current behavior, operate the application or change
its implementation. The current implementation build is application **0.5.24 /
AppFS revision 38 / Linux 6.18.35-gaijin-m5**, with first-run home creation,
setup and confirmed dirty-home reset. Its [device checks](validation/home-storage.md)
remain pending. M1–M5 were accepted on the revision-33 baseline; the user
confirmed its device checks passed on 8 November 2026.

## Find the right document

| Need | Read |
| --- | --- |
| Install, update or run diagnostics | [Installation](../INSTALL.md) |
| Understand boot, hardware ownership, USB, ABI and packaging | [Architecture](architecture.md) |
| Build, test, find source/output files or maintain the workspace | [Development and workspace](development.md) |
| Return to Launcher, halt or power off | [Power commands](guides/power.md) |
| Use the local console, framebuffer, backlight and M5 checks | [Display](guides/display.md) |
| Validate the vi/kernel pointer fix | [User-access correction](guides/user-access.md) |
| Validate standalone battery boot and late USB attachment | [Revision 33 validation](validation/revision33.md) |
| Review the earlier application identifier repair | [Revision 32 validation](validation/revision32.md) |
| Review the earlier vi and user-access build checks | [Revision 31 validation](validation/revision31.md) |
| Understand keyboard mapping, repeat, recovery and the M4/M5 boundary | [Keyboard](guides/keyboard.md) |
| Understand SD/DMA, root transition and storage regression checks | [SD storage](guides/sd-storage.md) |
| Provision, use, resize or recover persistent home directories | [Persistent state](guides/persistence.md) |
| Review first-run home creation, setup UX and production dependencies | [First-run storage proposal](guides/persistence.md#proposed-first-run-storage) |
| Validate home creation, setup, reset and host-recovery reuse | [Home-storage validation](validation/home-storage.md) |
| Test HOME refresh without resetting an existing image | [Revision 37 display diagnostic](validation/revision37.md) |
| Test remaining startup blue flashes and inspect refresh delays | [Revision 38 boot display check](validation/revision38.md) |
| Check accepted results, limitations and original captures | [Validation](validation/README.md) |
| Inspect persistence failures, fixes and recovery evidence | [Persistence validation](validation/persistence.md) |
| Read the original architecture and milestone baseline | [Original project specification](reference/Tanmatsu_Linux_Project_Specification.docx) |
| Check source pins, compatibility targets and evidence hashes | [Source lock](../sources.lock.json) |

The topic guides describe implementation and operating procedures. Validation
records distinguish captured results, user confirmations and remaining gaps.
Raw logs and inventories live under `validation/evidence/`, grouped by tested
release. The original specification is a baseline: accepted decisions and the
current guides take precedence where the implementation evolved, including
NOMMU hush replacing the initially proposed ash.

## Milestone status

| Milestone | Status and evidence |
| --- | --- |
| M1 — USB shell and userspace ABI | Closed PASS, accepted 28 October 2026; [validation record](validation/README.md) |
| M2 — Linux SD access | Closed PASS, accepted 29 October 2026; [SD results](validation/README.md#accepted-milestone-result--m2-linux-sd-closed--pass) |
| M3 — Persistent state | Closed PASS, accepted 29 October 2026; [persistence results](validation/persistence.md) |
| M4 — Keyboard | Complete through the keyboard work accepted early as M2 on 28 October; [29 October review](guides/keyboard.md#m4-completion-and-m5-handoff) |
| M5 — Framebuffer and fbcon | Closed PASS, accepted 8 November 2026 by [user-confirmed revision-33 checks](validation/revision33.md) |
| M6 — Wi-Fi | Planned; C6 transport/firmware compatibility, connectivity and recovery |
| M7 — Package manager | Planned; signed index and .tpk lifecycle, ABI/corruption checks and interrupted activation |
| M8 — LVGL tile shell | Planned after reliable local input/display and CLI foundations |

Historical identifiers such as `m1-smoke`, `m2-input-test` and kernel `M0`/`M2`
names remain valid. The early M2 keyboard label and original M4 refer to the
same accepted input implementation. Branding, ABI descriptions and Launcher
metadata are applied to the current release and checked during packaging.

## Open work

1. **Display polish and session policy.** Continue LCD flicker and refresh/cache
   latency investigation beyond the accepted functional checks. F1–F6/Fn
   session, Launcher, GUI and power shortcuts remain unassigned.
2. **Read-only validation and offline recovery.** Distinguish absent, clean,
   dirty, incompatible and incomplete state before writable activation. Preserve
   rejected images and creation artifacts, use RAM homes and show host-recovery
   instructions. All repair, migration and backup restoration stay on the
   host; no target checker or dirty-image repair map is planned. A recognized
   dirty image may instead be explicitly erased/recreated when the user accepts
   losing its contents; incompatible images and partial transactions remain
   host-only cases. See the
   [recovery policy](guides/persistence.md#offline-host-recovery-policy) and
   [historical feasibility review](validation/persistence.md#startup-repair-feasibility).
3. **First-run storage device acceptance.** The current
   [implementation](guides/persistence.md#proposed-first-run-storage) creates a
   factory 32 MiB image in the loader, preserves clean state and supports a
   confirmed dirty-home reset through the standard AppFS argument. Complete
   the [device procedure](validation/home-storage.md) for real SD writes,
   inflater/RAM behavior, progress teardown, relaunch and power interruption.
   Recovery, migration, backup restoration and capacity changes stay on the
   host. Live resize and literal SD partition creation are separate work.
4. **USB and hardware stress.** Investigate timeouts/flow control, quantify
   simultaneous SD/input/USB latency, run longer stress and wider card tests,
   and complete the keyboard guide's physical-key and fault follow-ups.
5. **RTC/time initialization.** The clock currently starts at the Unix epoch.

These follow-ups preserve the accepted M1–M5 gates. Read-only validation,
host-recovery guidance and provisioning improvements support a stable release.

## Keeping the project compact

Maintain one document per topic. Add procedures to the relevant guide, accepted
results to validation, and roadmap changes here. Avoid new milestone recap
files that repeat a guide. Preserve original captures/inventories unchanged;
link them from validation and update live provenance paths in the source lock.

The [workspace guide](development.md#source-only-checkout-and-cleanup) describes
the source-only checkout, rebuilding generated files and preserving private
recovery material outside the project.
