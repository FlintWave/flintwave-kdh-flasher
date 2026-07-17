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
        """Background check for a newer release; surface as a status-bar link.

        We deliberately don't try to apply the update in-app — auto-update on
        Linux git installs has historically been unreliable. Instead, when a
        newer version is detected we show a clickable link in the status bar
        that opens the GitHub releases page in the user's default browser.
        """
        import time
        time.sleep(2)  # Let the UI finish rendering before touching the status bar
        try:
            has_update, local_info, remote_info = updater.check_for_update()
            if has_update:
                wx.CallAfter(self.notify_update, local_info, remote_info)
        except Exception:
            pass

    def notify_update(self, local_info, remote_info):
        """An update is available — show the Update Available link in the status bar."""
        frame = self.frame
        if frame._closing or not frame:
            return
        # Single source of truth for the running version lives in gui_main;
        # deferred import avoids a circular import at module load (matches
        # updater.get_current_version's pattern).
        from gui_main import VERSION
        url = updater.get_releases_url()
        self._update_url = url
        try:
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
        # The link was added to the sizer while hidden, which caches a 0-width
        # slot and clips the label on Show(). Re-pin the min size to the
        # current best size so longer translations (e.g. "Mise à jour disponible")
        # render in full.
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
        # May be posted from the background manifest fetch after the frame has
        # started closing — bail rather than touch destroyed widgets.
        if frame._closing or not frame:
            return
        radio = frame._get_selected_radio()
        group_sel = frame._get_selected_group()
        # A variant family row keeps its answer controls visible (so the user
        # can correct a mis-click) whether or not the variant is resolved yet.
        if group_sel:
            frame._render_variant_options(*group_sel)
        else:
            frame._clear_variant_panel()

        if radio:
            # Concrete radio (plain row, or a group with a resolved variant).
            url, version = self.get_firmware_url_and_version(radio)
            has_url = bool(url)
            # Never re-enable Download during an in-progress operation; the
            # busy-end gating pass will restore the correct state.
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
            # A variant family is selected but not resolved ("I'm not sure" or
            # unanswered): keep Download disabled until a variant is chosen.
            if not frame._busy:
                frame.download_btn.Enable(False)
            frame.download_btn.SetLabel(t("button.identify_first"))

        frame._set_hint(frame._compute_hint_state())

    def on_radio_changed(self, event):
        frame = self.frame
        # Picking a different radio clears any sticky terminal state so the
        # hint panel doesn't keep showing the previous flash's completion copy.
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
        # _get_selected_radio() returns None for an unresolved variant group
        # (unanswered or "I'm not sure"), so this guard also refuses to start a
        # download until the user has identified their hardware variant — belt
        # and suspenders behind the disabled Download button. The app never
        # guesses; the concrete member id resolves only after an explicit answer.
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

        # Get expected SHA-256 from manifest if available
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
                frame.set_progress(pct * 0.8)  # 80% for download

            # Use url as override if it differs from the hardcoded one
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
            frame._terminal_state = None  # path change will recompute hint

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
