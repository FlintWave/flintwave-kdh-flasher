"""
Dialog windows for FlintWave Flash.
About dialog and Test report dialog.

Port discovery and batch flash are now built into the main window's
Handset column; their old wizard dialogs (PortFinderDialog, BatchFlashDialog)
have been removed.
"""

import os
import wx
import wx.adv

from gui_themes import apply_theme_to_dialog
from i18n import t, is_rtl


def _apply_direction(window):
    """Mirror the dialog's layout direction with the active language."""
    try:
        window.SetLayoutDirection(
            wx.Layout_RightToLeft if is_rtl() else wx.Layout_LeftToRight
        )
    except Exception:
        pass


def show_about_dialog(frame):
    """Show the About dialog with version, links, and license."""
    VERSION = "26.05.3"

    dlg = wx.Dialog(frame, title=t("dialog.about.title"), size=(420, 440),
                    style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
    dlg.SetMinSize((420, 440))
    dlg.SetMaxSize((420, 560))
    _apply_direction(dlg)

    notebook = wx.Notebook(dlg)
    _apply_direction(notebook)

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

    title = wx.StaticText(about_panel, label=t("app.title"))
    title.SetFont(wx.Font(14, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
    about_sizer.Add(title, 0, wx.ALIGN_CENTER)

    ver = wx.StaticText(about_panel,
                        label=t("dialog.about.version").format(version=VERSION))
    about_sizer.Add(ver, 0, wx.ALIGN_CENTER | wx.TOP, 5)
    about_sizer.AddSpacer(10)

    desc = wx.StaticText(about_panel,
        label=t("app.about_blurb"),
        style=wx.ALIGN_CENTRE_HORIZONTAL)
    about_sizer.Add(desc, 0, wx.ALIGN_CENTER)
    about_sizer.AddSpacer(15)

    copy_text = wx.StaticText(about_panel, label=t("app.copyright"))
    about_sizer.Add(copy_text, 0, wx.ALIGN_CENTER)
    about_sizer.AddSpacer(5)

    gh_link = wx.adv.HyperlinkCtrl(about_panel,
        label=t("dialog.about.github_link"),
        url="https://github.com/FlintWave/flintwave-kdh-flasher")
    about_sizer.Add(gh_link, 0, wx.ALIGN_CENTER)
    about_sizer.AddSpacer(2)

    license_link = wx.adv.HyperlinkCtrl(about_panel,
        label=t("dialog.about.license_link"),
        url="https://www.gnu.org/licenses/gpl-3.0.html")
    about_sizer.Add(license_link, 0, wx.ALIGN_CENTER)

    about_panel.SetSizer(about_sizer)
    notebook.AddPage(about_panel, t("dialog.about.tab_about"))

    # License page
    license_panel = wx.Panel(notebook)
    license_sizer = wx.BoxSizer(wx.VERTICAL)
    license_text = wx.TextCtrl(license_panel,
        style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP)
    license_text.SetFont(wx.Font(9, wx.FONTFAMILY_TELETYPE,
                                 wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
    license_text.SetValue(t("dialog.about.license_text"))
    license_sizer.Add(license_text, 1, wx.EXPAND | wx.ALL, 10)
    license_panel.SetSizer(license_sizer)
    notebook.AddPage(license_panel, t("dialog.about.tab_license"))

    dlg_sizer = wx.BoxSizer(wx.VERTICAL)
    dlg_sizer.Add(notebook, 1, wx.EXPAND | wx.ALL, 5)
    close_btn = wx.Button(dlg, wx.ID_CLOSE, t("button.close"))
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

    status = t("dialog.report.result_success") if success else t("dialog.report.result_failure")
    fw_file = os.path.basename(firmware_path) if firmware_path else "unknown"

    report_body = (
        t("dialog.report.body_radio").format(radio=radio_name)
        + t("dialog.report.body_firmware").format(firmware=fw_file)
        + t("dialog.report.body_result").format(status=status)
        + t("dialog.report.body_os").format(
            os=f"{platform.system()} {platform.release()}")
        + t("dialog.report.body_python").format(python=platform.python_version())
    )
    if error_msg:
        report_body += t("dialog.report.body_error").format(error=error_msg)
    report_body += t("dialog.report.body_notes")
    if log_content:
        # Truncate log to last 2000 chars to keep URL manageable
        truncated = log_content[-2000:] if len(log_content) > 2000 else log_content
        report_body += t("dialog.report.body_log_header") + truncated + "\n"

    subject = t("dialog.report.subject").format(radio=radio_name, status=status)

    dlg = wx.Dialog(frame, title=t("dialog.report.title"), size=(520, 500))
    dlg.SetMinSize((480, 400))
    dlg.SetMaxSize((600, 600))
    _apply_direction(dlg)
    sizer = wx.BoxSizer(wx.VERTICAL)

    ui_font = wx.Font(11, wx.FONTFAMILY_DEFAULT,
                      wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
    mono_font = wx.Font(10, wx.FONTFAMILY_TELETYPE,
                        wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)

    msg = t("dialog.report.success") if success else t("dialog.report.failure")

    msg_text = wx.StaticText(dlg, label=msg)
    msg_text.SetFont(ui_font)
    sizer.Add(msg_text, 0, wx.LEFT | wx.TOP | wx.RIGHT, 15)

    preview = wx.TextCtrl(dlg, value=report_body,
                          style=wx.TE_MULTILINE)
    preview.SetFont(mono_font)
    preview.SetInsertionPointEnd()
    sizer.Add(preview, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 15)

    sizer.AddSpacer(10)

    hint = wx.StaticText(dlg, label=t("dialog.report.email_hint"))
    hint.SetFont(wx.Font(9, wx.FONTFAMILY_DEFAULT,
                          wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
    sizer.Add(hint, 0, wx.ALIGN_CENTER)

    sizer.AddSpacer(10)

    btn_sizer = wx.BoxSizer(wx.HORIZONTAL)

    submit_btn = wx.Button(dlg, label=t("button.submit"))
    submit_btn.SetFont(ui_font)
    submit_btn.Bind(wx.EVT_BUTTON, lambda e: (
        wx.LaunchDefaultBrowser(
            "https://github.com/FlintWave/flintwave-kdh-flasher/issues/new?"
            + urllib.parse.urlencode({
                "title": subject,
                "body": preview.GetValue(),
                "labels": "test-report"
            })
        ),
        dlg.EndModal(wx.ID_OK)
    ))
    btn_sizer.Add(submit_btn, 0, wx.RIGHT, 8)

    skip_btn = wx.Button(dlg, wx.ID_CANCEL, label=t("button.skip"))
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
