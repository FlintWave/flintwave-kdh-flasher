# Decompose gui_main Workers — Proposal

## Why

`gui_main.py` (`FlasherFrame`) is still ~1,828 lines after four extraction PRs
(gui_titlebar #16, gui_statusbar #17, gui_columns #18, gui_handset #19). The
established pattern works: construction and behavior move into a component
module, the frame keeps thin same-named delegators so worker-thread
`wx.CallAfter` chains and cross-module call sites (gui_columns bindings,
HandsetController) don't churn, and protocol-adjacent logic gets pure/injectable
helpers that are unit-tested headlessly.

What remains on the frame is dominated by a single unextracted responsibility:
the **operation worker threads**. Roughly 750 lines (lines ~1045–1796) are the
flash / batch-flash / BTF-flash / download / dry-run / diagnostics handlers and
their background threads, plus their shared plumbing (`log_msg`, `set_progress`,
`set_buttons`, permission-error hints, test-report offer). Two supporting
concerns are tangled in with them — the hint/radio-info presentation the workers
drive, and the firmware-discovery + updater plumbing they depend on.

This coupling has a concrete cost: the batch-flash path already delegates to the
mock-testable driver function `driver.flash_to_port` and is covered end-to-end by
`mock_bootloader`, but the **single-flash KDH path (`_flash_thread`), the KDH
`_dryrun_thread`, and `_diag_thread` hand-roll the serial protocol inline** and
so have zero headless coverage — they can only be verified against real hardware.
Extracting them behind a controller boundary is the precondition for closing that
gap.

## What Changes

Three independently-mergeable extraction slices, each keeping the suite green and
shipping as its own PR, ordered lowest-risk-first:

1. **Slice 1 — `gui_hints` (HintPresenter):** move the hint state machine and
   per-radio info rendering (`_get_hint_copy`, `_format_radio_info`, `_set_hint`,
   `_compute_hint_state`, `_on_state_change`, `HINT_STATES`/`_RADIO_INFO_STATES`)
   into a presenter. No threads, no serial. Frame keeps same-named delegators;
   `_format_radio_info` becomes a pure `format_radio_info(radio, …)` helper unit-
   tested without a display. **Risk: low.**

2. **Slice 2 — `gui_download` (DownloadController):** move firmware acquisition
   and update notification — the download worker (`on_download`, `_download_thread`,
   `on_browse`), firmware discovery (`_get_firmware_url_and_version`,
   `_update_radio_info`, `on_radio_changed`), and the updater/manifest background
   tasks (`_fetch_manifest`, `_check_update`, `_notify_update`, `_show_update_link`).
   Network + file + status-bar widgets only — **no serial, no hardware gate.**
   Testable with the existing mocked-`dl`/`updater` suites. **Risk: medium.**

3. **Slice 3 — `gui_flash` (FlashController):** move the serial operation workers —
   `on_flash`/`_flash_thread`/`_flash_thread_btf`/`_batch_flash_thread`,
   `on_dry_run`/`_dryrun_thread`, `on_diag`/`_diag_thread`, plus
   `_prompt_continue_batch`, `_offer_test_report`, `_offer_firmware_cleanup`, and
   the worker plumbing (`log_msg`, `set_progress`, `set_buttons`). This is the
   change's namesake and highest-value slice. It touches threads + serial + live
   widgets, so it **requires the manual hardware checklist before merge**. A
   follow-on commit converges the hand-rolled single-flash / dry-run / diagnostics
   paths onto the already-tested driver functions so the GUI worker becomes a thin,
   `mock_bootloader`-testable wrapper. **Risk: high.**

## Impact

- **Affected code:** `gui_main.py` shrinks from ~1,828 to roughly ~950 lines,
  becoming a coordinator (construction, i18n/retranslate, workflow gating, font/
  theme, menu handlers). New modules `gui_hints.py`, `gui_download.py`,
  `gui_flash.py`. Shared helpers `_get_selected_radio` and `_driver_for` stay on
  the frame (used by handset, hints, flash, and gating alike).
- **Behavior:** none intended per slice — extractions are move-plus-delegator, and
  every existing call site keeps its name. The Slice 3 driver-convergence commit is
  the one behavior-adjacent step and is gated by the hardware checklist.
- **Tests:** existing pure tests (`gui_workflow`, `gui_handset` enumeration,
  end-to-end `mock_bootloader` flash) stay green unchanged. New headless units for
  `format_radio_info` and, after Slice 3 convergence, `mock_bootloader` coverage of
  the single-flash / dry-run / diagnostics GUI wrappers.
- **Backwards compatible:** no user-facing change; `radios.json` /
  `firmware_manifest.json` / i18n catalogs untouched.
- **Deferred (out of scope):** i18n/retranslate plumbing and font/theme controls
  remain on the frame; they are noted in design.md as future slices once the
  worker decomposition lands.
