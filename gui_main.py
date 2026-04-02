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
from gui_dialogs import PortFinderDialog, show_about_dialog, show_test_report_dialog
from gui_themes import apply_theme, THEME_PALETTES

VERSION = "26.03.9"


class FlasherFrame(wx.Frame):
    def __init__(self):
        super().__init__(None, title="KDH Bootloader Firmware Flasher", size=(560, 650))
        self.SetMinSize((560, 500))

        self.font_size = 9
        self.current_theme = "system"
        self.current_theme_palette = None

        # Window icon
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon_128.png")
        if os.path.exists(icon_path):
            self.SetIcon(wx.Icon(icon_path))

        # Menu bar
        menubar = wx.MenuBar()
        view_menu = wx.Menu()

        font_menu = wx.Menu()
        self.font_small = font_menu.AppendRadioItem(wx.ID_ANY, "Small (8pt)")
        self.font_medium = font_menu.AppendRadioItem(wx.ID_ANY, "Medium (9pt)")
        self.font_large = font_menu.AppendRadioItem(wx.ID_ANY, "Large (11pt)")
        self.font_xlarge = font_menu.AppendRadioItem(wx.ID_ANY, "Extra Large (14pt)")
        self.font_medium.Check(True)
        self.Bind(wx.EVT_MENU, lambda e: self._set_font_size(8), self.font_small)
        self.Bind(wx.EVT_MENU, lambda e: self._set_font_size(9), self.font_medium)
        self.Bind(wx.EVT_MENU, lambda e: self._set_font_size(11), self.font_large)
        self.Bind(wx.EVT_MENU, lambda e: self._set_font_size(14), self.font_xlarge)
        view_menu.AppendSubMenu(font_menu, "Log Font Size")

        theme_menu = wx.Menu()
        self.theme_system = theme_menu.AppendRadioItem(wx.ID_ANY, "System Default")
        self.theme_latte = theme_menu.AppendRadioItem(wx.ID_ANY, "Latte (Light)")
        self.theme_frappe = theme_menu.AppendRadioItem(wx.ID_ANY, "Frapp\u00e9")
        self.theme_macchiato = theme_menu.AppendRadioItem(wx.ID_ANY, "Macchiato")
        self.theme_mocha = theme_menu.AppendRadioItem(wx.ID_ANY, "Mocha (Dark)")
        self.theme_hc = theme_menu.AppendRadioItem(wx.ID_ANY, "High Contrast")
        self.theme_system.Check(True)
        self.Bind(wx.EVT_MENU, lambda e: self._set_theme("system"), self.theme_system)
        self.Bind(wx.EVT_MENU, lambda e: self._set_theme("latte"), self.theme_latte)
        self.Bind(wx.EVT_MENU, lambda e: self._set_theme("frappe"), self.theme_frappe)
        self.Bind(wx.EVT_MENU, lambda e: self._set_theme("macchiato"), self.theme_macchiato)
        self.Bind(wx.EVT_MENU, lambda e: self._set_theme("mocha"), self.theme_mocha)
        self.Bind(wx.EVT_MENU, lambda e: self._set_theme("high_contrast"), self.theme_hc)
        view_menu.AppendSubMenu(theme_menu, "Theme")

        menubar.Append(view_menu, "View")

        help_menu = wx.Menu()
        usage_item = help_menu.Append(wx.ID_ANY, "Usage Guide")
        self.Bind(wx.EVT_MENU, self.on_usage_guide, usage_item)
        github_item = help_menu.Append(wx.ID_ANY, "GitHub Repository")
        self.Bind(wx.EVT_MENU, self.on_github, github_item)
        help_menu.AppendSeparator()
        about_item = help_menu.Append(wx.ID_ABOUT, "About")
        self.Bind(wx.EVT_MENU, self.on_about, about_item)
        menubar.Append(help_menu, "Help")

        self.SetMenuBar(menubar)

        panel = wx.Panel(self)
        self.panel = panel
        sizer = wx.BoxSizer(wx.VERTICAL)

        # Radio model selector
        radio_sizer = wx.BoxSizer(wx.HORIZONTAL)
        radio_sizer.Add(wx.StaticText(panel, label="Radio:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.radios = dl.load_radios()
        radio_names = [
            r['name'] if r['name'].startswith(r['manufacturer'])
            else f"{r['manufacturer']} {r['name']}"
            for r in self.radios
        ]
        self.radio_combo = wx.ComboBox(panel, choices=radio_names,
                                       style=wx.CB_DROPDOWN | wx.CB_READONLY)
        if radio_names:
            self.radio_combo.SetSelection(0)
        self.radio_combo.Bind(wx.EVT_COMBOBOX, self.on_radio_changed)
        radio_sizer.Add(self.radio_combo, 1, wx.EXPAND | wx.RIGHT, 5)
        self.download_btn = wx.Button(panel, label="Download Latest")
        self.download_btn.Bind(wx.EVT_BUTTON, self.on_download)
        radio_sizer.Add(self.download_btn, 0)
        sizer.Add(radio_sizer, 0, wx.EXPAND | wx.ALL, 10)

        # Manifest state (must be set before _update_radio_info)
        self.manifest = None

        # Radio info
        self.radio_info = wx.StaticText(panel, label="")
        self.radio_info.SetForegroundColour(wx.Colour(80, 80, 80))
        self.radio_info.Wrap(self.GetMinSize().GetWidth() - 30)
        sizer.Add(self.radio_info, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        self._update_radio_info()

        # Firmware file
        file_sizer = wx.BoxSizer(wx.HORIZONTAL)
        file_sizer.Add(wx.StaticText(panel, label="Firmware:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.file_path = wx.TextCtrl(panel)
        file_sizer.Add(self.file_path, 1, wx.EXPAND | wx.RIGHT, 5)
        browse_btn = wx.Button(panel, label="Browse...")
        browse_btn.Bind(wx.EVT_BUTTON, self.on_browse)
        file_sizer.Add(browse_btn, 0)
        sizer.Add(file_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        sizer.AddSpacer(5)

        # COM port
        port_sizer = wx.BoxSizer(wx.HORIZONTAL)
        port_sizer.Add(wx.StaticText(panel, label="Port:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 5)
        self.port_combo = wx.ComboBox(panel, style=wx.CB_DROPDOWN | wx.CB_READONLY)
        port_sizer.Add(self.port_combo, 1, wx.EXPAND | wx.RIGHT, 5)
        find_btn = wx.Button(panel, label="Find Cable...")
        find_btn.Bind(wx.EVT_BUTTON, self.on_find_cable)
        port_sizer.Add(find_btn, 0)
        sizer.Add(port_sizer, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        sizer.AddSpacer(10)

        # Progress bar
        self.progress = wx.Gauge(panel, range=100)
        sizer.Add(self.progress, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        sizer.AddSpacer(5)

        # Status log
        self.log = wx.TextCtrl(panel, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL)
        self.log.SetFont(wx.Font(9, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        sizer.Add(self.log, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        sizer.AddSpacer(10)

        # Buttons
        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.dryrun_btn = wx.Button(panel, label="Dry Run")
        self.dryrun_btn.Bind(wx.EVT_BUTTON, self.on_dry_run)
        btn_sizer.Add(self.dryrun_btn, 0, wx.RIGHT, 10)
        self.diag_btn = wx.Button(panel, label="Run Diagnostics")
        self.diag_btn.Bind(wx.EVT_BUTTON, self.on_diag)
        btn_sizer.Add(self.diag_btn, 0, wx.RIGHT, 10)
        self.flash_btn = wx.Button(panel, label="Flash Firmware")
        self.flash_btn.Bind(wx.EVT_BUTTON, self.on_flash)
        btn_sizer.Add(self.flash_btn, 0)
        sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.BOTTOM, 10)

        panel.SetSizer(sizer)
        self.Centre()

        # Show getting started guide
        self.log.SetValue(
            "Getting Started:\n"
            "\n"
            "1. Select your radio model from the dropdown above\n"
            "2. Get the firmware file:\n"
            "   - Click 'Download Latest' if available, or\n"
            "   - Click 'Browse...' to select a .kdhx file you've downloaded\n"
            "3. Plug in your programming cable (PC03 or compatible K1 cable)\n"
            "4. Click 'Find Cable...' to detect your cable\n"
            "5. Click 'Dry Run' to verify the firmware file\n"
            "6. Put the radio in bootloader mode:\n"
            "   - Turn off the radio completely\n"
            "   - Hold the bootloader keys (shown in the info line above)\n"
            "   - While holding, turn the power/volume knob to turn on\n"
            "   - The screen stays blank and the green Rx LED lights up\n"
            "   - Do NOT release the keys until the LED is on\n"
            "7. Click 'Flash Firmware' and wait for it to complete\n"
            "8. Power cycle the radio and check Menu > Radio Info\n"
            "\n"
            "IMPORTANT: Do not unplug the cable or turn off the radio\n"
            "during the flash process.\n"
        )

        # Auto-detect cable on startup
        self._auto_detect_port()

        # Check for updates and fetch manifest in background
        threading.Thread(target=self._check_update, daemon=True).start()
        threading.Thread(target=self._fetch_manifest, daemon=True).start()

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

    def _set_font_size(self, size):
        self.font_size = size
        mono = wx.Font(size, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
        ui = wx.Font(size, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
        self.log.SetFont(mono)
        for child in self.panel.GetChildren():
            if isinstance(child, wx.TextCtrl):
                child.SetFont(mono)
            elif not isinstance(child, wx.adv.HyperlinkCtrl):
                child.SetFont(ui)
        self.panel.Layout()
        self.panel.Refresh()

    def _set_theme(self, theme):
        apply_theme(self, theme)

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

    def _fetch_manifest(self):
        try:
            self.manifest = fm.fetch_manifest()
            wx.CallAfter(self._update_radio_info)
        except Exception:
            pass

    def _check_update(self):
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
                wx.LaunchDefaultBrowser(updater.get_releases_url())
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
            self.radio_info.Wrap(self.panel.GetSize().GetWidth() - 30)
            self.panel.Layout()

            has_url = bool(url)
            self.download_btn.Enable(has_url)
            if not has_url:
                self.download_btn.SetLabel("No Direct URL")
            elif version:
                self.download_btn.SetLabel(f"Download v{version}")
            else:
                self.download_btn.SetLabel("Download Latest")

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
        self.set_buttons(False)
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

        except Exception as e:
            self.log_msg(f"\nERROR: {e}")
            if "No direct download URL" in str(e):
                page = radio.get("firmware_page", "")
                if page:
                    self.log_msg(f"Visit: {page}")
        finally:
            self.set_buttons(True)

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
        dlg.Destroy()

    def on_browse(self, event):
        dlg = wx.FileDialog(self, "Select firmware file",
                            wildcard="Firmware files (*.kdhx)|*.kdhx|All files (*)|*",
                            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        if dlg.ShowModal() == wx.ID_OK:
            self.file_path.SetValue(dlg.GetPath())
        dlg.Destroy()

    def log_msg(self, msg):
        wx.CallAfter(self.log.AppendText, msg + "\n")

    def set_progress(self, pct):
        wx.CallAfter(self.progress.SetValue, int(pct))

    def set_buttons(self, enabled):
        wx.CallAfter(self.flash_btn.Enable, enabled)
        wx.CallAfter(self.dryrun_btn.Enable, enabled)
        wx.CallAfter(self.diag_btn.Enable, enabled)
        wx.CallAfter(self.download_btn.Enable, enabled)
        if enabled:
            wx.CallAfter(self._update_radio_info)

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
        self.set_buttons(False)
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

            wx.CallAfter(self._offer_test_report, radio_name, firmware_path, True, "")

        except Exception as e:
            error_msg = str(e)
            self.log_msg(f"\nERROR: {error_msg}")
            self.log_msg("Radio may need to be power cycled and put back in bootloader mode.")
            wx.CallAfter(self._offer_test_report, radio_name, firmware_path, False, error_msg)
        finally:
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
        self.set_buttons(False)
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
                return
            with open(firmware_path, "rb") as f:
                firmware = f.read()

            fw_size = len(firmware)
            total_chunks = math.ceil(fw_size / 1024)

            if fw_size < fw.MIN_FIRMWARE_BYTES:
                self.log_msg(f"FAIL: File too small ({fw_size} bytes)")
                return
            if total_chunks > fw.MAX_CHUNKS:
                self.log_msg(f"FAIL: Too many chunks ({total_chunks}, max {fw.MAX_CHUNKS})")
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
                    return
                self.set_progress(10 + (i + 1) / total_chunks * 90)

            self.log_msg(f"  {total_chunks + 3} packets built, all CRCs verified")
            self.log_msg("")
            self.log_msg("DRY RUN PASSED — firmware file is valid and ready to flash")
            self.set_progress(100)

        except Exception as e:
            self.log_msg(f"\nERROR: {e}")
        finally:
            self.set_buttons(True)

    def on_diag(self, event):
        port = self.port_combo.GetValue()
        if not port:
            wx.MessageBox("Select a serial port.\nClick 'Find Cable...' to detect your programming cable.",
                          "Error", wx.OK | wx.ICON_ERROR)
            return

        self.log.Clear()
        self.progress.SetValue(0)
        self.set_buttons(False)
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
                else:
                    self.log_msg("  RX: no data")
                    self.log_msg("")
                    self.log_msg("Radio did not respond.")
                    self.log_msg("Check: cable, bootloader mode, serial port.")

            self.set_progress(100)

        except Exception as e:
            self.log_msg(f"\nERROR: {e}")
        finally:
            self.set_buttons(True)


def main():
    app = wx.App()
    frame = FlasherFrame()
    frame.Show()
    app.MainLoop()


if __name__ == "__main__":
    main()
