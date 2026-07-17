#!/usr/bin/env python3
"""
Borderless status bar component.

Extracted from the FlasherFrame god-class: the row of click-target "links"
along the bottom of the window — font-size cycler, theme toggle, language
picker on the left; usage guide, update-available link, and about on the right.

The status bar is a *view*: it builds the widgets and routes their clicks to
frame-level actions (``_cycle_font`` / ``_toggle_theme`` / ``_open_language_dialog``
/ ``on_usage_guide`` / ``on_about``), which mutate app-wide state (fonts across
every widget, the active theme, the language) and therefore stay on the frame.
The frame still updates a few of these widgets directly (the font/theme/lang
labels, showing the update link), so ``StatusBar`` exposes them as attributes and
the frame keeps handles to them.

Like ``gui_titlebar``, the ``wx`` import is guarded so the module and the pure
``theme_toggle_glyph`` helper stay importable in a headless / pyserial-free test
environment; ``StatusBar`` itself is only defined when wx is present.
"""

try:
    import wx
    import wx.adv
except ImportError:
    wx = None

RELEASES_URL = "https://github.com/FlintWave/flintwave-kdh-flasher/releases/latest"


def theme_toggle_glyph(current_theme):
    """Glyph for the theme toggle — it points at the *destination* theme.

    Dark (mocha) is active → show a sun (☀, "switch to light"); light (latte)
    is active → show a moon (☾, "switch to dark"). Centralizes a choice that was
    duplicated across the builder, the toggle handler, and the launch code.
    """
    return "☀" if current_theme == "mocha" else "☾"


if wx is not None:

    class StatusBar(wx.Panel):
        """Row of click-target links: font / theme / language | usage / update / about."""

        def __init__(self, parent, frame):
            super().__init__(parent)
            self._frame = frame
            self._build()

        def _make_link(self, label, tooltip_key, handler, label_key=None):
            """A clickable StaticText (no button border), registered for i18n."""
            link = wx.StaticText(self, label=label)
            link.SetCursor(wx.Cursor(wx.CURSOR_HAND))
            # _tr_tooltip sets the tooltip now and registers it for live
            # retranslation on language change.
            self._frame._tr_tooltip(link, tooltip_key)
            if label_key is not None:
                self._frame._tr_label(link, label_key)
            link.Bind(wx.EVT_LEFT_DOWN, lambda e: handler())
            return link

        def _build(self):
            frame = self._frame
            from i18n import t

            bar_sizer = wx.BoxSizer(wx.HORIZONTAL)

            self.font_btn = self._make_link(
                f"{frame.font_size}pt", "tooltip.font_cycle", frame._cycle_font)

            self.theme_btn = self._make_link(
                theme_toggle_glyph(frame.current_theme),
                "tooltip.theme_toggle", frame._toggle_theme)

            # Language picker (was a title-bar dropdown pre-v26.05.5). Click
            # opens a modal listing every supported language in its native
            # script; the button shows the active language.
            self.lang_btn = self._make_link(
                frame._language_button_label(),
                "tooltip.language", frame._open_language_dialog)

            usage_link = self._make_link(
                t("statusbar.usage"), "tooltip.usage",
                lambda: frame.on_usage_guide(None), label_key="statusbar.usage")
            about_link = self._make_link(
                t("statusbar.about"), "tooltip.about",
                lambda: frame.on_about(None), label_key="statusbar.about")

            # Hidden hyperlink: when _check_update finds a newer release the
            # frame sets its URL and Show()s it. Click opens the releases page.
            self.update_link = wx.adv.HyperlinkCtrl(
                self, label=t("statusbar.update_available"), url=RELEASES_URL,
                style=wx.adv.HL_ALIGN_LEFT | wx.NO_BORDER)
            frame._tr_label(self.update_link, "statusbar.update_available")
            self.update_link.Hide()

            bar_sizer.AddSpacer(12)
            bar_sizer.Add(self.font_btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 16)
            bar_sizer.Add(self.theme_btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 16)
            bar_sizer.Add(self.lang_btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 16)
            bar_sizer.AddStretchSpacer(1)
            bar_sizer.Add(usage_link, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 16)
            bar_sizer.Add(self.update_link, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 16)
            bar_sizer.Add(about_link, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 16)
            bar_sizer.AddSpacer(4)

            self.SetSizer(bar_sizer)
            self.SetMinSize(wx.Size(-1, 32))
            frame._rtl_targets.append(self)

else:  # pragma: no cover - exercised only in wx-less test environments
    StatusBar = None
