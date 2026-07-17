# Decompose gui_main Workers — Implementation Tasks

Each slice is a self-contained PR. A slice MUST leave `python3 tests.py` green on
its own and be mergeable independently. Slices land in order (1 → 2 → 3); the
hardware-checklist gate applies only to the slice that touches serial (Slice 3).

## Pre-flight (once, before Slice 1)

- [ ] Confirm baseline green: `python3 tests.py`
- [ ] Note the delegator idiom in `gui_handset.py` / `gui_columns.py` (frame
      publishes the component, keeps same-named thin delegators + property shims)
- [ ] Confirm no source behavior change is intended in any move-only commit

---

## Slice 1 — `gui_hints` (HintPresenter) — risk: low

1. [ ] Create `gui_hints.py` with a guarded `import wx` (headless-importable),
       mirroring the header/docstring style of `gui_handset.py`
2. [ ] Add pure `format_radio_info(radio, firmware_version)` — move the string-
       building out of `_format_radio_info`; no widget access
3. [ ] Add `HintPresenter(frame)` owning `set_hint`, `compute_hint_state`,
       `get_hint_copy`, and `on_state_change`; reach back through `frame` for
       `hint_text`, `_get_selected_radio`, `_get_firmware_url_and_version`,
       `_firmware_ready`, `_selected_handset_indices`, `_busy` / `_terminal_state`
       / `_busy_state`, `font_size`
4. [ ] In `gui_main.py`, construct `self.hints = HintPresenter(self)` in
       `__init__` and replace the moved bodies with thin delegators keeping the
       exact names: `_set_hint`, `_compute_hint_state`, `_get_hint_copy`,
       `_format_radio_info`, `_on_state_change`
5. [ ] Keep the `HINT_STATES` / `_RADIO_INFO_STATES` class attributes resolving to
       the gui_workflow source of truth (referenced by tests + `retranslate_ui`)
6. [ ] Verify unchanged call sites still resolve: `retranslate_ui`,
       `HandsetController.on_check_changed` / `_probe_thread`,
       `_update_workflow_gating`, `_update_radio_info`, every worker's
       `wx.CallAfter` hint refresh
7. [ ] Add headless test `TestRadioInfoFormatting` for `format_radio_info`
       (skip-if-unimportable, per `TestHandsetPortEnumeration`)
8. [ ] Run `python3 tests.py` — all green (existing `TestHintCopy`,
       `TestWorkflowStateMachine` unchanged)
9. [ ] Manual smoke (headed): launch, change radio / firmware / handset selection,
       switch language — hint panel and per-radio info render correctly
10. [ ] `CHANGELOG.md` entry; open PR "Extract hint presenter from gui_main (#NN)"

---

## Slice 2 — `gui_download` (DownloadController) — risk: medium (no hardware gate)

1. [ ] Create `gui_download.py` with guarded `import wx`
2. [ ] Add `DownloadController(frame)` owning the download worker
       (`on_download`, `_download_thread`, `on_browse`), firmware discovery
       (`_get_firmware_url_and_version`, `_update_radio_info`, `on_radio_changed`),
       and the background tasks (`_fetch_manifest`, `_check_update`,
       `_notify_update`, `_show_update_link`)
3. [ ] Expose `frame.manifest` as a property shim over the controller (pattern:
       `_handset_ports` → `handset.ports`) so hints/flash keep reading it
4. [ ] Leave `_get_selected_radio` and `_driver_for` on the frame (shared with
       handset/hints/flash) — do NOT move them into this controller
5. [ ] Replace moved bodies with thin frame delegators keeping names: `on_download`,
       `on_browse`, `on_radio_changed`, `_update_radio_info`,
       `_get_firmware_url_and_version` (bound by gui_columns / read by hints+flash)
6. [ ] Confirm all worker→widget updates stay on `wx.CallAfter` and the `_closing`
       guards in `_notify_update` / `_update_radio_info` are preserved
7. [ ] Keep the `_busy` interlock: download sets `_busy` / `_busy_state`
       ("downloading") exactly as before so flashing can't start concurrently
8. [ ] Add headless test: `_download_thread` scales progress to 80 % and sets
       `_terminal_state` on success/failure using a fake `dl` (reuse
       `TestDownloader` / `TestUpdater` seams; no `mock_bootloader`)
9. [ ] Run `python3 tests.py` — all green
10. [ ] Manual smoke (headed, network): pick a radio → Download → firmware lands
       and path populates; simulate/observe update-available link
11. [ ] `CHANGELOG.md` entry; open PR "Extract download + updater controller from
       gui_main (#NN)"

---

## Slice 3 — `gui_flash` (FlashController) — risk: high (hardware-checklist gate)

### Commit 3a — move-only (behavior preserved)

1. [ ] Create `gui_flash.py` with guarded `import wx`
2. [ ] Add `FlashController(frame)` owning `on_flash`, `_flash_thread`,
       `_flash_thread_btf`, `_batch_flash_thread`, `_prompt_continue_batch`,
       `on_dry_run`, `_dryrun_thread`, `on_diag`, `_diag_thread`,
       `_offer_test_report`, `_offer_firmware_cleanup`, `log_msg`,
       `set_progress`, `set_buttons`
3. [ ] Leave `_is_permission_denied` and `_log_dialout_hint` on the frame (shared
       with HandsetController's probe); leave `_get_selected_radio` /
       `_driver_for` / `_get_firmware_url_and_version` on the frame
4. [ ] Replace moved bodies with thin frame delegators keeping names: `on_flash`,
       `on_dry_run`, `on_diag`, `log_msg`, `set_progress`, `set_buttons`
5. [ ] Confirm the batch path still calls `frame._set_handset_status` /
       `_set_handset_progress` / `_selected_handset_indices` and the gui_handset
       `STATUS_*` constants — unchanged
6. [ ] Verify no worker touches a widget except via `wx.CallAfter`; `_busy` /
       `_terminal_state` transitions and the `set_buttons(True)` re-gating in
       `finally` blocks are byte-for-byte preserved
7. [ ] Run `python3 tests.py` — all green (`TestEndToEndFlashKDH`,
       `TestEndToEndFlashBTF`, `TestBatchFlashRouting` unchanged at driver level)
8. [ ] **HARDWARE CHECKLIST (gate — must pass before merge):** on a real radio,
       verify single KDH flash, batch KDH flash across 2 ports (incl. one induced
       failure → Continue/Stop prompt), BTF flash (RT-950 Pro), dry-run, and
       diagnostics; confirm per-row Status/Progress, log output, permission-denied
       (dialout) hint, and test-report + cleanup prompts all behave as before
9. [ ] `CHANGELOG.md` entry; open PR "Extract flash/dry-run/diagnostics workers
       from gui_main (#NN)" with the hardware-checklist results in the description

### Commit 3b — driver convergence (closes headless-coverage gap)

10. [ ] Replace the hand-rolled inline serial in `_flash_thread` (single-flash KDH)
        with a call to `fw.flash_to_port(port, firmware, log_cb=…, progress_cb=…)`,
        preserving log/progress/version-recording/test-report behavior
11. [ ] Route the KDH branch of `_dryrun_thread` through `fw.dry_run` and
        `_diag_thread` through `fw.run_diagnostics` (matching the batch path's use
        of `driver.flash_to_port`)
12. [ ] Add `mock_bootloader.patch_serial` tests for the GUI single-flash / dry-run
        / diagnostics wrappers (mirror `_flash` in `TestEndToEndFlashKDH`)
13. [ ] Run `python3 tests.py` — new wrapper tests green
14. [ ] **HARDWARE CHECKLIST (gate again — behavior-adjacent):** re-verify single
        flash, dry-run, diagnostics on a real radio
15. [ ] `CHANGELOG.md` entry; open PR with hardware-checklist results

---

## Definition of done (whole change)

- [ ] `gui_hints.py`, `gui_download.py`, `gui_flash.py` exist; `gui_main.py` is a
      coordinator (~950 lines) with same-named delegators for every moved method
- [ ] No user-facing behavior change; no changes to `radios.json` /
      `firmware_manifest.json` / i18n catalogs
- [ ] `python3 tests.py` green after each slice; new headless units added per slice
- [ ] Slice 3 merged only after its hardware checklist passed (both commits)
- [ ] Each slice shipped as its own squash-merged PR
