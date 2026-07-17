# Decompose gui_main Workers — Design

## What remains on the frame today

`gui_main.py` after PR #19 groups into these responsibilities (line ranges
approximate, current file):

| Block | Methods | Lines |
| --- | --- | --- |
| Frame construction | `__init__` | 54–282 |
| i18n / retranslate | `_tr_label`, `_tr_tooltip`, `_resolve_direction`, `_language_button_label`, `_open_language_dialog`, `retranslate_ui`, `_refresh_radio_dropdown` | 287–495 |
| Layout / theme | `_column_heading`, `_toggle_theme`, `_on_hints_size` | 501–536 |
| Handset delegators | `_handset_ports` … `_selected_handset_devices` | 547–576 |
| Workflow gating | `_firmware_ready`, `_handset_ready`, `_update_workflow_gating`, `_pulse_arrow` | 582–710 |
| Font controls | `_set_font_size`, `_cycle_font` | 716–780 |
| **Hint state machine** | `_get_hint_copy`, `_format_radio_info`, `_set_hint`, `_compute_hint_state`, `_on_state_change` | 786–894 |
| Menu handlers | `on_usage_guide`, `on_github`, `on_about`, `_on_close` | 900–921 |
| **Updater / manifest** | `_fetch_manifest`, `_check_update`, `_notify_update`, `_show_update_link` | 927–977 |
| **Firmware discovery** | `_get_selected_radio`, `_driver_for`, `_get_firmware_url_and_version`, `_update_radio_info`, `on_radio_changed` | 979–1043 |
| **Download worker** | `on_download`, `_download_thread`, `on_browse` | 1045–1121 |
| **Worker plumbing** | `log_msg`, `set_progress`, `set_buttons` | 1123–1151 |
| **Flash workers** | `on_flash`, `_batch_flash_thread`, `_prompt_continue_batch`, `_flash_thread`, `_flash_thread_btf`, `_is_permission_denied`, `_log_dialout_hint`, `_offer_test_report`, `_offer_firmware_cleanup` | 1153–1593 |
| **Dry-run worker** | `on_dry_run`, `_dryrun_thread` | 1595–1712 |
| **Diagnostics worker** | `on_diag`, `_diag_thread` | 1714–1796 |
| Module entry | `detect_os_theme`, `main` | 1799–1828 |

The bold blocks are what this change extracts. The non-bold blocks stay on the
frame as the coordinator core (a later change may extract i18n and font/theme).

## Collaboration idiom being reused

The gui_handset extraction sets the contract this change follows:

- The component takes `frame` in its constructor and reaches back through it for
  everything only the frame owns (widgets, `_busy`/`_closing` flags, protocol
  dispatch, cross-component feedback).
- The frame publishes the component (`self.handset = HandsetController(self)`)
  and exposes **thin same-named delegators** (`_set_handset_status` →
  `self.handset.set_status`) plus **property shims** for state the old code read
  as an attribute (`_handset_ports` → `self.handset.ports`).
- Every worker→widget update goes through `wx.CallAfter`; the component never
  touches a widget from a background thread directly.
- Pure logic (`enumerate_serial_ports`, `poll_signature`) is module-level and
  injectable so it unit-tests without pyserial or a display; the threaded/widget
  half is verified against hardware.

Each slice below is a straight application of that idiom.

---

## Slice 1 — `gui_hints` (HintPresenter)

**Moves:** `_get_hint_copy`, `_format_radio_info`, `_set_hint`,
`_compute_hint_state`, `_on_state_change`, and the `HINT_STATES` /
`_RADIO_INFO_STATES` bindings. `_format_radio_info` is refactored into a
module-level pure `format_radio_info(radio, url_version)` that takes the radio
dict and resolved firmware version and returns the string (the frame passes
`_get_firmware_url_and_version(radio)` in), so the string-building is testable
with no widgets.

**Stays on the frame:** the `hint_text` TextCtrl (built in `__init__`), and the
state the presenter reads — `_get_selected_radio`, `_get_firmware_url_and_version`,
`_firmware_ready`, `_selected_handset_indices`, and the `_busy` / `_terminal_state`
/ `_busy_state` / `font_size` fields. The presenter holds `frame` and reads these
through it, exactly as HandsetController does.

**Collaboration contract:** frame keeps delegators `_set_hint`,
`_compute_hint_state`, `_format_radio_info`, `_on_state_change`, and the class
attributes `HINT_STATES` / `_RADIO_INFO_STATES` (referenced by tests and by
`retranslate_ui`). Call sites that keep working unchanged: `retranslate_ui`
(`_set_hint(self._compute_hint_state())`), HandsetController
(`frame._set_hint`, `frame._compute_hint_state` in `on_check_changed` /
`_probe_thread`), `_update_workflow_gating`, `_update_radio_info`, and every
worker's `wx.CallAfter(lambda: self._set_hint(self._compute_hint_state()))`.

**Risk: low.** No threads, no serial. `_set_hint` renders into a TextCtrl on the
GUI thread (workers already marshal to it via `wx.CallAfter`), so the widget
contact is unchanged.

**Test strategy:** the decision half is already pure and covered
(`gui_workflow.compute_hint_state`, `TestWorkflowStateMachine`,
`TestHintCopy`) — those stay green untouched. Add a headless unit for the new
`format_radio_info` pure helper (bootloader-keys / connector / tested / notes /
latest-version formatting) following the `TestHandsetPortEnumeration` skip-if-
unimportable pattern. No `mock_bootloader`, no hardware.

---

## Slice 2 — `gui_download` (DownloadController)

**Moves:** the download worker (`on_download`, `_download_thread`, `on_browse`),
firmware discovery (`_get_firmware_url_and_version`, `_update_radio_info`,
`on_radio_changed`), and the updater/manifest background tasks (`_fetch_manifest`,
`_check_update`, `_notify_update`, `_show_update_link`). `self.manifest` is set by
the controller and exposed on the frame as a property shim (like
`_handset_ports`) so hints/flash keep reading `frame.manifest`.

**Stays on the frame:** `_get_selected_radio` and `_driver_for` — both are shared
by HandsetController (probe), the hint presenter, and the flash workers, so they
remain frame-level shared helpers, not owned by this controller. Widgets
`download_btn`, `file_path`, `radio_combo`, `progress`, `update_link`,
`status_bar_panel` stay frame-owned (built by gui_columns / gui_statusbar); the
controller drives them through the frame via `wx.CallAfter`.

**Collaboration contract:** delegators kept on the frame — `on_download`,
`on_browse`, `on_radio_changed` (bound by gui_columns to `frame.on_*`),
`_update_radio_info` (called by `set_buttons`, `retranslate_ui`, `__init__`, and
the manifest-fetch `wx.CallAfter`), and `_get_firmware_url_and_version` (read by
the hint presenter and by `_flash_thread`). Cross-controller reads go through the
frame delegator surface, matching how HandsetController calls `frame._driver_for`.

**Risk: medium.** Two background threads (`_check_update`, `_fetch_manifest`) and
the download worker touch the network, the filesystem, and status-bar / download-
button widgets — but **no serial and no live-radio state**, so **no hardware
checklist is required**. The main hazards are `wx.CallAfter` ordering against
`_closing` (already guarded in `_notify_update` / `_update_radio_info`) and the
`_busy` interlock the download worker shares with flashing.

**Test strategy:** exercise the worker orchestration with the existing mocked
seams — `TestDownloader` (`dl.download_and_extract`) and `TestUpdater`
(`updater.check_for_update`). Add a headless unit asserting `_download_thread`
scales progress to 80 % for the download phase and sets `_terminal_state`
correctly on success/failure with a fake `dl`. No `mock_bootloader`, no hardware.

---

## Slice 3 — `gui_flash` (FlashController)

**Moves:** the serial operation workers and their plumbing — `on_flash`,
`_flash_thread`, `_flash_thread_btf`, `_batch_flash_thread`,
`_prompt_continue_batch`, `on_dry_run`, `_dryrun_thread`, `on_diag`,
`_diag_thread`, `_offer_test_report`, `_offer_firmware_cleanup`, and the worker
plumbing `log_msg`, `set_progress`, `set_buttons`.

**Stays on the frame:** `_is_permission_denied` and `_log_dialout_hint` remain
frame-level shared serial-error helpers — HandsetController's `_probe_thread`
already calls `frame._log_dialout_hint`, so moving them would only add a reverse
dependency for no benefit. Shared helpers `_get_selected_radio`,
`_driver_for`, `_get_firmware_url_and_version` stay on the frame; the controller
reads them through `frame`. Widgets (`flash_btn`, `progress`, `log`, `file_path`,
handset list via the existing `_set_handset_status`/`_set_handset_progress`
delegators) stay frame/handset-owned.

**Collaboration contract:** delegators kept on the frame — `on_flash`,
`on_dry_run`, `on_diag` (bound by gui_columns), and `log_msg`, `set_progress`,
`set_buttons` (called from the workers and reused wherever progress/log is
posted). The batch path keeps calling `frame._set_handset_status` /
`_set_handset_progress` / `_selected_handset_indices` (HandsetController
delegators) and the `STATUS_*` constants imported from gui_handset — unchanged.

**Risk: high.** Threads + serial + live widgets. Per project convention this
slice **requires the manual hardware checklist (PR #19 pattern) before merge.**

**Test strategy — two commits:**
1. *Move-only* (preserve behavior): relocate the workers verbatim behind the
   controller. Verified by the hardware checklist (single-flash, batch, BTF,
   dry-run, diagnostics on a real radio) plus the unchanged end-to-end
   `mock_bootloader` driver tests (`TestEndToEndFlashKDH`, `TestEndToEndFlashBTF`,
   `TestBatchFlashRouting`), which cover the driver level the batch path already
   uses.
2. *Driver convergence* (closes the headless-coverage gap): the single-flash KDH
   `_flash_thread`, the KDH branch of `_dryrun_thread`, and `_diag_thread`
   currently hand-roll `serial.Serial` + `send_command` inline, so they have **no
   headless coverage**. Converge them onto the already-tested driver functions
   `fw.flash_to_port` / `fw.dry_run` / `fw.run_diagnostics` (the batch path
   already uses `driver.flash_to_port`). The GUI worker then becomes a thin
   wrapper testable with `mock_bootloader.patch_serial`, matching `_flash`
   in `TestEndToEndFlashKDH`. This is behavior-adjacent, so it stays a separate
   commit and re-runs the hardware checklist.

---

## Why this order

The slices have no hard dependency on each other (each is a self-contained
move-plus-delegator), so ordering is chosen for **safest incremental merges,
lowest-risk-first**, with a secondary rationale that earlier slices stabilize the
surfaces the later ones lean on:

1. **Hints first** — zero threads/serial, smallest blast radius, and it hardens
   the presentation surface (`_set_hint` / `_compute_hint_state`) that all three
   workers call. Extracting it before the workers means the Slice 3 move lands
   against a delegator that is already stable rather than a method that is itself
   about to move.
2. **Download second** — network-only, no hardware gate, so it can merge on CI
   evidence alone. It isolates firmware discovery (`_get_firmware_url_and_version`,
   `manifest`) that both the hint presenter and the flash workers read, giving
   Slice 3 a settled dependency.
3. **Flash last** — the highest-value slice (the change's namesake) but the only
   one that needs the hardware checklist. Sequencing it last lets it lean on the
   now-stable hint and firmware-info delegators and keeps the risky, slow-to-
   verify change off the critical path of the two cheap wins.

An alternative "unblocking-value-first" order would put Flash first because it is
the point of the change; it is rejected because the flash slice is exactly the one
whose verification is slowest (hardware) and whose correctness most benefits from
the other two surfaces already being stable.

## Residual frame after this change (future slices, out of scope)

After the three slices, `gui_main.py` retains construction, i18n/retranslate
plumbing, workflow gating, font/theme controls, menu handlers, and the shared
`_get_selected_radio` / `_driver_for` helpers. Two further slices are noted for a
later change: **`gui_i18n`** (retranslate registry + language dialog +
`_refresh_radio_dropdown`) and **`gui_appearance`** (`_set_font_size` /
`_cycle_font` / `_toggle_theme` / `_pulse_arrow`). They are deliberately excluded
here to keep this change scoped to the worker decomposition its id names.
