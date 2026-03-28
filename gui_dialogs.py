"""
Dialog windows for the KDH flasher GUI.
PortFinderDialog, About dialog, and Test report dialog.
"""

import os
import wx
import wx.adv
import serial
import serial.tools.list_ports

from gui_ports import KNOWN_CABLES, FTDI_VID_PID
from gui_themes import apply_theme_to_dialog


class PortFinderDialog(wx.Dialog):
    """Port finder wizard that scans for serial devices."""

    def __init__(self, parent):
        super().__init__(parent, title="Find Programming Cable", size=(520, 370))
        self.SetMinSize((520, 370))
        self.selected_port = None

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
        tip = wx.StaticText(self, label=(
            "Tip: If your cable isn't listed, unplug it, click Rescan, plug it back\n"
            "in, then click Rescan again. The new entry is your cable."
        ))
        tip.SetForegroundColour(wx.Colour(100, 100, 100))
        sizer.Add(tip, 0, wx.LEFT | wx.RIGHT, 10)

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
                self.port_list.SetItemBackgroundColour(idx, wx.Colour(220, 255, 220))

        if auto_select >= 0:
            self.port_list.Select(auto_select)
            self.port_list.Focus(auto_select)
            self.status_text.SetLabel("PC03 cable detected (highlighted in green)")
            self.status_text.SetForegroundColour(wx.Colour(0, 128, 0))
        elif self.ports:
            self.status_text.SetLabel(f"{len(self.ports)} port(s) found")
            self.status_text.SetForegroundColour(wx.Colour(0, 0, 0))
        else:
            self.status_text.SetLabel("No serial ports detected. Is the cable plugged in?")
            self.status_text.SetForegroundColour(wx.Colour(200, 0, 0))

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
    VERSION = "26.03.4"

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
    copy_text.SetForegroundColour(wx.Colour(120, 120, 120))
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

    # Apply current theme to dialog
    apply_theme_to_dialog(frame, dlg, {
        "panels": [dlg, notebook, about_panel, license_panel],
        "about_children": about_panel.GetChildren(),
        "copy_text": copy_text,
        "license_text": license_text,
        "close_btn": close_btn,
    })

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


def show_test_report_dialog(frame, radio_name, firmware_path, success, error_msg):
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
    report_body += "\nAdditional notes:\n"

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
    hint.SetForegroundColour(wx.Colour(120, 120, 120))
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
    dlg.Centre()
    dlg.ShowModal()
    dlg.Destroy()
