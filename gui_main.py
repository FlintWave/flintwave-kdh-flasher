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
import wx.lib.scrolledpanel

import flash_firmware as fw
import firmware_download as dl
import firmware_manifest as fm
import firmware_version as fv
import updater
import serial

from gui_ports import list_serial_ports, find_programming_cable, KNOWN_CABLES, FTDI_VID_PID
from gui_dialogs import (
    show_about_dialog,
    show_test_report_dialog,
)
from gui_themes import apply_theme, THEME_PALETTES, MOCHA_PALETTE

VERSION = "26.05.2"

FONT_SIZES = [9, 11, 12, 14, 16]

# Handset list status strings
STATUS_UNKNOWN = "Unknown"
STATUS_PROBING = "Probing…"
STATUS_READY = "Ready"
STATUS_NO_RESP = "No response"
STATUS_FLASHING = "Flashing…"
STATUS_DONE = "Done"
STATUS_FAILED = "Failed"
STATUS_SKIPPED = "Skipped"


class FlasherFrame(wx.Frame):
    def __init__(self):
        # 16:9 default (1280x720), 16:9 minimum (960x540) for BalenaEtcher-like proportions.
        # NO_BORDER hides the OS title bar; we draw our own.  RESIZE_BORDER keeps
        # the window resizable from its edges.
        super().__init__(None, title="KDH Bootloader Firmware Flasher",
                         size=(1280, 720),
                         style=wx.NO_BORDER | wx.RESIZE_BORDER |
                         wx.MINIMIZE_BOX | wx.CLOSE_BOX |
                         wx.CLIP_CHILDREN)
        self.SetMinSize((960, 540))

        self.font_size = 12
        self.current_theme = "mocha"
        self.current_theme_palette = MOCHA_PALETTE
        self._busy = False
        self._terminal_state = None  # set to "complete"/"failed" by threads
        self._handset_ports = []     # list of dicts: device, cable, vid_pid, status, progress
        self._port_poll_signature = None  # last (device,cable,vid_pid) tuple for change detection
        self._update_url = None       # set by _check_update when an update is detected

        # Window icon
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon_128.png")
        if os.path.exists(icon_path):
            self.SetIcon(wx.Icon(icon_path))

        panel = wx.Panel(self)
        self.panel = panel
        root_sizer = wx.BoxSizer(wx.VERTICAL)

        # ---- Custom title bar (replaces OS chrome) ----
        self.title_bar = self._build_title_bar(panel)
        root_sizer.Add(self.title_bar, 0, wx.EXPAND)

        # ---- Top row: three columns separated by ">" arrows ----
        top_row = wx.BoxSizer(wx.HORIZONTAL)

        # Manifest state (must be set before _update_radio_info)
        self.manifest = None
        self.radios = dl.load_radios()

        col_firmware = self._build_firmware_column(panel)
        col_handset = self._build_handset_column(panel)
        col_flash = self._build_flash_column(panel)

        # Bumped one size larger from previous 20pt to give more visual weight.
        arrow_font = wx.Font(28, wx.FONTFAMILY_DEFAULT,
                             wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
        self.arrow1 = wx.StaticText(panel, label="›")  # firmware → handset
        self.arrow1.SetFont(arrow_font)
        self.arrow2 = wx.StaticText(panel, label="›")  # handset → flash
        self.arrow2.SetFont(arrow_font)
        # Pulse-state per arrow. Keyed by id(arrow) so we don't re-pulse
        # on every redundant gating refresh.
        self._arrow_pulse_timers = {}
        self._arrow_unlocked = {id(self.arrow1): False, id(self.arrow2): False}

        top_row.Add(col_firmware, 1, wx.EXPAND | wx.ALL, 8)
        top_row.Add(self.arrow1, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT | wx.RIGHT, 4)
        top_row.Add(col_handset, 1, wx.EXPAND | wx.ALL, 8)
        top_row.Add(self.arrow2, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT | wx.RIGHT, 4)
        top_row.Add(col_flash, 1, wx.EXPAND | wx.ALL, 8)

        # Top row gets 2/3 of vertical space (BalenaEtcher-style); middle (hints+log) gets 1/3.
        root_sizer.Add(top_row, 2, wx.EXPAND)

        # Thin (1px) divider between top columns and bottom hint/log row,
        # centered and ~80% of the window width.
        divider_row = wx.BoxSizer(wx.HORIZONTAL)
        self._divider1 = wx.StaticLine(panel, style=wx.LI_HORIZONTAL,
                                       size=(-1, 1))
        self._divider1.SetMinSize(wx.Size(-1, 1))
        divider_row.AddStretchSpacer(1)
        divider_row.Add(self._divider1, 8, wx.EXPAND)
        divider_row.AddStretchSpacer(1)
        # Generous breathing room above and below the divider so the columns
        # don't feel cramped against it.
        root_sizer.Add(divider_row, 0, wx.EXPAND | wx.TOP | wx.BOTTOM, 16)

        # ---- Middle row: instructions panel + log, each with a static label ----
        middle_row = wx.BoxSizer(wx.HORIZONTAL)

        # Instructions panel (left half). Use a read-only wx.TextCtrl with
        # rich-text styling for the body; native multi-line TextCtrl gives us
        # word-wrap + a v-scrollbar for free, which the previous
        # StaticText-in-ScrolledPanel approach couldn't reliably deliver.
        self._instructions_outer = wx.Panel(panel)
        self._instructions_outer.SetMinSize(wx.Size(1, -1))
        outer_sizer = wx.BoxSizer(wx.VERTICAL)
        instructions_label = self._column_heading(self._instructions_outer,
                                                  "Instructions")
        outer_sizer.Add(instructions_label, 0, wx.ALIGN_CENTER_HORIZONTAL | wx.TOP, 0)

        # hints_panel kept as a name for back-compat with apply_theme paths;
        # it's now just a thin wrapper that owns the TextCtrl.
        self.hints_panel = wx.Panel(self._instructions_outer)
        self.hints_panel.SetMinSize(wx.Size(1, -1))
        hp_sizer = wx.BoxSizer(wx.VERTICAL)
        self.hint_text = wx.TextCtrl(
            self.hints_panel,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2 |
            wx.TE_BESTWRAP | wx.BORDER_NONE)
        # Provide compatibility shims so older code referring to hint_title /
        # hint_body keeps working — they map to the same TextCtrl.
        self.hint_title = self.hint_text
        self.hint_body = self.hint_text
        hp_sizer.Add(self.hint_text, 1, wx.EXPAND | wx.ALL, 10)
        self.hints_panel.SetSizer(hp_sizer)

        outer_sizer.Add(self.hints_panel, 1, wx.EXPAND)
        self._instructions_outer.SetSizer(outer_sizer)

        # Log panel (right half) — heading + textarea
        self.log_panel = wx.Panel(panel)
        self.log_panel.SetMinSize(wx.Size(200, -1))
        log_sizer = wx.BoxSizer(wx.VERTICAL)
        log_label = self._column_heading(self.log_panel, "Log")
        log_sizer.Add(log_label, 0, wx.ALIGN_CENTER_HORIZONTAL | wx.TOP, 0)
        # Drop wx.HSCROLL — without it, multi-line TextCtrl word-wraps long
        # lines at the right edge instead of pushing them off-screen.
        # wx.TE_DONTWRAP is what would force horizontal scrolling; we omit it.
        self.log = wx.TextCtrl(self.log_panel,
                               style=wx.TE_MULTILINE | wx.TE_READONLY |
                               wx.TE_BESTWRAP)
        self.log.SetFont(wx.Font(self.font_size, wx.FONTFAMILY_TELETYPE,
                                 wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        log_sizer.Add(self.log, 1, wx.EXPAND | wx.ALL, 10)
        self.log_panel.SetSizer(log_sizer)

        middle_row.Add(self._instructions_outer, 1, wx.EXPAND | wx.LEFT | wx.BOTTOM, 8)
        middle_row.AddSpacer(32)  # generous breathing room between Instructions and Log
        middle_row.Add(self.log_panel, 1, wx.EXPAND | wx.RIGHT | wx.BOTTOM, 8)
        root_sizer.Add(middle_row, 1, wx.EXPAND)

        # ---- Bottom status bar: borderless text/icon links, darker background ----
        self.status_bar_panel = self._build_status_bar(panel)
        root_sizer.Add(self.status_bar_panel, 0, wx.EXPAND)

        panel.SetSizer(root_sizer)
        self.Centre()

        # Bind change events that update hint state
        self.file_path.Bind(wx.EVT_TEXT, self._on_state_change)

        # Initial population. Don't probe or auto-check anything yet — the
        # Handset column is gated until a radio + firmware are chosen, and
        # we don't want to touch serial ports the user hasn't unlocked.
        # The first probe is fired from _update_workflow_gating the moment
        # the firmware gate flips on.
        self._update_radio_info()
        self._refresh_handset_ports(probe=False, preserve_checks=True)
        self._set_hint(self._compute_hint_state())
        # Initial gating state (locks Handset + Flash columns until firmware
        # is chosen). Done synchronously so the locked state is visible the
        # moment the window paints, not one event-loop tick later.
        self._update_workflow_gating()

        # Background: update check, manifest fetch, port-change polling
        threading.Thread(target=self._check_update, daemon=True).start()
        threading.Thread(target=self._fetch_manifest, daemon=True).start()
        threading.Thread(target=self._port_poll_loop, daemon=True).start()

    # ------------------------------------------------------------------
    # Layout builders
    # ------------------------------------------------------------------

    def _column_heading(self, parent, text):
        """Return a styled heading StaticText for a borderless column.

        Heading widgets are tracked in self._column_headings so _set_font_size
        can give them a bigger/bolder font than the body text.
        """
        h = wx.StaticText(parent, label=text)
        h.SetFont(wx.Font(self.font_size + 3, wx.FONTFAMILY_DEFAULT,
                          wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        if not hasattr(self, "_column_headings"):
            self._column_headings = []
        self._column_headings.append(h)
        return h

    def _build_firmware_column(self, parent):
        # Borderless: a wx.Panel + a heading StaticText, no StaticBox.
        col = wx.Panel(parent)
        col.SetMinSize(wx.Size(240, -1))
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self._column_heading(col, "Firmware"), 0, wx.ALIGN_CENTER_HORIZONTAL | wx.BOTTOM, 6)

        # First entry is a placeholder so the user has to actively pick a
        # radio (instead of getting whichever radio happened to be in
        # radios.json[0] by default). _get_selected_radio() treats index 0 as
        # "no radio selected" (returns None).
        self.RADIO_PLACEHOLDER = "— Select your radio —"
        radio_names = [self.RADIO_PLACEHOLDER] + [
            r['name'] if r['name'].startswith(r['manufacturer'])
            else f"{r['manufacturer']} {r['name']}"
            for r in self.radios
        ]
        self.radio_combo = wx.ComboBox(col, choices=radio_names,
                                       style=wx.CB_DROPDOWN | wx.CB_READONLY)
        self.radio_combo.SetSelection(0)
        self.radio_combo.Bind(wx.EVT_COMBOBOX, self.on_radio_changed)
        # On GTK with CB_READONLY, clicking the text portion does nothing —
        # only the arrow drops down. Bind LEFT_DOWN so a click anywhere on the
        # combo opens the list.
        def _open_combo(event):
            try:
                self.radio_combo.Popup()
            except Exception:
                pass
            event.Skip()
        self.radio_combo.Bind(wx.EVT_LEFT_DOWN, _open_combo)
        sizer.Add(self.radio_combo, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)

        self.download_btn = wx.Button(col, label="Download Latest")
        self.download_btn.Bind(wx.EVT_BUTTON, self.on_download)
        sizer.Add(self.download_btn, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)

        file_row = wx.BoxSizer(wx.HORIZONTAL)
        self.file_path = wx.TextCtrl(col)
        file_row.Add(self.file_path, 1, wx.EXPAND | wx.RIGHT, 4)
        browse_btn = wx.Button(col, label="Browse…")
        browse_btn.Bind(wx.EVT_BUTTON, self.on_browse)
        file_row.Add(browse_btn, 0)
        sizer.Add(file_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)

        sizer.AddStretchSpacer(1)
        col.SetSizer(sizer)
        return col

    def _build_handset_column(self, parent):
        col = wx.Panel(parent)
        col.SetMinSize(wx.Size(280, -1))
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self._column_heading(col, "Handset"), 0, wx.ALIGN_CENTER_HORIZONTAL | wx.BOTTOM, 6)

        # Multi-select list of detected serial ports / cables. Each row has
        # a checkbox; FTDI/PC03 cables auto-check on detection. Status column
        # shows probe results (Ready / No response) and per-port flash progress.
        self.handset_list = wx.ListCtrl(col, style=wx.LC_REPORT)
        self._handset_checkboxes_supported = False
        try:
            self.handset_list.EnableCheckBoxes(True)
            self._handset_checkboxes_supported = True
        except Exception:
            self._handset_checkboxes_supported = False
        self.handset_list.InsertColumn(0, "Port", width=110)
        self.handset_list.InsertColumn(1, "Cable / Chip", width=140)
        self.handset_list.InsertColumn(2, "Status", width=110)
        self.handset_list.InsertColumn(3, "%", width=50)
        sizer.Add(self.handset_list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)

        if self._handset_checkboxes_supported:
            self.handset_list.Bind(wx.EVT_LIST_ITEM_CHECKED, self._on_handset_check_changed)
            self.handset_list.Bind(wx.EVT_LIST_ITEM_UNCHECKED, self._on_handset_check_changed)
        else:
            self.handset_list.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_handset_check_changed)
            self.handset_list.Bind(wx.EVT_LIST_ITEM_DESELECTED, self._on_handset_check_changed)

        # Selection summary + selection helpers
        self.handset_summary = wx.StaticText(col, label="0 selected / 0 detected")
        sizer.Add(self.handset_summary, 0, wx.LEFT | wx.RIGHT | wx.TOP, 6)

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        self.refresh_btn = wx.Button(col, label="Refresh / Probe")
        self.refresh_btn.SetToolTip(
            "Re-scan serial ports and probe each one for a radio in bootloader mode.")
        self.refresh_btn.Bind(wx.EVT_BUTTON, lambda e: self._refresh_handset_ports(probe=True))
        btn_row.Add(self.refresh_btn, 1, wx.RIGHT, 4)

        self.select_all_btn = wx.Button(col, label="All")
        self.select_all_btn.SetToolTip("Check every detected handset.")
        self.select_all_btn.Bind(wx.EVT_BUTTON, lambda e: self._set_all_handsets_checked(True))
        btn_row.Add(self.select_all_btn, 0, wx.RIGHT, 4)

        self.select_none_btn = wx.Button(col, label="None")
        self.select_none_btn.SetToolTip("Uncheck every handset.")
        self.select_none_btn.Bind(wx.EVT_BUTTON, lambda e: self._set_all_handsets_checked(False))
        btn_row.Add(self.select_none_btn, 0)
        sizer.Add(btn_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)

        col.SetSizer(sizer)
        return col

    def _build_flash_column(self, parent):
        col = wx.Panel(parent)
        col.SetMinSize(wx.Size(220, -1))
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self._column_heading(col, "Flash"), 0, wx.ALIGN_CENTER_HORIZONTAL | wx.BOTTOM, 6)

        self.flash_btn = wx.Button(col, label="Flash Firmware")
        flash_font = wx.Font(12, wx.FONTFAMILY_DEFAULT,
                             wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
        self.flash_btn.SetFont(flash_font)
        self.flash_btn.Bind(wx.EVT_BUTTON, self.on_flash)
        sizer.Add(self.flash_btn, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)

        self.progress = wx.Gauge(col, range=100)
        sizer.Add(self.progress, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)

        sizer.AddSpacer(4)

        sec_row = wx.BoxSizer(wx.HORIZONTAL)
        self.dryrun_btn = wx.Button(col, label="Dry Run")
        self.dryrun_btn.Bind(wx.EVT_BUTTON, self.on_dry_run)
        sec_row.Add(self.dryrun_btn, 1, wx.RIGHT, 4)
        self.diag_btn = wx.Button(col, label="Diagnostics")
        self.diag_btn.Bind(wx.EVT_BUTTON, self.on_diag)
        sec_row.Add(self.diag_btn, 1)
        sizer.Add(sec_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)

        sizer.AddStretchSpacer(1)
        col.SetSizer(sizer)
        return col

    def _build_title_bar(self, parent):
        """Custom borderless title bar with app title + minimize/close.

        We removed the OS title bar so the chrome themes consistently with the
        rest of the app. Drag the title bar (or title label / icon) to move
        the window. No maximize button (per user preference).
        """
        bar = wx.Panel(parent)
        sizer = wx.BoxSizer(wx.HORIZONTAL)

        # App icon at far left (small, scaled from icon_128).
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "icon_128.png")
        if os.path.exists(icon_path):
            img = wx.Image(icon_path).Rescale(20, 20, wx.IMAGE_QUALITY_HIGH)
            self._title_icon = wx.StaticBitmap(bar, bitmap=wx.Bitmap(img))
            sizer.Add(self._title_icon, 0,
                      wx.ALIGN_CENTER_VERTICAL | wx.LEFT | wx.RIGHT, 8)
        else:
            self._title_icon = None

        self.title_label = wx.StaticText(bar, label=self.GetTitle())
        title_font = wx.Font(self.font_size, wx.FONTFAMILY_DEFAULT,
                             wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
        self.title_label.SetFont(title_font)
        sizer.Add(self.title_label, 0,
                  wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 4)

        sizer.AddStretchSpacer(1)

        def make_chrome_btn(label, tooltip, handler):
            b = wx.StaticText(bar, label=label)
            b.SetCursor(wx.Cursor(wx.CURSOR_HAND))
            b.SetToolTip(tooltip)
            b.Bind(wx.EVT_LEFT_DOWN, lambda e: handler())
            chrome_font = wx.Font(self.font_size + 2, wx.FONTFAMILY_DEFAULT,
                                  wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
            b.SetFont(chrome_font)
            return b

        self._minimize_btn = make_chrome_btn("—", "Minimize", self.Iconize)
        self._close_btn = make_chrome_btn("✕", "Close", self.Close)
        sizer.Add(self._minimize_btn, 0,
                  wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 16)
        sizer.Add(self._close_btn, 0,
                  wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 16)

        bar.SetSizer(sizer)
        bar.SetMinSize(wx.Size(-1, 36))

        # Drag-to-move on title bar background, the title label, and the icon.
        for w in (bar, self.title_label):
            w.Bind(wx.EVT_LEFT_DOWN, self._on_titlebar_press)
            w.Bind(wx.EVT_MOTION, self._on_titlebar_drag)
            w.Bind(wx.EVT_LEFT_UP, self._on_titlebar_release)
        if self._title_icon is not None:
            self._title_icon.Bind(wx.EVT_LEFT_DOWN, self._on_titlebar_press)
            self._title_icon.Bind(wx.EVT_MOTION, self._on_titlebar_drag)
            self._title_icon.Bind(wx.EVT_LEFT_UP, self._on_titlebar_release)

        self._drag_offset = None
        return bar

    def _on_titlebar_press(self, event):
        """Start a drag-to-move from the title bar."""
        # Capture the offset between mouse and window's top-left so we can
        # subtract it from each subsequent mouse position to compute the
        # window's new origin.
        self._drag_offset = wx.GetMousePosition() - self.GetPosition()
        w = event.GetEventObject()
        if not w.HasCapture():
            w.CaptureMouse()

    def _on_titlebar_drag(self, event):
        if (event.Dragging() and event.LeftIsDown()
                and self._drag_offset is not None):
            self.Move(wx.GetMousePosition() - self._drag_offset)

    def _on_titlebar_release(self, event):
        w = event.GetEventObject()
        if w.HasCapture():
            w.ReleaseMouse()
        self._drag_offset = None

    def _build_status_bar(self, parent):
        """Borderless status bar with text/icon click-targets (no button frames)."""
        bar = wx.Panel(parent)
        bar_sizer = wx.BoxSizer(wx.HORIZONTAL)

        def make_link(label, tooltip, handler):
            """Create a clickable StaticText (no button border)."""
            link = wx.StaticText(bar, label=label)
            link.SetCursor(wx.Cursor(wx.CURSOR_HAND))
            link.SetToolTip(tooltip)
            link.Bind(wx.EVT_LEFT_DOWN, lambda e: handler())
            return link

        self.font_btn = make_link(
            f"{self.font_size}pt", "Cycle UI / log font size", self._cycle_font)

        # Theme toggle: glyph reflects the destination — sun = "switch to light",
        # moon = "switch to dark". Currently mocha (dark) → show sun.
        self.theme_btn = make_link(
            "☀" if self.current_theme == "mocha" else "☾",
            "Toggle light / dark theme", self._toggle_theme)

        usage_link = make_link("Usage", "Open the Usage Guide",
                               lambda: self.on_usage_guide(None))
        about_link = make_link("About", "About this app",
                               lambda: self.on_about(None))

        # Hidden hyperlink: when _check_update finds a newer release we set
        # its URL and Show() it. Click opens the releases page.
        self.update_link = wx.adv.HyperlinkCtrl(
            bar, label="Update Available",
            url="https://github.com/FlintWave/flintwave-kdh-flasher/releases/latest",
            style=wx.adv.HL_ALIGN_LEFT | wx.NO_BORDER)
        self.update_link.Hide()

        bar_sizer.AddSpacer(12)
        bar_sizer.Add(self.font_btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 16)
        bar_sizer.Add(self.theme_btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 16)
        bar_sizer.AddStretchSpacer(1)
        bar_sizer.Add(usage_link, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 16)
        bar_sizer.Add(self.update_link, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 16)
        bar_sizer.Add(about_link, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 16)
        bar_sizer.AddSpacer(4)

        bar.SetSizer(bar_sizer)
        bar.SetMinSize(wx.Size(-1, 32))
        return bar

    def _toggle_theme(self):
        """Switch between mocha (dark) and latte (light), re-render everything."""
        new_theme = "latte" if self.current_theme == "mocha" else "mocha"
        apply_theme(self, new_theme)
        # Re-glyph the toggle to point at the *next* destination.
        self.theme_btn.SetLabel("☀" if self.current_theme == "mocha" else "☾")
        # Force a repaint of every dialog-style child so they pick up the swap.
        self.panel.Layout()
        self.panel.Refresh()

    # ------------------------------------------------------------------
    # Wrap helpers
    # ------------------------------------------------------------------

    def _on_hints_size(self, event):
        # No-op: wx.TextCtrl handles wrap natively now. Kept as an event
        # binding placeholder in case something still calls it.
        if event:
            event.Skip()

    # ------------------------------------------------------------------
    # Handset list management (port discovery, probe, checkboxes)
    # ------------------------------------------------------------------

    def _enumerate_serial_ports(self):
        """Return list of dicts describing currently visible USB serial ports.

        We filter out non-USB serial ports (motherboard 16550 /dev/ttyS*) since
        all known KDH programming cables are USB-attached. Showing 30+ unused
        UARTs would also balloon probe time (each port takes ~1.5s).
        """
        import serial.tools.list_ports
        out = []
        for p in serial.tools.list_ports.comports():
            if not (p.vid and p.pid):
                continue  # skip non-USB ports
            vid_pid = (p.vid, p.pid)
            cable = KNOWN_CABLES.get(vid_pid, p.description or "")
            out.append({
                "device": p.device,
                "cable": cable,
                "vid_pid": vid_pid,
                "status": STATUS_UNKNOWN,
                "progress": "",
                "is_pc03": vid_pid == FTDI_VID_PID,
            })
        return out

    def _refresh_handset_ports(self, probe=False, preserve_checks=False):
        """Re-enumerate ports and rebuild the handset list.

        If probe=True, sends a CMD_HANDSHAKE to each port in a background thread
        and updates Status. PC03 cables are auto-checked unless preserve_checks
        is True (used by the polling loop so we don't fight the user).
        """
        if self._busy:
            return  # don't reshape the list while flashing

        previously_checked = set()
        if preserve_checks:
            for i in range(self.handset_list.GetItemCount()):
                if self._is_handset_checked(i):
                    previously_checked.add(self._handset_ports[i]["device"])

        new_ports = self._enumerate_serial_ports()
        self.handset_list.DeleteAllItems()
        self._handset_ports = new_ports

        for entry in new_ports:
            # Show only the device basename (e.g. "ttyUSB0") — the full path
            # is kept in entry["device"] for serial.Serial calls.
            display_port = os.path.basename(entry["device"]) or entry["device"]
            idx = self.handset_list.InsertItem(
                self.handset_list.GetItemCount(), display_port)
            self.handset_list.SetItem(idx, 1, entry["cable"])
            self.handset_list.SetItem(idx, 2, entry["status"])
            self.handset_list.SetItem(idx, 3, entry["progress"])

            should_check = (
                entry["device"] in previously_checked
                or (not preserve_checks and entry["is_pc03"])
            )
            if should_check:
                self._set_handset_check(idx, True)

        self._refresh_handset_summary()

        if probe and new_ports:
            self.refresh_btn.Disable()
            threading.Thread(target=self._probe_thread, daemon=True).start()

    def _probe_thread(self):
        """Send CMD_HANDSHAKE to every listed port; update Status as we go.

        If the very first probe hits PermissionError (Linux dialout), surface
        the hint once and abort instead of marking every port "No response".
        """
        permission_blocked = False
        for idx, entry in enumerate(list(self._handset_ports)):
            wx.CallAfter(self._set_handset_status, idx, STATUS_PROBING)
            try:
                ready = fw.probe_port(entry["device"], timeout=1.5)
            except PermissionError:
                permission_blocked = True
                wx.CallAfter(self._log_dialout_hint, entry["device"])
                # Mark this port and all remaining ones as Unknown rather
                # than No response — they may well be radios.
                for remaining_idx in range(idx, len(self._handset_ports)):
                    wx.CallAfter(self._set_handset_status,
                                 remaining_idx, STATUS_UNKNOWN)
                break
            except Exception:
                ready = False
            else:
                new_status = STATUS_READY if ready else STATUS_NO_RESP
                wx.CallAfter(self._set_handset_status, idx, new_status)
                if ready:
                    wx.CallAfter(self._set_handset_check, idx, True)
        wx.CallAfter(self.refresh_btn.Enable)
        if not permission_blocked:
            wx.CallAfter(lambda: self._set_hint(self._compute_hint_state()))

    def _port_poll_loop(self):
        """Background thread: detect plug/unplug events and trigger refresh.

        Polls every 2 seconds; when the set of visible ports changes (and we
        aren't busy), refresh the list while preserving user-made checkbox state.
        """
        import time
        while True:
            time.sleep(2.0)
            try:
                ports = self._enumerate_serial_ports()
                signature = tuple(sorted(p["device"] for p in ports))
                if signature != self._port_poll_signature:
                    self._port_poll_signature = signature
                    if not self._busy:
                        wx.CallAfter(
                            self._refresh_handset_ports,
                            False, True
                        )
            except Exception:
                pass

    # --- Handset list helpers ---

    def _set_handset_status(self, idx, status):
        if 0 <= idx < len(self._handset_ports):
            self._handset_ports[idx]["status"] = status
            try:
                self.handset_list.SetItem(idx, 2, status)
            except Exception:
                pass
            self._refresh_handset_summary()

    def _set_handset_progress(self, idx, text):
        if 0 <= idx < len(self._handset_ports):
            self._handset_ports[idx]["progress"] = text
            try:
                self.handset_list.SetItem(idx, 3, text)
            except Exception:
                pass

    def _set_handset_check(self, idx, checked):
        if not (0 <= idx < self.handset_list.GetItemCount()):
            return
        if self._handset_checkboxes_supported:
            self.handset_list.CheckItem(idx, checked)
        else:
            self.handset_list.Select(idx, on=1 if checked else 0)
        self._refresh_handset_summary()

    def _is_handset_checked(self, idx):
        if not (0 <= idx < self.handset_list.GetItemCount()):
            return False
        if self._handset_checkboxes_supported:
            return self.handset_list.IsItemChecked(idx)
        return self.handset_list.IsSelected(idx)

    def _set_all_handsets_checked(self, checked):
        for idx in range(self.handset_list.GetItemCount()):
            self._set_handset_check(idx, checked)

    def _on_handset_check_changed(self, event):
        self._refresh_handset_summary()
        self._terminal_state = None
        self._set_hint(self._compute_hint_state())
        self._update_workflow_gating()
        if event:
            event.Skip()

    def _refresh_handset_summary(self):
        total = self.handset_list.GetItemCount()
        sel = sum(1 for i in range(total) if self._is_handset_checked(i))
        self.handset_summary.SetLabel(f"{sel} selected / {total} detected")

    def _selected_handset_indices(self):
        return [i for i in range(self.handset_list.GetItemCount())
                if self._is_handset_checked(i)]

    def _selected_handset_devices(self):
        return [self._handset_ports[i]["device"]
                for i in self._selected_handset_indices()]

    # ------------------------------------------------------------------
    # Workflow gating: enforce Firmware → Handset → Flash order
    # ------------------------------------------------------------------

    def _firmware_ready(self):
        """True once a firmware file path has been chosen / downloaded."""
        try:
            path = self.file_path.GetValue().strip()
            return bool(path) and os.path.exists(path)
        except Exception:
            return False

    def _handset_ready(self):
        """True once at least one handset is checked in the list."""
        return bool(self._selected_handset_indices())

    def _update_workflow_gating(self):
        """Enable / disable buttons in each column based on workflow state.

        Workflow tiers:
          1. Pick a radio (firmware-column dropdown) — Download button
             stays disabled until a real radio is selected.
          2. Get a firmware file (Download or Browse) — Handset column
             stays locked until file_path exists.
          3. Check a handset — Flash column stays locked until at least
             one handset is checked.
        When a column transitions locked → unlocked, the arrow leading to
        it pulses briefly to cue the user.
        """
        # Don't fight an in-progress flash by toggling buttons mid-operation.
        if self._busy:
            return

        radio_chosen = self._get_selected_radio() is not None
        firmware = self._firmware_ready()
        handset = self._handset_ready()

        # Firmware column: Download requires a real radio. Browse is always
        # available (user may already have a .kdhx on disk).
        try:
            self.download_btn.Enable(radio_chosen)
        except Exception:
            pass

        # Handset column gate
        for w in (self.refresh_btn, self.select_all_btn, self.select_none_btn,
                  self.handset_list):
            try:
                w.Enable(firmware)
            except Exception:
                pass
        # Flash column gate
        for w in (self.flash_btn, self.dryrun_btn, self.diag_btn):
            try:
                w.Enable(firmware and handset)
            except Exception:
                pass

        # Pulse only ONE arrow at a time, on the gate that just transitioned:
        #   - arrow1 (firmware → handset) pulses when firmware becomes ready
        #   - arrow2 (handset → flash) pulses when a handset becomes checked
        # Tracking the gates independently keeps them from firing together
        # in the case where a handset was auto-checked before firmware was
        # selected (which would otherwise unlock Flash the moment firmware
        # loads, double-pulsing).
        for arrow, gate_now in ((self.arrow1, firmware),
                                (self.arrow2, handset)):
            gate_before = self._arrow_unlocked.get(id(arrow), False)
            if gate_now and not gate_before:
                self._pulse_arrow(arrow)
                # When the Handset column unlocks (firmware gate flips on),
                # kick off the first probe of the connected ports. Until
                # this point we deliberately leave the list passive so we
                # never touch serial devices the user hasn't unlocked yet.
                if arrow is self.arrow1:
                    self._refresh_handset_ports(probe=True)
            self._arrow_unlocked[id(arrow)] = gate_now

    def _pulse_arrow(self, arrow, cycles=3):
        """Pulse a column-separator arrow with a green glow.

        Each cycle fades the arrow's foreground from the current text color
        up to a saturated bright green and back. Tuned slow enough (~750ms
        per cycle) to be obviously animated rather than a single brief flash.
        """
        # Cancel any in-flight pulse on this arrow first so re-triggers don't
        # stack. wx.Timer instances stored by id(arrow).
        old_timer = self._arrow_pulse_timers.get(id(arrow))
        if old_timer and old_timer.IsRunning():
            old_timer.Stop()

        palette = self.current_theme_palette or MOCHA_PALETTE
        normal = wx.Colour(*palette[3])     # text
        # Override the palette green with a brighter, more saturated value so
        # it reads as a "glow" against either Mocha or Latte backgrounds.
        glow = wx.Colour(80, 250, 120)

        steps_per_cycle = 15        # smoother fade
        total_steps = cycles * steps_per_cycle
        interval_ms = 50            # 15 * 50 = 750 ms per cycle

        state = {"i": 0}
        timer = wx.Timer(self)

        def lerp_color(t):
            """Triangle wave between normal (t=0) and glow (t=0.5) and back."""
            if t < 0.5:
                k = t * 2
            else:
                k = (1 - t) * 2
            r = int(normal.Red() + (glow.Red() - normal.Red()) * k)
            g = int(normal.Green() + (glow.Green() - normal.Green()) * k)
            b = int(normal.Blue() + (glow.Blue() - normal.Blue()) * k)
            return wx.Colour(r, g, b)

        def on_tick(evt, _arrow=arrow, _state=state, _timer=timer):
            i = _state["i"]
            if i >= total_steps:
                _timer.Stop()
                _arrow.SetForegroundColour(normal)
                _arrow.Refresh()
                return
            cycle_t = (i % steps_per_cycle) / steps_per_cycle
            _arrow.SetForegroundColour(lerp_color(cycle_t))
            _arrow.Refresh()
            _state["i"] += 1

        self.Bind(wx.EVT_TIMER, on_tick, timer)
        self._arrow_pulse_timers[id(arrow)] = timer
        timer.Start(interval_ms)

    # ------------------------------------------------------------------
    # Font controls (theme is now fixed to Frappe; no toggle)
    # ------------------------------------------------------------------

    def _set_font_size(self, size):
        self.font_size = size
        mono = wx.Font(size, wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
        ui = wx.Font(size, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
        ui_bold = wx.Font(size, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
        flash_font = wx.Font(size + 3, wx.FONTFAMILY_DEFAULT,
                             wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
        heading_font = wx.Font(size + 3, wx.FONTFAMILY_DEFAULT,
                               wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)

        headings = set(getattr(self, "_column_headings", []))

        # Recursive walk so deeply nested controls (column boxes, hints, status bar)
        # all pick up the font change
        def walk(w):
            yield w
            try:
                for c in w.GetChildren():
                    yield from walk(c)
            except Exception:
                return

        # Arrows get their own (larger) font; don't let the font cycler stomp it.
        arrow_font = wx.Font(size + 16, wx.FONTFAMILY_DEFAULT,
                             wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
        arrows = {self.arrow1, self.arrow2}

        for w in walk(self.panel):
            if w is self.log:
                w.SetFont(mono)
            elif w is self.flash_btn:
                w.SetFont(flash_font)
            elif w is self.hint_title:
                w.SetFont(ui_bold)
            elif w in headings:
                w.SetFont(heading_font)
            elif w in arrows:
                w.SetFont(arrow_font)
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
        # Legacy no-op (TextCtrl handles its own wrap/resize).
        pass

    def _cycle_font(self):
        try:
            idx = FONT_SIZES.index(self.font_size)
        except ValueError:
            idx = -1
        new_size = FONT_SIZES[(idx + 1) % len(FONT_SIZES)]
        self._set_font_size(new_size)
        self.font_btn.SetLabel(f"{new_size}pt")
        self.status_bar_panel.Layout()

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
        "no_handset": (
            "Step 2 — Connect a handset",
            "Plug in your programming cable (PC03 or compatible K1 cable). "
            "Detected ports appear in the Handset column; PC03 cables are "
            "auto-checked. Click “Refresh / Probe” after plugging in a new "
            "cable to re-scan and check which ports respond like a radio in "
            "bootloader mode."
        ),
        "batch_ready": (
            "Step 4 — Batch flash",
            "Multiple handsets are checked. Clicking “Flash Firmware” will "
            "flash the same firmware to each one sequentially. Make sure every "
            "selected radio is in bootloader mode (green Rx LED on) before "
            "starting."
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
            "Flash complete!",
            "Power cycle the radio and check Menu › Radio Info to confirm "
            "the new version. See the log on the right for full details."
        ),
        "dryrun_complete": (
            "Dry run passed",
            "The firmware file is structurally valid and every packet's CRC "
            "verified. No data was sent to a radio. Put your radio in "
            "bootloader mode and click Flash Firmware when you're ready."
        ),
        "diag_complete": (
            "Diagnostics passed",
            "The selected handset answered the bootloader handshake — the "
            "cable, port, and bootloader-mode setup are all working. You can "
            "proceed to flash."
        ),
        "failed": (
            "Operation failed",
            "The last operation did not complete successfully. Read the log on "
            "the right for the error message. The radio may need to be power "
            "cycled and put back in bootloader mode before trying again."
        ),
    }

    # States during which it's useful to also show the per-radio info
    # (bootloader keys, connector type, notes from radios.json).
    _RADIO_INFO_STATES = {"no_firmware", "no_handset", "ready_flash",
                          "ready_dryrun", "batch_ready"}

    def _format_radio_info(self):
        """Return per-radio instructions for the active radio, or empty string."""
        radio = self._get_selected_radio()
        if not radio:
            return ""
        bits = []
        keys = radio.get("bootloader_keys")
        connector = radio.get("connector")
        tested = radio.get("tested")
        # Same dedup logic as the dropdown: skip the manufacturer prefix when
        # the name already starts with it (e.g. "BTECH BF-F8HP Pro").
        manufacturer = radio.get("manufacturer", "")
        name = radio.get("name", "")
        full_name = name if name.startswith(manufacturer) else f"{manufacturer} {name}".strip()
        bits.append(f"Radio: {full_name}")
        if keys:
            bits.append(f"Bootloader keys: {keys}")
        if connector:
            bits.append(f"Connector: {connector}")
        bits.append("Tested with this tool" if tested else "Untested with this tool")
        _, version = self._get_firmware_url_and_version(radio)
        if version:
            bits.append(f"Latest firmware: v{version}")
        notes = radio.get("notes")
        if notes:
            bits.append("")
            bits.append(notes)
        return "\n".join(bits)

    def _set_hint(self, state):
        if state not in self.HINT_COPY:
            return
        title, body = self.HINT_COPY[state]
        # In idle / pre-flash states, append the per-radio instructions so the
        # user has bootloader keys / connector / notes visible while choosing
        # firmware and prepping the radio.
        if state in self._RADIO_INFO_STATES:
            radio_info = self._format_radio_info()
            if radio_info:
                body = f"{body}\n\n— Selected radio —\n{radio_info}"
        # Render into the rich-text TextCtrl: bold title on its own line, blank
        # line, then body. SetDefaultStyle + AppendText is more reliable than
        # SetStyle on GTK (where the underlying GtkTextView has its own
        # attribute system that wx.TextAttr doesn't always reach via SetStyle).
        self.hint_text.Freeze()
        try:
            self.hint_text.Clear()
            bold = wx.Font(self.font_size, wx.FONTFAMILY_DEFAULT,
                           wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
            normal = wx.Font(self.font_size, wx.FONTFAMILY_DEFAULT,
                             wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
            bold_attr = wx.TextAttr()
            bold_attr.SetFont(bold)
            normal_attr = wx.TextAttr()
            normal_attr.SetFont(normal)

            self.hint_text.SetDefaultStyle(bold_attr)
            self.hint_text.AppendText(title + "\n\n")
            self.hint_text.SetDefaultStyle(normal_attr)
            self.hint_text.AppendText(body)

            self.hint_text.SetInsertionPoint(0)
            self.hint_text.ShowPosition(0)
        finally:
            self.hint_text.Thaw()

    def _compute_hint_state(self):
        if self._terminal_state in ("complete", "failed",
                                    "dryrun_complete", "diag_complete"):
            return self._terminal_state
        if self._busy:
            return self._busy_state if hasattr(self, "_busy_state") else "flashing"
        if not self.file_path.GetValue():
            return "no_firmware"
        if not self._selected_handset_indices():
            return "no_handset"
        if len(self._selected_handset_indices()) > 1:
            return "batch_ready"
        return "ready_flash"

    def _on_state_change(self, event):
        # User-initiated change clears any sticky terminal state
        self._terminal_state = None
        self._set_hint(self._compute_hint_state())
        self._update_workflow_gating()
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
                wx.CallAfter(self._notify_update, local_info, remote_info)
        except Exception:
            pass

    def _notify_update(self, local_info, remote_info):
        """An update is available — show the Update Available link in the status bar."""
        url = updater.get_releases_url()
        self._update_url = url
        try:
            self.update_link.SetURL(url)
            self.update_link.SetToolTip(
                f"Currently running v{VERSION} (latest available: {remote_info}).\n"
                f"Click to open the releases page in your browser."
            )
            self.update_link.Show()
            self.status_bar_panel.Layout()
        except Exception:
            pass

    def _get_selected_radio(self):
        # Combo entry 0 is the "— Select your radio —" placeholder. Real
        # radios start at index 1 and map to self.radios[idx - 1].
        idx = self.radio_combo.GetSelection()
        if idx >= 1 and (idx - 1) < len(self.radios):
            return self.radios[idx - 1]
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
        """Refresh the Download button label/state for the selected radio.

        Per-radio info (bootloader keys, connector, notes) is rendered inline
        in the hints panel via _set_hint(); this method only owns the
        Download button + hint refresh.
        """
        radio = self._get_selected_radio()
        if radio:
            url, version = self._get_firmware_url_and_version(radio)
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
        self._update_workflow_gating()

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
        wx.CallAfter(self.refresh_btn.Enable, enabled)
        wx.CallAfter(self.select_all_btn.Enable, enabled)
        wx.CallAfter(self.select_none_btn.Enable, enabled)
        if enabled:
            wx.CallAfter(self._update_radio_info)
            # Recompute hint AFTER the thread has set _terminal_state
            wx.CallAfter(lambda: self._set_hint(self._compute_hint_state()))
            # Re-apply workflow gating so we don't enable buttons that
            # should still be locked (e.g. Flash without a handset selected).
            wx.CallAfter(self._update_workflow_gating)

    def on_flash(self, event):
        firmware_path = self.file_path.GetValue()
        if not firmware_path:
            wx.MessageBox("Select a firmware file.", "Error", wx.OK | wx.ICON_ERROR)
            return

        selected = self._selected_handset_indices()
        if not selected:
            wx.MessageBox(
                "Check at least one handset in the Handset column.\n"
                "Click 'Refresh / Probe' if your cable isn't listed.",
                "No handset selected", wx.OK | wx.ICON_ERROR)
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

        # Same/older version checks (only meaningful for the single-handset path)
        file_version = fv.extract_version_from_filename(os.path.basename(firmware_path))
        if len(selected) == 1 and radio and file_version:
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

        # Confirmation: single vs batch
        if len(selected) == 1:
            port_label = self._handset_ports[selected[0]]["device"]
            dlg = wx.MessageDialog(self,
                f"{warning}"
                f"Make sure the {radio_name} on {port_label} is in bootloader mode:\n\n"
                f"1. Power off the radio\n"
                f"2. Hold {keys}\n"
                f"3. Turn power knob to turn on\n"
                f"4. Screen stays blank, green LED lights up\n\n"
                f"Do not disconnect the radio or cable during the update!\n\n"
                f"Ready to flash?",
                "Confirm", wx.YES_NO | wx.ICON_WARNING)
        else:
            ports_label = ", ".join(self._handset_ports[i]["device"] for i in selected)
            dlg = wx.MessageDialog(self,
                f"{warning}"
                f"Flash {len(selected)} handsets sequentially:\n  {ports_label}\n\n"
                f"Each {radio_name} must be in bootloader mode:\n"
                f"  • Power off, hold {keys}, then turn the power knob on\n"
                f"  • Screen stays blank, green Rx LED lights up\n\n"
                f"Do not disconnect any cable during the batch!\n\n"
                f"Ready to start?",
                "Confirm Batch Flash", wx.YES_NO | wx.ICON_WARNING)

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

        if len(selected) == 1:
            port = self._handset_ports[selected[0]]["device"]
            threading.Thread(target=self._flash_thread,
                             args=(port, firmware_path, selected[0]),
                             daemon=True).start()
        else:
            threading.Thread(target=self._batch_flash_thread,
                             args=(list(selected), firmware_path),
                             daemon=True).start()

    def _batch_flash_thread(self, selected_idxs, firmware_path):
        """Sequentially flash the same firmware to every checked handset.

        On per-port failure: prompt user to skip + continue or stop. Marks
        each row's Status (Flashing… → Done/Failed/Skipped) and Progress.
        """
        radio = self._get_selected_radio()
        radio_name = radio["name"] if radio else "Unknown"

        # Validate firmware once up front
        try:
            with open(firmware_path, "rb") as f:
                firmware_bytes = f.read()
            fw.validate_firmware(firmware_bytes, firmware_path)
        except Exception as e:
            self.log_msg(f"\nERROR: Firmware validation failed: {e}")
            self._terminal_state = "failed"
            self._busy = False
            self.set_buttons(True)
            return

        total = len(selected_idxs)
        succeeded = failed = skipped = 0
        try:
            for n, idx in enumerate(selected_idxs):
                entry = self._handset_ports[idx]
                port = entry["device"]
                wx.CallAfter(self._set_handset_status, idx, STATUS_FLASHING)
                wx.CallAfter(self._set_handset_progress, idx, "0%")
                self.log_msg(f"\n[{n + 1}/{total}] Flashing {port} ({entry['cable']})…")

                def log_cb(msg, _idx=idx):
                    self.log_msg(f"  [{self._handset_ports[_idx]['device']}] {msg}")

                def progress_cb(pct, _idx=idx):
                    pct_int = int(pct)
                    wx.CallAfter(self._set_handset_progress, _idx, f"{pct_int}%")

                try:
                    fw.flash_to_port(port, firmware_bytes,
                                     log_cb=log_cb, progress_cb=progress_cb)
                except Exception as e:
                    failed += 1
                    wx.CallAfter(self._set_handset_status, idx, STATUS_FAILED)
                    wx.CallAfter(self._set_handset_progress, idx, "—")
                    self.log_msg(f"  ERROR: {e}")
                    if self._is_permission_denied(e):
                        self._log_dialout_hint(port)
                        self.log_msg("Aborting batch — fix the permission and retry.")
                        for skip_idx in selected_idxs[n + 1:]:
                            wx.CallAfter(self._set_handset_status,
                                         skip_idx, STATUS_SKIPPED)
                            skipped += 1
                        break
                    if n < total - 1:
                        if not self._prompt_continue_batch(port, str(e)):
                            self.log_msg("Batch stopped by user.")
                            for skip_idx in selected_idxs[n + 1:]:
                                wx.CallAfter(self._set_handset_status,
                                             skip_idx, STATUS_SKIPPED)
                                skipped += 1
                            break
                        self.log_msg("Continuing with next handset…")
                else:
                    succeeded += 1
                    wx.CallAfter(self._set_handset_status, idx, STATUS_DONE)
                    wx.CallAfter(self._set_handset_progress, idx, "100%")
                self.set_progress(int((n + 1) * 100 / total))
        finally:
            self.log_msg(
                f"\nBatch finished: {succeeded} succeeded, "
                f"{failed} failed, {skipped} skipped."
            )
            self._terminal_state = "complete" if failed == 0 and skipped == 0 else "failed"
            self._busy = False
            self.set_buttons(True)

    def _prompt_continue_batch(self, port, err):
        """Block worker thread until user picks Continue or Stop on batch failure."""
        ev = threading.Event()
        choice = {"continue": False}

        def show():
            dlg = wx.MessageDialog(self,
                f"Flashing {port} failed:\n\n{err}\n\n"
                "Continue with the next selected handset?",
                "Batch Flash Failure", wx.YES_NO | wx.ICON_WARNING)
            choice["continue"] = (dlg.ShowModal() == wx.ID_YES)
            dlg.Destroy()
            ev.set()

        wx.CallAfter(show)
        ev.wait()
        return choice["continue"]

    def _flash_thread(self, port, firmware_path, handset_idx=None):
        radio = self._get_selected_radio()
        radio_name = radio["name"] if radio else "Unknown"

        if handset_idx is not None:
            wx.CallAfter(self._set_handset_status, handset_idx, STATUS_FLASHING)
            wx.CallAfter(self._set_handset_progress, handset_idx, "0%")

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
                    if handset_idx is not None:
                        wx.CallAfter(self._set_handset_progress, handset_idx, f"{int(pct)}%")
                    if (i + 1) % 10 == 0 or i == total_chunks - 1:
                        self.log_msg(f"  {pct:.0f}% ({i + 1}/{total_chunks})")

                self.log_msg("[3/3] Finalizing...")
                fw.send_command(ser, fw.CMD_UPDATE_END, 0)

            self.log_msg("  OK")
            self.log_msg("")
            self.log_msg("Firmware update complete!")
            self.log_msg("Power cycle the radio and check Menu > Radio Info.")
            if handset_idx is not None:
                wx.CallAfter(self._set_handset_status, handset_idx, STATUS_DONE)
                wx.CallAfter(self._set_handset_progress, handset_idx, "100%")

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
            if self._is_permission_denied(e):
                self._log_dialout_hint(port)
            else:
                self.log_msg("Radio may need to be power cycled and put back in bootloader mode.")
            self._terminal_state = "failed"
            if handset_idx is not None:
                wx.CallAfter(self._set_handset_status, handset_idx, STATUS_FAILED)
                wx.CallAfter(self._set_handset_progress, handset_idx, "—")
            wx.CallAfter(self._offer_test_report, radio_name, firmware_path, False, error_msg)
        finally:
            self._busy = False
            self.set_buttons(True)

    def _is_permission_denied(self, exc):
        """True if the exception looks like a serial-port EACCES (Linux dialout)."""
        if isinstance(exc, PermissionError):
            return True
        msg = str(exc)
        return "[Errno 13]" in msg or "Permission denied" in msg

    def _log_dialout_hint(self, port):
        """Surface a friendly explanation when /dev/ttyUSB* is denied at open()."""
        self.log_msg("")
        self.log_msg("This is a Linux serial-port permission issue, not a flashing problem.")
        self.log_msg(f"Your user is not in the 'dialout' group, so it can't open {port}.")
        self.log_msg("Fix it once and you won't see this again:")
        self.log_msg("  sudo usermod -aG dialout $USER")
        self.log_msg("Then log out and back in (a full re-login, not just a new terminal).")

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
            self._terminal_state = "dryrun_complete"

        except Exception as e:
            self.log_msg(f"\nERROR: {e}")
            self._terminal_state = "failed"
        finally:
            self._busy = False
            self.set_buttons(True)

    def on_diag(self, event):
        # Diagnostics runs on a single port — use the first checked handset.
        selected = self._selected_handset_indices()
        if not selected:
            wx.MessageBox(
                "Check at least one handset in the Handset column to run "
                "diagnostics on it.\nClick 'Refresh / Probe' to re-scan.",
                "No handset selected", wx.OK | wx.ICON_ERROR)
            return
        port = self._handset_ports[selected[0]]["device"]

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
                    self._terminal_state = "diag_complete"
                else:
                    self.log_msg("  RX: no data")
                    self.log_msg("")
                    self.log_msg("Radio did not respond.")
                    self.log_msg("Check: cable, bootloader mode, serial port.")
                    self._terminal_state = "failed"

            self.set_progress(100)

        except Exception as e:
            self.log_msg(f"\nERROR: {e}")
            if self._is_permission_denied(e):
                self._log_dialout_hint(port)
            self._terminal_state = "failed"
        finally:
            self._busy = False
            self.set_buttons(True)


def detect_os_theme():
    """Return 'mocha' or 'latte' based on the host OS's color scheme.

    Uses wx.SystemSettings.GetAppearance() (cross-platform: GTK on Linux,
    AppKit on macOS, UxTheme on Windows). Falls back to 'mocha' on any
    error so users on older wxPython still get a usable theme.
    """
    try:
        appearance = wx.SystemSettings.GetAppearance()
        return "mocha" if appearance.IsUsingDarkBackground() else "latte"
    except Exception:
        return "mocha"


def main():
    app = wx.App()
    frame = FlasherFrame()
    initial_theme = detect_os_theme()
    frame.current_theme = initial_theme
    frame.theme_btn.SetLabel("☀" if initial_theme == "mocha" else "☾")
    frame.Show()
    apply_theme(frame, initial_theme)
    # Apply the default font size to every widget so the user gets 12pt UI on
    # first launch (instead of only after they cycle the font button).
    frame._set_font_size(frame.font_size)
    app.MainLoop()


if __name__ == "__main__":
    main()
