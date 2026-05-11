# Changelog

## v26.05.3 — 2026-05-10

### Licensing

- **Relicensed from MIT to GNU GPL v3.0.** All code in this repository is now distributed under the terms of the GNU General Public License, version 3 — see [LICENSE](LICENSE) for the full text. The About dialog's License tab and the README license section have been updated to reflect the new terms, and the `.deb` / `.rpm` package metadata now declares `GPL-3.0`. Any future redistribution or derivative work must comply with the GPL's copyleft terms.

### Removed

- **Codeberg mirror.** The Codeberg link in the About dialog and the two Codeberg URLs in `updater.py`'s `EXPECTED_ORIGINS` set have been removed. GitHub is now the sole authoritative source for the project. The mirror repository at `codeberg.org/flintwaveradio/flintwave-kdh-flasher` is being retired.

## v26.05.2 — 2026-05-09

### Changed

- **Deferred initial port probing.** At launch the Handset list now stays passive — no `CMD_HANDSHAKE` traffic and no auto-checking of PC03 cables until the user has picked a radio and a firmware file. The first probe fires the moment the Handset column unlocks (at the same time `arrow1` pulses green). Hot-plug detection still runs in the background but only refreshes the list view; it never touches serial devices on its own. Avoids surprising the user with serial I/O on devices they haven't authorized the app to talk to yet.

## v26.05.1 — 2026-05-09

### Major rework of the GUI

- **Three-column layout** modeled on BalenaEtcher: **Firmware → Handset → Flash**, with `›` arrows between columns. Below: an **Instructions** panel (left) and a scrolling **Log** (right).
- **Workflow gating** — Handset and Flash columns are disabled until their prerequisites are met. The Download button is disabled until a radio is picked. Each arrow softly pulses green when its destination column unlocks, so the next step is visually obvious.
- **Per-radio Instructions panel** — bootloader keys, connector, tested status, latest firmware version, and freeform notes from `radios.json` are rendered into the bottom-left panel and update live as the radio dropdown changes.
- **Custom title bar** — the OS chrome is hidden in favor of a themed title bar with app icon, drag-to-move, minimize, and close (no maximize). Edge-resize is preserved.
- **Borderless columns**, centered headings, generous spacing, a slim 50%-gray divider between the top and bottom rows.
- **Default dimensions:** 1280×720 (16:9), minimum 960×540.

### Handset column (replaces the old Find Cable wizard and Batch Flash dialog)

- Multi-select list of detected USB serial ports — checkboxes, **All / None** buttons, **Refresh / Probe** to re-scan and re-handshake.
- USB-only filter — the 16550-style `/dev/ttyS*` motherboard ports are hidden, so probing isn't slowed by 30+ phantom UARTs.
- **Auto-refresh** — a background poller picks up plug/unplug events within ~2 seconds; user-made check states are preserved.
- **Probe on refresh** — each port gets a `CMD_HANDSHAKE` and is marked `Ready` if a radio in bootloader mode answers.
- **Batch flash is now inline** — checking multiple handsets and clicking Flash Firmware runs them sequentially, with per-row `Status` and `%` progress and a continue/stop prompt on per-port failures.
- Port column shows the basename only (e.g. `ttyUSB0`), not the full `/dev/...` path.

### Themes

- Two Catppuccin palettes: **Mocha** (dark) and **Latte** (light).
- The app reads `wx.SystemSettings.GetAppearance()` on launch and starts in the matching theme.
- A **☀ / ☾** toggle in the status bar switches at runtime; both palettes are applied recursively to every widget (panels, buttons, list, log, dialogs).
- Buttons render with rounded corners via injected GTK CSS; disabled state dims them so workflow gating reads at a glance.

### Auto-update

- Removed the in-app `git pull`-and-restart flow (it was unreliable on Linux git installs).
- Replaced with an unobtrusive **Update Available** link in the status bar that appears only when a newer release is detected; clicking it opens the releases page in the user's default browser.

### Status bar

- Borderless text labels (no button frames): font-size cycler, theme toggle, Usage Guide, and About.
- Slightly darker background to visually separate from the main panel.
- The font cycler now cycles 9 / 11 / 12 / 14 / 16 pt, defaults to **12 pt**, and applies to every widget on launch (column headings get +3pt and bold).

### Errors and diagnostics

- Linux `EACCES` on `/dev/ttyUSB*` is now caught at the flash, batch, diagnostics, and probe paths and surfaced as a one-line dialout-group fix-it hint instead of a cryptic `[Errno 13]` traceback.
- `flash_firmware.probe_port` re-raises `PermissionError` so the GUI can react instead of marking every port "No response".
- Dry-run and Diagnostics now have their own terminal-state hints (`dryrun_complete`, `diag_complete`) so the user is no longer told to "power-cycle the radio" after a dry run that never touched it.

### Tests

- 95 → **97** unit tests, all passing on Python 3.12 with wxPython 4.2.1.
- New coverage: Mocha + Latte palette shape, `THEME_PALETTES` keys, all required `HINT_COPY` states (incl. `no_handset`, `batch_ready`, `dryrun_complete`, `diag_complete`), handset-status string constants, manufacturer-name dedup against `radios.json`, releases-URL helper.

### Security

- `/.github/workflows/build-release.yml` now declares `permissions: contents: read` at the workflow level (the `release` job overrides locally to `contents: write`). Resolves three CodeQL `actions/missing-workflow-permissions` alerts.

### Removed

- `PortFinderDialog` — folded into the Handset column.
- `BatchFlashDialog` — folded into the Handset column.
- The five-theme menu (Latte / Frappé / Macchiato / Mocha / High Contrast) — collapsed to two themes plus a single toggle.

---

Earlier releases are recorded in the git log; see [the commit history on GitHub](https://github.com/FlintWave/flintwave-kdh-flasher/commits/master).
