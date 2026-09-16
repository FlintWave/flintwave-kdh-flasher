#!/usr/bin/env python3
"""
Firmware acquisition + update notification: the download/updater controller.

This is the *controller* half of the Firmware column's runtime — everything
that reaches the network or the filesystem to get firmware onto the machine,
plus the "a newer app release exists" status-bar notification. It owns:

  * **Firmware discovery** — ``get_firmware_url_and_version`` (manifest-first URL
    / version resolution) and ``update_radio_info`` (Download button label /
    enabled state for the current selection, variant-walkthrough refresh, hint
    refresh), driven by the radio dropdown via ``on_radio_changed``.
  * **The download worker** — ``on_download`` (confirmation / untested gate /
    ``_busy`` interlock) and its background ``download_thread`` (network fetch +
    extract, progress scaled to 80 % for the download phase), plus ``on_browse``
    for picking a local ``.kdhx``.
  * **Background tasks** — ``fetch_manifest`` and ``check_update`` run on daemon
    threads at startup; ``notify_update`` / ``show_update_link`` surface a newer
    release as a clickable status-bar link.
  * **Auto-update** — ``check_update`` now downloads the update asset in the
    background. If the user hasn't started work yet, it installs immediately
    and restarts. If work is in progress, the download continues silently and
    a "Restart to Update" button appears in the status bar.

``DownloadController`` collaborates with the owning frame for everything only
the frame can provide: the ``download_btn`` / ``file_path`` / ``radio_combo`` /
``progress`` / ``log`` / ``update_link`` / ``status_bar_panel`` widgets (built by
gui_columns / gui_statusbar), the ``_busy`` / ``_busy_state`` / ``_terminal_state``
/ ``_closing`` flags it shares with the flash workers, the worker plumbing
(``set_buttons`` / ``set_progress`` / ``log_msg``), the hint refresh
(``_compute_hint_state`` / ``_set_hint``) and workflow gating, and the selection
model. ``self.manifest`` is owned here and exposed on the frame as a read-only
``manifest`` property shim (exactly as ``_handset_ports`` shims ``handset.ports``)
so the hint presenter and flash workers keep reading ``frame.manifest``.

Boundary note — the selection model stays on the frame. ``_get_selected_radio``
and ``_driver_for`` are shared by HandsetController, the hint presenter and the
flash workers, so they remain frame-level helpers, not owned here. The
hardware-variant **answer widgets** (``_render_variant_options`` /
``_clear_variant_panel`` / ``_on_variant_chosen`` / ``_get_selected_group``) also
stay on the frame: although firmware discovery drives them, they manipulate the
frame-owned ``_variant_panel`` and form one selection cluster with
``_get_selected_radio`` / ``_selected_row`` / ``_radio_rows``. ``update_radio_info``
reaches back through the frame for them, matching how HandsetController calls
``frame._driver_for``.

The frame keeps thin same-named delegators (``on_download`` / ``on_browse`` /
``on_radio_changed`` / ``_update_radio_info`` / ``_get_firmware_url_and_version``
and the background-task methods) so gui_columns event bindings, the hint
presenter, the flash workers and the ``__init__`` daemon-thread targets keep
calling the same names, exactly as HandsetController's delegators do.
"""

import os
import tempfile
import threading

try:
    import wx
except ImportError:
    wx = None

import firmware_download as dl
import firmware_manifest as fm
import updater
from i18n import t


class DownloadController:
    """Owns firmware discovery, the download worker, and update notification."""

    def __init__(self, frame):
        self.frame = frame
        # Manifest state (must be resolvable before the first update_radio_info).
        # The frame exposes this as a read-only `manifest` property shim so the
        # hint presenter and flash workers keep reading frame.manifest.
        self.manifest = None
        self._update_url = None   # set by notify_update when an update is detected

        # Auto-updater state
        self._update_downloaded_path = None
        self._update_remote_version = None
        self._update_download_cancelled = False

    # ------------------------------------------------------------------
    # Background tasks
    # ------------------------------------------------------------------

    def fetch_manifest(self):
        try:
            self.manifest = fm.fetch_manifest()
            wx.CallAfter(self.update_radio_info)
        except Exception:
            pass

    def check_update(self):
        """Background check for a newer release; auto-download and install.

        On startup (before the user has interacted): downloads the update,
        shows a progress indicator in the status bar, and if the user still
        hasn't started work, installs + restarts automatically.

        If the user starts work during the download: the download continues
        silently, and once complete a "Restart to Update" button appears in
        the status bar.
        """
        import time
        time.sleep(2)
        try:
            has_update, local_info, remote_info = updater.check_for_update()
            if not has_update:
                return

            self._update_remote_version = remote_info

            # For git installs, try a quick git pull
            if updater.is_git_install():
                wx.CallAfter(self._show_update_checking)
                success, msg = updater.apply_update()
                if success and not self._user_has_started_work():
                    wx.CallAfter(self._do_restart)
                elif success:
                    wx.CallAfter(self._show_restart_button)
                else:
                    wx.CallAfter(self.notify_update, local_info, remote_info)
                return

            # Packaged builds: fetch release info and download the asset
            release = updater.get_latest_release_full()
            if not release:
                wx.CallAfter(self.notify_update, local_info, remote_info)
                return

            asset_name, asset_url, asset_size = updater.get_platform_asset_url(release)
            if not asset_url:
                wx.CallAfter(self.notify_update, local_info, remote_info)
                return

            if not updater.can_auto_install():
                wx.CallAfter(self.notify_update, local_info, remote_info)
                return

            wx.CallAfter(self._show_update_progress, 0,
                         t("statusbar.update_downloading"))

            dest = os.path.join(tempfile.gettempdir(),
                                "flintwave-flash-update-" + asset_name)
            try:
                def on_progress(downloaded, total):
                    if self._update_download_cancelled:
                        return
                    if total > 0:
                        pct = int(downloaded * 100 / total)
                    else:
                        pct = 0
                    wx.CallAfter(self._show_update_progress, pct)

                updater.download_update(asset_url, dest,
                                        progress_callback=on_progress)
            except Exception:
                wx.CallAfter(self.notify_update, local_info, remote_info)
                return

            self._update_downloaded_path = dest

            # Install the update (stage the binary)
            success, msg = updater.install_update(dest)
            if not success:
                wx.CallAfter(self.notify_update, local_info, remote_info)
                return

            if not self._user_has_started_work():
                wx.CallAfter(self._do_restart)
            else:
                wx.CallAfter(self._show_restart_button)

        except Exception:
            # Background thread — never crash the app for an update failure
            pass

    def _user_has_started_work(self):
        """True if the user has interacted with any workflow step."""
        frame = self.frame
        if frame._closing:
            return True
        if frame._busy:
            return True
        try:
            if frame.file_path.GetValue():
                return True
        except Exception:
            # Widget may be destroyed during shutdown
            return True
        return False

    def _show_update_checking(self):
        frame = self.frame
        if frame._closing:
            return
        self._show_update_progress(
            -1, t("statusbar.update_checking"))

    def _show_update_progress(self, pct, label=None):
        """Show download progress in the status bar."""
        frame = self.frame
        if frame._closing:
            return
        try:
            progress_widget = frame.status_bar_panel.update_progress
            if label:
                text = label
            elif pct >= 0:
                text = t("statusbar.update_downloading_pct").format(pct=pct)
            else:
                text = t("statusbar.update_checking")

            progress_widget.SetLabel(text)
            if not progress_widget.IsShown():
                progress_widget.Show()
                progress_widget.SetMinSize(progress_widget.GetBestSize())
            frame.status_bar_panel.Layout()
        except Exception:
            # Status bar widget may not exist yet during early startup
            pass

    def _show_restart_button(self):
        """Show the 'Restart to Update' button in the status bar."""
        frame = self.frame
        if frame._closing:
            return
        try:
            # Hide progress text
            frame.status_bar_panel.update_progress.Hide()
            # Show restart button
            restart_btn = frame.status_bar_panel.restart_btn
            if self._update_remote_version:
                restart_btn.SetToolTip(
                    t("statusbar.restart_tooltip").format(
                        version=self._update_remote_version))
            restart_btn.Show()
            restart_btn.SetMinSize(restart_btn.GetBestSize())

            # Style the restart button to stand out
            palette = frame.current_theme_palette
            if palette:
                restart_btn.SetOwnForegroundColour(wx.Colour(*palette[5]))

            frame.status_bar_panel.Layout()
        except Exception:
            # Best-effort UI update; don't block the restart path
            pass

    def _do_restart(self):
        """Install the staged update and restart the app."""
        frame = self.frame
        if frame._closing:
            return
        try:
            frame.status_bar_panel.update_progress.Hide()
            frame.status_bar_panel.restart_btn.Hide()
            frame.status_bar_panel.Layout()
        except Exception:
            # Cosmetic cleanup before restart; safe to ignore
            pass
        if updater.is_git_install():
            updater.restart_app()
        else:
            updater.apply_staged_update()

    def notify_update(self, local_info, remote_info):
        """An update is available — show the Update Available link in the status bar."""
        frame = self.frame
        if frame._closing or not frame:
            return
        from gui_main import VERSION
        url = updater.get_releases_url()
        self._update_url = url
        try:
            # Hide progress/restart widgets
            frame.status_bar_panel.update_progress.Hide()
            frame.update_link.SetURL(url)
            frame.update_link.SetToolTip(
                t("statusbar.update_tooltip").format(
                    local=VERSION, remote=remote_info)
            )
            self.show_update_link()
        except Exception:
            pass

    def show_update_link(self):
        frame = self.frame
        frame.update_link.Show()
        try:
            frame.update_link.SetMinSize(frame.update_link.GetBestSize())
        except Exception:
            pass
        frame.status_bar_panel.Layout()

    # ------------------------------------------------------------------
    # Firmware discovery
    # ------------------------------------------------------------------

    def get_firmware_url_and_version(self, radio):
        """Get the best firmware URL and version for a radio.

        Checks manifest first (may have newer URL), falls back to radios.json.
        Returns (url, version) where either may be None.
        """
        manifest_info = fm.get_radio_firmware_info(radio["id"], self.manifest)
        manifest_url = manifest_info.get("firmware_url") if manifest_info else None
        manifest_ver = manifest_info.get("firmware_version") if manifest_info else None

        url = manifest_url or radio.get("firmware_url")
        version = manifest_ver
        return url, version

    def update_radio_info(self):
        """Refresh the Download button label/state for the selected radio.

        Per-radio info (bootloader keys, connector, notes) is rendered inline
        in the hints panel via _set_hint(); this method owns the Download
        button, the hint refresh, and the variant walkthrough panel.
        """
        frame = self.frame
        if frame._closing or not frame:
            return
        radio = frame._get_selected_radio()
        group_sel = frame._get_selected_group()
        if group_sel:
            frame._render_variant_options(*group_sel)
        else:
            frame._clear_variant_panel()

        if radio:
            url, version = self.get_firmware_url_and_version(radio)
            has_url = bool(url)
            if not frame._busy:
                frame.download_btn.Enable(has_url)
            if not has_url:
                frame.download_btn.SetLabel(t("button.no_direct_url"))
            elif version:
                frame.download_btn.SetLabel(
                    t("button.download_versioned").format(version=version))
            else:
                frame.download_btn.SetLabel(t("button.download_latest"))
        elif group_sel:
            if not frame._busy:
                frame.download_btn.Enable(False)
            frame.download_btn.SetLabel(t("button.identify_first"))

        frame._set_hint(frame._compute_hint_state())

    def on_radio_changed(self, event):
        frame = self.frame
        frame._terminal_state = None
        self.update_radio_info()
        frame._update_workflow_gating()

    # ------------------------------------------------------------------
    # Download worker
    # ------------------------------------------------------------------

    def on_download(self, event):
        frame = self.frame
        if frame._busy:
            return
        radio = frame._get_selected_radio()
        if not radio:
            return

        if not radio.get("tested"):
            dlg = wx.MessageDialog(frame,
                t("dialog.untested_body").format(radio=radio['name']),
                t("dialog.untested_title"), wx.YES_NO | wx.ICON_WARNING)
            if dlg.ShowModal() != wx.ID_YES:
                dlg.Destroy()
                return
            dlg.Destroy()

        url, _ = self.get_firmware_url_and_version(radio)

        manifest_info = fm.get_radio_firmware_info(radio["id"], self.manifest)
        expected_sha256 = manifest_info.get("firmware_sha256") if manifest_info else None

        frame.log.Clear()
        frame.progress.SetValue(0)
        frame._busy = True
        frame._busy_state = "downloading"
        frame._terminal_state = None
        frame.set_buttons(False)
        frame._set_hint("downloading")
        threading.Thread(target=self.download_thread,
                         args=(radio, url, expected_sha256), daemon=True).start()

    def download_thread(self, radio, url=None, expected_sha256=None):
        frame = self.frame
        try:
            frame.log_msg(t("log.downloading_for").format(radio=radio['name']))
            frame.log_msg(t("log.url").format(url=url or radio.get('firmware_url', 'N/A')))
            frame.log_msg("")

            def on_progress(pct):
                frame.set_progress(pct * 0.8)

            url_override = url if url != radio.get("firmware_url") else None
            kdhx_path, _ = dl.download_and_extract(
                radio["id"], progress_callback=on_progress,
                url_override=url_override,
                expected_sha256=expected_sha256,
            )

            frame.set_progress(100)
            frame.log_msg(t("log.firmware_extracted").format(path=kdhx_path))
            frame.log_msg("")
            frame.log_msg(t("log.firmware_ready"))

            wx.CallAfter(frame.file_path.SetValue, kdhx_path)
            frame._terminal_state = None

        except Exception as e:
            frame.log_msg(t("log.error_prefix").format(message=e))
            if "No direct download URL" in str(e):
                page = radio.get("firmware_page", "")
                if page:
                    frame.log_msg(t("log.visit_page").format(url=page))
            frame._terminal_state = "failed"
        finally:
            frame._busy = False
            frame.set_buttons(True)

    def on_browse(self, event):
        frame = self.frame
        dlg = wx.FileDialog(frame, t("filedlg.select_firmware"),
                            wildcard=t("filedlg.wildcard"),
                            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        if dlg.ShowModal() == wx.ID_OK:
            frame.file_path.SetValue(dlg.GetPath())
        dlg.Destroy()
        frame._terminal_state = None
        frame._set_hint(frame._compute_hint_state())
