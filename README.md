# Gaijin

Native RV32 NOMMU Linux for Tanmatsu rev7, launched from AppFS. The current
build is **application 0.5.19 / AppFS revision 33 / Linux 6.18.35-gaijin-m5**.

This build includes the [user-access and vi correction](Documentation/guides/user-access.md).
Revision 33 removes the USB-reader dependency from early boot, prefers the LCD
console after panel setup and starts the local shell first. The user confirmed
all [revision-33 device checks](Documentation/validation/revision33.md) passed on
8 November 2026, including standalone boot and late USB attachment.
The AppFS identifier and SD directory are `com.github.siviqt.gaijin`.
Revision 32 repairs the identifier mismatch in revision 31; see the
[repair and validation record](Documentation/validation/revision32.md).
The [Penguin launcher icons](launcher/ICONS.md) are bundled at 16, 32 and 64 pixels.
It starts login shells on the LCD and USB consoles; an open USB terminal is optional.
The loader explicitly cycles SD power before mounting the card. Linux uses the
SD-backed read-only base and persistent home directories. **M1–M5 are accepted**.

Start with the [documentation index](Documentation/README.md) for guides,
milestone status and open work, or [installation instructions](INSTALL.md)
to run the current build.

## Build and install

Activate ESP-IDF v6.0.2 and its P4 compiler, then run from the source checkout:

```sh
CROSS_COMPILE=riscv32-esp-elf- make
make test
make verify
```

Copy `build/sd/` to the card and use `make install` to upload the matching
loader. `build/gaijin-current.zip` contains the same installation set and
documentation. Follow the installation guide for the complete procedure.
[Development and workspace](Documentation/development.md) covers prerequisites,
offline builds, partial builds and output locations.

User state lives separately at `/gaijin/userdata.ext2`; preserve it during
updates. Before physical reset to Launcher, stop applications and run
`cd /; gaijin-persist stop`. See [persistent state](Documentation/guides/persistence.md)
for provisioning, RAM fallback and offline recovery.

## Source map

| Path | Purpose |
| --- | --- |
| `main/`, `components/`, `launcher/` | AppFS loader, libfdt and Launcher assets |
| `linux/` | Board configuration, DTS and one consolidated kernel patch |
| `userspace/` | Buildroot extension, packages and runtime scripts |
| `tools/`, `tests/` | Build, packaging, verification and host regressions |
| [Documentation/](Documentation/README.md) | Architecture, guides, roadmap and validation evidence |
| `build/` | Generated components, dependency caches and current installation set |
| [sources.lock.json](sources.lock.json) | Source pins and acceptance provenance |
| [THIRD_PARTY.md](THIRD_PARTY.md) | Third-party provenance, notices and complete license texts |

Hardware targets: BSP 1.6.4, Launcher 0.2.8, Radio 3.4.0 and Coprocessor r9.
USB `ttyUJ0` provides boot logs and a login shell alongside LCD `tty1`.
See the [display guide](Documentation/guides/display.md) for M5 checks.

Use `shutdown` to return to Launcher, `poweroff` to turn the device off, or
`halt` to stop Linux with power on. See the [power guide](Documentation/guides/power.md).
