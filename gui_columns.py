#!/usr/bin/env python3
"""
The three workflow-column components.

Extracted from the FlasherFrame god-class: the BalenaEtcher-style
Firmware → Handset → Flash columns that make up the main window body.

Each column is a ``wx.Panel`` subclass that builds its widgets and wires them to
frame-level handlers (``on_download`` / ``on_flash`` / ``_refresh_handset_ports`` …).
Unlike the title/status bars, these columns are the app's core interaction
surface and their widgets are read and written from all over the frame (the
port-poll loop, probe threads, workflow gating, the flash/download workers).
Rather than churn those ~100 call sites, each component assigns its widgets back
onto the frame as attributes (e.g. ``frame.radio_combo``) exactly as the old
builder methods did — the construction moves out, the wiring stays identical.

The heading factory (``frame._column_heading``) stays on the frame because the
log and instructions panels use it too; the columns call it as a collaborator.

The ``wx`` import is guarded so this module and the pure ``radio_display_name``
helper stay importable in a headless / pyserial-free test environment; the
column classes are only defined when wx is present.
"""

try:
    import wx
except ImportError:
    wx = None


def radio_display_name(name, manufacturer):
    """Full radio label without double-stamping the manufacturer.

    If the model name already starts with the manufacturer (e.g.
    ``"BTECH BF-F8HP Pro"``), use it as-is; otherwise prefix the manufacturer
    (``"Baofeng" + "UV-25 Plus"`` → ``"Baofeng UV-25 Plus"``). Shared by the
    firmware dropdown and the frame's per-radio info panel so the rule lives in
    exactly one place.
    """
    return name if name.startswith(manufacturer) else f"{manufacturer} {name}".strip()


if wx is not None:

    class FirmwareColumn(wx.Panel):
        """Column 1: radio picker, Download, and firmware path + Browse."""

        def __init__(self, parent, frame):
            super().__init__(parent)
            self._frame = frame
            self._build()

        def _build(self):
            frame = self._frame
            from i18n import t

            self.SetMinSize(wx.Size(240, -1))
            sizer = wx.BoxSizer(wx.VERTICAL)
            sizer.Add(frame._column_heading(self, "column.firmware"), 0,
                      wx.ALIGN_CENTER_HORIZONTAL | wx.BOTTOM, 6)

            # First entry is a placeholder so the user has to actively pick a
            # radio (instead of getting whichever radio happened to be in
            # radios.json[0]). _get_selected_radio() treats index 0 as "no radio
            # selected" (returns None). frame.radio_dropdown_labels() collapses
            # each hardware-variant group into a single family row; ungrouped
            # radios render one-to-one.
            frame.RADIO_PLACEHOLDER = t("radio.placeholder")
            radio_names = frame.radio_dropdown_labels()
            self.radio_combo = wx.ComboBox(self, choices=radio_names,
                                           style=wx.CB_DROPDOWN | wx.CB_READONLY)
            self.radio_combo.SetSelection(0)
            self.radio_combo.Bind(wx.EVT_COMBOBOX, frame.on_radio_changed)

            # On GTK with CB_READONLY, clicking the text portion does nothing —
            # only the arrow drops down. Bind LEFT_DOWN so a click anywhere on
            # the combo opens the list.
            def _open_combo(event):
                try:
                    self.radio_combo.Popup()
                except Exception:
                    # Best-effort UX: Popup() is unsupported on some wx
                    # backends. Falling through still lets the native arrow
                    # open the list, so a failure here is non-fatal.
                    pass
                event.Skip()
            self.radio_combo.Bind(wx.EVT_LEFT_DOWN, _open_combo)
            sizer.Add(self.radio_combo, 0,
                      wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)

            self.download_btn = wx.Button(self, label=t("button.download_latest"))
            self.download_btn.Bind(wx.EVT_BUTTON, frame.on_download)
            # download_btn's label is set dynamically by _update_radio_info
            # (Download Latest / Download v… / No Direct URL), so it isn't
            # tracked in _i18n_widgets — retranslate_ui re-invokes it.
            sizer.Add(self.download_btn, 0,
                      wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)

            file_row = wx.BoxSizer(wx.HORIZONTAL)
            self.file_path = wx.TextCtrl(self)
            file_row.Add(self.file_path, 1, wx.EXPAND | wx.RIGHT, 4)
            self.browse_btn = wx.Button(self, label=t("button.browse"))
            frame._tr_label(self.browse_btn, "button.browse")
            self.browse_btn.Bind(wx.EVT_BUTTON, frame.on_browse)
            file_row.Add(self.browse_btn, 0)
            sizer.Add(file_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)

            # Hardware-variant walkthrough (question + identification steps +
            # one answer per variant) renders here, in the column's otherwise
            # empty space directly under the picker it refines. It previously
            # rendered below the Instructions text, where the directions were
            # scrolled out of view and the answers were squeezed to zero
            # height at small window sizes (found in hardware testing).
            # _render_variant_options fills it; hidden for concrete radios.
            self.variant_box = wx.Panel(self)
            self.variant_box.SetSizer(wx.BoxSizer(wx.VERTICAL))
            self.variant_box.Hide()
            # Proportion 1 with no competing stretch spacer: when shown, the
            # walkthrough owns all of the column's leftover height (it was
            # getting squeezed when it had to share); when hidden, the
            # leftover is simply empty, same as the old stretch spacer.
            sizer.Add(self.variant_box, 1,
                      wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)
            self.SetSizer(sizer)
            frame._rtl_targets.append(self)

            # Expose widgets on the frame (unchanged call sites elsewhere).
            frame.radio_combo = self.radio_combo
            frame.download_btn = self.download_btn
            frame.file_path = self.file_path
            frame.browse_btn = self.browse_btn
            frame._variant_panel = self.variant_box

    class HandsetColumn(wx.Panel):
        """Column 2: multi-select list of detected serial ports + selection helpers."""

        def __init__(self, parent, frame):
            super().__init__(parent)
            self._frame = frame
            self._build()

        def _build(self):
            frame = self._frame
            from i18n import t

            self.SetMinSize(wx.Size(280, -1))
            sizer = wx.BoxSizer(wx.VERTICAL)
            sizer.Add(frame._column_heading(self, "column.handset"), 0,
                      wx.ALIGN_CENTER_HORIZONTAL | wx.BOTTOM, 6)

            # Multi-select list of detected serial ports / cables. Each row has
            # a checkbox; FTDI/PC03 cables auto-check on detection. Status column
            # shows probe results and per-port flash progress.
            self.handset_list = wx.ListCtrl(self, style=wx.LC_REPORT)
            checkboxes_supported = False
            try:
                self.handset_list.EnableCheckBoxes(True)
                checkboxes_supported = True
            except Exception:
                checkboxes_supported = False
            # Publish to the frame before _apply_handset_columns (which reads
            # frame.handset_list) runs.
            frame.handset_list = self.handset_list
            frame._handset_checkboxes_supported = checkboxes_supported
            frame._apply_handset_columns()
            sizer.Add(self.handset_list, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)

            if checkboxes_supported:
                self.handset_list.Bind(wx.EVT_LIST_ITEM_CHECKED,
                                       frame._on_handset_check_changed)
                self.handset_list.Bind(wx.EVT_LIST_ITEM_UNCHECKED,
                                       frame._on_handset_check_changed)
            else:
                self.handset_list.Bind(wx.EVT_LIST_ITEM_SELECTED,
                                       frame._on_handset_check_changed)
                self.handset_list.Bind(wx.EVT_LIST_ITEM_DESELECTED,
                                       frame._on_handset_check_changed)

            # Selection summary + selection helpers. _refresh_handset_summary()
            # owns the rendering via the i18n "handset.summary" template.
            self.handset_summary = wx.StaticText(
                self, label=t("handset.summary").format(selected=0, total=0))
            sizer.Add(self.handset_summary, 0, wx.LEFT | wx.RIGHT | wx.TOP, 6)

            btn_row = wx.BoxSizer(wx.HORIZONTAL)
            self.refresh_btn = wx.Button(self, label=t("button.refresh_probe"))
            frame._tr_label(self.refresh_btn, "button.refresh_probe")
            frame._tr_tooltip(self.refresh_btn, "tooltip.refresh")
            self.refresh_btn.Bind(
                wx.EVT_BUTTON, lambda e: frame._refresh_handset_ports(probe=True))
            btn_row.Add(self.refresh_btn, 1, wx.RIGHT, 4)

            self.select_all_btn = wx.Button(self, label=t("button.select_all"))
            frame._tr_label(self.select_all_btn, "button.select_all")
            frame._tr_tooltip(self.select_all_btn, "tooltip.select_all")
            self.select_all_btn.Bind(
                wx.EVT_BUTTON, lambda e: frame._set_all_handsets_checked(True))
            btn_row.Add(self.select_all_btn, 0, wx.RIGHT, 4)

            self.select_none_btn = wx.Button(self, label=t("button.select_none"))
            frame._tr_label(self.select_none_btn, "button.select_none")
            frame._tr_tooltip(self.select_none_btn, "tooltip.select_none")
            self.select_none_btn.Bind(
                wx.EVT_BUTTON, lambda e: frame._set_all_handsets_checked(False))
            btn_row.Add(self.select_none_btn, 0)
            sizer.Add(btn_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)

            self.SetSizer(sizer)
            frame._rtl_targets.append(self)
            frame._rtl_targets.append(self.handset_list)

            # Expose remaining widgets on the frame (handset_list already set).
            frame.handset_summary = self.handset_summary
            frame.refresh_btn = self.refresh_btn
            frame.select_all_btn = self.select_all_btn
            frame.select_none_btn = self.select_none_btn

    class FlashColumn(wx.Panel):
        """Column 3: Flash button, progress gauge, Dry Run / Diagnostics."""

        def __init__(self, parent, frame):
            super().__init__(parent)
            self._frame = frame
            self._build()

        def _build(self):
            frame = self._frame
            from i18n import t

            self.SetMinSize(wx.Size(220, -1))
            sizer = wx.BoxSizer(wx.VERTICAL)
            sizer.Add(frame._column_heading(self, "column.flash"), 0,
                      wx.ALIGN_CENTER_HORIZONTAL | wx.BOTTOM, 6)

            self.flash_btn = wx.Button(self, label=t("button.flash_firmware"))
            frame._tr_label(self.flash_btn, "button.flash_firmware")
            flash_font = wx.Font(12, wx.FONTFAMILY_DEFAULT,
                                 wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
            self.flash_btn.SetFont(flash_font)
            self.flash_btn.Bind(wx.EVT_BUTTON, frame.on_flash)
            sizer.Add(self.flash_btn, 0,
                      wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 6)

            self.progress = wx.Gauge(self, range=100)
            sizer.Add(self.progress, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)

            sizer.AddSpacer(4)

            sec_row = wx.BoxSizer(wx.HORIZONTAL)
            self.dryrun_btn = wx.Button(self, label=t("button.dry_run"))
            frame._tr_label(self.dryrun_btn, "button.dry_run")
            self.dryrun_btn.Bind(wx.EVT_BUTTON, frame.on_dry_run)
            sec_row.Add(self.dryrun_btn, 1, wx.RIGHT, 4)
            self.diag_btn = wx.Button(self, label=t("button.diagnostics"))
            frame._tr_label(self.diag_btn, "button.diagnostics")
            self.diag_btn.Bind(wx.EVT_BUTTON, frame.on_diag)
            sec_row.Add(self.diag_btn, 1)
            sizer.Add(sec_row, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 6)

            sizer.AddStretchSpacer(1)
            self.SetSizer(sizer)
            frame._rtl_targets.append(self)

            # Expose widgets on the frame (unchanged call sites elsewhere).
            frame.flash_btn = self.flash_btn
            frame.progress = self.progress
            frame.dryrun_btn = self.dryrun_btn
            frame.diag_btn = self.diag_btn

else:  # pragma: no cover - exercised only in wx-less test environments
    FirmwareColumn = None
    HandsetColumn = None
    FlashColumn = None
