# GUI Architecture Specification

Delta for the ongoing decomposition of `gui_main.py` (`FlasherFrame`) into
collaborating component modules. These requirements codify the invariants the
existing extractions (gui_titlebar, gui_statusbar, gui_columns, gui_handset,
gui_workflow) already follow, and that this change's three new slices
(`gui_hints`, `gui_download`, `gui_flash`) MUST also follow.

## ADDED Requirements

### Requirement: Component extraction preserves call-site names

When behavior is moved from `FlasherFrame` into a component module, the frame
SHALL retain a thin delegator (or property shim) with the **same name** as the
moved method or attribute, so that existing cross-module call sites — gui_columns
event bindings, HandsetController callbacks, `retranslate_ui`, workflow gating,
and worker `wx.CallAfter` chains — continue to work without modification. An
extraction SHALL NOT rename or remove a symbol that a call site outside the new
component still references.

#### Scenario: Worker calls a delegator that was extracted

- **GIVEN** `_set_hint` and `_compute_hint_state` have been moved into a
  `HintPresenter` component
- **WHEN** a flash worker thread runs
  `wx.CallAfter(lambda: self._set_hint(self._compute_hint_state()))`
- **THEN** the frame SHALL still expose `_set_hint` and `_compute_hint_state` as
  delegators forwarding to the presenter
- **AND** the worker source SHALL be unchanged by the extraction

#### Scenario: gui_columns button binding survives extraction

- **GIVEN** `on_download` has been moved into a `DownloadController`
- **WHEN** `FirmwareColumn` binds its button with
  `self.download_btn.Bind(wx.EVT_BUTTON, frame.on_download)`
- **THEN** `frame.on_download` SHALL still resolve to a delegator that invokes the
  controller
- **AND** the gui_columns binding SHALL be unchanged

#### Scenario: Attribute read as state survives extraction

- **GIVEN** the manifest is now owned by a `DownloadController`
- **WHEN** the hint presenter or a flash worker reads `frame.manifest`
- **THEN** the frame SHALL expose `manifest` as a property shim over the
  controller, exactly as `_handset_ports` shims `handset.ports`

### Requirement: Worker threads never touch widgets except via wx.CallAfter

Background worker threads (flash, batch-flash, BTF-flash, download, dry-run,
diagnostics, port-poll, probe, update-check, manifest-fetch) SHALL NOT call any
wx widget method directly. All widget mutation originating on a worker thread
SHALL be marshalled onto the GUI thread via `wx.CallAfter` (or an equivalent
frame helper such as `log_msg` / `set_progress` / `set_buttons` that itself uses
`wx.CallAfter`). A worker SHALL also honor the `_closing` guard so a `wx.CallAfter`
that lands after the frame begins tearing down does not touch destroyed widgets.

#### Scenario: Per-row status update from a flash worker

- **GIVEN** a batch flash worker thread flashing multiple ports
- **WHEN** it updates a handset row's Status or Progress cell
- **THEN** it SHALL do so through `wx.CallAfter(self._set_handset_status, …)` /
  `wx.CallAfter(self._set_handset_progress, …)`
- **AND** it SHALL NOT call `handset_list.SetItem` directly from the worker thread

#### Scenario: Background task posts after the window is closing

- **GIVEN** the update-check or manifest-fetch thread completes after the user
  closed the window
- **WHEN** its completion callback runs on the GUI thread
- **THEN** the callback SHALL check `_closing` / frame validity before touching
  the status bar or download button, as `_notify_update` and `_update_radio_info`
  already do

#### Scenario: Log and progress marshalling helpers

- **GIVEN** the `FlashController` owns `log_msg` and `set_progress`
- **WHEN** any worker appends a log line or updates the progress gauge
- **THEN** those helpers SHALL wrap the widget call in `wx.CallAfter`
- **AND** workers SHALL route all log/progress output through them rather than
  touching `self.log` / `self.progress` directly

### Requirement: Protocol-adjacent logic is headlessly testable

Logic that is adjacent to the serial protocol or workflow decisions SHALL be
structured so it can be exercised without a display and without live hardware:
pure decision/formatting helpers SHALL be module-level functions (or take
injectable dependencies), and GUI worker threads that drive the flash/dry-run/
diagnostics protocol SHALL delegate the on-the-wire work to the driver functions
(`flash_to_port` / `dry_run` / `run_diagnostics` / `probe_port`) that are testable
against `mock_bootloader`, rather than hand-rolling `serial.Serial` inline. A
change that touches worker threads together with serial I/O and live widgets SHALL
pass the manual hardware checklist before merge.

#### Scenario: Pure helper is unit-tested without wx or pyserial

- **GIVEN** per-radio info formatting is extracted as
  `format_radio_info(radio, firmware_version)`
- **WHEN** the test suite runs in a headless / wx-less environment
- **THEN** the helper SHALL be importable and unit-testable without constructing a
  frame or opening a serial port, following the `enumerate_serial_ports` /
  `compute_hint_state` precedent

#### Scenario: Single-flash worker is coverable end-to-end via the mock bootloader

- **GIVEN** the single-flash KDH, dry-run, and diagnostics paths currently
  hand-roll `serial.Serial` + `send_command` inline and have no headless coverage
- **WHEN** they are converged onto `fw.flash_to_port` / `fw.dry_run` /
  `fw.run_diagnostics` (as the batch path already uses `driver.flash_to_port`)
- **THEN** the GUI worker wrapper SHALL be exercisable end-to-end with
  `mock_bootloader.patch_serial`, matching the existing `TestEndToEndFlashKDH`
  driver-level tests

#### Scenario: Serial-touching slice is gated by the hardware checklist

- **GIVEN** the `gui_flash` slice moves worker threads that open serial ports and
  update live widgets
- **WHEN** the slice is prepared for merge
- **THEN** the PR SHALL include the manual hardware-checklist results (single,
  batch, BTF, dry-run, diagnostics on a real radio), per the PR #19 pattern
- **AND** CI green alone SHALL NOT be sufficient to merge that slice
