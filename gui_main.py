#!/usr/bin/env python3
"""
GUI frontend for the KDH bootloader firmware flasher.
Supports BTECH, Baofeng, Radtel, and other KDH-based radios.
Cross-platform: works on Linux, macOS, and Windows.
"""

import os
import sys
import threading
import wx
import wx.adv

import flash_firmware as fw
import firmware_download as dl
import firmware_manifest as fm
import firmware_version as fv
import updater
import serial

from gui_ports import list_serial_ports, find_programming_cable
from gui_dialogs import (
    PortFinderDialog,
    BatchFlashDialog,
    show_about_dialog,
    show_test_report_dialog,
)
from gui_themes import apply_theme, THEME_PALETTES

VERSION = "26.04.2"

FONT_SIZES = [8, 9, 11, 14]


class FlasherFrame(wx.Frame):
    def __init__(self):
        super().__init__(None, title="KDH Bootloader Firmware Flasher", size=(900, 720))
        self.SetMinSize((820, 620))

        self.font_size = 9
        self.current_theme = "latte"
        self.current_theme_palette = None
        self._busy = False
        self._terminal_state = None  # set to "complete"/"failed" by threads

        # Window icon
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon_128.png")
        if os.path.exists(icon_path):
            self.SetIcon(wx.Icon(icon_path))

        panel = wx.Panel(self)
        self.panel = panel
        root_sizer = wx.BoxSizer(wx.VERTICAL)

        # ---- Top row: three columns separated by ">" arrows ----
        top_row = wx.BoxSizer(wx.HORIZONTAL)

        # Manifest state (must be set before _update_radio_info)
        self.manifest = None
        self.radios = dl.load_radios()

        col_firmware = self._build_firmware_column(panel)
        col_cable = self._build_cable_column(panel)
        col_flash = self._build_flash_column(panel)

        arrow_font = wx.Font(20, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
        self.arrow1 = wx.StaticText(panel, label="›")  # single right-pointing angle
        self.arrow1.SetFont(arrow_font)
        self.arrow2 = wx.StaticText(panel, label="›")
        self.arrow2.SetFont(arrow_font)

        top_row.Add(col_firmware, 1, wx.EXPAND | wx.ALL, 8)
        top_row.Add(self.arrow1, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT | wx.RIGHT, 4)
        top_row.Add(col_cable, 1, wx.EXPAND | wx.ALL, 8)
        top_row.Add(self.arrow2, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT | wx.RIGHT, 4)
        top_row.Add(col_flash, 1, wx.EXPAND | wx.ALL, 8)

        root_sizer.Add(top_row, 0, wx.EXPAND)

        # ---- Middle row: hints panel + log ----
        middle_row = wx.BoxSizer(wx.HORIZONTAL)

        self.hints_panel = wx.Panel(panel)
        hints_sizer = wx.BoxSizer(wx.VERTICAL)
        self.hint_title = wx.StaticText(self.hints_panel, label="")
        self.hint_title.SetFont(wx.Font(11, wx.FONTFAMILY_DEFAULT,
                                        wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        hints_sizer.Add(self.hint_title, 0, wx.ALL, 10)
        self.hint_body = wx.StaticText(self.hints_panel, label="")
        hints_sizer.Add(self.hint_body, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        self.hints_panel.SetSizer(hints_sizer)
        self.hints_panel.Bind(wx.EVT_SIZE, self._on_hints_size)

        self.log = wx.TextCtrl(panel,
                               style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL)
        self.log.SetFont(wx.Font(self.font_size, wx.FONTFAMILY_TELETYPE,
                                 wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))

        middle_row.Add(self.hints_panel, 35, wx.EXPAND | wx.LEFT | wx.BOTTOM, 8)
        middle_row.Add(self.log, 65, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        root_sizer.Add(middle_row, 1, wx.EXPAND)

        # ---- Bottom: status bar with icon-style toggle buttons ----
        self.status_bar_panel = self._build_status_bar(panel)
        root_sizer.Add(self.status_bar_panel, 0, wx.EXPAND)

        panel.SetSizer(root_sizer)
        self.Centre()

        # Bind change events that update hint state
        self.file_path.Bind(wx.EVT_TEXT, self._on_state_change)
        self.port_combo.Bind(wx.EVT_COMBOBOX, self._on_state_change)

        # Initial population
        self._update_radio_info()
        self._auto_detect_port()
        self._set_hint(self._compute_hint_state())

        # Check for updates and fetch manifest in background
        threading.Thread(target=self._check_update, daemon=True).start()
        threading.Thread(target=self._fetch_manifest, daemon=True).start()

    # ------------------------------------------------------------------
    # Layout builders
    # ------------------------------------------------------------------

    def _build_firmware_column(self, parent):
        box = wx.StaticBox(parent, label="Firmware")
        sizer = wx.StaticBoxSizer(box, wx.VERTICAL)

        radio_names = [
            r['name'] if r['name'].startswith(r['manufacturer'])
            else f"{r['manufacturer']} {r['name']}"
            for r in self.radios
        ]
        self.radio_combo = wx.ComboBox(box, choices=radio_names,
                                       style=wx.CB_DROPDOWN | wx.CB_READONLY)
        if radio_names:
            self.radio_combo.SetSelection(0)
        self.radio_combo.Bind(wx.EVT_COMBOBOX, self.on_radio_changed)
        sizer.Add(self.radio_combo, 0, wx.EXPAND | wx.ALL, 6)

        self.radio_info = wx.StaticText(box, label="")
        sizer.Add(self.radio_info, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)

        self.download_btn = wx.Button(box, label="Download Latest")
        self.download_btn.Bind(wx.EVT_BUTTON, self.on_download)
        sizer.Add(self.download_btn, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)

        file_row = wx.BoxSizer(wx.HORIZONTAL)
        self.file_path = wx.TextCtrl(box)
        file_row.Add(self.file_path, 1, wx.EXPAND | wx.RIGHT, 4)
        browse_btn = wx.Button(box, label="Browse…")
        browse_btn.Bind(wx.EVT_BUTTON, self.on_browse)
        file_row.Add(browse_btn, 0)
        sizer.Add(file_row, 0, wx.EXPAND | wx.ALL, 6)

        # Re-wrap radio_info on column resize
        box.Bind(wx.EVT_SIZE, lambda e: (self._wrap_radio_info(box), e.Skip()))
        return sizer

    def _build_cable_column(self, parent):
        box = wx.StaticBox(parent, label="Cable")
        sizer = wx.StaticBoxSizer(box, wx.VERTICAL)

        self.port_combo = wx.ComboBox(box, style=wx.CB_DROPDOWN | wx.CB_READONLY)
        sizer.Add(self.port_combo, 0, wx.EXPAND | wx.ALL, 6)

        find_btn = wx.Button(box, label="Find Cable…")
        find_btn.Bind(wx.EVT_BUTTON, self.on_find_cable)
        sizer.Add(find_btn, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)

        self.batch_btn = wx.Button(box, label="Batch Flash…")
        self.batch_btn.SetToolTip(
            "Flash the same firmware to multiple connected radios in sequence.")
        self.batch_btn.Bind(wx.EVT_BUTTON, self.on_batch_flash)
        sizer.Add(self.batch_btn, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)

        sizer.AddStretchSpacer(1)
        return sizer

    def _build_flash_column(self, parent):
        box = wx.StaticBox(parent, label="Flash")
        sizer = wx.StaticBoxSizer(box, wx.VERTICAL)

        self.flash_btn = wx.Button(box, label="Flash Firmware")
        flash_font = wx.Font(12, wx.FONTFAMILY_DEFAULT,
                             wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
        self.flash_btn.SetFont(flash_font)
        self.flash_btn.Bind(wx.EVT_BUTTON, self.on_flash)
        sizer.Add(self.flash_btn, 0, wx.EXPAND | wx.ALL, 6)

        self.progress = wx.Gauge(box, range=100)
        sizer.Add(self.progress, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)

        sizer.AddSpacer(4)

        sec_row = wx.BoxSizer(wx.HORIZONTAL)
        self.dryrun_btn = wx.Button(box, label="Dry Run")
        self.dryrun_btn.Bind(wx.EVT_BUTTON, self.on_dry_run)
        sec_row.Add(self.dryrun_btn, 1, wx.RIGHT, 4)
        self.diag_btn = wx.Button(box, label="Diagnostics")
        self.diag_btn.Bind(wx.EVT_BUTTON, self.on_diag)
        sec_row.Add(self.diag_btn, 1)
        sizer.Add(sec_row, 0, wx.EXPAND | wx.ALL, 6)

        sizer.AddStretchSpacer(1)
        return sizer

    def _build_status_bar(self, parent):
        bar = wx.Panel(parent)
        bar_sizer = wx.BoxSizer(wx.HORIZONTAL)

        def make_btn(label, tooltip, handler):
            b = wx.Button(bar, label=label, style=wx.BORDER_NONE)
            b.SetToolTip(tooltip)
            b.Bind(wx.EVT_BUTTON, lambda e: handler())
            return b

        # Theme toggle: glyph reflects the theme you'd switch TO.
        # Currently latte (light) → show moon (click to go dark).
        self.theme_btn = make_btn("☾", "Toggle dark / light theme", self._toggle_theme)
        self.theme_btn.SetMinSize((40, 28))

        self.font_btn = make_btn(f"{self.font_size} pt", "Cycle log font size", self._cycle_font)
        self.font_btn.SetMinSize((52, 28))

        usage_btn = make_btn("?", "Open Usage Guide", lambda: self.on_usage_guide(None))
        usage_btn.SetMinSize((36, 28))

        github_btn = make_btn("GH", "Open GitHub repository", lambda: self.on_github(None))
        github_btn.SetMinSize((40, 28))

        about_btn = make_btn("ⓘ", "About", lambda: self.on_about(None))
        about_btn.SetMinSize((36, 28))

        bar_sizer.Add(self.theme_btn, 0, wx.ALL, 4)
        bar_sizer.Add(self.font_btn, 0, wx.TOP | wx.BOTTOM, 4)
        bar_sizer.AddStretchSpacer(1)
        bar_sizer.Add(usage_btn, 0, wx.ALL, 4)
        bar_sizer.Add(github_btn, 0, wx.TOP | wx.BOTTOM, 4)
        bar_sizer.Add(about_btn, 0, wx.ALL, 4)

        bar.SetSizer(bar_sizer)
        return bar

    # ------------------------------------------------------------------
    # Wrap helpers
    # ------------------------------------------------------------------

    def _wrap_radio_info(self, box):
        try:
            w = box.GetClientSize().GetWidth() - 24
            if w > 50:
                self.radio_info.Wrap(w)
        except Exception:
            pass

    def _on_hints_size(self, event):
        try:
            w = self.hints_panel.GetClientSize().GetWidth() - 20
            if w > 50:
                self.hint_body.Wrap(w)
        except Exception:
            pass
        event.Skip()

    # ------------------------------------------------------------------
    # Auto-detect / device handling
    # ------------------------------------------------------------------

    def _auto_detect_port(self):
        ports = list_serial_ports()
        port_devices = [p[0] for p in ports]
        port_labels = [p[1] for p in ports]
        self.port_combo.Set(port_devices)

        device, label = find_programming_cable()
        if device and device in port_devices:
            self.port_combo.SetSelection(port_devices.index(device))
            self.log.AppendText(f"Auto-detected: {label}\n")
        elif port_devices:
            self.port_combo.SetSelection(0)

        self._set_hint(self._compute_hint_state())

    # ------------------------------------------------------------------
    # Theme + font controls
    # ------------------------------------------------------------------

    def _set_theme(self, theme):
        apply_theme(self, theme)
        self._update_theme_glyph()

    def _toggle_theme(self):
        new_theme = "mocha" if self.current_theme == "latte" else "latte"
        apply_theme(self, new_theme)
        self._update_theme_glyph()

    def _update_theme_glyph(self):
        # Glyph shows the destination theme: moon = "go to dark", sun = "go to light".
        self.theme_btn.SetLabel("☀" if self.current_theme == "mocha" else "☾")

    def _set_font_size(self, size):
        self.font_size = size
        mono = wx.Font(size, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
        ui = wx.Font(size, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
        ui_bold = wx.Font(size, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
        flash_font = wx.Font(size + 3, wx.FONTFAMILY_DEFAULT,
                             wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)

        # Recursive walk so deeply nested controls (column boxes, hints, status bar)
        # all pick up the font change
        def walk(w):
            yield w
            try:
                for c in w.GetChildren():
                    yield from walk(c)
            except Exception:
                return

        for w in walk(self.panel):
            if w is self.log:
                w.SetFont(mono)
            elif w is self.flash_btn:
                w.SetFont(flash_font)
            elif w is self.hint_title:
                w.SetFont(ui_bold)
            elif isinstance(w, wx.adv.HyperlinkCtrl):
                continue
            elif isinstance(w, wx.TextCtrl):
                w.SetFont(mono)
            else:
                try:
                    w.SetFont(ui)
                except Exception:
                    pass

        self._on_hints_size_force()
        self.panel.Layout()
        self.panel.Refresh()

    def _on_hints_size_force(self):
        try:
            w = self.hints_panel.GetClientSize().GetWidth() - 20
            if w > 50:
                self.hint_body.Wrap(w)
        except Exception:
            pass

    def _cycle_font(self):
        try:
            idx = FONT_SIZES.index(self.font_size)
        except ValueError:
            idx = -1
        new_size = FONT_SIZES[(idx + 1) % len(FONT_SIZES)]
        self._set_font_size(new_size)
        self.font_btn.SetLabel(f"{new_size} pt")

    # ------------------------------------------------------------------
    # Hint state machine
    # ------------------------------------------------------------------

    HINT_COPY = {
        "no_firmware": (
            "Step 1 — Get firmware",
            "Choose a radio model in the Firmware column, then click "
            "“Download Latest” if available, or “Browse…” "
            "to pick a .kdhx file you already downloaded."
        ),
        "no_cable": (
            "Step 2 — Connect the cable",
            "Plug in your programming cable (PC03 or compatible K1 cable), then "
            "click “Find Cable…” to detect it. The auto-detected "
            "PC03 entry will be highlighted."
        ),
        "ready_dryrun": (
            "Step 3 — Verify the firmware",
            "Click “Dry Run” to validate the firmware file and confirm "
            "the packets build without serial communication. Optionally click "
            "“Diagnostics” to test the cable."
        ),
        "ready_flash": (
            "Step 4 — Flash",
            "Put the radio in bootloader mode:\n"
            "  1. Power the radio off completely\n"
            "  2. Hold the bootloader keys (shown in the radio info)\n"
            "  3. While holding, turn the power knob to turn on\n"
            "  4. Screen stays blank, green Rx LED lights up\n"
            "  5. Do NOT release the keys until the LED is on\n\n"
            "Then click “Flash Firmware”. Do not unplug the cable or "
            "turn off the radio during the flash."
        ),
        "downloading": (
            "Downloading firmware…",
            "Fetching the latest firmware. The progress bar tracks the download. "
            "Once finished, the file will be filled in automatically."
        ),
        "flashing": (
            "Flashing in progress…",
            "Streaming firmware to the radio. Do NOT unplug the cable, power off "
            "the radio, or close this window until the operation completes."
        ),
        "dryrun": (
            "Dry run in progress…",
            "Validating the firmware file and verifying packet CRCs. "
            "No serial communication is happening."
        ),
        "diagnostics": (
            "Diagnostics in progress…",
            "Sending a handshake to the cable to confirm connectivity. "
            "If the radio responds, flashing should work."
        ),
        "complete": (
            "Done!",
            "Power cycle the radio and check Menu › Radio Info to confirm "
            "the new version. See the log on the right for full details."
        ),
        "failed": (
            "Operation failed",
            "The last operation did not complete successfully. Read the log on "
            "the right for the error message. The radio may need to be power "
            "cycled and put back in bootloader mode before trying again."
        ),
    }

    def _set_hint(self, state):
        if state not in self.HINT_COPY:
            return
        title, body = self.HINT_COPY[state]
        self.hint_title.SetLabel(title)
        self.hint_body.SetLabel(body)
        self._on_hints_size_force()
        self.hints_panel.Layout()

    def _compute_hint_state(self):
        if self._terminal_state in ("complete", "failed"):
            return self._terminal_state
        if self._busy:
            return self._busy_state if hasattr(self, "_busy_state") else "flashing"
        if not self.file_path.GetValue():
            return "no_firmware"
        if not self.port_combo.GetValue():
            return "no_cable"
        return "ready_flash"

    def _on_state_change(self, event):
        # User-initiated change clears any sticky terminal state
        self._terminal_state = None
        self._set_hint(self._compute_hint_state())
        if event:
            event.Skip()

    # ------------------------------------------------------------------
    # Menu / status bar action handlers (preserved)
    # ------------------------------------------------------------------

    def on_usage_guide(self, event):
        guide_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "USAGE.md")
        if os.path.exists(guide_path):
            wx.LaunchDefaultBrowser("file://" + guide_path)
        else:
            wx.LaunchDefaultBrowser("https://github.com/FlintWave/flintwave-kdh-flasher/blob/master/USAGE.md")

    def on_github(self, event):
        wx.LaunchDefaultBrowser("https://github.com/FlintWave/flintwave-kdh-flasher")

    def on_about(self, event):
        show_about_dialog(self)

    # ------------------------------------------------------------------
    # Background tasks
    # ------------------------------------------------------------------

    def _fetch_manifest(self):
        try:
            self.manifest = fm.fetch_manifest()
            wx.CallAfter(self._update_radio_info)
        except Exception:
            pass

    def _check_update(self):
        import time
        time.sleep(2)  # Let the UI finish rendering before showing dialogs
        try:
            has_update, local_info, remote_info = updater.check_for_update()
            if has_update:
                wx.CallAfter(self._prompt_update, local_info, remote_info)
        except Exception:
            pass

    def _prompt_update(self, local_info, remote_info):
        if updater.is_git_install():
            dlg = wx.MessageDialog(self,
                f"A newer version is available.\n\n"
                f"Local:  {local_info}\n"
                f"Remote: {remote_info}\n\n"
                "Update now? (the app will restart)",
                "Update Available", wx.YES_NO | wx.ICON_INFORMATION)
            if dlg.ShowModal() == wx.ID_YES:
                success, msg = updater.apply_update()
                if success:
                    wx.MessageBox(
                        "Updated successfully. The app will now restart.",
                        "Updated", wx.OK | wx.ICON_INFORMATION)
                    self._restart()
                else:
                    wx.MessageBox(f"Update failed:\n{msg}", "Error", wx.OK | wx.ICON_ERROR)
            dlg.Destroy()
        else:
            dlg = wx.MessageDialog(self,
                f"A newer version is available.\n\n"
                f"Installed: {local_info}\n"
                f"Latest:    {remote_info}\n\n"
                "Open the downloads page?",
                "Update Available", wx.YES_NO | wx.ICON_INFORMATION)
            if dlg.ShowModal() == wx.ID_YES:
                url = updater.get_releases_url()
                if not wx.LaunchDefaultBrowser(url):
                    import webbrowser
                    webbrowser.open(url)
            dlg.Destroy()

    def _restart(self):
        os.execv(sys.executable, [sys.executable] + sys.argv)

    def _get_selected_radio(self):
        idx = self.radio_combo.GetSelection()
        if 0 <= idx < len(self.radios):
            return self.radios[idx]
        return None

    def _get_firmware_url_and_version(self, radio):
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

    def _update_radio_info(self):
        radio = self._get_selected_radio()
        if radio:
            tested = "Tested" if radio.get("tested") else "Untested"
            info = f"Bootloader: {radio['bootloader_keys']}  |  Connector: {radio['connector']}  |  {tested}"

            url, version = self._get_firmware_url_and_version(radio)
            if version:
                info += f"  |  Latest FW: v{version}"

            notes = radio.get("notes", "")
            if notes:
                info += f"\n{notes}"

            self.radio_info.SetLabel(info)
            try:
                box = self.radio_info.GetParent()
                self._wrap_radio_info(box)
            except Exception:
                pass
            self.panel.Layout()

            has_url = bool(url)
            self.download_btn.Enable(has_url)
            if not has_url:
                self.download_btn.SetLabel("No Direct URL")
            elif version:
                self.download_btn.SetLabel(f"Download v{version}")
            else:
                self.download_btn.SetLabel("Download Latest")

        self._set_hint(self._compute_hint_state())

    def on_radio_changed(self, event):
        self._update_radio_info()

    def on_download(self, event):
        radio = self._get_selected_radio()
        if not radio:
            return

        if not radio.get("tested"):
            dlg = wx.MessageDialog(self,
                f"{radio['name']} has NOT been tested with this tool.\n\n"
                "The protocol should be compatible, but flashing untested\n"
                "firmware could potentially brick the radio.\n\n"
                "Download anyway?",
                "Untested Radio", wx.YES_NO | wx.ICON_WARNING)
            if dlg.ShowModal() != wx.ID_YES:
                dlg.Destroy()
                return
            dlg.Destroy()

        url, _ = self._get_firmware_url_and_version(radio)

        # Get expected SHA-256 from manifest if available
        manifest_info = fm.get_radio_firmware_info(radio["id"], self.manifest)
        expected_sha256 = manifest_info.get("firmware_sha256") if manifest_info else None

        self.log.Clear()
        self.progress.SetValue(0)
        self._busy = True
        self._busy_state = "downloading"
        self._terminal_state = None
        self.set_buttons(False)
        self._set_hint("downloading")
        threading.Thread(target=self._download_thread,
                         args=(radio, url, expected_sha256), daemon=True).start()

    def _download_thread(self, radio, url=None, expected_sha256=None):
        try:
            self.log_msg(f"Downloading firmware for {radio['name']}...")
            self.log_msg(f"URL: {url or radio.get('firmware_url', 'N/A')}")
            self.log_msg("")

            def on_progress(pct):
                self.set_progress(pct * 0.8)  # 80% for download

            # Use url as override if it differs from the hardcoded one
            url_override = url if url != radio.get("firmware_url") else None
            kdhx_path, _ = dl.download_and_extract(
                radio["id"], progress_callback=on_progress,
                url_override=url_override,
                expected_sha256=expected_sha256,
            )

            self.set_progress(100)
            self.log_msg(f"Firmware extracted: {kdhx_path}")
            self.log_msg("")
            self.log_msg("Firmware ready. You can now flash it.")

            wx.CallAfter(self.file_path.SetValue, kdhx_path)
            self._terminal_state = None  # path change will recompute hint

        except Exception as e:
            self.log_msg(f"\nERROR: {e}")
            if "No direct download URL" in str(e):
                page = radio.get("firmware_page", "")
                if page:
                    self.log_msg(f"Visit: {page}")
            self._terminal_state = "failed"
        finally:
            self._busy = False
            self.set_buttons(True)

    def on_batch_flash(self, event):
        firmware_path = self.file_path.GetValue()
        if not firmware_path or not os.path.exists(firmware_path):
            wx.MessageBox(
                "Select a firmware file first (Browse… or Download Latest in "
                "the Firmware column). Batch Flash uses the same firmware for "
                "every selected radio.",
                "No firmware", wx.OK | wx.ICON_INFORMATION)
            return
        radio = self._get_selected_radio()
        dlg = BatchFlashDialog(self, firmware_path, radio)
        dlg.ShowModal()
        dlg.Destroy()

    def on_find_cable(self, event):
        dlg = PortFinderDialog(self)
        if dlg.ShowModal() == wx.ID_OK and dlg.selected_port:
            port = dlg.selected_port
            # Update combo
            ports = [p[0] for p in list_serial_ports()]
            self.port_combo.Set(ports)
            if port in ports:
                self.port_combo.SetSelection(ports.index(port))
            else:
                self.port_combo.SetValue(port)
            self.log.AppendText(f"Selected: {port}\n")
            self._terminal_state = None
            self._set_hint(self._compute_hint_state())
        dlg.Destroy()

    def on_browse(self, event):
        dlg = wx.FileDialog(self, "Select firmware file",
                            wildcard="Firmware files (*.kdhx)|*.kdhx|All files (*)|*",
                            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        if dlg.ShowModal() == wx.ID_OK:
            self.file_path.SetValue(dlg.GetPath())
        dlg.Destroy()
        self._terminal_state = None
        self._set_hint(self._compute_hint_state())

    def log_msg(self, msg):
        wx.CallAfter(self.log.AppendText, msg + "\n")

    def set_progress(self, pct):
        wx.CallAfter(self.progress.SetValue, int(pct))

    def set_buttons(self, enabled):
        wx.CallAfter(self.flash_btn.Enable, enabled)
        wx.CallAfter(self.dryrun_btn.Enable, enabled)
        wx.CallAfter(self.diag_btn.Enable, enabled)
        wx.CallAfter(self.download_btn.Enable, enabled)
        wx.CallAfter(self.batch_btn.Enable, enabled)
        if enabled:
            wx.CallAfter(self._update_radio_info)
            # Recompute hint AFTER the thread has set _terminal_state
            wx.CallAfter(lambda: self._set_hint(self._compute_hint_state()))

    def on_flash(self, event):
        port = self.port_combo.GetValue()
        firmware_path = self.file_path.GetValue()

        if not port:
            wx.MessageBox("Select a serial port.\nClick 'Find Cable...' to detect your programming cable.",
                          "Error", wx.OK | wx.ICON_ERROR)
            return
        if not firmware_path:
            wx.MessageBox("Select a firmware file.", "Error", wx.OK | wx.ICON_ERROR)
            return

        radio = self._get_selected_radio()
        if radio:
            keys = radio["bootloader_keys"]
            radio_name = radio["name"]
            tested = radio.get("tested", False)
        else:
            keys = "the bootloader keys (check your radio's manual)"
            radio_name = "your radio"
            tested = False

        warning = ""
        if not tested:
            warning = (
                f"NOTE: {radio_name} has NOT been tested with this tool.\n"
                "The protocol should be compatible, but proceed with caution.\n\n"
            )

        # Check if same version was already flashed
        file_version = fv.extract_version_from_filename(os.path.basename(firmware_path))
        if radio and file_version:
            last = fm.get_last_flashed(radio["id"])
            if last and last.get("version") == file_version:
                same_dlg = wx.MessageDialog(self,
                    f"You already flashed v{file_version} to this radio.\n\n"
                    "Flash the same version again?",
                    "Same Version", wx.YES_NO | wx.ICON_QUESTION)
                if same_dlg.ShowModal() != wx.ID_YES:
                    same_dlg.Destroy()
                    return
                same_dlg.Destroy()
            elif last and last.get("version") and fv.compare_versions(file_version, last["version"]) < 0:
                older_dlg = wx.MessageDialog(self,
                    f"This firmware (v{file_version}) is older than what was\n"
                    f"last flashed (v{last['version']}).\n\n"
                    "Flash an older version?",
                    "Older Version", wx.YES_NO | wx.ICON_WARNING)
                if older_dlg.ShowModal() != wx.ID_YES:
                    older_dlg.Destroy()
                    return
                older_dlg.Destroy()

        dlg = wx.MessageDialog(self,
            f"{warning}"
            f"Make sure the {radio_name} is in bootloader mode:\n\n"
            f"1. Power off the radio\n"
            f"2. Hold {keys}\n"
            f"3. Turn power knob to turn on\n"
            f"4. Screen stays blank, green LED lights up\n\n"
            f"Do not disconnect the radio or cable during the update!\n\n"
            f"Ready to flash?",
            "Confirm", wx.YES_NO | wx.ICON_WARNING)
        if dlg.ShowModal() != wx.ID_YES:
            dlg.Destroy()
            return
        dlg.Destroy()

        self.log.Clear()
        self.progress.SetValue(0)
        self._busy = True
        self._busy_state = "flashing"
        self._terminal_state = None
        self.set_buttons(False)
        self._set_hint("flashing")
        threading.Thread(target=self._flash_thread, args=(port, firmware_path), daemon=True).start()

    def _flash_thread(self, port, firmware_path):
        radio = self._get_selected_radio()
        radio_name = radio["name"] if radio else "Unknown"

        try:
            import math
            import time
            import os
            import hashlib

            fw_size = os.path.getsize(firmware_path)
            if fw_size > fw.MAX_FIRMWARE_BYTES:
                raise ValueError(f"File too large ({fw_size} bytes)")
            with open(firmware_path, "rb") as f:
                firmware = f.read()

            fw.validate_firmware(firmware, firmware_path)
            total_chunks = math.ceil(len(firmware) / 1024)
            sha256 = hashlib.sha256(firmware).hexdigest()
            self.log_msg(f"Firmware: {firmware_path}")
            self.log_msg(f"Size: {len(firmware)} bytes, {total_chunks} chunks")
            self.log_msg(f"SHA-256: {sha256}")
            self.log_msg(f"Port: {port}")
            self.log_msg("")

            with serial.Serial(
                port=port, baudrate=115200, bytesize=8,
                parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
                timeout=2.0, write_timeout=2.0
            ) as ser:
                ser.dtr = True
                ser.rts = True
                time.sleep(0.1)
                ser.reset_input_buffer()
                ser.reset_output_buffer()

                self.log_msg("[1/3] Bootloader handshake...")
                fw.send_command(ser, fw.CMD_HANDSHAKE, 0, b"BOOTLOADER")
                self.log_msg("  OK")

                self.log_msg(f"[2/3] Sending firmware ({total_chunks} chunks)...")
                fw.send_command(ser, fw.CMD_UPDATE_DATA_PACKAGES, 0, bytes([total_chunks]))

                for i in range(total_chunks):
                    offset = i * 1024
                    chunk = firmware[offset:offset + 1024]
                    if len(chunk) < 1024:
                        chunk = chunk + b'\x00' * (1024 - len(chunk))
                    fw.send_command(ser, fw.CMD_UPDATE, i & 0xFF, chunk)
                    pct = ((i + 1) / total_chunks) * 100
                    self.set_progress(pct)
                    if (i + 1) % 10 == 0 or i == total_chunks - 1:
                        self.log_msg(f"  {pct:.0f}% ({i + 1}/{total_chunks})")

                self.log_msg("[3/3] Finalizing...")
                fw.send_command(ser, fw.CMD_UPDATE_END, 0)

            self.log_msg("  OK")
            self.log_msg("")
            self.log_msg("Firmware update complete!")
            self.log_msg("Power cycle the radio and check Menu > Radio Info.")

            # Record flash version
            file_version = fv.extract_version_from_filename(os.path.basename(firmware_path))
            if radio and file_version:
                try:
                    fm.record_flash(radio["id"], file_version, sha256)
                    self.log_msg(f"Recorded: v{file_version} flashed to {radio_name}")
                except Exception:
                    pass
                # Compare against latest known
                _, latest_ver = self._get_firmware_url_and_version(radio)
                if latest_ver and file_version:
                    cmp = fv.compare_versions(file_version, latest_ver)
                    if cmp == 0:
                        self.log_msg(f"Firmware v{file_version} is the latest available.")
                    elif cmp < 0:
                        self.log_msg(f"Note: v{latest_ver} is available (you flashed v{file_version}).")

            self._terminal_state = "complete"
            wx.CallAfter(self._offer_test_report, radio_name, firmware_path, True, "")

        except Exception as e:
            error_msg = str(e)
            self.log_msg(f"\nERROR: {error_msg}")
            self.log_msg("Radio may need to be power cycled and put back in bootloader mode.")
            self._terminal_state = "failed"
            wx.CallAfter(self._offer_test_report, radio_name, firmware_path, False, error_msg)
        finally:
            self._busy = False
            self.set_buttons(True)

    def _offer_test_report(self, radio_name, firmware_path, success, error_msg):
        log_content = self.log.GetValue()
        show_test_report_dialog(self, radio_name, firmware_path, success, error_msg, log_content)
        if success:
            self._offer_firmware_cleanup(firmware_path)

    def _offer_firmware_cleanup(self, firmware_path):
        """Ask user if they want to delete downloaded firmware files."""
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

            dlg = wx.MessageDialog(self,
                f"Downloaded firmware files are using {size_mb:.1f} MB:\n"
                f"{download_dir}\n\n"
                f"Delete them to free up space?\n"
                f"(You can always re-download later)",
                "Clean Up Downloads",
                wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION)

            if dlg.ShowModal() == wx.ID_YES:
                import shutil
                shutil.rmtree(download_dir, ignore_errors=True)
                self.log_msg("Downloaded firmware files cleaned up.")
            dlg.Destroy()
        except Exception:
            pass

    def on_dry_run(self, event):
        firmware_path = self.file_path.GetValue()
        if not firmware_path:
            wx.MessageBox("Select a firmware file first.", "Error", wx.OK | wx.ICON_ERROR)
            return

        self.log.Clear()
        self.progress.SetValue(0)
        self._busy = True
        self._busy_state = "dryrun"
        self._terminal_state = None
        self.set_buttons(False)
        self._set_hint("dryrun")
        threading.Thread(target=self._dryrun_thread, args=(firmware_path,), daemon=True).start()

    def _dryrun_thread(self, firmware_path):
        try:
            import os
            import hashlib
            import math

            self.log_msg("*** DRY RUN MODE — no serial communication ***")
            self.log_msg("")

            fw_size = os.path.getsize(firmware_path)
            if fw_size > fw.MAX_FIRMWARE_BYTES:
                self.log_msg(f"FAIL: File too large ({fw_size} bytes, max {fw.MAX_FIRMWARE_BYTES})")
                self._terminal_state = "failed"
                return
            with open(firmware_path, "rb") as f:
                firmware = f.read()

            fw_size = len(firmware)
            total_chunks = math.ceil(fw_size / 1024)

            if fw_size < fw.MIN_FIRMWARE_BYTES:
                self.log_msg(f"FAIL: File too small ({fw_size} bytes)")
                self._terminal_state = "failed"
                return
            if total_chunks > fw.MAX_CHUNKS:
                self.log_msg(f"FAIL: Too many chunks ({total_chunks}, max {fw.MAX_CHUNKS})")
                self._terminal_state = "failed"
                return

            sha256 = hashlib.sha256(firmware).hexdigest()
            self.log_msg(f"Firmware: {firmware_path}")
            self.log_msg(f"Size: {fw_size} bytes, {total_chunks} chunks")
            self.log_msg(f"SHA-256: {sha256}")
            self.log_msg("")

            sp = int.from_bytes(firmware[0:4], "little")
            reset = int.from_bytes(firmware[4:8], "little")
            ok_sp = 0x20000000 <= sp <= 0x20100000
            ok_reset = 0x08000000 <= reset <= 0x08100000
            self.log_msg("ARM vector table check:")
            self.log_msg(f"  Stack pointer:  0x{sp:08X} {'(valid)' if ok_sp else '(INVALID)'}")
            self.log_msg(f"  Reset handler:  0x{reset:08X} {'(valid)' if ok_reset else '(INVALID)'}")
            if not ok_sp or not ok_reset:
                self.log_msg("")
                self.log_msg("FAIL: Invalid ARM vector table")
                self._terminal_state = "failed"
                return

            self.log_msg("")
            self.log_msg("Building and verifying all packets...")
            self.set_progress(10)

            for i in range(total_chunks):
                chunk = firmware[i * 1024:(i + 1) * 1024]
                p = fw.build_packet(fw.CMD_UPDATE, i & 0xFF, chunk)
                payload = p[1:-3]
                pkt_crc = (p[-3] << 8) | p[-2]
                if fw.crc16_ccitt(payload) != pkt_crc:
                    self.log_msg(f"FAIL: CRC self-check failed on chunk {i}")
                    self._terminal_state = "failed"
                    return
                self.set_progress(10 + (i + 1) / total_chunks * 90)

            self.log_msg(f"  {total_chunks + 3} packets built, all CRCs verified")
            self.log_msg("")
            self.log_msg("DRY RUN PASSED — firmware file is valid and ready to flash")
            self.set_progress(100)
            self._terminal_state = "complete"

        except Exception as e:
            self.log_msg(f"\nERROR: {e}")
            self._terminal_state = "failed"
        finally:
            self._busy = False
            self.set_buttons(True)

    def on_diag(self, event):
        port = self.port_combo.GetValue()
        if not port:
            wx.MessageBox("Select a serial port.\nClick 'Find Cable...' to detect your programming cable.",
                          "Error", wx.OK | wx.ICON_ERROR)
            return

        self.log.Clear()
        self.progress.SetValue(0)
        self._busy = True
        self._busy_state = "diagnostics"
        self._terminal_state = None
        self.set_buttons(False)
        self._set_hint("diagnostics")
        threading.Thread(target=self._diag_thread, args=(port,), daemon=True).start()

    def _diag_thread(self, port):
        try:
            import time

            self.log_msg(f"Running diagnostics on {port}...")
            self.log_msg("")

            with serial.Serial(
                port=port, baudrate=115200, bytesize=8,
                parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
                timeout=1.0
            ) as ser:
                ser.dtr = True
                ser.rts = True
                time.sleep(0.1)

                self.log_msg(f"  Baud: {ser.baudrate}, DTR: {ser.dtr}, RTS: {ser.rts}")
                self.log_msg(f"  CTS: {ser.cts}, DSR: {ser.dsr}")
                self.log_msg("")

                self.log_msg("Sending CMD_HANDSHAKE...")
                packet = fw.build_packet(fw.CMD_HANDSHAKE, 0, b"BOOTLOADER")
                self.log_msg(f"  TX: {packet.hex()}")
                ser.reset_input_buffer()
                ser.write(packet)
                ser.flush()

                self.set_progress(50)
                time.sleep(1.0)
                avail = ser.in_waiting
                if avail:
                    data = ser.read(min(avail, 128))
                    self.log_msg(f"  RX ({avail} bytes): {data.hex()}")
                    self.log_msg("")
                    self.log_msg("Radio is responding! Flash should work.")
                    self._terminal_state = "complete"
                else:
                    self.log_msg("  RX: no data")
                    self.log_msg("")
                    self.log_msg("Radio did not respond.")
                    self.log_msg("Check: cable, bootloader mode, serial port.")
                    self._terminal_state = "failed"

            self.set_progress(100)

        except Exception as e:
            self.log_msg(f"\nERROR: {e}")
            self._terminal_state = "failed"
        finally:
            self._busy = False
            self.set_buttons(True)


def main():
    app = wx.App()
    frame = FlasherFrame()
    frame.Show()
    apply_theme(frame, "latte")
    frame._update_theme_glyph()
    app.MainLoop()


if __name__ == "__main__":
    main()
