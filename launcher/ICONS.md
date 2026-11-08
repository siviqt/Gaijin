# Gaijin launcher icons

Penguin is the selected set. All three choices are included in the AppFS
package as RGBA PNG files at exactly 16, 32 and 64 pixels square.

| Choice | Directory | Design |
| --- | --- | --- |
| A — Terminal | `icons/terminal/` | Mint terminal prompt on charcoal |
| B — Penguin | `icons/penguin/` | Penguin face on navy; selected |
| C — G Chip | `icons/g-chip/` | Ivory G on a violet chip |

Each directory contains `icon16.png`, `icon32.png` and `icon64.png`. The
metadata's `icon` entries point to Penguin at all three sizes. `icon.png`
is a compatibility copy of the selected 32-pixel icon.

To select another bundled set, change the three `icon` paths in
`launcher/metadata.json`, update the compatibility copy and repackage. The
firmware version and AppFS executable revision remain 0.5.17 / 31 because
this update changes icon assets only.

The artwork was created with built-in image generation and exported to exact
RGBA dimensions. The generation prompts are recorded in
`launcher/icon-prompts.json`. High-resolution working masters are kept outside
the source tree; the nine release PNGs are the authoritative shipped assets.
