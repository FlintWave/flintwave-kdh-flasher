#!/usr/bin/env python3
"""
GUI frontend for FlintWave Flash.
Supports BTECH, Baofeng, Radtel, and other KDH-bootloader radios.
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
import i18n
from i18n import t
import updater
import serial

from gui_ports import list_serial_ports, find_programming_cable, KNOWN_CABLES, FTDI_VID_PID
from gui_dialogs import (
    show_about_dialog,
    show_test_report_dialog,
)
from gui_themes import apply_theme, THEME_PALETTES, MOCHA_PALETTE

VERSION = "26.05.3"

FONT_SIZES = [9, 11, 12, 14, 16]

# Handset list status values are i18n keys; the rendering layer calls t() on
# them whenever a status cell is written. Status comparisons throughout the
# module continue to use these symbolic constants verbatim — only the on-screen
# representation runs through the translation table.
STATUS_UNKNOWN = "status.unknown"
STATUS_PROBING = "status.probing"
STATUS_READY = "status.ready"
STATUS_NO_RESP = "status.no_response"
STATUS_FLASHING = "status.flashing"
STATUS_DONE = "status.done"
STATUS_FAILED = "status.failed"
STATUS_SKIPPED = "status.skipped"


class FlasherFrame(wx.Frame):
    def __init__(self):
        # Load English fallback synchronously, then load the saved language from
        # cache if available. A non-English code with no cache will fall back to
        # English here and re-request its catalog the next time the user picks
        # it from the dropdown.
        i18n.load_bundled_en()
        saved_language = fm.get_language(default="en")
        if saved_language != "en":
            i18n.set_language_sync_if_cached(saved_language)

        # 16:9 default (1280x720), 16:9 minimum (960x540) for BalenaEtcher-like proportions.
        # NO_BORDER hides the OS title bar; we draw our own.  RESIZE_BORDER keeps
        # the window resizable from its edges.
        super().__init__(None, title=t("app.title"),
                         size=(1280, 720),
                         style=wx.NO_BORDER | wx.RESIZE_BORDER |
                         wx.MINIMIZE_BOX | wx.CLOSE_BOX |
                         wx.CLIP_CHILDREN)
        self.SetMinSize((960, 540))

        # Translation registry: list of (widget, kind, key) tuples populated by
        # _tr_label / _tr_tooltip. retranslate_ui walks this list to re-apply
        # translated labels in place. _rtl_targets holds container windows that
        # need an explicit SetLayoutDirection call on language change because
        # the propagation from the frame isn't reliable on all platforms.
        self._i18n_widgets = []
        self._rtl_targets = []
        self._prev_lang_index = i18n.index_of(i18n.current_code())

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
                                                  "column.instructions")
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
        log_label = self._column_heading(self.log_panel, "column.log")
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

        # Apply layout direction once at startup. For LTR languages this is a
        # no-op; for RTL (Arabic) it mirrors every sizer registered in
        # _rtl_targets so the first frame paints in the correct direction.
        if i18n.is_rtl():
            direction = wx.Layout_RightToLeft
            try:
                self.SetLayoutDirection(direction)
            except Exception:
                pass
            for target in self._rtl_targets:
                try:
                    target.SetLayoutDirection(direction)
                except Exception:
                    pass

        # If the user's saved language wasn't cached locally, kick off an
        # async download now so the UI reapplies the translation as soon as
        # the file lands. set_language_sync_if_cached returned False in that
        # case but i18n.current_code() is still "en".
        if saved_language != "en" and i18n.current_code() == "en":
            def _on_lang_loaded(success, _code=saved_language):
                if success:
                    wx.CallAfter(self.retranslate_ui)
            i18n.set_language(saved_language, on_done=_on_lang_loaded)

        # Background: update check, manifest fetch, port-change polling
        threading.Thread(target=self._check_update, daemon=True).start()
        threading.Thread(target=self._fetch_manifest, daemon=True).start()
        threading.Thread(target=self._port_poll_loop, daemon=True).start()

    # ------------------------------------------------------------------
    # i18n helpers
    # ------------------------------------------------------------------

    def _tr_label(self, widget, key):
        """Register `widget` for live label retranslation and set its label."""
        try:
            widget.SetLabel(t(key))
        except Exception:
            pass
        self._i18n_widgets.append((widget, "label", key))
        return widget

    def _tr_tooltip(self, widget, key):
        """Register `widget` for live tooltip retranslation and set its tooltip."""
        try:
            widget.SetToolTip(t(key))
        except Exception:
            pass
        self._i18n_widgets.append((widget, "tooltip", key))
        return widget

    def _resolve_direction(self):
        return (wx.Layout_RightToLeft if i18n.is_rtl()
                else wx.Layout_LeftToRight)

    def _apply_handset_columns(self):
        """Insert / rebuild the handset table column headers in the active language.

        Column header alignment doesn't auto-mirror under SetLayoutDirection, so
        we re-create the columns with the right format whenever the language
        changes.
        """
        if not hasattr(self, "handset_list"):
            return
        fmt = (wx.LIST_FORMAT_RIGHT if i18n.is_rtl()
               else wx.LIST_FORMAT_LEFT)
        # Stash existing widths so we don't lose user resize state.
        had_columns = self.handset_list.GetColumnCount() > 0
        widths = ([self.handset_list.GetColumnWidth(i) for i in range(4)]
                  if had_columns else [110, 140, 110, 50])
        self.handset_list.ClearAll()
        for idx, (key, width) in enumerate(zip(
                ("handset.col_port", "handset.col_cable",
                 "handset.col_status", "handset.col_percent"),
                widths)):
            self.handset_list.InsertColumn(idx, t(key),
                                           width=width, format=fmt)

    def _on_lang_change(self, event):
        """User picked a language from the title-bar dropdown."""
        if not hasattr(self, "lang_choice"):
            return
        idx = self.lang_choice.GetSelection()
        if idx < 0 or idx >= len(i18n.LANGUAGES):
            return
        code, label = i18n.LANGUAGES[idx]
        prev_index = self._prev_lang_index
        if code == i18n.current_code():
            self._prev_lang_index = idx
            return

        # Disable the dropdown while we (possibly) download.
        self.lang_choice.Disable()
        try:
            self.log_msg(t("lang.downloading").format(language=label))
        except Exception:
            pass

        def on_done(success, _code=code, _label=label, _prev=prev_index, _idx=idx):
            def apply_on_gui():
                self.lang_choice.Enable()
                if success:
                    try:
                        fm.set_language(_code)
                    except Exception:
                        pass
                    self._prev_lang_index = _idx
                    self.retranslate_ui()
                else:
                    self.lang_choice.SetSelection(_prev)
                    try:
                        self.log_msg(t("lang.download_failed").format(language=_label))
                    except Exception:
                        pass
            wx.CallAfter(apply_on_gui)

        i18n.set_language(code, on_done=on_done)

    def retranslate_ui(self):
        """Re-apply all translated labels/tooltips and adjust layout direction.

        Walks the registry populated by _tr_label / _tr_tooltip and re-fetches
        each string from the active catalog. Re-creates handset table column
        headers (their format flag doesn't auto-mirror) and refreshes the
        rendered status cells. Calls SetLayoutDirection on every container in
        _rtl_targets so RTL languages mirror the sizer layout reliably across
        platforms.
        """
        direction = self._resolve_direction()
        # Top-down direction propagation first, so subsequent label sets land
        # on widgets that already know whether to render LTR or RTL.
        try:
            self.SetLayoutDirection(direction)
        except Exception:
            pass
        for target in self._rtl_targets:
            try:
                target.SetLayoutDirection(direction)
            except Exception:
                pass

        # Window title (both wx.Frame.SetTitle and the custom title-bar label).
        try:
            self.SetTitle(t("app.title"))
        except Exception:
            pass
        if hasattr(self, "title_label") and self.title_label is not None:
            try:
                self.title_label.SetLabel(t("app.title"))
            except Exception:
                pass

        # Re-apply every registered label / tooltip.
        for widget, kind, key in self._i18n_widgets:
            try:
                if kind == "label":
                    widget.SetLabel(t(key))
                elif kind == "tooltip":
                    widget.SetToolTip(t(key))
            except Exception:
                continue

        # Handset table columns + cells.
        self._apply_handset_columns()
        for idx, entry in enumerate(self._handset_ports):
            try:
                self.handset_list.SetItem(idx, 2, t(entry["status"]))
            except Exception:
                continue
        self._refresh_handset_summary()
        self._refresh_radio_dropdown()
        self._update_radio_info()
        # Hint panel re-renders with the new language.
        try:
            self._set_hint(self._compute_hint_state())
        except Exception:
            pass

        # Force a relayout + repaint. On Windows the post-direction-change
        # state occasionally leaves residual artifacts; Refresh+Update clears
        # them.
        try:
            self.panel.Layout()
            self.panel.Refresh()
            self.Refresh()
            self.Update()
        except Exception:
            pass

    def _refresh_radio_dropdown(self):
        """Rebuild the radio combo so the placeholder is in the active language."""
        if not hasattr(self, "radio_combo"):
            return
        try:
            current = self.radio_combo.GetSelection()
            self.RADIO_PLACEHOLDER = t("radio.placeholder")
            radio_names = [self.RADIO_PLACEHOLDER] + [
                r['name'] if r['name'].startswith(r['manufacturer'])
                else f"{r['manufacturer']} {r['name']}"
                for r in self.radios
            ]
            self.radio_combo.SetItems(radio_names)
            self.radio_combo.SetSelection(max(0, current))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Layout builders
    # ------------------------------------------------------------------

    def _column_heading(self, parent, key):
        """Return a styled heading StaticText for a borderless column.

        Heading widgets are tracked in self._column_headings so _set_font_size
        can give them a bigger/bolder font than the body text. The `key` is a
        translation key (e.g. "column.firmware"); the heading is registered for
        live retranslation.
        """
        h = wx.StaticText(parent, label=t(key))
        self._tr_label(h, key)
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
        sizer.Add(self._column_heading(col, "column.firmware"), 0, wx.ALIGN_CENTER_HORIZONTAL | wx.BOTTOM, 6)

        # First entry is a placeholder so the user has to actively pick a
        # radio (instead of getting whichever radio happened to be in
        # radios.json[0] by default). _get_selected_radio() treats index 0 as
        # "no radio selected" (returns None).
        self.RADIO_PLACEHOLDER = t("radio.placeholder")
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

        self.download_btn = wx.Button(col, label=t("button.download_latest"))
        self.download_btn.Bind(wx.EVT_BUTTON, self.on_download)
        # Note: download_btn's label is set dynamically by _update_radio_info
        # (Download Latest / Download v… / No Direct URL) and so isn't tracked
        # in _i18n_widgets — retranslate_ui re-invokes _update_radio_info.
        sizer.Add(self.download_btn, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)

        file_row = wx.BoxSizer(wx.HORIZONTAL)
        self.file_path = wx.TextCtrl(col)
        file_row.Add(self.file_path, 1, wx.EXPAND | wx.RIGHT, 4)
        browse_btn = wx.Button(col, label=t("button.browse"))
        self._tr_label(browse_btn, "button.browse")
        browse_btn.Bind(wx.EVT_BUTTON, self.on_browse)
        file_row.Add(browse_btn, 0)
        sizer.Add(file_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)

        sizer.AddStretchSpacer(1)
        col.SetSizer(sizer)
        self._rtl_targets.append(col)
        return col

    def _build_handset_column(self, parent):
        col = wx.Panel(parent)
        col.SetMinSize(wx.Size(280, -1))
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self._column_heading(col, "column.handset"), 0, wx.ALIGN_CENTER_HORIZONTAL | wx.BOTTOM, 6)

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
        self._apply_handset_columns()
        sizer.Add(self.handset_list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)

        if self._handset_checkboxes_supported:
            self.handset_list.Bind(wx.EVT_LIST_ITEM_CHECKED, self._on_handset_check_changed)
            self.handset_list.Bind(wx.EVT_LIST_ITEM_UNCHECKED, self._on_handset_check_changed)
        else:
            self.handset_list.Bind(wx.EVT_LIST_ITEM_SELECTED, self._on_handset_check_changed)
            self.handset_list.Bind(wx.EVT_LIST_ITEM_DESELECTED, self._on_handset_check_changed)

        # Selection summary + selection helpers. Summary text is computed via
        # the i18n "handset.summary" template; _refresh_handset_summary() owns
        # the rendering.
        self.handset_summary = wx.StaticText(
            col, label=t("handset.summary").format(selected=0, total=0))
        sizer.Add(self.handset_summary, 0, wx.LEFT | wx.RIGHT | wx.TOP, 6)

        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        self.refresh_btn = wx.Button(col, label=t("button.refresh_probe"))
        self._tr_label(self.refresh_btn, "button.refresh_probe")
        self._tr_tooltip(self.refresh_btn, "tooltip.refresh")
        self.refresh_btn.Bind(wx.EVT_BUTTON, lambda e: self._refresh_handset_ports(probe=True))
        btn_row.Add(self.refresh_btn, 1, wx.RIGHT, 4)

        self.select_all_btn = wx.Button(col, label=t("button.select_all"))
        self._tr_label(self.select_all_btn, "button.select_all")
        self._tr_tooltip(self.select_all_btn, "tooltip.select_all")
        self.select_all_btn.Bind(wx.EVT_BUTTON, lambda e: self._set_all_handsets_checked(True))
        btn_row.Add(self.select_all_btn, 0, wx.RIGHT, 4)

        self.select_none_btn = wx.Button(col, label=t("button.select_none"))
        self._tr_label(self.select_none_btn, "button.select_none")
        self._tr_tooltip(self.select_none_btn, "tooltip.select_none")
        self.select_none_btn.Bind(wx.EVT_BUTTON, lambda e: self._set_all_handsets_checked(False))
        btn_row.Add(self.select_none_btn, 0)
        sizer.Add(btn_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)

        col.SetSizer(sizer)
        self._rtl_targets.append(col)
        self._rtl_targets.append(self.handset_list)
        return col

    def _build_flash_column(self, parent):
        col = wx.Panel(parent)
        col.SetMinSize(wx.Size(220, -1))
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self._column_heading(col, "column.flash"), 0, wx.ALIGN_CENTER_HORIZONTAL | wx.BOTTOM, 6)

        self.flash_btn = wx.Button(col, label=t("button.flash_firmware"))
        self._tr_label(self.flash_btn, "button.flash_firmware")
        flash_font = wx.Font(12, wx.FONTFAMILY_DEFAULT,
                             wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
        self.flash_btn.SetFont(flash_font)
        self.flash_btn.Bind(wx.EVT_BUTTON, self.on_flash)
        sizer.Add(self.flash_btn, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)

        self.progress = wx.Gauge(col, range=100)
        sizer.Add(self.progress, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)

        sizer.AddSpacer(4)

        sec_row = wx.BoxSizer(wx.HORIZONTAL)
        self.dryrun_btn = wx.Button(col, label=t("button.dry_run"))
        self._tr_label(self.dryrun_btn, "button.dry_run")
        self.dryrun_btn.Bind(wx.EVT_BUTTON, self.on_dry_run)
        sec_row.Add(self.dryrun_btn, 1, wx.RIGHT, 4)
        self.diag_btn = wx.Button(col, label=t("button.diagnostics"))
        self._tr_label(self.diag_btn, "button.diagnostics")
        self.diag_btn.Bind(wx.EVT_BUTTON, self.on_diag)
        sec_row.Add(self.diag_btn, 1)
        sizer.Add(sec_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)

        sizer.AddStretchSpacer(1)
        col.SetSizer(sizer)
        self._rtl_targets.append(col)
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

        # Language dropdown lives between the title and the window-control
        # glyphs (minimize / close). Under RTL it mirrors to the left side.
        self.lang_choice = wx.Choice(bar, choices=[label for _, label in i18n.LANGUAGES])
        self.lang_choice.SetSelection(i18n.index_of(i18n.current_code()))
        self.lang_choice.SetToolTip(t("titlebar.language_tooltip"))
        self._tr_tooltip(self.lang_choice, "titlebar.language_tooltip")
        self.lang_choice.Bind(wx.EVT_CHOICE, self._on_lang_change)
        sizer.Add(self.lang_choice, 0,
                  wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12)

        def make_chrome_btn(label, tooltip_key, handler):
            b = wx.StaticText(bar, label=label)
            b.SetCursor(wx.Cursor(wx.CURSOR_HAND))
            b.SetToolTip(t(tooltip_key))
            self._tr_tooltip(b, tooltip_key)
            b.Bind(wx.EVT_LEFT_DOWN, lambda e: handler())
            chrome_font = wx.Font(self.font_size + 2, wx.FONTFAMILY_DEFAULT,
                                  wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
            b.SetFont(chrome_font)
            return b

        self._minimize_btn = make_chrome_btn("—", "titlebar.minimize_tooltip", self.Iconize)
        self._close_btn = make_chrome_btn("✕", "titlebar.close_tooltip", self.Close)
        sizer.Add(self._minimize_btn, 0,
                  wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 16)
        sizer.Add(self._close_btn, 0,
                  wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 16)

        bar.SetSizer(sizer)
        bar.SetMinSize(wx.Size(-1, 36))
        self._rtl_targets.append(bar)

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

        def make_link(label, tooltip_key, handler, label_key=None):
            """Create a clickable StaticText (no button border)."""
            link = wx.StaticText(bar, label=label)
            link.SetCursor(wx.Cursor(wx.CURSOR_HAND))
            link.SetToolTip(t(tooltip_key))
            self._tr_tooltip(link, tooltip_key)
            if label_key is not None:
                self._tr_label(link, label_key)
            link.Bind(wx.EVT_LEFT_DOWN, lambda e: handler())
            return link

        self.font_btn = make_link(
            f"{self.font_size}pt", "tooltip.font_cycle", self._cycle_font)

        # Theme toggle: glyph reflects the destination — sun = "switch to light",
        # moon = "switch to dark". Currently mocha (dark) → show sun.
        self.theme_btn = make_link(
            "☀" if self.current_theme == "mocha" else "☾",
            "tooltip.theme_toggle", self._toggle_theme)

        usage_link = make_link(t("statusbar.usage"), "tooltip.usage",
                               lambda: self.on_usage_guide(None),
                               label_key="statusbar.usage")
        about_link = make_link(t("statusbar.about"), "tooltip.about",
                               lambda: self.on_about(None),
                               label_key="statusbar.about")

        # Hidden hyperlink: when _check_update finds a newer release we set
        # its URL and Show() it. Click opens the releases page.
        self.update_link = wx.adv.HyperlinkCtrl(
            bar, label=t("statusbar.update_available"),
            url="https://github.com/FlintWave/flintwave-kdh-flasher/releases/latest",
            style=wx.adv.HL_ALIGN_LEFT | wx.NO_BORDER)
        self._tr_label(self.update_link, "statusbar.update_available")
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
        self._rtl_targets.append(bar)
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
            self.handset_list.SetItem(idx, 2, t(entry["status"]))
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
                self.handset_list.SetItem(idx, 2, t(status))
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
        self.handset_summary.SetLabel(
            t("handset.summary").format(selected=sel, total=total))

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

    # Set of hint state IDs. The (title, body) strings are resolved dynamically
    # from the active translation catalog by _get_hint_copy(); keeping this as a
    # set rather than a dict-of-strings means language changes are picked up
    # without rebuilding any structures.
    HINT_STATES = {
        "no_firmware", "no_handset", "batch_ready", "ready_dryrun",
        "ready_flash", "downloading", "flashing", "dryrun", "diagnostics",
        "complete", "dryrun_complete", "diag_complete", "failed",
    }

    def _get_hint_copy(self, state):
        """Return (title, body) for a hint state in the active language."""
        if state not in self.HINT_STATES:
            return None
        return (t(f"hint.{state}.title"), t(f"hint.{state}.body"))

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
        bits.append(t("info.radio_label").format(name=full_name))
        if keys:
            bits.append(t("info.bootloader_keys").format(keys=keys))
        if connector:
            bits.append(t("info.connector").format(connector=connector))
        bits.append(t("info.tested") if tested else t("info.untested"))
        _, version = self._get_firmware_url_and_version(radio)
        if version:
            bits.append(t("info.latest_firmware").format(version=version))
        notes = radio.get("notes")
        if notes:
            bits.append("")
            bits.append(notes)
        return "\n".join(bits)

    def _set_hint(self, state):
        copy = self._get_hint_copy(state)
        if copy is None:
            return
        title, body = copy
        # In idle / pre-flash states, append the per-radio instructions so the
        # user has bootloader keys / connector / notes visible while choosing
        # firmware and prepping the radio.
        if state in self._RADIO_INFO_STATES:
            radio_info = self._format_radio_info()
            if radio_info:
                body = f"{body}\n\n{t('info.selected_radio_header')}\n{radio_info}"
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
                t("statusbar.update_tooltip").format(
                    local=VERSION, remote=remote_info)
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
                self.download_btn.SetLabel(t("button.no_direct_url"))
            elif version:
                self.download_btn.SetLabel(
                    t("button.download_versioned").format(version=version))
            else:
                self.download_btn.SetLabel(t("button.download_latest"))

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
                t("dialog.untested_body").format(radio=radio['name']),
                t("dialog.untested_title"), wx.YES_NO | wx.ICON_WARNING)
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
            self.log_msg(t("log.downloading_for").format(radio=radio['name']))
            self.log_msg(t("log.url").format(url=url or radio.get('firmware_url', 'N/A')))
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
            self.log_msg(t("log.firmware_extracted").format(path=kdhx_path))
            self.log_msg("")
            self.log_msg(t("log.firmware_ready"))

            wx.CallAfter(self.file_path.SetValue, kdhx_path)
            self._terminal_state = None  # path change will recompute hint

        except Exception as e:
            self.log_msg(t("log.error_prefix").format(message=e))
            if "No direct download URL" in str(e):
                page = radio.get("firmware_page", "")
                if page:
                    self.log_msg(t("log.visit_page").format(url=page))
            self._terminal_state = "failed"
        finally:
            self._busy = False
            self.set_buttons(True)

    def on_browse(self, event):
        dlg = wx.FileDialog(self, t("filedlg.select_firmware"),
                            wildcard=t("filedlg.wildcard"),
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
            wx.MessageBox(t("dialog.error_select_firmware"),
                          t("dialog.error_title"), wx.OK | wx.ICON_ERROR)
            return

        selected = self._selected_handset_indices()
        if not selected:
            wx.MessageBox(t("dialog.error_no_handset_flash"),
                          t("dialog.error_no_handset_title"),
                          wx.OK | wx.ICON_ERROR)
            return

        radio = self._get_selected_radio()
        if radio:
            keys = radio["bootloader_keys"]
            radio_name = radio["name"]
            tested = radio.get("tested", False)
        else:
            keys = t("fallback.bootloader_keys")
            radio_name = t("fallback.radio_name")
            tested = False

        warning = ""
        if not tested:
            warning = t("dialog.untested_warning").format(radio=radio_name)

        # Same/older version checks (only meaningful for the single-handset path)
        file_version = fv.extract_version_from_filename(os.path.basename(firmware_path))
        if len(selected) == 1 and radio and file_version:
            last = fm.get_last_flashed(radio["id"])
            if last and last.get("version") == file_version:
                same_dlg = wx.MessageDialog(self,
                    t("dialog.same_version_body").format(version=file_version),
                    t("dialog.same_version_title"),
                    wx.YES_NO | wx.ICON_QUESTION)
                if same_dlg.ShowModal() != wx.ID_YES:
                    same_dlg.Destroy()
                    return
                same_dlg.Destroy()
            elif last and last.get("version") and fv.compare_versions(file_version, last["version"]) < 0:
                older_dlg = wx.MessageDialog(self,
                    t("dialog.older_version_body").format(
                        file_version=file_version, last_version=last['version']),
                    t("dialog.older_version_title"),
                    wx.YES_NO | wx.ICON_WARNING)
                if older_dlg.ShowModal() != wx.ID_YES:
                    older_dlg.Destroy()
                    return
                older_dlg.Destroy()

        # Confirmation: single vs batch
        if len(selected) == 1:
            port_label = self._handset_ports[selected[0]]["device"]
            dlg = wx.MessageDialog(self,
                t("dialog.confirm_single").format(
                    warning=warning, radio=radio_name,
                    port=port_label, keys=keys),
                t("dialog.confirm_title"), wx.YES_NO | wx.ICON_WARNING)
        else:
            ports_label = ", ".join(self._handset_ports[i]["device"] for i in selected)
            dlg = wx.MessageDialog(self,
                t("dialog.confirm_batch_body").format(
                    warning=warning, count=len(selected),
                    ports=ports_label, radio=radio_name, keys=keys),
                t("dialog.confirm_batch_title"),
                wx.YES_NO | wx.ICON_WARNING)

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
        radio_name = radio["name"] if radio else t("fallback.radio_unknown")

        # Validate firmware once up front
        try:
            with open(firmware_path, "rb") as f:
                firmware_bytes = f.read()
            fw.validate_firmware(firmware_bytes, firmware_path)
        except Exception as e:
            self.log_msg(t("log.error_prefix").format(message=e))
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
                self.log_msg(t("log.batch_start").format(
                    n=n + 1, total=total, port=port, cable=entry['cable']))

                def log_cb(msg, _idx=idx):
                    self.log_msg(t("log.batch_per_port").format(
                        port=self._handset_ports[_idx]['device'], message=msg))

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
                    self.log_msg(t("log.batch_error").format(message=e))
                    if self._is_permission_denied(e):
                        self._log_dialout_hint(port)
                        self.log_msg(t("log.batch_abort_permission"))
                        for skip_idx in selected_idxs[n + 1:]:
                            wx.CallAfter(self._set_handset_status,
                                         skip_idx, STATUS_SKIPPED)
                            skipped += 1
                        break
                    if n < total - 1:
                        if not self._prompt_continue_batch(port, str(e)):
                            self.log_msg(t("log.batch_stopped"))
                            for skip_idx in selected_idxs[n + 1:]:
                                wx.CallAfter(self._set_handset_status,
                                             skip_idx, STATUS_SKIPPED)
                                skipped += 1
                            break
                        self.log_msg(t("log.batch_continuing"))
                else:
                    succeeded += 1
                    wx.CallAfter(self._set_handset_status, idx, STATUS_DONE)
                    wx.CallAfter(self._set_handset_progress, idx, "100%")
                self.set_progress(int((n + 1) * 100 / total))
        finally:
            self.log_msg(t("log.batch_summary").format(
                ok=succeeded, failed=failed, skipped=skipped))
            self._terminal_state = "complete" if failed == 0 and skipped == 0 else "failed"
            self._busy = False
            self.set_buttons(True)

    def _prompt_continue_batch(self, port, err):
        """Block worker thread until user picks Continue or Stop on batch failure."""
        ev = threading.Event()
        choice = {"continue": False}

        def show():
            dlg = wx.MessageDialog(self,
                t("dialog.batch_failure_body").format(port=port, error=err),
                t("dialog.batch_failure_title"),
                wx.YES_NO | wx.ICON_WARNING)
            choice["continue"] = (dlg.ShowModal() == wx.ID_YES)
            dlg.Destroy()
            ev.set()

        wx.CallAfter(show)
        ev.wait()
        return choice["continue"]

    def _flash_thread(self, port, firmware_path, handset_idx=None):
        radio = self._get_selected_radio()
        radio_name = radio["name"] if radio else t("fallback.radio_unknown")

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
                raise ValueError(t("log.file_too_large").format(size=fw_size))
            with open(firmware_path, "rb") as f:
                firmware = f.read()

            fw.validate_firmware(firmware, firmware_path)
            total_chunks = math.ceil(len(firmware) / 1024)
            sha256 = hashlib.sha256(firmware).hexdigest()
            self.log_msg(t("log.firmware_path").format(path=firmware_path))
            self.log_msg(t("log.size_chunks").format(size=len(firmware), chunks=total_chunks))
            self.log_msg(t("log.sha256").format(hash=sha256))
            self.log_msg(t("log.port").format(port=port))
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

                self.log_msg(t("log.step_handshake"))
                fw.send_command(ser, fw.CMD_HANDSHAKE, 0, b"BOOTLOADER")
                self.log_msg(t("log.step_handshake_ok"))

                self.log_msg(t("log.step_sending").format(chunks=total_chunks))
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
                        self.log_msg(t("log.progress_line").format(
                            pct=f"{pct:.0f}", done=i + 1, total=total_chunks))

                self.log_msg(t("log.step_finalize"))
                fw.send_command(ser, fw.CMD_UPDATE_END, 0)

            self.log_msg(t("log.step_handshake_ok"))
            self.log_msg("")
            self.log_msg(t("log.flash_complete"))
            self.log_msg(t("log.power_cycle"))
            if handset_idx is not None:
                wx.CallAfter(self._set_handset_status, handset_idx, STATUS_DONE)
                wx.CallAfter(self._set_handset_progress, handset_idx, "100%")

            # Record flash version
            file_version = fv.extract_version_from_filename(os.path.basename(firmware_path))
            if radio and file_version:
                try:
                    fm.record_flash(radio["id"], file_version, sha256)
                    self.log_msg(t("log.recorded_flash").format(
                        version=file_version, radio=radio_name))
                except Exception:
                    pass
                # Compare against latest known
                _, latest_ver = self._get_firmware_url_and_version(radio)
                if latest_ver and file_version:
                    cmp = fv.compare_versions(file_version, latest_ver)
                    if cmp == 0:
                        self.log_msg(t("log.fw_is_latest").format(version=file_version))
                    elif cmp < 0:
                        self.log_msg(t("log.fw_newer_available").format(
                            latest=latest_ver, current=file_version))

            self._terminal_state = "complete"
            wx.CallAfter(self._offer_test_report, radio_name, firmware_path, True, "")

        except Exception as e:
            error_msg = str(e)
            self.log_msg(t("log.error_prefix").format(message=error_msg))
            if self._is_permission_denied(e):
                self._log_dialout_hint(port)
            else:
                self.log_msg(t("log.may_need_power_cycle"))
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
        self.log_msg(t("log.dialout_intro"))
        self.log_msg(t("log.dialout_user").format(port=port))
        self.log_msg(t("log.dialout_fix_intro"))
        self.log_msg(t("log.dialout_fix_cmd"))
        self.log_msg(t("log.dialout_relogin"))

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
                t("dialog.cleanup_body").format(
                    size_mb=size_mb, path=download_dir),
                t("dialog.cleanup_title"),
                wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION)

            if dlg.ShowModal() == wx.ID_YES:
                import shutil
                shutil.rmtree(download_dir, ignore_errors=True)
                self.log_msg(t("log.cleanup_done"))
            dlg.Destroy()
        except Exception:
            pass

    def on_dry_run(self, event):
        firmware_path = self.file_path.GetValue()
        if not firmware_path:
            wx.MessageBox(t("dialog.error_select_firmware_first"),
                          t("dialog.error_title"), wx.OK | wx.ICON_ERROR)
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

            self.log_msg(t("log.dryrun_header"))
            self.log_msg("")

            fw_size = os.path.getsize(firmware_path)
            if fw_size > fw.MAX_FIRMWARE_BYTES:
                self.log_msg(t("log.fail_too_large").format(
                    size=fw_size, max=fw.MAX_FIRMWARE_BYTES))
                self._terminal_state = "failed"
                return
            with open(firmware_path, "rb") as f:
                firmware = f.read()

            fw_size = len(firmware)
            total_chunks = math.ceil(fw_size / 1024)

            if fw_size < fw.MIN_FIRMWARE_BYTES:
                self.log_msg(t("log.fail_too_small").format(size=fw_size))
                self._terminal_state = "failed"
                return
            if total_chunks > fw.MAX_CHUNKS:
                self.log_msg(t("log.fail_too_many_chunks").format(
                    chunks=total_chunks, max=fw.MAX_CHUNKS))
                self._terminal_state = "failed"
                return

            sha256 = hashlib.sha256(firmware).hexdigest()
            self.log_msg(t("log.firmware_path").format(path=firmware_path))
            self.log_msg(t("log.size_chunks").format(size=fw_size, chunks=total_chunks))
            self.log_msg(t("log.sha256").format(hash=sha256))
            self.log_msg("")

            sp = int.from_bytes(firmware[0:4], "little")
            reset = int.from_bytes(firmware[4:8], "little")
            ok_sp = 0x20000000 <= sp <= 0x20100000
            ok_reset = 0x08000000 <= reset <= 0x08100000
            valid_lbl = t("log.validity_valid")
            invalid_lbl = t("log.validity_invalid")
            self.log_msg(t("log.vector_table_check"))
            self.log_msg(t("log.stack_pointer").format(
                value=sp, validity=valid_lbl if ok_sp else invalid_lbl))
            self.log_msg(t("log.reset_handler").format(
                value=reset, validity=valid_lbl if ok_reset else invalid_lbl))
            if not ok_sp or not ok_reset:
                self.log_msg("")
                self.log_msg(t("log.invalid_vector"))
                self._terminal_state = "failed"
                return

            self.log_msg("")
            self.log_msg(t("log.building_packets"))
            self.set_progress(10)

            for i in range(total_chunks):
                chunk = firmware[i * 1024:(i + 1) * 1024]
                p = fw.build_packet(fw.CMD_UPDATE, i & 0xFF, chunk)
                payload = p[1:-3]
                pkt_crc = (p[-3] << 8) | p[-2]
                if fw.crc16_ccitt(payload) != pkt_crc:
                    self.log_msg(t("log.crc_fail_chunk").format(chunk=i))
                    self._terminal_state = "failed"
                    return
                self.set_progress(10 + (i + 1) / total_chunks * 90)

            self.log_msg(t("log.packets_built").format(count=total_chunks + 3))
            self.log_msg("")
            self.log_msg(t("log.dryrun_passed"))
            self.set_progress(100)
            self._terminal_state = "dryrun_complete"

        except Exception as e:
            self.log_msg(t("log.error_prefix").format(message=e))
            self._terminal_state = "failed"
        finally:
            self._busy = False
            self.set_buttons(True)

    def on_diag(self, event):
        # Diagnostics runs on a single port — use the first checked handset.
        selected = self._selected_handset_indices()
        if not selected:
            wx.MessageBox(t("dialog.error_no_handset_diag"),
                          t("dialog.error_no_handset_title"),
                          wx.OK | wx.ICON_ERROR)
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

            self.log_msg(t("log.diag_running").format(port=port))
            self.log_msg("")

            with serial.Serial(
                port=port, baudrate=115200, bytesize=8,
                parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
                timeout=1.0
            ) as ser:
                ser.dtr = True
                ser.rts = True
                time.sleep(0.1)

                self.log_msg(t("log.diag_serial_info").format(
                    baud=ser.baudrate, dtr=ser.dtr, rts=ser.rts))
                self.log_msg(t("log.diag_modem_lines").format(
                    cts=ser.cts, dsr=ser.dsr))
                self.log_msg("")

                self.log_msg(t("log.diag_sending"))
                packet = fw.build_packet(fw.CMD_HANDSHAKE, 0, b"BOOTLOADER")
                self.log_msg(t("log.diag_tx").format(hex=packet.hex()))
                ser.reset_input_buffer()
                ser.write(packet)
                ser.flush()

                self.set_progress(50)
                time.sleep(1.0)
                avail = ser.in_waiting
                if avail:
                    data = ser.read(min(avail, 128))
                    self.log_msg(t("log.diag_rx").format(count=avail, hex=data.hex()))
                    self.log_msg("")
                    self.log_msg(t("log.diag_responding"))
                    self._terminal_state = "diag_complete"
                else:
                    self.log_msg(t("log.diag_no_rx"))
                    self.log_msg("")
                    self.log_msg(t("log.diag_no_response"))
                    self.log_msg(t("log.diag_check"))
                    self._terminal_state = "failed"

            self.set_progress(100)

        except Exception as e:
            self.log_msg(t("log.error_prefix").format(message=e))
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
