# Gaijin documentation

Use this index to find the current behavior, operate the application or change
its implementation. The current build is application **0.5.18 / AppFS revision
32 / Linux 6.18.35-gaijin-m5**. M1–M4 are accepted. M5 — Framebuffer and fbcon
is implemented and awaits hardware acceptance.

## Find the right document

| Need | Read |
| --- | --- |
| Install, update or run diagnostics | [Installation](../INSTALL.md) |
| Understand boot, hardware ownership, USB, ABI and packaging | [Architecture](architecture.md) |
| Build, test, find source/output files or maintain the workspace | [Development and workspace](development.md) |
| Return to Launcher, halt or power off | [Power commands](guides/power.md) |
| Use the local console, framebuffer, backlight and M5 checks | [Display](guides/display.md) |
| Validate the vi/kernel pointer fix | [User-access correction](guides/user-access.md) |
| Review the application identifier repair and current device checks | [Revision 32 validation](validation/revision32.md) |
| Review the earlier vi and user-access build checks | [Revision 31 validation](validation/revision31.md) |
| Understand keyboard mapping, repeat, recovery and the M4/M5 boundary | [Keyboard](guides/keyboard.md) |
| Understand SD/DMA, root transition and storage regression checks | [SD storage](guides/sd-storage.md) |
| Provision, use, resize or recover persistent home directories | [Persistent state](guides/persistence.md) |
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
| M5 — Framebuffer and fbcon | Implemented; host checks and cross-builds pass; [hardware acceptance pending](guides/display.md#hardware-acceptance-procedure) |
| M6 — Wi-Fi | Planned; C6 transport/firmware compatibility, connectivity and recovery |
| M7 — Package manager | Planned; signed index and .tpk lifecycle, ABI/corruption checks and interrupted activation |
| M8 — LVGL tile shell | Planned after reliable local input/display and CLI foundations |

Historical identifiers such as `m1-smoke`, `m2-input-test` and kernel `M0`/`M2`
names remain valid. The early M2 keyboard label and original M4 refer to the
same accepted input implementation. Branding, ABI descriptions and Launcher
metadata are applied to the current release and checked during packaging.

## Open work

1. **M5 hardware acceptance.** Validate landscape orientation and RGB order,
   readability, backlight, local punctuation/modifiers/repeat, shell editing,
   Ctrl-C and memory/input under display and SD load. Follow the
   [display procedure](guides/display.md#hardware-acceptance-procedure).
   F1–F6/Fn session, Launcher, GUI and power shortcuts remain unassigned.
2. **Power-transition acceptance.** Validate Launcher return, physical
   power-off and clean persistence on the device. Follow the
   [power guide](guides/power.md#device-acceptance). RTC and power-key policy
   remain separate follow-ups.
3. **Conservative startup repair.** Validate image allocation/geometry, keep
   ext2 unmounted during checking, and mount only after successful repair.
   Never simply clear the dirty flag. The pinned Buildroot e2fsprogs package
   depends on MMU; a NOMMU checker needs execution, peak-RAM and interrupted
   repair testing. [Feasibility and evidence](validation/persistence.md#startup-repair-feasibility).
4. **Provisioning and resize UX.** Decide first-start size selection, dedicated
   versus shared card use, and offline/online resize scope. Current provisioning
   and offline growth are explicit host operations in the persistence guide.
5. **USB and hardware stress.** Investigate timeouts/flow control, quantify
   simultaneous SD/input/USB latency, run longer stress and wider card tests,
   and complete the keyboard guide's physical-key and fault follow-ups.
6. **RTC/time initialization.** The clock currently starts at the Unix epoch.

These follow-ups preserve the accepted M1–M4 gates. The filesystem repair,
shutdown and provisioning items become especially useful near a stable release.

## Keeping the project compact

Maintain one document per topic. Add procedures to the relevant guide, accepted
results to validation, and roadmap changes here. Avoid new milestone recap
files that repeat a guide. Preserve original captures/inventories unchanged;
link them from validation and update live provenance paths in the source lock.

The [workspace guide](development.md#source-only-checkout-and-cleanup) describes
the source-only checkout, rebuilding generated files and preserving private
recovery material outside the project.
