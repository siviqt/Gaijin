# Development and workspace

Gaijin has one current source configuration, component build set, staged SD
application and ZIP. Work in source files, build the affected components, then
package and verify the complete set. The [documentation index](README.md)
contains milestone status and open work; [architecture](architecture.md)
describes the runtime and contracts.

## Build and check

Prerequisites: ESP-IDF v6.0.2 and its P4 compiler; Python 3.12+; GNU make,
C/C++, Git, patch, dtc/fdtget, bc, bison, flex, OpenSSL development headers,
cpio, rsync, file, unzip, wget, Perl and Buildroot host dependencies. The
Espressif compiler builds the kernel and loader; Buildroot builds the NOMMU
userspace toolchain. [sources.lock.json](../sources.lock.json) pins inputs.
Initial builds need network access or populated source/download caches.

```sh
CROSS_COMPILE=riscv32-esp-elf- make
make test
make verify
```

| Command | Effect |
| --- | --- |
| `make` / `make build` | Build loader, kernel and userspace; replace the current complete SD set and ZIP |
| `make loader`, `make kernel`, `make userspace` | Build one component without changing the installation set |
| `make package` | Assemble the existing components, documents, provenance and notices |
| `make test` | Run host regressions; these do not emulate P4 hardware |
| `make verify` | Reject missing, modified or stale staged files |
| `make install` | Verify, then upload the exact staged loader with its metadata revision |
| `make run` | Start the installed AppFS application through BadgeLink |

`make sdcard`, `make m1`, `make m2` and `make m3` are historical build aliases.
Use the primary commands above for new instructions. Override `IDF_PATH` and
`BADGELINK` as needed. After partial builds, run `make package` before install.

The complete AppFS directory is `build/sd/apps/com.github.siviq.gaijin/`.
Its metadata declares the boot files, notices and licenses as external-storage
assets. The identifier is defined in `main/boot_contract.h`; runtime scripts
and package tests use the same supported path.

Run `python3 tools/audit_public.py .` before preparing a public source export.
The kernel builder defaults to the neutral `builder@gaijin` banner, and the
userspace ABI report records intermediate basenames. Public hardware captures
are redacted; keep original diagnostics and user-image backups outside Git.
Set `GAIJIN_DEBUG_SERIAL` to the board's USB/JTAG serial before using the
capture helper. `GAIJIN_OPENOCD` accepts an OpenOCD executable or installation
directory; its default is `openocd` from `PATH`.

For offline sources:

```sh
CROSS_COMPILE=riscv32-esp-elf- make build \
  KERNEL_ARGS='--tarball /path/to/linux-6.18.35.tar.xz' \
  USERSPACE_ARGS='--source /path/to/clean/pinned/buildroot --download-dir /path/to/downloads'
```

## Where files belong

Paths below are relative to the source checkout, except device paths that
start with `/`.

| Path | Authoritative content |
| --- | --- |
| `main/` | Loader, boot contract, handoff and BSP SD diagnostic |
| `components/libfdt/`, `LICENSES/` | Vendored FDT implementation and license material |
| `launcher/` | AppFS metadata and icon |
| `linux/tanmatsu_defconfig`, `linux/tanmatsu.dts` | Supported board configuration and wiring |
| `linux/patches/` | Ordered board and user-access kernel patches |
| `userspace/configs/`, `userspace/busybox.fragment` | Buildroot and BusyBox configuration |
| `userspace/package/`, `userspace/overlay/` | Target programs and runtime files |
| `tools/` | Loader/kernel/userspace builders, boot packaging, release verification and state provisioning |
| `tests/`, `tests/fixtures/` | Host regressions and simulated target fixtures |
| `Documentation/` | Current guides, roadmap, reference and accepted evidence |
| `sources.lock.json`, `dependencies.lock`, `sdkconfig.defaults` | Source pins, IDF component pins and loader defaults |

`tests/kernel_source.py` extracts drivers from the current patch for host
fault injection. `tools/verify_userspace.py` checks NOMMU ABI/archive bounds;
`tools/package_current.py` owns the public release inventory, hashes and ZIP.
Do not hand-edit their generated output or prepared dependency sources.

## Generated outputs and caches

| Path | Purpose |
| --- | --- |
| `build/sd/apps/com.github.siviq.gaijin/` | Canonical installation tree; preserve external `/gaijin/userdata.ext2` |
| `build/gaijin-current.zip` | Single downloadable installation bundle |
| `build/CURRENT.json`, `build/SHA256SUMS` | Release identity and complete public-file inventory/hashes |
| `build/loader/`, `build/kernel/`, `build/userspace/` | Current component workspaces and ABI intermediates |
| `build/Documentation/`, `build/tools/`, `build/licenses/` | Generated release mirrors |
| `build/README.md`, `build/INSTALL.md`, `build/sources.lock.json`, `build/THIRD_PARTY.md` | Generated copies of their source files |
| `build/VERIFICATION.json`, `build/current-tests.log`, `build/current-package.log` | Latest workspace verification and check output |
| `build/linux-6.18.35/`, `build/linux-6.18.35.tar.xz` | Prepared runtime kernel source and pinned offline archive |
| `build/buildroot/`, `build/downloads/` | Pinned Buildroot checkout and dependency downloads |
| `build/host-hush/` | Native NOMMU regression-shell cache |
| `managed_components/`, `sdkconfig` | ESP-IDF generated dependencies and resolved configuration |
| `backups/` | Private original userdata recovery image and recovery metadata; excluded from releases |

Packaging discovers the curated documentation tree recursively. It replaces
release mirrors so moved or removed documents do not accumulate in the ZIP.
The source and ZIP use the same `README.md` and `INSTALL.md` names and relative
links. The original specification is bundled with the guides. Raw evidence and
reference bytes stay unchanged when moved; historical inventories retain their
original paths. See [evidence locations](validation/README.md#evidence-locations).

## Source-only checkout and cleanup

The project is kept as source, configuration, tests, documentation and license
material. Generated build trees, downloaded dependencies, diagnostic profiles,
resolved `sdkconfig` and Python caches were removed after the revision-30
build, verification and SD update. Rebuilding recreates them; initial builds
need network access or externally retained source/download caches.

The successful native U-mode adapter is part of the canonical kernel patch.
There is no separate machine-mode application profile or experimental kernel
folder to copy back into the source. `tools/debug/capture-frozen-p4.sh` is the
maintained capture entry point and writes new logs under `build/boot-trace/`.
`tools/verify_clic.py` checks the P4 trap adapter after a kernel build; use the
same `CROSS_COMPILE` prefix used for the build, then repeat with
`--negative-control` to verify that the model detects a stranded IRQ level.

Private card backups, the final bundle and matching kernel symbols are archived
outside this checkout under the user's Gaijin documentation archive. They are
not part of the public release inventory. Existing accepted evidence remains
in `Documentation/validation/evidence/`.

For an intentional source-only cleanup after a completed build, remove
`build/`, `managed_components/`, generated `sdkconfig`/`sdkconfig.old` and
`__pycache__` directories. Preserve private user-state backups outside the
checkout first. Never remove `sources.lock.json`, `dependencies.lock`,
`sdkconfig.defaults`, the consolidated kernel patch or userspace sources.
