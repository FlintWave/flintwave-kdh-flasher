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
import flash_btf as fw_btf
import firmware_download as dl
import firmware_manifest as fm
import firmware_version as fv
import i18n
from i18n import t, t_radio_field, t_variant_field
import serial

from gui_dialogs import (
    show_about_dialog,
    show_test_report_dialog,
)
from gui_themes import apply_theme, THEME_PALETTES, MOCHA_PALETTE
from gui_themes import _walk as _theme_walk, _style_widget as _theme_style_widget
from gui_workflow import (
    compute_gates,
    HINT_STATES as WORKFLOW_HINT_STATES,
    RADIO_INFO_STATES,
)
from gui_titlebar import TitleBar
from gui_statusbar import StatusBar, theme_toggle_glyph
from gui_columns import (
    FirmwareColumn, HandsetColumn, FlashColumn, radio_display_name,
)
from gui_hints import HintPresenter, format_variant_prompt
from gui_download import DownloadController
from gui_handset import (
    HandsetController,
    # Flash-worker status values (i18n keys) — comparisons use these symbolic
    # constants; only the on-screen text runs through t(). The full status
    # vocabulary lives in gui_handset alongside the controller that emits it.
    STATUS_FLASHING, STATUS_DONE, STATUS_FAILED, STATUS_SKIPPED,
)

VERSION = "26.07.0"

FONT_SIZES = [9, 11, 12, 14, 16]


class FlasherFrame(wx.Frame):
    # Sentinel stored in _variant_choice when the user answers "I'm not sure"
    # to a hardware-variant question. Distinct from "unanswered" (absent key)
    # so the info panel can show the firmware_page confirm link.
    VARIANT_UNSURE = "__unsure__"

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
        self._closing = False        # set on EVT_CLOSE so bg loops can stop
        self._terminal_state = None  # set to "complete"/"failed" by threads
        # Handset-column behavior (port discovery, probe, poll, selection) lives
        # in HandsetController; the frame exposes thin delegators below.
        self.handset = HandsetController(self)
        # Instructions-panel presentation (hint state machine + per-radio info)
        # lives in HintPresenter; the frame exposes thin delegators below.
        self.hints = HintPresenter(self)
        # Firmware acquisition + update notification (download worker, firmware
        # discovery, updater/manifest background tasks) lives in
        # DownloadController; the frame exposes thin delegators below and a
        # `manifest` property shim over the controller.
        self.download = DownloadController(self)

        # Window icon
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon_128.png")
        if os.path.exists(icon_path):
            self.SetIcon(wx.Icon(icon_path))

        panel = wx.Panel(self)
        self.panel = panel
        root_sizer = wx.BoxSizer(wx.VERTICAL)

        # ---- Custom title bar (replaces OS chrome) ----
        self.title_bar = TitleBar(panel, self)
        root_sizer.Add(self.title_bar, 0, wx.EXPAND)

        # ---- Main splitter: three-column workflow above, instructions/log
        # below. Replaces the old fixed 2:1 sizer stack so the user can drag
        # vertical space to whichever area needs it — the Instructions box was
        # chronically crushed at the fixed ratio. Sash positions persist
        # across runs (see _apply_sash_ratios / _on_sash_changed).
        self._main_split = wx.SplitterWindow(
            panel, style=wx.SP_LIVE_UPDATE | wx.SP_NOBORDER)
        # Base floor; the real (asymmetric) limits are enforced in
        # _clamp_main_sash — the three columns need more room than the
        # instructions/log row before their children start overlapping.
        self._main_split.SetMinimumPaneSize(120)
        # Window resizes distribute ~60/40, keeping the BalenaEtcher feel.
        self._main_split.SetSashGravity(0.6)
        self._main_split.Bind(wx.EVT_SPLITTER_SASH_POS_CHANGING,
                              self._on_main_sash_changing)
        # Gravity-driven window resizes bypass the CHANGING event and can
        # push the top pane under its minimum; re-clamp after each resize.
        self._main_split.Bind(wx.EVT_SIZE, self._on_main_split_size)

        top_panel = wx.Panel(self._main_split)
        top_row = wx.BoxSizer(wx.HORIZONTAL)

        # Manifest state is owned by DownloadController (constructed above, so
        # it's resolvable before the first _update_radio_info) and exposed here
        # via the `manifest` property shim.
        self.radios = dl.load_radios()
        # Hardware-variant groups collapse their sibling members into one
        # dropdown "family" row. _variant_choice maps a group id to the
        # answer the user picked: a concrete member radio id (resolved),
        # the VARIANT_UNSURE sentinel ("I'm not sure" → stop safe), or absent
        # (not yet answered). Selection never guesses — an unresolved group
        # keeps Download disabled.
        self._variant_choice = {}

        col_firmware = FirmwareColumn(top_panel, self)
        col_handset = HandsetColumn(top_panel, self)
        col_flash = FlashColumn(top_panel, self)

        # Bumped one size larger from previous 20pt to give more visual weight.
        arrow_font = wx.Font(28, wx.FONTFAMILY_DEFAULT,
                             wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
        self.arrow1 = wx.StaticText(top_panel, label="›")  # firmware → handset
        self.arrow1.SetFont(arrow_font)
        self.arrow2 = wx.StaticText(top_panel, label="›")  # handset → flash
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
        top_panel.SetSizer(top_row)

        # ---- Bottom half: instructions | log behind their own draggable
        # sash (the main splitter's sash replaces the old decorative
        # divider line).
        self._bottom_split = wx.SplitterWindow(
            self._main_split, style=wx.SP_LIVE_UPDATE | wx.SP_NOBORDER)
        self._bottom_split.SetMinimumPaneSize(220)
        self._bottom_split.SetSashGravity(0.5)

        # Instructions panel (left half). Use a read-only wx.TextCtrl with
        # rich-text styling for the body; native multi-line TextCtrl gives us
        # word-wrap + a v-scrollbar for free, which the previous
        # StaticText-in-ScrolledPanel approach couldn't reliably deliver.
        self._instructions_outer = wx.Panel(self._bottom_split)
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

        # (The hardware-variant walkthrough used to render here, under the
        # instructions text; it now lives in the Firmware column, directly
        # under the radio picker — see FirmwareColumn, which exposes it as
        # self._variant_panel.)
        self._instructions_outer.SetSizer(outer_sizer)

        # Log panel (right half) — heading + textarea
        self.log_panel = wx.Panel(self._bottom_split)
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

        self._bottom_split.SplitVertically(self._instructions_outer,
                                           self.log_panel)
        self._main_split.SplitHorizontally(top_panel, self._bottom_split)
        root_sizer.Add(self._main_split, 1, wx.EXPAND)

        # Apply saved (or default) sash ratios once the window has real
        # geometry, and persist any drag the user makes.
        wx.CallAfter(self._apply_sash_ratios)
        for splitter in (self._main_split, self._bottom_split):
            splitter.Bind(wx.EVT_SPLITTER_SASH_POS_CHANGED,
                          self._on_sash_changed)

        # ---- Bottom status bar: borderless text/icon links, darker background ----
        self.status_bar_panel = StatusBar(panel, self)
        # Keep frame-level handles to the widgets the frame updates directly
        # (font/theme/lang labels, the update-available link).
        self.font_btn = self.status_bar_panel.font_btn
        self.theme_btn = self.status_bar_panel.theme_btn
        self.lang_btn = self.status_bar_panel.lang_btn
        self.update_link = self.status_bar_panel.update_link
        root_sizer.Add(self.status_bar_panel, 0, wx.EXPAND)

        panel.SetSizer(root_sizer)
        self.Centre()

        # Bind change events that update hint state
        self.file_path.Bind(wx.EVT_TEXT, self._on_state_change)

        # Stop background loops cleanly when the window closes, and give the
        # borderless frame (no OS chrome) a keyboard way to quit — otherwise a
        # keyboard-only user has no route to close the app.
        self.Bind(wx.EVT_CLOSE, self._on_close)
        # Retain the id ref on the instance — a local would be garbage-collected
        # after __init__, freeing the id for recycling while the accelerator
        # table and Bind still reference its integer value.
        self._close_id = wx.NewIdRef()
        self.Bind(wx.EVT_MENU, lambda e: self.Close(), id=self._close_id)
        self.SetAcceleratorTable(wx.AcceleratorTable([
            wx.AcceleratorEntry(wx.ACCEL_CTRL, ord("W"), self._close_id),
            wx.AcceleratorEntry(wx.ACCEL_CTRL, ord("Q"), self._close_id),
            wx.AcceleratorEntry(wx.ACCEL_CMD, ord("W"), self._close_id),
            wx.AcceleratorEntry(wx.ACCEL_CMD, ord("Q"), self._close_id),
        ]))

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
        threading.Thread(target=self.handset.port_poll_loop, daemon=True).start()

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

    def _language_button_label(self):
        """Status-bar text for the language button — native label of the
        currently active language, prefixed with a globe glyph so it's
        visually distinct from the textual font/theme controls."""
        for code, label in i18n.LANGUAGES:
            if code == i18n.current_code():
                return f"\U0001F310 {label}"
        return "\U0001F310"  # globe alone if current code is unknown

    def _open_language_dialog(self):
        """Modal language picker. Single-select listbox with native-script
        labels; OK applies the change (downloading the catalog if needed),
        Cancel reverts."""
        dlg = wx.Dialog(self, title=t("dialog.language.title"),
                        style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        dlg.SetMinSize((320, 360))

        sizer = wx.BoxSizer(wx.VERTICAL)
        prompt = wx.StaticText(dlg, label=t("dialog.language.prompt"))
        sizer.Add(prompt, 0, wx.ALL, 12)

        # Unreviewed (machine-translated) catalogs get a "help review" tag so
        # native speakers know where help is wanted (see CONTRIBUTING.md).
        labels = [
            label if i18n.is_reviewed(code)
            else f"{label} — {t('dialog.language.unreviewed')}"
            for code, label in i18n.LANGUAGES
        ]
        listbox = wx.ListBox(dlg, choices=labels, style=wx.LB_SINGLE)
        try:
            listbox.SetSelection(i18n.index_of(i18n.current_code()))
        except Exception:
            pass
        # Double-click on an entry = OK.
        listbox.Bind(wx.EVT_LISTBOX_DCLICK, lambda e: dlg.EndModal(wx.ID_OK))
        sizer.Add(listbox, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)

        sizer.AddSpacer(8)
        btn_row = dlg.CreateButtonSizer(wx.OK | wx.CANCEL)
        if btn_row is not None:
            sizer.Add(btn_row, 0, wx.EXPAND | wx.ALL, 12)
        dlg.SetSizer(sizer)

        # Re-use the existing dialog-theme helper so the modal matches the
        # current Latte/Mocha palette.
        try:
            from gui_themes import apply_theme_to_dialog
            apply_theme_to_dialog(self, dlg)
        except Exception:
            pass

        dlg.Centre()
        result = dlg.ShowModal()
        idx = listbox.GetSelection()
        dlg.Destroy()
        if result != wx.ID_OK or idx < 0 or idx >= len(i18n.LANGUAGES):
            return

        code, label = i18n.LANGUAGES[idx]
        if code == i18n.current_code():
            return

        try:
            self.log_msg(t("lang.downloading").format(language=label))
        except Exception:
            pass

        def on_done(success, _code=code, _label=label):
            def apply_on_gui():
                if success:
                    try:
                        fm.set_language(_code)
                    except Exception:
                        pass
                    self.retranslate_ui()
                    self.lang_btn.SetLabel(self._language_button_label())
                    self.status_bar_panel.Layout()
                else:
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
        title_bar = getattr(self, "title_bar", None)
        title_label = getattr(title_bar, "title_label", None)
        if title_label is not None:
            try:
                title_label.SetLabel(t("app.title"))
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

        # If the update_link is already visible, re-pin its min size so the
        # newly-translated label isn't clipped by the cached sizer slot.
        try:
            if hasattr(self, "update_link") and self.update_link.IsShown():
                self.update_link.SetMinSize(self.update_link.GetBestSize())
                self.status_bar_panel.Layout()
        except Exception:
            pass

        # Language button reflects the active language by name; refresh it
        # whenever any language change comes through retranslate_ui.
        try:
            if hasattr(self, "lang_btn"):
                self.lang_btn.SetLabel(self._language_button_label())
                self.status_bar_panel.Layout()
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
            self.radio_combo.SetItems(self.radio_dropdown_labels())
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

    def _toggle_theme(self):
        """Switch between mocha (dark) and latte (light), re-render everything."""
        new_theme = "latte" if self.current_theme == "mocha" else "mocha"
        apply_theme(self, new_theme)
        # Re-glyph the toggle to point at the *next* destination.
        self.theme_btn.SetLabel(theme_toggle_glyph(self.current_theme))
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

    # --- Handset column: behavior delegated to HandsetController (gui_handset)
    # These thin wrappers keep the existing call sites (flash workers,
    # workflow gating, gui_columns, retranslate_ui) unchanged while the logic
    # lives in the controller.

    @property
    def _handset_ports(self):
        return self.handset.ports

    def _apply_handset_columns(self):
        self.handset.apply_columns()

    def _refresh_handset_ports(self, probe=False, preserve_checks=False):
        self.handset.refresh_ports(probe=probe, preserve_checks=preserve_checks)

    def _refresh_handset_summary(self):
        self.handset.refresh_summary()

    def _set_handset_status(self, idx, status):
        self.handset.set_status(idx, status)

    def _set_handset_progress(self, idx, text):
        self.handset.set_progress(idx, text)

    def _set_all_handsets_checked(self, checked):
        self.handset.set_all_checked(checked)

    def _on_handset_check_changed(self, event):
        self.handset.on_check_changed(event)

    def _selected_handset_indices(self):
        return self.handset.selected_indices()

    def _selected_handset_devices(self):
        return self.handset.selected_devices()

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
        gates = compute_gates(radio_chosen=radio_chosen,
                              firmware_ready=firmware,
                              handset_ready=handset)

        # Firmware column: Download requires a real radio. Browse is always
        # available (user may already have a .kdhx on disk).
        try:
            self.download_btn.Enable(gates.download)
        except Exception:
            pass

        # Handset column gate
        for w in (self.refresh_btn, self.select_all_btn, self.select_none_btn,
                  self.handset_list):
            try:
                w.Enable(gates.handset)
            except Exception:
                pass
        # Flash column gate
        for w in (self.flash_btn, self.dryrun_btn, self.diag_btn):
            try:
                w.Enable(gates.flash)
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
    # Font controls (theme has a Mocha/Latte toggle in the status bar)
    # ------------------------------------------------------------------

    # Asymmetric limits for the main (vertical) split: below ~300px the
    # three workflow columns' children start overlapping (the variant
    # walkthrough compresses its text first, but buttons/rows have fixed
    # heights); the instructions/log row degrades gracefully down to ~140px
    # since both sides are scrollable text.
    _MAIN_TOP_MIN = 300
    _MAIN_BOTTOM_MIN = 140

    def _clamp_main_sash(self, position):
        height = max(1, self._main_split.GetClientSize().height)
        upper = max(self._MAIN_TOP_MIN, height - self._MAIN_BOTTOM_MIN)
        return max(self._MAIN_TOP_MIN, min(int(position), upper))

    def _on_main_sash_changing(self, event):
        event.SetSashPosition(self._clamp_main_sash(event.GetSashPosition()))

    def _on_main_split_size(self, event):
        event.Skip()
        if not self._main_split.IsSplit():
            return

        def _reclamp():
            clamped = self._clamp_main_sash(
                self._main_split.GetSashPosition())
            if clamped != self._main_split.GetSashPosition():
                self._main_split.SetSashPosition(clamped)

        wx.CallAfter(_reclamp)

    def _apply_sash_ratios(self):
        """Set both splitter sashes from persisted ratios (or defaults).

        Ratios rather than pixels so the layout scales with the window; runs
        via wx.CallAfter once the frame has real geometry. Clamped so a
        corrupt state file can't produce a collapsed pane.
        """
        try:
            saved = fm.get_ui_sashes()
        except Exception:
            saved = {}

        def _ratio(key, default):
            try:
                value = float(saved.get(key, default))
            except (TypeError, ValueError):
                value = default
            return min(0.85, max(0.15, value))

        height = max(1, self._main_split.GetClientSize().height)
        width = max(1, self._bottom_split.GetClientSize().width)
        self._main_split.SetSashPosition(
            self._clamp_main_sash(height * _ratio("main", 0.60)))
        self._bottom_split.SetSashPosition(int(width * _ratio("bottom", 0.50)))

    def _on_sash_changed(self, event):
        """Persist sash ratios after a user drag (best-effort)."""
        event.Skip()
        try:
            height = max(1, self._main_split.GetClientSize().height)
            width = max(1, self._bottom_split.GetClientSize().width)
            fm.set_ui_sashes(
                self._main_split.GetSashPosition() / height,
                self._bottom_split.GetSashPosition() / width)
        except Exception:
            # Losing a sash preference is not worth surfacing an error.
            pass

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
    # Source of truth lives in gui_workflow so the pure logic and its tests
    # share one definition.
    HINT_STATES = WORKFLOW_HINT_STATES

    def _get_hint_copy(self, state):
        # Delegates to HintPresenter; kept as a same-named thin wrapper.
        return self.hints.get_hint_copy(state)

    # States during which it's useful to also show the per-radio info
    # (bootloader keys, connector type, notes from radios.json). Defined in
    # gui_workflow alongside the state machine that produces these keys.
    _RADIO_INFO_STATES = RADIO_INFO_STATES

    def _format_radio_info(self):
        # Delegates to HintPresenter; the pure string-building lives in
        # gui_hints.format_radio_info / format_variant_prompt.
        return self.hints.radio_info()

    def _clear_variant_panel(self):
        """Empty and hide the variant walkthrough controls."""
        panel = getattr(self, "_variant_panel", None)
        if panel is None:
            return
        panel.DestroyChildren()
        panel.GetSizer().Clear()
        if panel.IsShown():
            panel.Hide()
            panel.GetParent().Layout()

    def _render_variant_options(self, group_id, group):
        """(Re)build the variant walkthrough in the Firmware column: the
        translated identification question and steps, one radio button per
        member (translated variant_label), an "I'm not sure" option, and a
        firmware_page confirm link when unsure. Choosing an option resolves
        (or unresolves) the group and re-runs _update_radio_info.

        Idempotent and translation-aware: retranslate_ui re-invokes it (via
        _update_radio_info) so a language switch rebuilds every label.
        """
        panel = self._variant_panel
        panel.DestroyChildren()
        sizer = panel.GetSizer()
        sizer.Clear()

        current = self._variant_choice.get(group_id)
        label_by_id = {o.get("radio_id"): o.get("label", "")
                       for o in group.get("options", [])}

        # Question + steps in a borderless read-only TextCtrl: word-wraps at
        # any column width and compresses (scrolls) when the window is small,
        # so the answer buttons below are never pushed out of view — they are
        # the actionable part. The name line is omitted; the picker above
        # already shows it.
        prompt = format_variant_prompt(group_id, group, include_name=False)
        prompt_text = wx.TextCtrl(
            panel, value=prompt,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_BESTWRAP |
            wx.BORDER_NONE)
        try:
            prompt_text.SetCanFocus(False)  # no GTK focus ring on click
        except Exception:
            pass
        sizer.Add(prompt_text, 1, wx.EXPAND | wx.BOTTOM, 6)

        # Hidden group anchor: GTK force-selects the first button of a radio
        # group, which made the unanswered state *look* like the first variant
        # was chosen while Download stayed locked. The hidden anchor absorbs
        # "no answer yet" so no visible option shows selected until the user
        # actually picks one.
        anchor = wx.RadioButton(panel, style=wx.RB_GROUP)
        anchor.Hide()
        anchor.SetValue(True)

        for radio_id in dl.variant_members(group_id):
            label = t_radio_field(radio_id, "variant_label",
                                  label_by_id.get(radio_id, radio_id))
            rb = wx.RadioButton(panel, label=label)
            rb.SetValue(current == radio_id)
            rb.Bind(wx.EVT_RADIOBUTTON,
                    lambda e, rid=radio_id: self._on_variant_chosen(group_id, rid))
            sizer.Add(rb, 0, wx.BOTTOM, 4)

        rb_unsure = wx.RadioButton(panel, label=t("info.variant_not_sure"))
        rb_unsure.SetValue(current == self.VARIANT_UNSURE)
        rb_unsure.Bind(
            wx.EVT_RADIOBUTTON,
            lambda e: self._on_variant_chosen(group_id, self.VARIANT_UNSURE))
        sizer.Add(rb_unsure, 0, wx.BOTTOM, 4)

        # When the user says "I'm not sure", surface the vendor page so they can
        # confirm their hardware before risking a wrong-variant flash.
        if current == self.VARIANT_UNSURE:
            page = group.get("firmware_page")
            if page:
                link = wx.adv.HyperlinkCtrl(
                    panel, wx.ID_ANY, t("info.variant_confirm_link"), page)
                sizer.Add(link, 0, wx.TOP, 2)

        # Match the surrounding theme (children built after apply_theme ran).
        palette = self.current_theme_palette or MOCHA_PALETTE
        for w in _theme_walk(panel):
            _theme_style_widget(w, palette)

        panel.Show()
        panel.GetParent().Layout()

    def _on_variant_chosen(self, group_id, choice):
        """Record a variant answer and refresh the panel + Download gating."""
        self._variant_choice[group_id] = choice
        self._terminal_state = None
        self._update_radio_info()
        self._update_workflow_gating()

    def _set_hint(self, state):
        # Delegates to HintPresenter; kept as a same-named thin wrapper so
        # worker wx.CallAfter chains, retranslate_ui and HandsetController keep
        # calling frame._set_hint unchanged.
        self.hints.set_hint(state)

    def _compute_hint_state(self):
        # Delegates to HintPresenter (pure decision logic in gui_workflow).
        return self.hints.compute_hint_state()

    def _on_state_change(self, event):
        # Delegates to HintPresenter.
        self.hints.on_state_change(event)

    # ------------------------------------------------------------------
    # Menu / status bar action handlers (preserved)
    # ------------------------------------------------------------------

    def on_usage_guide(self, event):
        guide_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "USAGE.md")
        if os.path.exists(guide_path):
            # Path.as_uri() produces a valid file:// URI on every platform
            # (Windows needs file:///C:/… with forward slashes; naive string
            # concatenation yields file://C:\… which won't open).
            import pathlib
            wx.LaunchDefaultBrowser(pathlib.Path(guide_path).as_uri())
        else:
            wx.LaunchDefaultBrowser("https://github.com/FlintWave/flintwave-kdh-flasher/blob/master/USAGE.md")

    def on_github(self, event):
        wx.LaunchDefaultBrowser("https://github.com/FlintWave/flintwave-kdh-flasher")

    def on_about(self, event):
        show_about_dialog(self)

    def _on_close(self, event):
        # Signal the daemon background loops (port poll, update check) to stop
        # touching the frame, then let the default close proceed.
        self._closing = True
        event.Skip()

    # ------------------------------------------------------------------
    # Background tasks — behavior in DownloadController (gui_download).
    # These thin wrappers keep the __init__ daemon-thread targets and the
    # wx.CallAfter update-notification chain calling the same names.
    # ------------------------------------------------------------------

    def _fetch_manifest(self):
        self.download.fetch_manifest()

    def _check_update(self):
        self.download.check_update()

    def _notify_update(self, local_info, remote_info):
        self.download.notify_update(local_info, remote_info)

    def _show_update_link(self):
        self.download.show_update_link()

    def _radio_rows(self):
        """Ordered dropdown rows, one per line the combo shows (excluding the
        placeholder). Each row is a dict:
            {"kind": "radio", "radio": <radio dict>}          — ungrouped radio
            {"kind": "group", "group_id": <id>, "group": <group dict>}

        Members of a hardware-variant group collapse into a single family row
        at the position of the group's first member; ungrouped radios map 1:1.
        Shared by the dropdown builder, the placeholder-refresh, and
        _get_selected_radio so the row↔index mapping lives in one place.
        """
        groups = dl.load_variant_groups()
        rows = []
        seen = set()
        for r in self.radios:
            gid = r.get("variant_group")
            if gid and gid in groups:
                if gid in seen:
                    continue
                seen.add(gid)
                rows.append({"kind": "group", "group_id": gid,
                             "group": groups[gid]})
            else:
                rows.append({"kind": "radio", "radio": r})
        return rows

    def _radio_row_label(self, row):
        """Display label for one dropdown row (family name for a group,
        deduped manufacturer+model for a radio)."""
        if row["kind"] == "group":
            gid = row["group_id"]
            grp = row["group"]
            name = t_variant_field(gid, "name", grp.get("name", gid))
            return radio_display_name(name, grp.get("manufacturer", ""))
        r = row["radio"]
        return radio_display_name(r.get("name", ""), r.get("manufacturer", ""))

    def radio_dropdown_labels(self):
        """Combo choices including the placeholder at index 0."""
        return [self.RADIO_PLACEHOLDER] + [
            self._radio_row_label(row) for row in self._radio_rows()]

    def _selected_row(self):
        """The _radio_rows() entry for the current combo selection, or None
        for the placeholder / out-of-range."""
        idx = self.radio_combo.GetSelection()
        if idx < 1:
            return None
        rows = self._radio_rows()
        if (idx - 1) >= len(rows):
            return None
        return rows[idx - 1]

    def _get_selected_group(self):
        """Return (group_id, group_dict) when a variant-group family row is
        selected — resolved or not — else None. Lets the info panel render the
        walkthrough and gate Download."""
        row = self._selected_row()
        if row and row["kind"] == "group":
            return row["group_id"], row["group"]
        return None

    def _get_selected_radio(self):
        # Combo entry 0 is the "— Select your radio —" placeholder. For a plain
        # radio row we return its dict. For a variant-group family row we return
        # the concrete member ONLY once the user has resolved the variant; an
        # unanswered group or "I'm not sure" returns None (Download stays gated,
        # the app never guesses a variant).
        row = self._selected_row()
        if row is None:
            return None
        if row["kind"] == "radio":
            return row["radio"]
        gid = row["group_id"]
        choice = self._variant_choice.get(gid)
        if choice and choice != self.VARIANT_UNSURE:
            return dl.resolve_variant(gid, choice)
        return None

    def _driver_for(self, radio):
        # Return the protocol driver module that probes/flashes this radio.
        # Both modules expose the same interface: probe_port, flash_to_port,
        # validate_firmware, MAX_FIRMWARE_BYTES. Default is "kdh" for any
        # radio without an explicit `protocol` field (backward compatible).
        proto = (radio or {}).get("protocol", "kdh")
        return fw_btf if proto == "btf" else fw

    # --- Firmware discovery + download worker + updater: behavior delegated to
    # DownloadController (gui_download). These thin wrappers keep the existing
    # call sites (gui_columns bindings, hint presenter, flash workers,
    # retranslate_ui, __init__) calling the same names while the logic lives in
    # the controller. `manifest` is a property shim over the controller, exactly
    # as `_handset_ports` shims `handset.ports`.

    @property
    def manifest(self):
        return self.download.manifest

    def _get_firmware_url_and_version(self, radio):
        return self.download.get_firmware_url_and_version(radio)

    def _update_radio_info(self):
        self.download.update_radio_info()

    def on_radio_changed(self, event):
        self.download.on_radio_changed(event)

    def on_download(self, event):
        self.download.on_download(event)

    def _download_thread(self, radio, url=None, expected_sha256=None):
        self.download.download_thread(radio, url, expected_sha256)

    def on_browse(self, event):
        self.download.on_browse(event)

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
        # Also lock the firmware inputs so a radio switch, path edit, or
        # Browse can't fire mid-operation (which would let a second worker
        # thread start on the same serial port). Re-enabled by the gating
        # pass below on completion.
        for w in ("radio_combo", "file_path", "browse_btn"):
            widget = getattr(self, w, None)
            if widget is not None:
                wx.CallAfter(widget.Enable, enabled)
        if enabled:
            wx.CallAfter(self._update_radio_info)
            # Recompute hint AFTER the thread has set _terminal_state
            wx.CallAfter(lambda: self._set_hint(self._compute_hint_state()))
            # Re-apply workflow gating so we don't enable buttons that
            # should still be locked (e.g. Flash without a handset selected).
            wx.CallAfter(self._update_workflow_gating)

    def on_flash(self, event):
        if self._busy:
            return
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
            keys = radio.get("bootloader_keys", t("fallback.bootloader_keys"))
            radio_name = radio.get("name", t("fallback.radio_name"))
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
        driver = self._driver_for(radio)
        try:
            with open(firmware_path, "rb") as f:
                firmware_bytes = f.read()
            driver.validate_firmware(firmware_bytes, firmware_path)
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
                    driver.flash_to_port(port, firmware_bytes,
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

        # BTF (RT-950 Pro) uses a different on-the-wire protocol than KDH;
        # delegate to the BTF-specific path. The KDH inline flow below stays
        # unchanged so existing translations and behavior are preserved.
        if (radio or {}).get("protocol") == "btf":
            return self._flash_thread_btf(port, firmware_path, handset_idx, radio)

        # Derive once, up front, so both the success and failure paths pass the
        # same identity to record_flash and to the test-report nag suppression.
        radio_id = radio["id"] if radio else None
        file_version = fv.extract_version_from_filename(
            os.path.basename(firmware_path))

        if handset_idx is not None:
            wx.CallAfter(self._set_handset_status, handset_idx, STATUS_FLASHING)
            wx.CallAfter(self._set_handset_progress, handset_idx, "0%")

        try:
            import math
            import time
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
            wx.CallAfter(self._offer_test_report, radio_name, firmware_path,
                         True, "", radio_id, file_version)

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
            wx.CallAfter(self._offer_test_report, radio_name, firmware_path,
                         False, error_msg, radio_id, file_version)
        finally:
            self._busy = False
            self.set_buttons(True)

    def _flash_thread_btf(self, port, firmware_path, handset_idx, radio):
        # Single-port BTF flash. Mirrors _flash_thread's structure (busy state,
        # per-handset status, log messages, post-flash version recording, test
        # report offer) but delegates the on-the-wire work to fw_btf.
        radio_name = radio["name"] if radio else t("fallback.radio_unknown")
        # Derive once, up front, so both the success and failure paths pass the
        # same identity to record_flash and to the test-report nag suppression.
        radio_id = radio["id"] if radio else None
        file_version = fv.extract_version_from_filename(
            os.path.basename(firmware_path))
        if handset_idx is not None:
            wx.CallAfter(self._set_handset_status, handset_idx, STATUS_FLASHING)
            wx.CallAfter(self._set_handset_progress, handset_idx, "0%")

        sha256 = ""
        try:
            import hashlib

            fw_size = os.path.getsize(firmware_path)
            if fw_size > fw_btf.MAX_FIRMWARE_BYTES:
                raise ValueError(t("log.file_too_large").format(size=fw_size))
            with open(firmware_path, "rb") as f:
                firmware = f.read()

            fw_btf.validate_firmware(firmware, firmware_path)
            sha256 = hashlib.sha256(firmware).hexdigest()
            self.log_msg(t("log.firmware_path").format(path=firmware_path))
            self.log_msg(t("log.port").format(port=port))
            self.log_msg("")

            def log_cb(msg):
                self.log_msg(msg)

            def progress_cb(pct):
                self.set_progress(pct)
                if handset_idx is not None:
                    wx.CallAfter(self._set_handset_progress,
                                 handset_idx, f"{int(pct)}%")

            fw_btf.flash_to_port(port, firmware,
                                 log_cb=log_cb, progress_cb=progress_cb)

            self.log_msg("")
            self.log_msg(t("log.flash_complete"))
            if handset_idx is not None:
                wx.CallAfter(self._set_handset_status, handset_idx, STATUS_DONE)
                wx.CallAfter(self._set_handset_progress, handset_idx, "100%")

            if radio and file_version:
                try:
                    fm.record_flash(radio["id"], file_version, sha256)
                    self.log_msg(t("log.recorded_flash").format(
                        version=file_version, radio=radio_name))
                except Exception:
                    pass

            self._terminal_state = "complete"
            wx.CallAfter(self._offer_test_report, radio_name, firmware_path,
                         True, "", radio_id, file_version)

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
            wx.CallAfter(self._offer_test_report, radio_name, firmware_path,
                         False, error_msg, radio_id, file_version)
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

    def _offer_test_report(self, radio_name, firmware_path, success, error_msg,
                           radio_id=None, file_version=None):
        # Nag suppression: once a report was submitted or explicitly skipped for
        # this radio id + firmware version, don't offer again for that same
        # combination (keyed the same grain record_flash uses). A plain Skip
        # does not record anything, so it keeps prompting on future flashes.
        if radio_id and fm.get_test_report_status(radio_id, file_version) in (
                "submitted", "skipped"):
            if success:
                self._offer_firmware_cleanup(firmware_path)
            return

        log_content = self.log.GetValue()
        status = show_test_report_dialog(self, radio_name, firmware_path,
                                         success, error_msg, log_content)
        if radio_id and status:
            try:
                fm.mark_test_report(radio_id, file_version, status)
            except Exception:
                # Recording the suppression state is best-effort: a corrupt or
                # unwritable state file must not turn a successful flash into
                # an error dialog. Worst case the user is asked again next time.
                pass
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
                # The firmware we just flashed lived in that dir and is now
                # gone. Clear the path field so the hint and workflow gating
                # reflect that no firmware is loaded (otherwise the textbox
                # shows a dead path and Flash stays enabled over a missing file).
                if firmware_path.startswith(download_dir):
                    self.file_path.SetValue("")
                    self._terminal_state = None
                    self._update_workflow_gating()
            dlg.Destroy()
        except Exception:
            pass

    def on_dry_run(self, event):
        if self._busy:
            return
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
        # BTF dry-run delegates to fw_btf.dry_run since the validation rules
        # (vector-table SP/PC range, file-size minimum) and the packet-builder
        # CRC self-checks differ from KDH. KDH path below is unchanged.
        radio = self._get_selected_radio()
        if (radio or {}).get("protocol") == "btf":
            try:
                self.log_msg(t("log.dryrun_header"))
                self.log_msg("")
                fw_btf.dry_run(firmware_path, log_cb=self.log_msg)
                self.set_progress(100)
                self._terminal_state = "dryrun_complete"
            except Exception as e:
                self.log_msg(t("log.error_prefix").format(message=e))
                self._terminal_state = "failed"
            finally:
                self._busy = False
                self.set_buttons(True)
            return

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
        if self._busy:
            return
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
                # Build the probe for the selected radio's protocol. A BTF
                # radio (RT-950 Pro) answers a different handshake than the KDH
                # bootloader, so sending the KDH packet would report "no
                # response" even for a healthy BTF radio.
                if (self._get_selected_radio() or {}).get("protocol") == "btf":
                    packet = fw_btf.build_packet(fw_btf.CMD_PROBE)
                else:
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
    frame.theme_btn.SetLabel(theme_toggle_glyph(initial_theme))
    frame.Show()
    apply_theme(frame, initial_theme)
    # Apply the default font size to every widget so the user gets 12pt UI on
    # first launch (instead of only after they cycle the font button).
    frame._set_font_size(frame.font_size)
    app.MainLoop()


if __name__ == "__main__":
    main()
