#!/usr/bin/env python3
"""
Serial operation workers: the flash / dry-run / diagnostics controller.

This is the *controller* half of the Flash column's runtime — the change's
namesake and highest-risk slice, because it is where background threads, live
serial ports, and live widgets all meet. It owns:

  * **The flash worker** — ``on_flash`` (validation / same-or-older-version
    guard / single-vs-batch confirmation / ``_busy`` interlock) and the three
    background workers behind it: ``flash_thread`` (single KDH),
    ``flash_thread_btf`` (single RT-950 Pro), and ``batch_flash_thread``
    (sequential multi-port with the Continue/Stop failure prompt in
    ``prompt_continue_batch``).
  * **Dry-run + diagnostics** — ``on_dry_run`` / ``dryrun_thread`` (packet-build
    validation, no serial) and ``on_diag`` / ``diag_thread`` (a single
    protocol-appropriate handshake probe over the wire).
  * **Post-flash prompts** — ``offer_test_report`` (with per radio+version nag
    suppression) and ``offer_firmware_cleanup``.
  * **Worker plumbing** — ``log_msg`` / ``set_progress`` / ``set_buttons``, the
    ``wx.CallAfter`` marshalling helpers every worker (here and in
    DownloadController) routes log / progress / button-state writes through.

``FlashController`` collaborates with the owning frame for everything only the
frame can provide: the ``flash_btn`` / ``dryrun_btn`` / ``diag_btn`` / ``log`` /
``progress`` / ``file_path`` widgets (built by gui_columns), the ``_busy`` /
``_busy_state`` / ``_terminal_state`` / ``_closing`` flags it shares with the
download worker, the selection model (``_get_selected_radio`` / ``_driver_for`` /
``_get_firmware_url_and_version`` / ``_selected_handset_indices`` /
``_handset_ports``), the handset-row updates (``_set_handset_status`` /
``_set_handset_progress`` — HandsetController delegators), the serial-error
helpers (``_is_permission_denied`` / ``_log_dialout_hint`` — shared with
HandsetController's probe, so they stay frame-level), and the hint / workflow
feedback (``_set_hint`` / ``_compute_hint_state`` / ``_update_radio_info`` /
``_update_workflow_gating``).

The frame keeps thin same-named delegators (``on_flash`` / ``on_dry_run`` /
``on_diag`` bound by gui_columns, and ``log_msg`` / ``set_progress`` /
``set_buttons`` called by DownloadController and the frame) so every existing
call site keeps calling the same names, exactly as HandsetController's and
DownloadController's delegators do. Every worker→widget update is marshalled onto
the GUI thread with ``wx.CallAfter``; no worker touches a widget directly.

``STATUS_*`` are the handset-list status i18n keys, imported from gui_handset so
the batch/single workers and the controller that renders the cells share one
definition.
"""

import os
import threading

try:
    import wx
except ImportError:
    wx = None

import flash_firmware as fw
import flash_btf as fw_btf
import firmware_manifest as fm
import firmware_version as fv
from i18n import t
from gui_dialogs import show_test_report_dialog
from gui_handset import (
    STATUS_FLASHING, STATUS_DONE, STATUS_FAILED, STATUS_SKIPPED,
)


class FlashController:
    """Owns the flash / dry-run / diagnostics workers and their plumbing."""

    def __init__(self, frame):
        self.frame = frame

    # ------------------------------------------------------------------
    # Worker plumbing — every worker→widget write goes through these so it
    # is marshalled onto the GUI thread via wx.CallAfter.
    # ------------------------------------------------------------------

    def log_msg(self, msg):
        frame = self.frame
        wx.CallAfter(frame.log.AppendText, msg + "\n")

    def set_progress(self, pct):
        frame = self.frame
        wx.CallAfter(frame.progress.SetValue, int(pct))

    def set_buttons(self, enabled):
        frame = self.frame
        wx.CallAfter(frame.flash_btn.Enable, enabled)
        wx.CallAfter(frame.dryrun_btn.Enable, enabled)
        wx.CallAfter(frame.diag_btn.Enable, enabled)
        wx.CallAfter(frame.download_btn.Enable, enabled)
        wx.CallAfter(frame.refresh_btn.Enable, enabled)
        wx.CallAfter(frame.select_all_btn.Enable, enabled)
        wx.CallAfter(frame.select_none_btn.Enable, enabled)
        # Also lock the firmware inputs so a radio switch, path edit, or
        # Browse can't fire mid-operation (which would let a second worker
        # thread start on the same serial port). Re-enabled by the gating
        # pass below on completion.
        for w in ("radio_combo", "file_path", "browse_btn"):
            widget = getattr(frame, w, None)
            if widget is not None:
                wx.CallAfter(widget.Enable, enabled)
        if enabled:
            wx.CallAfter(frame._update_radio_info)
            # Recompute hint AFTER the thread has set _terminal_state
            wx.CallAfter(lambda: frame._set_hint(frame._compute_hint_state()))
            # Re-apply workflow gating so we don't enable buttons that
            # should still be locked (e.g. Flash without a handset selected).
            wx.CallAfter(frame._update_workflow_gating)

    # ------------------------------------------------------------------
    # Flash worker
    # ------------------------------------------------------------------

    def on_flash(self, event):
        frame = self.frame
        if frame._busy:
            return
        firmware_path = frame.file_path.GetValue()
        if not firmware_path:
            wx.MessageBox(t("dialog.error_select_firmware"),
                          t("dialog.error_title"), wx.OK | wx.ICON_ERROR)
            return

        selected = frame._selected_handset_indices()
        if not selected:
            wx.MessageBox(t("dialog.error_no_handset_flash"),
                          t("dialog.error_no_handset_title"),
                          wx.OK | wx.ICON_ERROR)
            return

        radio = frame._get_selected_radio()
        if radio:
            keys = radio.get("bootloader_keys", t("fallback.bootloader_keys"))
            radio_name = radio.get("name", t("fallback.radio_name"))
            tested = radio.get("tested", False)
        else:
            keys = t("fallback.bootloader_keys")
            radio_name = t("fallback.radio_name")
            tested = False

        warning = ""
        if not tested:
            warning = t("dialog.untested_warning").format(radio=radio_name)

        # Same/older version checks (only meaningful for the single-handset path)
        file_version = fv.extract_version_from_filename(os.path.basename(firmware_path))
        if len(selected) == 1 and radio and file_version:
            last = fm.get_last_flashed(radio["id"])
            if last and last.get("version") == file_version:
                same_dlg = wx.MessageDialog(frame,
                    t("dialog.same_version_body").format(version=file_version),
                    t("dialog.same_version_title"),
                    wx.YES_NO | wx.ICON_QUESTION)
                if same_dlg.ShowModal() != wx.ID_YES:
                    same_dlg.Destroy()
                    return
                same_dlg.Destroy()
            elif last and last.get("version") and fv.compare_versions(file_version, last["version"]) < 0:
                older_dlg = wx.MessageDialog(frame,
                    t("dialog.older_version_body").format(
                        file_version=file_version, last_version=last['version']),
                    t("dialog.older_version_title"),
                    wx.YES_NO | wx.ICON_WARNING)
                if older_dlg.ShowModal() != wx.ID_YES:
                    older_dlg.Destroy()
                    return
                older_dlg.Destroy()

        # Confirmation: single vs batch
        if len(selected) == 1:
            port_label = frame._handset_ports[selected[0]]["device"]
            dlg = wx.MessageDialog(frame,
                t("dialog.confirm_single").format(
                    warning=warning, radio=radio_name,
                    port=port_label, keys=keys),
                t("dialog.confirm_title"), wx.YES_NO | wx.ICON_WARNING)
        else:
            ports_label = ", ".join(frame._handset_ports[i]["device"] for i in selected)
            dlg = wx.MessageDialog(frame,
                t("dialog.confirm_batch_body").format(
                    warning=warning, count=len(selected),
                    ports=ports_label, radio=radio_name, keys=keys),
                t("dialog.confirm_batch_title"),
                wx.YES_NO | wx.ICON_WARNING)

        if dlg.ShowModal() != wx.ID_YES:
            dlg.Destroy()
            return
        dlg.Destroy()

        frame.log.Clear()
        frame.progress.SetValue(0)
        frame._busy = True
        frame._busy_state = "flashing"
        frame._terminal_state = None
        self.set_buttons(False)
        frame._set_hint("flashing")

        if len(selected) == 1:
            port = frame._handset_ports[selected[0]]["device"]
            threading.Thread(target=self.flash_thread,
                             args=(port, firmware_path, selected[0]),
                             daemon=True).start()
        else:
            threading.Thread(target=self.batch_flash_thread,
                             args=(list(selected), firmware_path),
                             daemon=True).start()

    def batch_flash_thread(self, selected_idxs, firmware_path):
        """Sequentially flash the same firmware to every checked handset.

        On per-port failure: prompt user to skip + continue or stop. Marks
        each row's Status (Flashing… → Done/Failed/Skipped) and Progress.
        """
        frame = self.frame
        radio = frame._get_selected_radio()

        # Validate firmware once up front
        driver = frame._driver_for(radio)
        try:
            with open(firmware_path, "rb") as f:
                firmware_bytes = f.read()
            driver.validate_firmware(firmware_bytes, firmware_path)
        except Exception as e:
            self.log_msg(t("log.error_prefix").format(message=e))
            frame._terminal_state = "failed"
            frame._busy = False
            self.set_buttons(True)
            return

        total = len(selected_idxs)
        succeeded = failed = skipped = 0
        try:
            for n, idx in enumerate(selected_idxs):
                entry = frame._handset_ports[idx]
                port = entry["device"]
                wx.CallAfter(frame._set_handset_status, idx, STATUS_FLASHING)
                wx.CallAfter(frame._set_handset_progress, idx, "0%")
                self.log_msg(t("log.batch_start").format(
                    n=n + 1, total=total, port=port, cable=entry['cable']))

                def log_cb(msg, _idx=idx):
                    self.log_msg(t("log.batch_per_port").format(
                        port=frame._handset_ports[_idx]['device'], message=msg))

                def progress_cb(pct, _idx=idx):
                    pct_int = int(pct)
                    wx.CallAfter(frame._set_handset_progress, _idx, f"{pct_int}%")

                try:
                    driver.flash_to_port(port, firmware_bytes,
                                         log_cb=log_cb, progress_cb=progress_cb)
                except Exception as e:
                    failed += 1
                    wx.CallAfter(frame._set_handset_status, idx, STATUS_FAILED)
                    wx.CallAfter(frame._set_handset_progress, idx, "—")
                    self.log_msg(t("log.batch_error").format(message=e))
                    if frame._is_permission_denied(e):
                        frame._log_dialout_hint(port)
                        self.log_msg(t("log.batch_abort_permission"))
                        for skip_idx in selected_idxs[n + 1:]:
                            wx.CallAfter(frame._set_handset_status,
                                         skip_idx, STATUS_SKIPPED)
                            skipped += 1
                        break
                    if n < total - 1:
                        if not self.prompt_continue_batch(port, str(e)):
                            self.log_msg(t("log.batch_stopped"))
                            for skip_idx in selected_idxs[n + 1:]:
                                wx.CallAfter(frame._set_handset_status,
                                             skip_idx, STATUS_SKIPPED)
                                skipped += 1
                            break
                        self.log_msg(t("log.batch_continuing"))
                else:
                    succeeded += 1
                    wx.CallAfter(frame._set_handset_status, idx, STATUS_DONE)
                    wx.CallAfter(frame._set_handset_progress, idx, "100%")
                self.set_progress(int((n + 1) * 100 / total))
        finally:
            self.log_msg(t("log.batch_summary").format(
                ok=succeeded, failed=failed, skipped=skipped))
            frame._terminal_state = "complete" if failed == 0 and skipped == 0 else "failed"
            frame._busy = False
            self.set_buttons(True)

    def prompt_continue_batch(self, port, err):
        """Block worker thread until user picks Continue or Stop on batch failure."""
        frame = self.frame
        ev = threading.Event()
        choice = {"continue": False}

        def show():
            dlg = wx.MessageDialog(frame,
                t("dialog.batch_failure_body").format(port=port, error=err),
                t("dialog.batch_failure_title"),
                wx.YES_NO | wx.ICON_WARNING)
            choice["continue"] = (dlg.ShowModal() == wx.ID_YES)
            dlg.Destroy()
            ev.set()

        wx.CallAfter(show)
        ev.wait()
        return choice["continue"]

    def flash_thread(self, port, firmware_path, handset_idx=None):
        frame = self.frame
        radio = frame._get_selected_radio()
        radio_name = radio["name"] if radio else t("fallback.radio_unknown")

        # BTF (RT-950 Pro) uses a different on-the-wire protocol than KDH;
        # delegate to the BTF-specific path. The KDH inline flow below stays
        # unchanged so existing translations and behavior are preserved.
        if (radio or {}).get("protocol") == "btf":
            # Workers don't return values; call-then-return keeps the exits
            # uniform instead of propagating the BTF worker's None.
            self.flash_thread_btf(port, firmware_path, handset_idx, radio)
            return

        # Derive once, up front, so both the success and failure paths pass the
        # same identity to record_flash and to the test-report nag suppression.
        radio_id = radio["id"] if radio else None
        file_version = fv.extract_version_from_filename(
            os.path.basename(firmware_path))

        if handset_idx is not None:
            wx.CallAfter(frame._set_handset_status, handset_idx, STATUS_FLASHING)
            wx.CallAfter(frame._set_handset_progress, handset_idx, "0%")

        try:
            # `os` is the module-level import (line above uses os.path.basename
            # before this try); a function-local `import os` here would make os
            # a local for the whole function and raise UnboundLocalError on that
            # earlier reference — a latent crash in the pre-convergence inline
            # worker that had no headless coverage to catch it.
            import math
            import hashlib

            fw_size = os.path.getsize(firmware_path)
            if fw_size > fw.MAX_FIRMWARE_BYTES:
                raise ValueError(t("log.file_too_large").format(size=fw_size))
            with open(firmware_path, "rb") as f:
                firmware = f.read()

            fw.validate_firmware(firmware, firmware_path)
            total_chunks = math.ceil(len(firmware) / 1024)
            sha256 = hashlib.sha256(firmware).hexdigest()
            self.log_msg(t("log.firmware_path").format(path=firmware_path))
            self.log_msg(t("log.size_chunks").format(size=len(firmware), chunks=total_chunks))
            self.log_msg(t("log.sha256").format(hash=sha256))
            self.log_msg(t("log.port").format(port=port))
            self.log_msg("")

            def log_cb(msg):
                self.log_msg(msg)

            def progress_cb(pct):
                self.set_progress(pct)
                if handset_idx is not None:
                    wx.CallAfter(frame._set_handset_progress,
                                 handset_idx, f"{int(pct)}%")

            # Converged onto the mock_bootloader-testable driver function: the
            # handshake → announce → stream → finalize sequence was hand-rolled
            # as a serial.Serial + send_command loop inline here, so the single-
            # flash KDH path had no headless coverage. flash_to_port is the same
            # on-the-wire flow the batch path already drives; the GUI worker is
            # now a thin wrapper (validation + preamble/post logging + version
            # recording + test-report offer) around it.
            fw.flash_to_port(port, firmware, log_cb=log_cb, progress_cb=progress_cb)

            self.log_msg("")
            self.log_msg(t("log.flash_complete"))
            self.log_msg(t("log.power_cycle"))
            if handset_idx is not None:
                wx.CallAfter(frame._set_handset_status, handset_idx, STATUS_DONE)
                wx.CallAfter(frame._set_handset_progress, handset_idx, "100%")

            # Record flash version
            if radio and file_version:
                try:
                    fm.record_flash(radio["id"], file_version, sha256)
                    self.log_msg(t("log.recorded_flash").format(
                        version=file_version, radio=radio_name))
                except Exception:
                    # Recording flash history is bookkeeping: a corrupt or
                    # unwritable state file must not fail a flash that already
                    # succeeded on the radio.
                    pass
                # Compare against latest known
                _, latest_ver = frame._get_firmware_url_and_version(radio)
                if latest_ver and file_version:
                    cmp = fv.compare_versions(file_version, latest_ver)
                    if cmp == 0:
                        self.log_msg(t("log.fw_is_latest").format(version=file_version))
                    elif cmp < 0:
                        self.log_msg(t("log.fw_newer_available").format(
                            latest=latest_ver, current=file_version))

            frame._terminal_state = "complete"
            wx.CallAfter(self.offer_test_report, radio_name, firmware_path,
                         True, "", radio_id, file_version)

        except Exception as e:
            error_msg = str(e)
            self.log_msg(t("log.error_prefix").format(message=error_msg))
            if frame._is_permission_denied(e):
                frame._log_dialout_hint(port)
            else:
                self.log_msg(t("log.may_need_power_cycle"))
            frame._terminal_state = "failed"
            if handset_idx is not None:
                wx.CallAfter(frame._set_handset_status, handset_idx, STATUS_FAILED)
                wx.CallAfter(frame._set_handset_progress, handset_idx, "—")
            wx.CallAfter(self.offer_test_report, radio_name, firmware_path,
                         False, error_msg, radio_id, file_version)
        finally:
            frame._busy = False
            self.set_buttons(True)

    def flash_thread_btf(self, port, firmware_path, handset_idx, radio):
        # Single-port BTF flash. Mirrors flash_thread's structure (busy state,
        # per-handset status, log messages, post-flash version recording, test
        # report offer) but delegates the on-the-wire work to fw_btf.
        frame = self.frame
        radio_name = radio["name"] if radio else t("fallback.radio_unknown")
        # Derive once, up front, so both the success and failure paths pass the
        # same identity to record_flash and to the test-report nag suppression.
        radio_id = radio["id"] if radio else None
        file_version = fv.extract_version_from_filename(
            os.path.basename(firmware_path))
        if handset_idx is not None:
            wx.CallAfter(frame._set_handset_status, handset_idx, STATUS_FLASHING)
            wx.CallAfter(frame._set_handset_progress, handset_idx, "0%")

        sha256 = ""
        try:
            # Use the module-level `os` (see flash_thread): a function-local
            # `import os` would UnboundLocalError on the os.path.basename call
            # above.
            import hashlib

            fw_size = os.path.getsize(firmware_path)
            if fw_size > fw_btf.MAX_FIRMWARE_BYTES:
                raise ValueError(t("log.file_too_large").format(size=fw_size))
            with open(firmware_path, "rb") as f:
                firmware = f.read()

            fw_btf.validate_firmware(firmware, firmware_path)
            sha256 = hashlib.sha256(firmware).hexdigest()
            self.log_msg(t("log.firmware_path").format(path=firmware_path))
            self.log_msg(t("log.port").format(port=port))
            self.log_msg("")

            def log_cb(msg):
                self.log_msg(msg)

            def progress_cb(pct):
                self.set_progress(pct)
                if handset_idx is not None:
                    wx.CallAfter(frame._set_handset_progress,
                                 handset_idx, f"{int(pct)}%")

            fw_btf.flash_to_port(port, firmware,
                                 log_cb=log_cb, progress_cb=progress_cb)

            self.log_msg("")
            self.log_msg(t("log.flash_complete"))
            if handset_idx is not None:
                wx.CallAfter(frame._set_handset_status, handset_idx, STATUS_DONE)
                wx.CallAfter(frame._set_handset_progress, handset_idx, "100%")

            if radio and file_version:
                try:
                    fm.record_flash(radio["id"], file_version, sha256)
                    self.log_msg(t("log.recorded_flash").format(
                        version=file_version, radio=radio_name))
                except Exception:
                    # Same as the KDH path: history bookkeeping is best-effort
                    # and must not fail an already-successful flash.
                    pass

            frame._terminal_state = "complete"
            wx.CallAfter(self.offer_test_report, radio_name, firmware_path,
                         True, "", radio_id, file_version)

        except Exception as e:
            error_msg = str(e)
            self.log_msg(t("log.error_prefix").format(message=error_msg))
            if frame._is_permission_denied(e):
                frame._log_dialout_hint(port)
            else:
                self.log_msg(t("log.may_need_power_cycle"))
            frame._terminal_state = "failed"
            if handset_idx is not None:
                wx.CallAfter(frame._set_handset_status, handset_idx, STATUS_FAILED)
                wx.CallAfter(frame._set_handset_progress, handset_idx, "—")
            wx.CallAfter(self.offer_test_report, radio_name, firmware_path,
                         False, error_msg, radio_id, file_version)
        finally:
            frame._busy = False
            self.set_buttons(True)

    def offer_test_report(self, radio_name, firmware_path, success, error_msg,
                          radio_id=None, file_version=None):
        # Nag suppression: once a report was submitted or explicitly skipped for
        # this radio id + firmware version, don't offer again for that same
        # combination (keyed the same grain record_flash uses). A plain Skip
        # does not record anything, so it keeps prompting on future flashes.
        frame = self.frame
        if radio_id and fm.get_test_report_status(radio_id, file_version) in (
                "submitted", "skipped"):
            if success:
                self.offer_firmware_cleanup(firmware_path)
            return

        log_content = frame.log.GetValue()
        status = show_test_report_dialog(frame, radio_name, firmware_path,
                                         success, error_msg, log_content)
        if radio_id and status:
            try:
                fm.mark_test_report(radio_id, file_version, status)
            except Exception:
                # Recording the suppression state is best-effort: a corrupt or
                # unwritable state file must not turn a successful flash into
                # an error dialog. Worst case the user is asked again next time.
                pass
        if success:
            self.offer_firmware_cleanup(firmware_path)

    def offer_firmware_cleanup(self, firmware_path):
        """Ask user if they want to delete downloaded firmware files."""
        frame = self.frame
        import firmware_download as dl

        # Only offer cleanup if the firmware was downloaded by us
        if not firmware_path or not firmware_path.startswith(dl.DOWNLOAD_DIR):
            return

        try:
            download_dir = dl.DOWNLOAD_DIR
            files = os.listdir(download_dir)
            if not files:
                return

            total_size = sum(
                os.path.getsize(os.path.join(download_dir, f))
                for f in files
                if os.path.isfile(os.path.join(download_dir, f))
            )
            size_mb = total_size / (1024 * 1024)

            dlg = wx.MessageDialog(frame,
                t("dialog.cleanup_body").format(
                    size_mb=size_mb, path=download_dir),
                t("dialog.cleanup_title"),
                wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION)

            if dlg.ShowModal() == wx.ID_YES:
                import shutil
                shutil.rmtree(download_dir, ignore_errors=True)
                self.log_msg(t("log.cleanup_done"))
                # The firmware we just flashed lived in that dir and is now
                # gone. Clear the path field so the hint and workflow gating
                # reflect that no firmware is loaded (otherwise the textbox
                # shows a dead path and Flash stays enabled over a missing file).
                if firmware_path.startswith(download_dir):
                    frame.file_path.SetValue("")
                    frame._terminal_state = None
                    frame._update_workflow_gating()
            dlg.Destroy()
        except Exception:
            # The cleanup offer is a courtesy prompt after the flash finished;
            # any dialog/filesystem hiccup here is not worth surfacing.
            pass

    # ------------------------------------------------------------------
    # Dry-run worker
    # ------------------------------------------------------------------

    def on_dry_run(self, event):
        frame = self.frame
        if frame._busy:
            return
        firmware_path = frame.file_path.GetValue()
        if not firmware_path:
            wx.MessageBox(t("dialog.error_select_firmware_first"),
                          t("dialog.error_title"), wx.OK | wx.ICON_ERROR)
            return

        frame.log.Clear()
        frame.progress.SetValue(0)
        frame._busy = True
        frame._busy_state = "dryrun"
        frame._terminal_state = None
        self.set_buttons(False)
        frame._set_hint("dryrun")
        threading.Thread(target=self.dryrun_thread, args=(firmware_path,), daemon=True).start()

    def dryrun_thread(self, firmware_path):
        # Both protocols delegate to their driver's dry_run (packet-build
        # validation + CRC self-checks; no serial). The KDH path used to
        # hand-roll that validation/packet-build loop inline here — the same
        # work fw.dry_run already does and TestDryRun already covers — so it
        # had no shared source of truth. It now routes through
        # fw.dry_run(log_cb=…) exactly as the BTF branch already routed through
        # fw_btf.dry_run, selected via the frame's _driver_for dispatch.
        frame = self.frame
        radio = frame._get_selected_radio()
        driver = frame._driver_for(radio)
        try:
            self.log_msg(t("log.dryrun_header"))
            self.log_msg("")
            # KDH dry_run returns False on a validation failure; BTF dry_run
            # raises (caught below). Either signal means the dry run failed.
            ok = driver.dry_run(firmware_path, log_cb=self.log_msg)
            self.set_progress(100)
            frame._terminal_state = (
                "dryrun_complete" if ok is not False else "failed")
        except Exception as e:
            self.log_msg(t("log.error_prefix").format(message=e))
            frame._terminal_state = "failed"
        finally:
            frame._busy = False
            self.set_buttons(True)

    # ------------------------------------------------------------------
    # Diagnostics worker
    # ------------------------------------------------------------------

    def on_diag(self, event):
        frame = self.frame
        if frame._busy:
            return
        # Diagnostics runs on a single port — use the first checked handset.
        selected = frame._selected_handset_indices()
        if not selected:
            wx.MessageBox(t("dialog.error_no_handset_diag"),
                          t("dialog.error_no_handset_title"),
                          wx.OK | wx.ICON_ERROR)
            return
        port = frame._handset_ports[selected[0]]["device"]

        frame.log.Clear()
        frame.progress.SetValue(0)
        frame._busy = True
        frame._busy_state = "diagnostics"
        frame._terminal_state = None
        self.set_buttons(False)
        frame._set_hint("diagnostics")
        threading.Thread(target=self.diag_thread, args=(port,), daemon=True).start()

    def diag_thread(self, port):
        frame = self.frame
        try:
            self.log_msg(t("log.diag_running").format(port=port))
            self.log_msg("")

            # Converged onto the driver's diagnostic_probe: opening the port,
            # sending the protocol-appropriate handshake/probe and reading the
            # reply was hand-rolled as serial.Serial inline here, so the
            # diagnostics path had no headless coverage. The probe is dispatched
            # through _driver_for so a BTF radio (RT-950 Pro) still gets its
            # CMD_PROBE rather than the KDH handshake; the driver returns the
            # raw exchange and the GUI formats the i18n log lines below.
            driver = frame._driver_for(frame._get_selected_radio())
            info = driver.diagnostic_probe(port)

            self.log_msg(t("log.diag_serial_info").format(
                baud=info["baudrate"], dtr=info["dtr"], rts=info["rts"]))
            self.log_msg(t("log.diag_modem_lines").format(
                cts=info["cts"], dsr=info["dsr"]))
            self.log_msg("")

            self.log_msg(t("log.diag_sending"))
            self.log_msg(t("log.diag_tx").format(hex=info["tx_hex"]))
            self.set_progress(50)
            if info["responding"]:
                self.log_msg(t("log.diag_rx").format(
                    count=info["rx_count"], hex=info["rx_hex"]))
                self.log_msg("")
                self.log_msg(t("log.diag_responding"))
                frame._terminal_state = "diag_complete"
            else:
                self.log_msg(t("log.diag_no_rx"))
                self.log_msg("")
                self.log_msg(t("log.diag_no_response"))
                self.log_msg(t("log.diag_check"))
                frame._terminal_state = "failed"

            self.set_progress(100)

        except Exception as e:
            self.log_msg(t("log.error_prefix").format(message=e))
            if frame._is_permission_denied(e):
                frame._log_dialout_hint(port)
            frame._terminal_state = "failed"
        finally:
            frame._busy = False
            self.set_buttons(True)
