# Changelog

## Unreleased

### UX / UI

- **The window now reflows via draggable splitters.** The fixed 2:1 layout is replaced by a main sash between the three workflow columns and the instructions/log row, plus a second sash between Instructions and Log — drag space to whatever you're reading (the Instructions box was chronically crushed). Ratios persist across runs; asymmetric minimums keep the columns above the height where their controls would overlap while letting the bottom row compress gracefully; window resizes keep the ~60/40 feel and re-clamp.
- **The hardware-variant walkthrough moved into the Firmware column**, directly under the radio picker, where it is fully visible at every window size. It previously rendered below the Instructions text, where the directions were scrolled out of view and the answer options were squeezed to zero height at the default window size (found in hardware + screenshot testing). The Instructions panel now shows a translated one-line pointer instead, no answer appears pre-selected before the user actually picks one (hidden GTK group anchor), and the question/steps text compresses before the answer buttons ever would.
- **Stale translation caches can no longer hide new strings.** Downloaded catalogs cached by an older app version predate newly shipped keys; the loader now overlays the cache on the bundled catalog so every key this build knows about resolves to at least its bundled translation. Previously a stale cache left just the new strings (e.g. the variant walkthrough) in English while the rest of the UI was translated.

### Architecture

- **Flash/dry-run/diagnostics workers extracted from the main frame** (`gui_flash.py`, decomposition slice 3 of 3) and converged onto the driver functions (`flash_to_port` / `dry_run` / new `diagnostic_probe`), giving the single-flash, dry-run, and diagnostics paths their first headless mock-bootloader coverage. That new coverage immediately caught an `UnboundLocalError` introduced on master by the test-report wiring (`os.path.basename` before a function-local `import os`) that broke single-handset flashing — fixed here before any release shipped it. `gui_main.py` drops to ~1,188 lines (from ~2,305 at the start of the decomposition).
- **Firmware download and update-check extracted from the main frame** (`gui_download.py`, decomposition slice 2 of 3). The download worker, firmware discovery (manifest + variant gating), and the updater/manifest background tasks now live in a `DownloadController` with headless tests over a stub frame and fake downloader; the frame keeps same-named delegators and a read-only `manifest` property shim. `gui_main.py` drops to ~1,826 lines. Behavior unchanged.
- **Hint/info rendering extracted from the main frame** (`gui_hints.py`, decomposition slice 1 of 3). The hint state machine and per-radio info panel — including the hardware-variant prompt text — now live in a `HintPresenter` with pure, headlessly tested formatting functions; the frame keeps thin same-named delegators so worker threads and sibling components are untouched. `gui_main.py` drops to ~1,978 lines. Behavior unchanged.

### Test reports

- **The post-flash test-report offer no longer nags.** A new "don't ask again for this radio + firmware version" checkbox suppresses the prompt per (radio, version) — recorded in the state file — while a plain Skip keeps asking next time, and a new firmware version re-opens the offer. The report URL/body builders were extracted from the dialog into pure functions with real tests (URL-length budget included), replacing tests that re-derived the logic by hand. Reports keep the `test-report` label + `Radio:` body-line convention maintainers use to flip `tested` flags.

### Hardware variants

- **Guided hardware-variant identification.** Radios that ship as non-interchangeable hardware versions behind one vendor bundle (BTECH BF-F8HP Pro NRF/NRFB, Radtel RT-490 old/new PCB) now appear as a single family row in the radio picker. Selecting the family walks the user through identifying their version (e.g. "radio off, hold 8 while powering on") with one option per variant and an explicit "I'm not sure" that fails safe — Download stays disabled with a link to the vendor page instead of risking a wrong-variant flash. Fully translated in all 7 catalogs; radio ids, the remote manifest shape, and the post-download multi-match guard are unchanged, so released clients are unaffected.
- The RT-490 old-PCB bundle's own 4-file split (GPS/NoGPS × HW V1.0/V2.0) still stops safely at the multiple-files guard — there is no known reliable identification procedure for those sub-variants; community info is requested in the radio's notes.

### Translations

- **Community translation review process.** All 7 non-English catalogs are machine-translated; a new CONTRIBUTING.md documents how native speakers review them — safety-critical strings first (bootloader key sequences, hardware-variant warnings, confirm/untested dialogs) — with per-language tracking issues labeled `translation-review`. The language picker now marks unreviewed languages ("machine translated, help review", localized) via a new `i18n.is_reviewed()` helper reading the catalog's `_meta.reviewed` flag, and tests enforce the `_meta.reviewed` convention plus the localized picker hint.

### Build / CI

- **Tests now run on Linux, Windows, and macOS.** The test workflow gained an OS matrix (`fail-fast: false`); Windows/macOS legs reuse the release build's setup-python + pip-wheel install path, so the wx GUI tests execute on the platforms most users run instead of Linux-only.
- **Workflow linting.** An `actionlint` job now checks `.github/workflows/**` on every change — intentionally complementary to CodeQL's security-focused actions analysis. Its first run caught two unquoted-expansion bugs in the release script's asset handling (now fixed with a bash array).
- **Weekly firmware-drift check.** A scheduled workflow re-downloads every pinned bundle and compares hashes, filing/updating a `firmware-drift` issue on verified drift and auto-closing it on recovery. Download failures are reported in the job summary but never as drift. This would have caught the 2026-07-10 silent repack within the week.

### Firmware integrity

- **All firmware downloads are now SHA-256 pinned.** `uv-25-pro`, `rt-470`, and `rt-490` had null hashes in the manifest — the blind spot that let the 2026-07-10 silent bundle repack go undetected. All three bundles were downloaded, their contents verified, and their hashes pinned; a new manifest-schema test blocks any future entry from shipping with a `firmware_url` but no hash.
- **UV-25 Plus/Pro firmware version corrected to V0.23.** Verification caught another silent vendor swap: the bundle at the (unchanged) manifest URL actually contains `UV25Pro_NRF_401+_V0.23`, not the V0.20 the manifest claimed. Released clients will now correctly see V0.23 as the latest.
- Known issue surfaced during verification: the RT-490 bundle ships **4** hardware-variant firmware files (GPS/NoGPS × HW V1.0/V2.0). Since the multi-match guard shipped in v26.07.0, that download fails safely instead of silently flashing the first file; proper variant selection lands with the `generalize-hardware-variants` change (see `openspec/changes/`).

## v26.07.0 — 2026-07-17

### Firmware download

- **BF-F8HP Pro NRF/NRFB hardware split.** BaofengTech silently repacked the V0.53 bundle (2026-07-10, same URL) into two hardware-specific firmware files, which broke the `BTECH_V*.kdhx` pattern and with it the download. The radio list now has separate "BTECH BF-F8HP Pro (NRF)" and "(NRFB)" entries — mirroring the RT-490 old/new-PCB precedent — whose notes walk through identifying the hardware version (radio off, hold 8 while powering on) and warn that the files are not interchangeable. Translated in all seven catalogs. NRF confirmed working on real hardware (#20).
- **The downloader no longer guesses between multiple matching firmware files.** It used to silently take the first match — with variant bundles that could flash the wrong hardware's firmware. Multiple matches now raise an error naming the files and directing the user to the hardware-specific radio entry; zero matches now list what the bundle actually contains, so a vendor repack is diagnosable instead of a dead end.
- **Bundle SHA-256 pinned for the BF-F8HP Pro** in the remote manifest, so the next silent repack fails loudly as a hash mismatch instead of a confusing pattern miss.

### Bug fixes

- **Diagnostics now honours the selected radio's protocol.** `_diag_thread` previously always sent the KDH `CMD_HANDSHAKE` probe, so running Diagnostics against a BTF radio (Radtel RT-950 Pro) reported "no response" even for a healthy radio in bootloader mode. It now sends the BTF `CMD_PROBE` when the selected radio uses `protocol: "btf"`, matching the flash and port-probe paths.
- **Re-entrancy: a second operation could start mid-flash.** The radio dropdown, firmware-path field, and Browse button stayed live during a flash/download, and `_update_radio_info` re-enabled the Download button while busy — so changing the radio mid-flash could re-arm Download and kick off a second worker thread on the same serial port. Those inputs are now locked while an operation is in progress, and the action handlers (`on_flash`/`on_download`/`on_dry_run`/`on_diag`) early-return if already busy.
- **Local usage guide now opens on Windows.** The "Usage Guide" link built a `file://` URI by string concatenation, producing an invalid `file://C:\…` on Windows. It now uses `pathlib.Path.as_uri()`, which emits a valid `file:///C:/…` on every platform.
- **Handset rows no longer get stuck showing "Probing…".** A non-permission error during a port probe (port vanished, busy, I/O error) left the row's status unchanged. It now falls back to "No response".
- **Probe/poll race.** The 2-second port-poll loop could rebuild the handset list mid-probe, invalidating the row indices the probe thread was writing to. Refreshes are now suppressed while a probe is in flight.

### UX / UI polish

- **Keyboard close for the borderless window.** With the OS title bar hidden, keyboard-only users had no way to quit. Ctrl/Cmd+W and Ctrl/Cmd+Q now close the app.
- **Stale "ready to flash" hint fixed.** The instructions panel now uses the same file-exists check as the button gating, so it can't advertise "ready to flash" while the Flash button is disabled because the firmware file is missing.
- **Sticky completion hint cleared on radio change.** Selecting a different radio after a flash no longer leaves the previous flash's "Flash complete!" copy showing.
- **Firmware cleanup clears the stale path.** After deleting downloaded firmware via the post-flash cleanup prompt, the firmware-path field is cleared and the workflow re-gates, instead of leaving a dead path that fails on the next flash attempt.

### Robustness

- `on_flash` reads `bootloader_keys`/`name` from the radio via `.get()` with fallbacks, so a `radios.json` entry missing a field can't raise instead of flashing.
- Background daemon loops (port poll, update check) stop touching the frame once it starts closing, avoiding "wrapped C/C++ object deleted" noise on exit.

### Translations

- Added the missing `radio.rt-490-new.*` strings (bootloader keys, connector, notes) to all seven non-English catalogs. The RT-490 (New PCB) entry shipped in v26.05.6 without them, which failed the per-radio translation-completeness test.

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
