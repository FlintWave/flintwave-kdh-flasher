"""
Dialog windows for the KDH flasher GUI.
PortFinderDialog, BatchFlashDialog, About dialog, and Test report dialog.
"""

import os
import threading
import wx
import wx.adv
import serial
import serial.tools.list_ports

import flash_firmware as fw
from gui_ports import KNOWN_CABLES, FTDI_VID_PID
from gui_themes import apply_theme_to_dialog


class PortFinderDialog(wx.Dialog):
    """Port finder wizard that scans for serial devices."""

    def __init__(self, parent):
        super().__init__(parent, title="Find Programming Cable", size=(520, 370))
        self.SetMinSize((520, 370))
        self.selected_port = None
        self._parent_frame = parent

        sizer = wx.BoxSizer(wx.VERTICAL)

        sizer.Add(wx.StaticText(self, label="Detected serial devices:"),
                  0, wx.LEFT | wx.TOP, 10)
        sizer.AddSpacer(5)

        self.port_list = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        self.port_list.InsertColumn(0, "Port", width=120)
        self.port_list.InsertColumn(1, "Cable / Chip", width=160)
        self.port_list.InsertColumn(2, "Serial #", width=100)
        self.port_list.InsertColumn(3, "USB ID", width=90)
        self.port_list.Bind(wx.EVT_LIST_ITEM_SELECTED, self.on_select)
        self.port_list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.on_double_click)
        sizer.Add(self.port_list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 10)

        sizer.AddSpacer(5)

        self.status_text = wx.StaticText(self, label="")
        sizer.Add(self.status_text, 0, wx.LEFT | wx.RIGHT, 10)

        sizer.AddSpacer(5)

        # Tip
        self.tip = wx.StaticText(self, label=(
            "Tip: If your cable isn't listed, unplug it, click Rescan, plug it back\n"
            "in, then click Rescan again. The new entry is your cable."
        ))
        sizer.Add(self.tip, 0, wx.LEFT | wx.RIGHT, 10)

        sizer.AddSpacer(10)

        btn_sizer = wx.BoxSizer(wx.HORIZONTAL)
        rescan_btn = wx.Button(self, label="Rescan")
        rescan_btn.Bind(wx.EVT_BUTTON, self.on_rescan)
        btn_sizer.Add(rescan_btn, 0, wx.RIGHT, 10)
        self.select_btn = wx.Button(self, wx.ID_OK, label="Use Selected")
        self.select_btn.Enable(False)
        btn_sizer.Add(self.select_btn, 0, wx.RIGHT, 10)
        cancel_btn = wx.Button(self, wx.ID_CANCEL, label="Cancel")
        btn_sizer.Add(cancel_btn, 0)
        sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.BOTTOM, 10)

        self.SetSizer(sizer)
        self.scan_ports()
        self.Centre()

        # Apply parent's theme palette to all widgets
        apply_theme_to_dialog(self._parent_frame, self)
        # Soften the tip color using the theme palette
        palette = getattr(self._parent_frame, "current_theme_palette", None)
        if palette:
            self.tip.SetOwnForegroundColour(wx.Colour(*palette[4]))  # subtext1
            self._restyle_status()

    def _palette(self):
        return getattr(self._parent_frame, "current_theme_palette", None)

    def _restyle_status(self):
        """Apply theme-aware accent colors on status_text and the auto-row highlight."""
        palette = self._palette()
        if not palette:
            return
        green = wx.Colour(*palette[5])
        text = wx.Colour(*palette[3])
        subtext1 = wx.Colour(*palette[4])
        # status_text color depends on which message is showing
        label = self.status_text.GetLabel()
        if "detected" in label.lower():
            self.status_text.SetForegroundColour(green)
        elif "no serial" in label.lower():
            # Reuse subtext1 (less alarming red) for the "no ports" message in dark mode
            self.status_text.SetForegroundColour(subtext1)
        else:
            self.status_text.SetForegroundColour(text)

    def scan_ports(self):
        self.port_list.DeleteAllItems()
        self.ports = []
        auto_select = -1

        for p in serial.tools.list_ports.comports():
            vid_pid = (p.vid, p.pid) if p.vid and p.pid else None
            cable = KNOWN_CABLES.get(vid_pid, "")
            usb_id = f"{p.vid:04X}:{p.pid:04X}" if p.vid and p.pid else ""
            sn = (p.serial_number or "")[:8]  # truncate for privacy

            idx = self.port_list.InsertItem(self.port_list.GetItemCount(), p.device)
            self.port_list.SetItem(idx, 1, cable or p.description or "")
            self.port_list.SetItem(idx, 2, sn[:4] + "..." if len(sn) > 4 else sn)
            self.port_list.SetItem(idx, 3, usb_id)
            self.ports.append(p.device)

            if vid_pid == FTDI_VID_PID:
                auto_select = idx
                palette = getattr(self, "_parent_frame", None)
                palette = getattr(palette, "current_theme_palette", None) if palette else None
                if palette:
                    # Use the theme's surface0 tint so the highlight blends with the dark/light bg
                    self.port_list.SetItemBackgroundColour(idx, wx.Colour(*palette[1]))
                else:
                    self.port_list.SetItemBackgroundColour(idx, wx.Colour(220, 255, 220))

        palette = self._palette()
        if auto_select >= 0:
            self.port_list.Select(auto_select)
            self.port_list.Focus(auto_select)
            self.status_text.SetLabel("PC03 cable detected (highlighted)")
            self.status_text.SetForegroundColour(
                wx.Colour(*palette[5]) if palette else wx.Colour(0, 128, 0))
        elif self.ports:
            self.status_text.SetLabel(f"{len(self.ports)} port(s) found")
            self.status_text.SetForegroundColour(
                wx.Colour(*palette[3]) if palette else wx.Colour(0, 0, 0))
        else:
            self.status_text.SetLabel("No serial ports detected. Is the cable plugged in?")
            self.status_text.SetForegroundColour(
                wx.Colour(*palette[4]) if palette else wx.Colour(200, 0, 0))

    def on_rescan(self, event):
        self.select_btn.Enable(False)
        self.selected_port = None
        self.scan_ports()

    def on_select(self, event):
        idx = event.GetIndex()
        if 0 <= idx < len(self.ports):
            self.selected_port = self.ports[idx]
            self.select_btn.Enable(True)

    def on_double_click(self, event):
        idx = event.GetIndex()
        if 0 <= idx < len(self.ports):
            self.selected_port = self.ports[idx]
            self.EndModal(wx.ID_OK)


def show_about_dialog(frame):
    """Show the About dialog with version, links, and license."""
    VERSION = "26.04.2"

    dlg = wx.Dialog(frame, title="About", size=(420, 440),
                    style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
    dlg.SetMinSize((420, 440))
    dlg.SetMaxSize((420, 560))

    notebook = wx.Notebook(dlg)

    # About page
    about_panel = wx.Panel(notebook)
    about_sizer = wx.BoxSizer(wx.VERTICAL)
    about_sizer.AddSpacer(15)

    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon_128.png")
    if os.path.exists(icon_path):
        img = wx.Image(icon_path).Rescale(64, 64, wx.IMAGE_QUALITY_HIGH)
        about_sizer.Add(wx.StaticBitmap(about_panel, bitmap=wx.Bitmap(img)),
                        0, wx.ALIGN_CENTER)
        about_sizer.AddSpacer(10)

    title = wx.StaticText(about_panel, label="KDH Bootloader Firmware Flasher")
    title.SetFont(wx.Font(14, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
    about_sizer.Add(title, 0, wx.ALIGN_CENTER)

    ver = wx.StaticText(about_panel, label=f"Version {VERSION}")
    about_sizer.Add(ver, 0, wx.ALIGN_CENTER | wx.TOP, 5)
    about_sizer.AddSpacer(10)

    desc = wx.StaticText(about_panel,
        label="Flash .kdhx firmware to BTECH, Baofeng, Radtel,\n"
              "and other KDH bootloader radios from any OS.",
        style=wx.ALIGN_CENTRE_HORIZONTAL)
    about_sizer.Add(desc, 0, wx.ALIGN_CENTER)
    about_sizer.AddSpacer(15)

    copy_text = wx.StaticText(about_panel, label="(c) 2026 FlintWave Radio Tools")
    about_sizer.Add(copy_text, 0, wx.ALIGN_CENTER)
    about_sizer.AddSpacer(5)

    gh_link = wx.adv.HyperlinkCtrl(about_panel,
        label="GitHub: FlintWave/flintwave-kdh-flasher",
        url="https://github.com/FlintWave/flintwave-kdh-flasher")
    about_sizer.Add(gh_link, 0, wx.ALIGN_CENTER)
    about_sizer.AddSpacer(2)

    cb_link = wx.adv.HyperlinkCtrl(about_panel,
        label="Codeberg: flintwaveradio/flintwave-kdh-flasher",
        url="https://codeberg.org/flintwaveradio/flintwave-kdh-flasher")
    about_sizer.Add(cb_link, 0, wx.ALIGN_CENTER)

    about_panel.SetSizer(about_sizer)
    notebook.AddPage(about_panel, "About")

    # License page
    license_panel = wx.Panel(notebook)
    license_sizer = wx.BoxSizer(wx.VERTICAL)
    license_text = wx.TextCtrl(license_panel,
        style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP)
    license_text.SetFont(wx.Font(9, wx.FONTFAMILY_TELETYPE,
                                 wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
    license_text.SetValue(
        "MIT License\n\n"
        "Copyright (c) 2026 FlintWave Radio Tools\n\n"
        "Permission is hereby granted, free of charge, to any person obtaining a copy "
        "of this software and associated documentation files (the \"Software\"), to deal "
        "in the Software without restriction, including without limitation the rights "
        "to use, copy, modify, merge, publish, distribute, sublicense, and/or sell "
        "copies of the Software, and to permit persons to whom the Software is "
        "furnished to do so, subject to the following conditions:\n\n"
        "The above copyright notice and this permission notice shall be included in all "
        "copies or substantial portions of the Software.\n\n"
        "THE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR "
        "IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, "
        "FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE "
        "AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER "
        "LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, "
        "OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE "
        "SOFTWARE."
    )
    license_sizer.Add(license_text, 1, wx.EXPAND | wx.ALL, 10)
    license_panel.SetSizer(license_sizer)
    notebook.AddPage(license_panel, "License")

    dlg_sizer = wx.BoxSizer(wx.VERTICAL)
    dlg_sizer.Add(notebook, 1, wx.EXPAND | wx.ALL, 5)
    close_btn = wx.Button(dlg, wx.ID_CLOSE, "Close")
    close_btn.Bind(wx.EVT_BUTTON, lambda e: dlg.EndModal(wx.ID_CLOSE))
    dlg_sizer.Add(close_btn, 0, wx.ALIGN_CENTER | wx.BOTTOM, 10)
    dlg.SetSizer(dlg_sizer)

    # Apply current theme to dialog (recursive walk handles all widgets)
    apply_theme_to_dialog(frame, dlg)

    # Subtle copyright color via the palette's subtext1
    palette = getattr(frame, "current_theme_palette", None)
    if palette:
        copy_text.SetOwnForegroundColour(wx.Colour(*palette[4]))
        license_text.SetOwnForegroundColour(wx.Colour(*palette[5]))  # green for that terminal feel

    # Apply current font size
    ui_font = wx.Font(frame.font_size, wx.FONTFAMILY_DEFAULT,
                      wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
    mono_font = wx.Font(frame.font_size, wx.FONTFAMILY_TELETYPE,
                        wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
    title.SetFont(wx.Font(frame.font_size + 4, wx.FONTFAMILY_DEFAULT,
                          wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
    ver.SetFont(ui_font)
    desc.SetFont(ui_font)
    copy_text.SetFont(ui_font)
    license_text.SetFont(mono_font)

    dlg.Centre()
    dlg.ShowModal()
    dlg.Destroy()


def show_test_report_dialog(frame, radio_name, firmware_path, success, error_msg, log_content=""):
    """Show the test report submission dialog after a flash attempt."""
    import platform
    import urllib.parse

    status = "SUCCESS" if success else "FAILED"
    fw_file = os.path.basename(firmware_path) if firmware_path else "unknown"

    report_body = (
        f"Radio: {radio_name}\n"
        f"Firmware: {fw_file}\n"
        f"Result: {status}\n"
        f"OS: {platform.system()} {platform.release()}\n"
        f"Python: {platform.python_version()}\n"
    )
    if error_msg:
        report_body += f"Error: {error_msg}\n"
    report_body += "\nAdditional notes:\n\n"
    if log_content:
        # Truncate log to last 2000 chars to keep URL manageable
        truncated = log_content[-2000:] if len(log_content) > 2000 else log_content
        report_body += f"--- Log ---\n{truncated}\n"

    title = f"Test Report: {radio_name} — {status}"

    dlg = wx.Dialog(frame, title="Submit Test Report", size=(520, 500))
    dlg.SetMinSize((480, 400))
    dlg.SetMaxSize((600, 600))
    sizer = wx.BoxSizer(wx.VERTICAL)

    ui_font = wx.Font(11, wx.FONTFAMILY_DEFAULT,
                      wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
    mono_font = wx.Font(10, wx.FONTFAMILY_TELETYPE,
                        wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)

    if success:
        msg = "Flash completed successfully!\nWould you like to submit a test report?"
    else:
        msg = "Flash failed.\nWould you like to submit a report to help us debug?"

    msg_text = wx.StaticText(dlg, label=msg)
    msg_text.SetFont(ui_font)
    sizer.Add(msg_text, 0, wx.LEFT | wx.TOP | wx.RIGHT, 15)

    preview = wx.TextCtrl(dlg, value=report_body,
                          style=wx.TE_MULTILINE)
    preview.SetFont(mono_font)
    preview.SetInsertionPointEnd()
    sizer.Add(preview, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 15)

    sizer.AddSpacer(10)

    hint = wx.StaticText(dlg, label="You can also email reports to flintwave@tuta.com")
    hint.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT,
                          wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
    sizer.Add(hint, 0, wx.ALIGN_CENTER)

    sizer.AddSpacer(10)

    btn_sizer = wx.BoxSizer(wx.HORIZONTAL)

    submit_btn = wx.Button(dlg, label="Submit")
    submit_btn.SetFont(ui_font)
    submit_btn.Bind(wx.EVT_BUTTON, lambda e: (
        wx.LaunchDefaultBrowser(
            "https://github.com/FlintWave/flintwave-kdh-flasher/issues/new?"
            + urllib.parse.urlencode({
                "title": title,
                "body": preview.GetValue(),
                "labels": "test-report"
            })
        ),
        dlg.EndModal(wx.ID_OK)
    ))
    btn_sizer.Add(submit_btn, 0, wx.RIGHT, 8)

    skip_btn = wx.Button(dlg, wx.ID_CANCEL, label="Skip")
    skip_btn.SetFont(ui_font)
    btn_sizer.Add(skip_btn, 0)

    sizer.Add(btn_sizer, 0, wx.ALIGN_CENTER | wx.BOTTOM, 15)

    dlg.SetSizer(sizer)

    # Apply parent theme to all widgets in the dialog
    apply_theme_to_dialog(frame, dlg)
    palette = getattr(frame, "current_theme_palette", None)
    if palette:
        hint.SetOwnForegroundColour(wx.Colour(*palette[4]))  # subtext1

    dlg.Centre()
    dlg.ShowModal()
    dlg.Destroy()


class BatchFlashDialog(wx.Dialog):
    """Sequential batch flash for OEM-style same-model handsets.

    Workflow:
      1. Scan: enumerate serial ports and probe each with CMD_HANDSHAKE.
         Ports that answer correctly are marked Ready and pre-checked.
      2. User toggles checkboxes / Select All / Select None.
      3. Flash All: sequentially flash the same firmware to each checked port.
         Stop on first failure; user is prompted to skip + continue or stop.
    """

    STATUS_UNKNOWN = "Unknown"
    STATUS_READY = "Ready"
    STATUS_NO_RESP = "No response"
    STATUS_BUSY = "Busy/locked"
    STATUS_FLASHING = "Flashing…"
    STATUS_DONE = "Done"
    STATUS_FAILED = "Failed"
    STATUS_SKIPPED = "Skipped"

    def __init__(self, parent, firmware_path, radio):
        title = f"Batch Flash — {radio['name']}" if radio else "Batch Flash"
        super().__init__(parent, title=title, size=(720, 560),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.SetMinSize((640, 480))

        self._parent_frame = parent
        self.firmware_path = firmware_path
        self.radio = radio
        self.ports = []         # list of dicts: device, cable, vid_pid, status, progress
        self._busy = False
        self._continue_event = threading.Event()
        self._user_choice = None  # "continue" or "stop"
        self._firmware_bytes = None

        sizer = wx.BoxSizer(wx.VERTICAL)

        header = wx.StaticText(self,
            label=f"Radio: {radio['name'] if radio else '(none)'}")
        header.SetFont(wx.Font(11, wx.FONTFAMILY_DEFAULT,
                               wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        sizer.Add(header, 0, wx.LEFT | wx.TOP | wx.RIGHT, 12)

        sub = wx.StaticText(self,
            label=f"Firmware: {os.path.basename(firmware_path) if firmware_path else '(none selected)'}")
        sizer.Add(sub, 0, wx.LEFT | wx.RIGHT, 12)

        if radio:
            keys_line = (
                f"Bootloader keys: {radio.get('bootloader_keys', '?')}"
                f"   Connector: {radio.get('connector', '?')}"
            )
            self.keys_text = wx.StaticText(self, label=keys_line)
            sizer.Add(self.keys_text, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        else:
            self.keys_text = None

        instr = wx.StaticText(self, label=(
            "Put each radio in bootloader mode (hold the bootloader keys, "
            "turn the power knob on; green Rx LED lights up), then click "
            "Scan. Ready radios are pre-checked."
        ))
        sizer.Add(instr, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        self._instr = instr

        # Port list with checkboxes
        self.port_list = wx.ListCtrl(self, style=wx.LC_REPORT)
        self._checkboxes_supported = False
        try:
            self.port_list.EnableCheckBoxes(True)
            self._checkboxes_supported = True
        except Exception:
            self._checkboxes_supported = False
        self.port_list.InsertColumn(0, "Port", width=120)
        self.port_list.InsertColumn(1, "Cable / Chip", width=200)
        self.port_list.InsertColumn(2, "Status", width=140)
        self.port_list.InsertColumn(3, "Progress", width=100)
        sizer.Add(self.port_list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)

        sel_row = wx.BoxSizer(wx.HORIZONTAL)
        self.scan_btn = wx.Button(self, label="Scan / Probe")
        self.scan_btn.Bind(wx.EVT_BUTTON, self.on_scan)
        sel_row.Add(self.scan_btn, 0, wx.RIGHT, 8)

        self.select_all_btn = wx.Button(self, label="Select All")
        self.select_all_btn.Bind(wx.EVT_BUTTON, lambda e: self._set_all_checked(True))
        sel_row.Add(self.select_all_btn, 0, wx.RIGHT, 8)

        self.select_none_btn = wx.Button(self, label="Select None")
        self.select_none_btn.Bind(wx.EVT_BUTTON, lambda e: self._set_all_checked(False))
        sel_row.Add(self.select_none_btn, 0, wx.RIGHT, 8)

        sel_row.AddStretchSpacer(1)

        self.summary = wx.StaticText(self, label="0 selected / 0 detected")
        sel_row.Add(self.summary, 0, wx.ALIGN_CENTER_VERTICAL)
        sizer.Add(sel_row, 0, wx.EXPAND | wx.ALL, 12)

        self.gauge = wx.Gauge(self, range=100)
        sizer.Add(self.gauge, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)

        sizer.AddSpacer(6)

        self.log = wx.TextCtrl(self,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL,
            size=(-1, 120))
        self.log.SetFont(wx.Font(9, wx.FONTFAMILY_TELETYPE,
                                 wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        sizer.Add(self.log, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        self.flash_btn = wx.Button(self, label="Flash All Selected")
        flash_font = wx.Font(11, wx.FONTFAMILY_DEFAULT,
                             wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
        self.flash_btn.SetFont(flash_font)
        self.flash_btn.Bind(wx.EVT_BUTTON, self.on_flash_all)
        btn_row.Add(self.flash_btn, 0, wx.RIGHT, 8)

        self.close_btn = wx.Button(self, wx.ID_CANCEL, label="Close")
        btn_row.Add(self.close_btn, 0)
        sizer.Add(btn_row, 0, wx.ALIGN_CENTER | wx.ALL, 12)

        self.SetSizer(sizer)
        self.Centre()

        if self._checkboxes_supported:
            self.port_list.Bind(wx.EVT_LIST_ITEM_CHECKED, self._on_check_changed)
            self.port_list.Bind(wx.EVT_LIST_ITEM_UNCHECKED, self._on_check_changed)
        else:
            self._instr.SetLabel(self._instr.GetLabel() + "\n"
                "(Your wxPython has no checkbox column; use Ctrl/Shift+click "
                "to multi-select rows.)")
            self.port_list.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_check_changed)
            self.port_list.Bind(wx.EVT_LIST_ITEM_DESELECTED, self._on_check_changed)

        wx.CallAfter(self.on_scan, None)
        apply_theme_to_dialog(self._parent_frame, self)

    # --- Scanning -----------------------------------------------------

    def on_scan(self, event):
        if self._busy:
            return
        self.scan_btn.Disable()
        self.flash_btn.Disable()
        self.port_list.DeleteAllItems()
        self.ports = []
        self._log("Scanning serial ports…")

        discovered = []
        for p in serial.tools.list_ports.comports():
            vid_pid = (p.vid, p.pid) if p.vid and p.pid else None
            cable = KNOWN_CABLES.get(vid_pid, p.description or "")
            discovered.append({
                "device": p.device,
                "cable": cable,
                "vid_pid": vid_pid,
                "status": self.STATUS_UNKNOWN,
                "progress": "",
            })

        if not discovered:
            self._log("  No serial ports found. Is the cable plugged in?")
            self.scan_btn.Enable()
            self.flash_btn.Enable()
            self._refresh_summary()
            return

        for entry in discovered:
            self._insert_row(entry)
        self.ports = discovered
        self._refresh_summary()

        threading.Thread(target=self._probe_thread, daemon=True).start()

    def _probe_thread(self):
        for idx, entry in enumerate(self.ports):
            wx.CallAfter(self._set_status, idx, "Probing…")
            ready = fw.probe_port(entry["device"], timeout=1.5)
            new_status = self.STATUS_READY if ready else self.STATUS_NO_RESP
            wx.CallAfter(self._set_status, idx, new_status)
            wx.CallAfter(self._set_check, idx, ready)
        wx.CallAfter(self._scan_complete)

    def _scan_complete(self):
        self.scan_btn.Enable()
        self.flash_btn.Enable()
        ready_count = sum(1 for p in self.ports if p["status"] == self.STATUS_READY)
        if ready_count:
            self._log(f"  {ready_count} radio(s) ready in bootloader mode.")
        else:
            self._log(
                "  No radios responded. Make sure each radio is in bootloader "
                "mode (green Rx LED on) before scanning."
            )
        self._refresh_summary()

    # --- List helpers -------------------------------------------------

    def _insert_row(self, entry):
        idx = self.port_list.InsertItem(self.port_list.GetItemCount(), entry["device"])
        self.port_list.SetItem(idx, 1, entry["cable"])
        self.port_list.SetItem(idx, 2, entry["status"])
        self.port_list.SetItem(idx, 3, entry["progress"])

    def _set_status(self, idx, status):
        if 0 <= idx < len(self.ports):
            self.ports[idx]["status"] = status
            self.port_list.SetItem(idx, 2, status)
            self._refresh_summary()

    def _set_progress(self, idx, text):
        if 0 <= idx < len(self.ports):
            self.ports[idx]["progress"] = text
            self.port_list.SetItem(idx, 3, text)

    def _set_check(self, idx, checked):
        if not (0 <= idx < self.port_list.GetItemCount()):
            return
        if self._checkboxes_supported:
            self.port_list.CheckItem(idx, checked)
        else:
            self.port_list.Select(idx, on=1 if checked else 0)
        self._refresh_summary()

    def _is_checked(self, idx):
        if self._checkboxes_supported:
            return self.port_list.IsItemChecked(idx)
        return self.port_list.IsSelected(idx)

    def _set_all_checked(self, checked):
        for idx in range(self.port_list.GetItemCount()):
            self._set_check(idx, checked)

    def _on_check_changed(self, event):
        self._refresh_summary()
        if event:
            event.Skip()

    def _refresh_summary(self):
        total = self.port_list.GetItemCount()
        sel = sum(1 for i in range(total) if self._is_checked(i))
        self.summary.SetLabel(f"{sel} selected / {total} detected")

    def _log(self, msg):
        wx.CallAfter(self.log.AppendText, msg + "\n")

    # --- Flashing -----------------------------------------------------

    def on_flash_all(self, event):
        if self._busy:
            return
        if not self.firmware_path or not os.path.exists(self.firmware_path):
            wx.MessageBox(
                "Select a firmware file in the main window before opening "
                "Batch Flash.", "No firmware", wx.OK | wx.ICON_ERROR, parent=self)
            return

        selected_idxs = [i for i in range(self.port_list.GetItemCount())
                         if self._is_checked(i)]
        if not selected_idxs:
            wx.MessageBox("Check at least one radio to flash.",
                          "Nothing selected", wx.OK | wx.ICON_INFORMATION,
                          parent=self)
            return

        confirm = wx.MessageDialog(self,
            f"Flash {len(selected_idxs)} radio(s) sequentially with\n"
            f"{os.path.basename(self.firmware_path)}?\n\n"
            "Each radio must remain in bootloader mode and connected for "
            "its turn. Do not unplug or power off any radio mid-flash.",
            "Confirm Batch Flash", wx.YES_NO | wx.ICON_WARNING)
        if confirm.ShowModal() != wx.ID_YES:
            confirm.Destroy()
            return
        confirm.Destroy()

        try:
            with open(self.firmware_path, "rb") as f:
                self._firmware_bytes = f.read()
            fw.validate_firmware(self._firmware_bytes, self.firmware_path)
        except Exception as e:
            wx.MessageBox(f"Firmware validation failed:\n{e}",
                          "Invalid firmware", wx.OK | wx.ICON_ERROR, parent=self)
            return

        self._busy = True
        self.scan_btn.Disable()
        self.flash_btn.Disable()
        self.select_all_btn.Disable()
        self.select_none_btn.Disable()
        self.gauge.SetValue(0)

        threading.Thread(target=self._flash_thread, args=(selected_idxs,),
                         daemon=True).start()

    def _flash_thread(self, selected_idxs):
        total = len(selected_idxs)
        completed = 0
        succeeded = 0
        failed = 0
        skipped = 0
        try:
            for n, idx in enumerate(selected_idxs):
                entry = self.ports[idx]
                port = entry["device"]
                wx.CallAfter(self._set_status, idx, self.STATUS_FLASHING)
                wx.CallAfter(self._set_progress, idx, "0%")
                self._log(f"\n[{n + 1}/{total}] Flashing {port} ({entry['cable']})…")

                def log_cb(msg, _idx=idx):
                    self._log(f"  [{self.ports[_idx]['device']}] {msg}")

                def progress_cb(pct, _idx=idx):
                    pct_int = int(pct)
                    wx.CallAfter(self._set_progress, _idx, f"{pct_int}%")

                try:
                    fw.flash_to_port(port, self._firmware_bytes,
                                     log_cb=log_cb, progress_cb=progress_cb)
                except Exception as e:
                    failed += 1
                    wx.CallAfter(self._set_status, idx, self.STATUS_FAILED)
                    wx.CallAfter(self._set_progress, idx, "—")
                    self._log(f"  ERROR: {e}")
                    completed += 1
                    self._update_gauge(completed, total)

                    if n < total - 1:
                        if not self._prompt_continue(port, str(e)):
                            self._log("Batch stopped by user.")
                            for skip_idx in selected_idxs[n + 1:]:
                                wx.CallAfter(self._set_status, skip_idx,
                                             self.STATUS_SKIPPED)
                                skipped += 1
                            break
                        self._log("Continuing with next radio…")
                else:
                    succeeded += 1
                    wx.CallAfter(self._set_status, idx, self.STATUS_DONE)
                    wx.CallAfter(self._set_progress, idx, "100%")
                    completed += 1
                    self._update_gauge(completed, total)

        finally:
            self._log(
                f"\nBatch finished: {succeeded} succeeded, "
                f"{failed} failed, {skipped} skipped."
            )
            wx.CallAfter(self._batch_finished)

    def _update_gauge(self, completed, total):
        if total <= 0:
            return
        wx.CallAfter(self.gauge.SetValue, int(completed * 100 / total))

    def _prompt_continue(self, port, err):
        """Block the worker thread until the user picks Continue or Stop."""
        self._user_choice = None
        self._continue_event.clear()
        wx.CallAfter(self._show_continue_dialog, port, err)
        self._continue_event.wait()
        return self._user_choice == "continue"

    def _show_continue_dialog(self, port, err):
        dlg = wx.MessageDialog(self,
            f"Flashing {port} failed:\n\n{err}\n\n"
            "Continue with the next selected radio?",
            "Batch Flash Failure",
            wx.YES_NO | wx.ICON_WARNING)
        result = dlg.ShowModal()
        dlg.Destroy()
        self._user_choice = "continue" if result == wx.ID_YES else "stop"
        self._continue_event.set()

    def _batch_finished(self):
        self._busy = False
        self.scan_btn.Enable()
        self.flash_btn.Enable()
        self.select_all_btn.Enable()
        self.select_none_btn.Enable()
