#!/usr/bin/env python3
"""
Instructions-panel presentation: the hint state machine and per-radio info.

This is the *presenter* half of the Instructions column. It owns the copy shown
in the read-only hint ``TextCtrl`` — resolving which workflow hint to show,
rendering its bold title / body into the widget, and appending the per-radio
info block (bootloader keys, connector, tested flag, latest firmware, notes) or,
for an unresolved hardware-variant family, the group's identification
question/steps.

``HintPresenter`` collaborates with the owning frame for everything only the
frame can provide: the ``hint_text`` TextCtrl and ``font_size``; the selection /
state readers (``_get_selected_radio``, ``_get_selected_group``,
``_get_firmware_url_and_version``, ``_firmware_ready``,
``_selected_handset_indices``); and the ``_busy`` / ``_terminal_state`` /
``_busy_state`` fields. The frame keeps thin same-named delegators
(``_set_hint``, ``_compute_hint_state``, ``_get_hint_copy``,
``_format_radio_info``, ``_on_state_change``) so worker ``wx.CallAfter`` chains,
HandsetController callbacks, ``retranslate_ui`` and gui_columns bindings keep
calling the same names, exactly as HandsetController's delegators do.

Boundary note — hardware-variant handling straddles two slices, so it splits by
*what it produces*:

  * The instructions-panel **text** is this presenter's job, so the pure
    ``format_variant_prompt(group_id, group)`` (family name + translated
    identification question/steps) lives here as a headless module function and
    is composed into the info block by ``format_radio_info``.
  * The variant **answer widgets** — the radio-button walkthrough panel
    (``_render_variant_options`` / ``_clear_variant_panel`` /
    ``_on_variant_chosen``) and the ``_get_selected_group`` selection reader —
    stay on the frame. They are driven by firmware discovery
    (``_update_radio_info``, a later decomposition slice) and manipulate live wx
    controls rather than the hint string, so pulling them in here would drag a
    different slice's responsibility into the presenter. The presenter reaches
    back through the frame for ``_get_selected_group`` when it needs to know a
    family is unresolved.

The hint *decision* logic (``compute_hint_state``, ``HINT_STATES``,
``RADIO_INFO_STATES``) already lives in ``gui_workflow`` and is reused unchanged;
the presenter only reads current frame state and feeds it in. The string-builders
(``format_radio_info`` / ``format_variant_prompt``) are pure module functions so
they unit-test without a display or pyserial, following the
``enumerate_serial_ports`` / ``compute_hint_state`` precedent.
"""

try:
    import wx
except ImportError:
    wx = None

from i18n import t, t_radio_field, t_variant_field
from gui_columns import radio_display_name
from gui_workflow import (
    compute_hint_state as _compute_hint_state_pure,
    HINT_STATES,
    RADIO_INFO_STATES,
)


def format_radio_info(radio, firmware_version):
    """Return the per-radio instructions block for a concrete radio.

    Pure string-building (no widget access): given the radio dict from
    ``radios.json`` and the resolved latest firmware version (from
    ``_get_firmware_url_and_version``), assemble the name / bootloader-keys /
    connector / tested / latest-firmware / notes lines, each run through the
    active translation catalog. Returns the joined text.
    """
    bits = []
    rid = radio.get("id", "")
    keys = radio.get("bootloader_keys")
    connector = radio.get("connector")
    tested = radio.get("tested")
    # Same dedup rule as the dropdown (shared helper), so the manufacturer
    # isn't double-stamped when the name already starts with it.
    full_name = radio_display_name(radio.get("name", ""),
                                   radio.get("manufacturer", ""))
    bits.append(t("info.radio_label").format(name=full_name))
    if keys:
        bits.append(t("info.bootloader_keys").format(
            keys=t_radio_field(rid, "bootloader_keys", keys)))
    if connector:
        bits.append(t("info.connector").format(
            connector=t_radio_field(rid, "connector", connector)))
    bits.append(t("info.tested") if tested else t("info.untested"))
    if firmware_version:
        bits.append(t("info.latest_firmware").format(version=firmware_version))
    notes = radio.get("notes")
    if notes:
        bits.append("")
        bits.append(t_radio_field(rid, "notes", notes))
    return "\n".join(bits)


def format_variant_prompt(group_id, group, include_name=True):
    """Text block for an unresolved variant group: family name (optional),
    then the translated identification question and steps. Pure — no widget
    access. The Firmware column's walkthrough passes include_name=False
    because it renders directly under the picker that already shows the name.
    """
    bits = []
    if include_name:
        name = radio_display_name(
            t_variant_field(group_id, "name", group.get("name", group_id)),
            group.get("manufacturer", ""))
        bits = [t("info.radio_label").format(name=name), ""]
    question = t_variant_field(group_id, "question", group.get("question", ""))
    steps = t_variant_field(group_id, "steps", group.get("steps", ""))
    if question:
        bits.append(t("info.variant_question"))
        bits.append(question)
    if steps:
        bits.append("")
        bits.append(t("info.variant_steps"))
        bits.append(steps)
    return "\n".join(bits)


class HintPresenter:
    """Owns the Instructions hint TextCtrl and the per-radio info rendering."""

    def __init__(self, frame):
        self.frame = frame

    def get_hint_copy(self, state):
        """Return (title, body) for a hint state in the active language."""
        if state not in HINT_STATES:
            return None
        return (t(f"hint.{state}.title"), t(f"hint.{state}.body"))

    def radio_info(self):
        """Per-radio instructions for the active selection, or empty string.

        When an unresolved variant group is selected (no concrete radio), the
        full identification walkthrough (question, steps, answers) renders in
        the Firmware column's variant panel where it is always visible; the
        Instructions text just points there. Embedding the whole prompt here
        buried it below the fold of the small Instructions box (found in
        hardware testing).
        """
        frame = self.frame
        radio = frame._get_selected_radio()
        if not radio:
            group_sel = frame._get_selected_group()
            if group_sel:
                return t("info.variant_pointer")
            return ""
        _, version = frame._get_firmware_url_and_version(radio)
        return format_radio_info(radio, version)

    def set_hint(self, state):
        frame = self.frame
        copy = self.get_hint_copy(state)
        if copy is None:
            return
        title, body = copy
        # In idle / pre-flash states, append the per-radio instructions so the
        # user has bootloader keys / connector / notes visible while choosing
        # firmware and prepping the radio.
        if state in RADIO_INFO_STATES:
            radio_info = self.radio_info()
            if radio_info:
                body = f"{body}\n\n{t('info.selected_radio_header')}\n{radio_info}"
        # Render into the rich-text TextCtrl: bold title on its own line, blank
        # line, then body. SetDefaultStyle + AppendText is more reliable than
        # SetStyle on GTK (where the underlying GtkTextView has its own
        # attribute system that wx.TextAttr doesn't always reach via SetStyle).
        frame.hint_text.Freeze()
        try:
            frame.hint_text.Clear()
            bold = wx.Font(frame.font_size, wx.FONTFAMILY_DEFAULT,
                           wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
            normal = wx.Font(frame.font_size, wx.FONTFAMILY_DEFAULT,
                             wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
            bold_attr = wx.TextAttr()
            bold_attr.SetFont(bold)
            normal_attr = wx.TextAttr()
            normal_attr.SetFont(normal)

            frame.hint_text.SetDefaultStyle(bold_attr)
            frame.hint_text.AppendText(title + "\n\n")
            frame.hint_text.SetDefaultStyle(normal_attr)
            frame.hint_text.AppendText(body)

            frame.hint_text.SetInsertionPoint(0)
            frame.hint_text.ShowPosition(0)
        finally:
            frame.hint_text.Thaw()

    def compute_hint_state(self):
        # Pure decision logic lives in gui_workflow.compute_hint_state; this
        # method only reads the current values off the frame. _firmware_ready()
        # checks path-present AND file-exists so the hint can't advance to
        # "ready to flash" while the Flash button stays disabled because the
        # referenced file is missing/deleted.
        frame = self.frame
        return _compute_hint_state_pure(
            terminal_state=frame._terminal_state,
            busy=frame._busy,
            firmware_ready=frame._firmware_ready(),
            handset_count=len(frame._selected_handset_indices()),
            busy_state=getattr(frame, "_busy_state", "flashing"),
        )

    def on_state_change(self, event):
        frame = self.frame
        # User-initiated change clears any sticky terminal state
        frame._terminal_state = None
        self.set_hint(self.compute_hint_state())
        frame._update_workflow_gating()
        if event:
            event.Skip()
