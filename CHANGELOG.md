# Changelog

## v26.05.6 — 2026-05-12

### Build / CI

- **Release workflow is now idempotent on duplicate tag triggers.** GitHub occasionally fires the release workflow twice for the same tag push (it happened on v26.05.5 — runs `25723159486` succeeded, `25723160099` failed with "release already exists"). Two changes prevent the race:
  - A `concurrency: release-${{ github.ref_name }}` block coalesces simultaneous tag-push triggers into a single workflow run.
  - The release step now checks for an existing release first and uploads assets with `--clobber` instead of failing if one exists. Either path produces a green status check.
- No app code changes — this release rebuilds the same set of binaries with the working release pipeline so future releases come up green from the start.

## v26.05.5 — 2026-05-12

### New radios

- **Radtel RT-950 Pro** (AT32F403A, .BTF firmware) is now supported as a first-class radio. The on-the-wire protocol is implemented in a sibling module (`flash_btf.py`) that shares the CRC-16/CCITT helper with the KDH path. The .BTF wrapper is sent to the radio as-is — the on-radio bootloader decrypts the XOR-encrypted payload using the 16-byte key embedded at file offset 0x400. Reference: [Hertzz58/Radtel-RT950-Pro-Firmware](https://github.com/Hertzz58/Radtel-RT950-Pro-Firmware) (GPL-3.0). UNTESTED on real hardware — community confirmation requested.
- **Seven JC-8629 family clones added to `radios.json`** as zero-code manifest entries. All seven (Socotran JC8629, Socotran FB8629, Jianpai 8800 Plus, Boristone 8RS, Abbree AR-869, HamGeek HG-590, MMLradio 8629) share the Math Mark JC-8629/8630 PCB with the existing Radtel RT-490, register as a single driver class in CHIRP issue #9665, and use the same `.kdhx` firmware. Source-of-truth English instructions live in `radios.json`; translations in `translations/*.json`.

### Multilingual UI

- **Per-radio bootloader instructions, connector type, and notes are now translated** for every supported language. A new `i18n.t_radio_field(radio_id, field, fallback)` helper looks up `radio.<id>.<field>` in the active catalog and falls back to the English source from `radios.json`, so contributors adding a new radio only edit one file. 252+ machine-translated strings shipped (12+1 radios × 3 fields × 7 languages); flagged `_meta.reviewed: false` for community refinement.
- **Language picker moved from the title bar to the status bar.** The old title-bar dropdown is replaced by a `🌐 <native-name>` clickable entry next to the font-size and theme controls. Clicking opens a modal with all 8 languages listed in their native scripts; double-clicking applies. The title bar is now reserved for identity (icon + title) and window controls only.

### Tests

- 98 → **112** unit tests, all passing on Python 3.12 with wxPython 4.2.1.
- New coverage: BTF protocol module (constants, packet framing, CRC self-consistency, response parsing, validation, error code map, radios.json registration); per-radio translation completeness and "model echoed input" detection across all 7 non-English catalogs.

### Known limitations

- The RT-950 Pro flow is implemented and statically validated against the reference impl, but has not yet been tested against a real RT-950 Pro radio. Test reports welcome.

## v26.05.4 — 2026-05-11

### Rebrand

- **Renamed to FlintWave Flash** (from "FlintWave KDH Flasher" / "KDH Bootloader Firmware Flasher"). Window title, About dialog, license header, installers, `.desktop` entry, Windows shortcut, macOS bundle identifier (`com.flintwave.kdh-flasher` → `com.flintwave.flash`), Linux package names (`flintwave-kdh-flasher` → `flintwave-flash`), and every CI-built artifact (`FlintWave-KDH-Flasher-*` → `FlintWave-Flash-*`) all rebrand to FlintWave Flash. The hardware-protocol nouns — `.kdhx` file extension, "KDH bootloader", `extract_kdhx()`, the "Other KDH Radio" entry in `radios.json` — are left unchanged because they name real things, not the app.
- **GitHub repository URL is unchanged** (`flintwave/flintwave-kdh-flasher`); all in-app URLs continue to point there.
- **User-data directory migrated** from `~/.flintwave-kdh-flasher` to `~/.flintwave-flash` on first launch (idempotent rename via `firmware_manifest._migrate_state_dir()`), preserving cached manifest data, last-flashed records, and downloaded firmware across the rename.

### Multilingual UI

- **Language dropdown in the title bar** offers English, 中文 (Simplified Chinese), Français, Deutsch, Italiano, Español, العربية (with full right-to-left layout mirroring via `wx.Layout_RightToLeft`), and Русский. Native-script labels.
- **English bundled, others downloaded on demand** from `translations/<code>.json` in this repo and cached under `~/.flintwave-flash/translations/`. Selection persists in `state.json` between sessions and applies live without restart via a register/apply table that re-renders every label, tooltip, status string, hint, and dialog. Missing keys fall back to English; missing English falls back to the raw key (visible bug, no crash).
- **RTL support is end-to-end**: `SetLayoutDirection` is applied to the frame, every column panel, the title and status bars, the handset `wx.ListCtrl` (with column-header alignment re-applied through a dedicated helper), and the log `wx.TextCtrl`. A `Refresh()`/`Update()` follows the direction change so Windows doesn't keep stale paint state. Forward-compatible for Hebrew/Persian/Urdu via `i18n.RTL_LANGUAGES`.

### Fixed

- **"Update Available" status-bar link no longer truncates.** The `wx.adv.HyperlinkCtrl` was added to its sizer while hidden, which cached a 0-width slot and clipped the label on `Show()`. The link now re-pins its min size to its best size on show and on every language change so longer translations (e.g. "Mise à jour disponible") render in full.
- **Auto-update branch detection.** `updater.apply_update()` no longer hardcodes `master` — it asks `git symbolic-ref refs/remotes/origin/HEAD` for the upstream branch and falls back to the current branch. Forks and renamed default branches now work. Also fixes `updater.get_local_version()` to look for `VERSION =` in `gui_main.py` first (the post-restructure canonical location) before falling back to `flash_firmware_gui.py`.

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
