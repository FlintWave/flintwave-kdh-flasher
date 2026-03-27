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

# GTK CSS theming for Linux — needed because native GTK widgets
# (menu bar, dropdown arrows, popup lists) ignore wxPython color setters
_gtk_available = False
try:
    if sys.platform.startswith("linux"):
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk, Gdk
        _gtk_available = True
except (ImportError, ValueError):
    pass
import flash_firmware as fw
import firmware_download as dl
import updater
import serial
import serial.tools.list_ports

# Known USB VID:PID pairs for compatible programming cables
KNOWN_CABLES = {
    (0x0403, 0x6015): "FTDI FT231X (PC03)",
    (0x0403, 0x6001): "FTDI FT232R",
    (0x0403, 0x6010): "FTDI FT2232",
    (0x0403, 0x6014): "FTDI FT232H",
    (0x067B, 0x2303): "Prolific PL2303",
    (0x067B, 0x23A3): "Prolific PL2303GS",
    (0x1A86, 0x7523): "CH340",
    (0x1A86, 0x55D4): "CH9102",
    (0x10C4, 0xEA60): "CP2102",
}

FTDI_VID_PID = (0x0403, 0x6015)  # PC03 cable


def list_serial_ports():
    """List serial ports with descriptions. Cross-platform."""
    ports = []
    for p in serial.tools.list_ports.comports():
        vid_pid = (p.vid, p.pid) if p.vid and p.pid else None
        cable = KNOWN_CABLES.get(vid_pid, "")
        if cable:
            label = f"{p.device} - {cable} [{p.serial_number or ''}]"
        elif p.description and p.description != "n/a":
            label = f"{p.device} - {p.description}"
        else:
            label = p.device
        ports.append((p.device, label.strip(), vid_pid))
    return ports


def find_programming_cable():
    """Auto-detect the BTECH PC03 cable or other FTDI cables."""
    ports = list_serial_ports()
    # Prefer exact PC03 match
    for device, label, vid_pid in ports:
        if vid_pid == FTDI_VID_PID:
            return device, label
    # Fall back to any known cable
    for device, label, vid_pid in ports:
        if vid_pid in KNOWN_CABLES:
            return device, label
    return None, None


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


class FlasherFrame(wx.Frame):
    def __init__(self):
        super().__init__(None, title="KDH Bootloader Firmware Flasher", size=(560, 500))
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
        radio_names = [f"{r['manufacturer']} {r['name']}" for r in self.radios]
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

        # Radio info
        self.radio_info = wx.StaticText(panel, label="")
        self.radio_info.SetForegroundColour(wx.Colour(80, 80, 80))
        sizer.Add(self.radio_info, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
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

        # Auto-detect cable on startup
        self._auto_detect_port()

        # Check for updates in background
        threading.Thread(target=self._check_update, daemon=True).start()

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
        panel = self.panel

        # Catppuccin palettes: (base, surface0, mantle, text, subtext1, green, link)
        # Using surface0 for interactive elements (buttons, dropdowns) for contrast
        themes = {
            "latte": (
                (239, 241, 245),  # base
                (204, 208, 218),  # surface0
                (230, 233, 239),  # mantle (log bg)
                (76, 79, 105),    # text
                (92, 95, 119),    # subtext1
                (64, 160, 43),    # green (log text)
                (30, 102, 245),   # link
            ),
            "frappe": (
                (48, 52, 70),     # base
                (65, 69, 89),     # surface0
                (41, 44, 60),     # mantle
                (198, 208, 245),  # text
                (181, 191, 226),  # subtext1
                (166, 209, 137),  # green
                (140, 170, 238),  # link
            ),
            "macchiato": (
                (36, 39, 58),     # base
                (54, 58, 79),     # surface0
                (30, 32, 48),     # mantle
                (202, 211, 245),  # text
                (184, 192, 224),  # subtext1
                (166, 218, 149),  # green
                (138, 173, 244),  # link
            ),
            "mocha": (
                (30, 30, 46),     # base
                (49, 50, 68),     # surface0
                (24, 24, 37),     # mantle
                (205, 214, 244),  # text
                (186, 194, 222),  # subtext1
                (166, 227, 161),  # green
                (137, 180, 250),  # link
            ),
            "high_contrast": (
                (0, 0, 0),        # base
                (30, 30, 30),     # surface0
                (0, 0, 0),        # mantle
                (255, 255, 0),    # text
                (255, 255, 100),  # subtext1
                (0, 255, 0),      # green
                (0, 255, 255),    # link
            ),
        }

        if theme not in themes:
            # System default — reset everything
            self.current_theme = "system"
            self.current_theme_palette = None
            panel.SetOwnBackgroundColour(wx.NullColour)
            panel.SetOwnForegroundColour(wx.NullColour)
            self.log.SetOwnBackgroundColour(wx.NullColour)
            self.log.SetOwnForegroundColour(wx.NullColour)
            self.radio_info.SetOwnForegroundColour(wx.Colour(80, 80, 80))
            for child in panel.GetChildren():
                child.SetOwnBackgroundColour(wx.NullColour)
                child.SetOwnForegroundColour(wx.NullColour)
            self._clear_gtk_css()
            panel.Refresh()
            for child in panel.GetChildren():
                child.Refresh()
            return

        self.current_theme = theme
        self.current_theme_palette = themes[theme]

        base, surface0, mantle, text, subtext1, green, link = \
            [wx.Colour(*c) for c in themes[theme]]

        panel.SetOwnBackgroundColour(base)
        panel.SetOwnForegroundColour(text)
        self.log.SetOwnBackgroundColour(mantle)
        self.log.SetOwnForegroundColour(green)
        self.radio_info.SetOwnForegroundColour(subtext1)

        for child in panel.GetChildren():
            if isinstance(child, wx.adv.HyperlinkCtrl):
                child.SetNormalColour(link)
                child.SetVisitedColour(link)
                child.SetHoverColour(green)
                child.SetOwnBackgroundColour(base)
            elif isinstance(child, (wx.Button,)):
                child.SetOwnBackgroundColour(surface0)
                child.SetOwnForegroundColour(text)
            elif isinstance(child, (wx.ComboBox,)):
                child.SetOwnBackgroundColour(surface0)
                child.SetOwnForegroundColour(text)
            elif isinstance(child, wx.TextCtrl):
                child.SetOwnBackgroundColour(mantle)
                child.SetOwnForegroundColour(text)
            elif isinstance(child, wx.Gauge):
                child.SetOwnBackgroundColour(surface0)
            elif isinstance(child, wx.StaticText):
                child.SetOwnForegroundColour(text)
                child.SetOwnBackgroundColour(base)
            else:
                child.SetOwnForegroundColour(text)
                child.SetOwnBackgroundColour(base)

        # Log gets green text for that terminal feel
        self.log.SetOwnForegroundColour(green)

        # Apply GTK CSS for native widgets (menus, dropdowns, arrows)
        if _gtk_available:
            self._apply_gtk_css(themes[theme])

        panel.Refresh()
        for child in panel.GetChildren():
            child.Refresh()

    def on_usage_guide(self, event):
        guide_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "USAGE.md")
        if os.path.exists(guide_path):
            wx.LaunchDefaultBrowser("file://" + guide_path)
        else:
            wx.LaunchDefaultBrowser("https://github.com/FlintWave/flintwave-kdh-flasher/blob/master/USAGE.md")

    def on_github(self, event):
        wx.LaunchDefaultBrowser("https://github.com/FlintWave/flintwave-kdh-flasher")

    def _apply_gtk_css(self, palette):
        """Apply theme colors to GTK native widgets via CSS."""
        base, surface0, mantle, text, subtext1, green, link = palette

        def rgb(c):
            return f"rgb({c[0]},{c[1]},{c[2]})"

        css = f"""
            menubar, menubar > menuitem {{
                background-color: {rgb(base)};
                color: {rgb(text)};
            }}
            menubar > menuitem:hover {{
                background-color: {rgb(surface0)};
            }}
            menu, menu > menuitem {{
                background-color: {rgb(surface0)};
                color: {rgb(text)};
            }}
            menu > menuitem:hover {{
                background-color: {rgb(link)};
                color: {rgb(base)};
            }}
            combobox, combobox button, combobox arrow {{
                background-color: {rgb(surface0)};
                color: {rgb(text)};
            }}
            combobox window, combobox window * {{
                background-color: {rgb(surface0)};
                color: {rgb(text)};
            }}
            button {{
                background-image: none;
                background-color: {rgb(surface0)};
                color: {rgb(text)};
                border-color: {rgb(mantle)};
            }}
            button:hover {{
                background-color: {rgb(link)};
                color: {rgb(base)};
            }}
            entry, textview, textview text {{
                background-color: {rgb(mantle)};
                color: {rgb(text)};
            }}
            scrollbar {{
                background-color: {rgb(base)};
            }}
            scrollbar slider {{
                background-color: {rgb(surface0)};
            }}
        """

        try:
            provider = Gtk.CssProvider()
            provider.load_from_data(css.encode())

            screen = Gdk.Screen.get_default()
            if not hasattr(self, '_gtk_css_provider'):
                self._gtk_css_provider = None
            if self._gtk_css_provider:
                Gtk.StyleContext.remove_provider_for_screen(screen, self._gtk_css_provider)
            Gtk.StyleContext.add_provider_for_screen(
                screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
            self._gtk_css_provider = provider
        except Exception:
            pass

    def _clear_gtk_css(self):
        """Remove custom GTK CSS to restore system theme."""
        if _gtk_available and hasattr(self, '_gtk_css_provider') and self._gtk_css_provider:
            try:
                screen = Gdk.Screen.get_default()
                Gtk.StyleContext.remove_provider_for_screen(screen, self._gtk_css_provider)
                self._gtk_css_provider = None
            except Exception:
                pass

    def on_about(self, event):
        VERSION = "26.03.1"
        dlg = wx.Dialog(self, title="About", size=(420, 440),
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

        link = wx.adv.HyperlinkCtrl(about_panel,
            label="github.com/FlintWave/flintwave-kdh-flasher",
            url="https://github.com/FlintWave/flintwave-kdh-flasher")
        about_sizer.Add(link, 0, wx.ALIGN_CENTER)

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
        if self.current_theme_palette:
            p = self.current_theme_palette
            base, surface0, mantle, text, subtext1, green, link_c = \
                [wx.Colour(*c) for c in p]
            for w in [dlg, notebook, about_panel, license_panel]:
                w.SetOwnBackgroundColour(base)
                w.SetOwnForegroundColour(text)
            for child in about_panel.GetChildren():
                if isinstance(child, wx.adv.HyperlinkCtrl):
                    child.SetNormalColour(link_c)
                    child.SetVisitedColour(link_c)
                    child.SetOwnBackgroundColour(base)
                elif isinstance(child, wx.StaticText):
                    child.SetOwnForegroundColour(text)
            copy_text.SetOwnForegroundColour(subtext1)
            license_text.SetOwnBackgroundColour(mantle)
            license_text.SetOwnForegroundColour(green)
            close_btn.SetOwnBackgroundColour(surface0)
            close_btn.SetOwnForegroundColour(text)

        # Apply current font size
        ui_font = wx.Font(self.font_size, wx.FONTFAMILY_DEFAULT,
                          wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
        mono_font = wx.Font(self.font_size, wx.FONTFAMILY_TELETYPE,
                            wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
        title.SetFont(wx.Font(self.font_size + 4, wx.FONTFAMILY_DEFAULT,
                              wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        ver.SetFont(ui_font)
        desc.SetFont(ui_font)
        copy_text.SetFont(ui_font)
        license_text.SetFont(mono_font)

        dlg.Centre()
        dlg.ShowModal()
        dlg.Destroy()

    def _check_update(self):
        try:
            has_update, local_sha, remote_sha = updater.check_for_update()
            if has_update:
                wx.CallAfter(self._prompt_update, local_sha, remote_sha)
        except Exception:
            pass

    def _prompt_update(self, local_sha, remote_sha):
        dlg = wx.MessageDialog(self,
            f"A newer version is available on GitHub.\n\n"
            f"Local:  {local_sha[:10]}\n"
            f"Remote: {remote_sha[:10]}\n\n"
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

    def _restart(self):
        import sys
        os.execv(sys.executable, [sys.executable] + sys.argv)

    def _get_selected_radio(self):
        idx = self.radio_combo.GetSelection()
        if 0 <= idx < len(self.radios):
            return self.radios[idx]
        return None

    def _update_radio_info(self):
        radio = self._get_selected_radio()
        if radio:
            tested = "Tested" if radio.get("tested") else "Untested"
            info = f"Bootloader: {radio['bootloader_keys']}  |  Connector: {radio['connector']}  |  {tested}"
            self.radio_info.SetLabel(info)
            has_url = bool(radio.get("firmware_url"))
            self.download_btn.Enable(has_url)
            if not has_url:
                self.download_btn.SetLabel("No Direct URL")
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

        self.log.Clear()
        self.progress.SetValue(0)
        self.set_buttons(False)
        threading.Thread(target=self._download_thread, args=(radio,), daemon=True).start()

    def _download_thread(self, radio):
        try:
            self.log_msg(f"Downloading firmware for {radio['name']}...")
            self.log_msg(f"URL: {radio['firmware_url']}")
            self.log_msg("")

            def on_progress(pct):
                self.set_progress(pct * 0.8)  # 80% for download

            kdhx_path, _ = dl.download_and_extract(
                radio["id"], progress_callback=on_progress
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
            wx.CallAfter(wx.MessageBox, "Firmware update complete!", "Success", wx.OK | wx.ICON_INFORMATION)

        except Exception as e:
            self.log_msg(f"\nERROR: {e}")
            self.log_msg("Radio may need to be power cycled and put back in bootloader mode.")
            wx.CallAfter(wx.MessageBox, f"Flash failed:\n{e}", "Error", wx.OK | wx.ICON_ERROR)
        finally:
            self.set_buttons(True)

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
