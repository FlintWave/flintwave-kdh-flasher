"""
Dialog windows for the KDH flasher GUI.
About dialog and Test report dialog.

Port discovery and batch flash are now built into the main window's
Handset column; their old wizard dialogs (PortFinderDialog, BatchFlashDialog)
have been removed.
"""

import os
import wx
import wx.adv

from gui_themes import apply_theme_to_dialog


def show_about_dialog(frame):
    """Show the About dialog with version, links, and license."""
    VERSION = "26.05.3"

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

    license_link = wx.adv.HyperlinkCtrl(about_panel,
        label="Licensed under GNU GPL v3.0",
        url="https://www.gnu.org/licenses/gpl-3.0.html")
    about_sizer.Add(license_link, 0, wx.ALIGN_CENTER)

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
        "FlintWave KDH Flasher\n"
        "Copyright (C) 2026 FlintWave Radio Tools\n\n"
        "This program is free software: you can redistribute it and/or modify "
        "it under the terms of the GNU General Public License as published by "
        "the Free Software Foundation, either version 3 of the License, or "
        "(at your option) any later version.\n\n"
        "This program is distributed in the hope that it will be useful, "
        "but WITHOUT ANY WARRANTY; without even the implied warranty of "
        "MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the "
        "GNU General Public License for more details.\n\n"
        "You should have received a copy of the GNU General Public License "
        "along with this program.  If not, see "
        "<https://www.gnu.org/licenses/>.\n\n"
        "The full GPL v3.0 license text ships with the source distribution "
        "in the LICENSE file at the root of the repository."
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
