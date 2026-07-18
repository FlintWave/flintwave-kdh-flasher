#!/usr/bin/env python3
"""
Custom borderless title bar component.

Extracted from the FlasherFrame god-class: the app removes the OS title bar so
the window chrome themes consistently with the rest of the UI, and this panel
supplies the replacement — app icon, title label, and minimize/close controls,
with drag-to-move on the bar / title / icon.

``TitleBar`` collaborates with the owning frame for the handful of things only
the frame can provide: the window operations it drives (``GetTitle``, ``Iconize``,
``Close``, and — via ``WindowDragger`` — ``Move`` / ``GetPosition``), the current
font size, and the frame's live-retranslation (``_tr_tooltip``) and RTL-mirroring
(``_rtl_targets``) registries so language switches keep working.

The module guards its ``wx`` import so the pure ``WindowDragger`` (re-exported for
convenience) and this module stay importable in a headless / pyserial-free test
environment; ``TitleBar`` itself is only defined when wx is present.
"""

import os

from window_drag import WindowDragger

try:
    import wx
except ImportError:
    wx = None


if wx is not None:

    class TitleBar(wx.Panel):
        """Borderless title bar: icon + title, minimize/close, drag-to-move."""

        def __init__(self, parent, frame):
            super().__init__(parent)
            self._frame = frame
            self._dragger = WindowDragger(frame, wx.GetMousePosition)
            self.title_icon = None
            self.title_label = None
            self._build()

        def _build(self):
            frame = self._frame
            sizer = wx.BoxSizer(wx.HORIZONTAL)

            # App icon at far left (small, scaled from icon_128).
            icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "icon_128.png")
            if os.path.exists(icon_path):
                img = wx.Image(icon_path).Rescale(20, 20, wx.IMAGE_QUALITY_HIGH)
                self.title_icon = wx.StaticBitmap(self, bitmap=wx.Bitmap(img))
                sizer.Add(self.title_icon, 0,
                          wx.ALIGN_CENTER_VERTICAL | wx.LEFT | wx.RIGHT, 8)

            self.title_label = wx.StaticText(self, label=frame.GetTitle())
            title_font = wx.Font(frame.font_size, wx.FONTFAMILY_DEFAULT,
                                 wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
            self.title_label.SetFont(title_font)
            sizer.Add(self.title_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 4)

            sizer.AddStretchSpacer(1)

            # The language picker used to live here; it moved to the status bar
            # in v26.05.5 so the title bar stays focused on identity + window
            # controls only.
            self._minimize_btn = self._make_chrome_btn(
                "—", "titlebar.minimize_tooltip", frame.Iconize)
            self._maximize_btn = self._make_chrome_btn(
                "□", "titlebar.maximize_tooltip", self._toggle_maximize)
            self._close_btn = self._make_chrome_btn(
                "✕", "titlebar.close_tooltip", frame.Close)
            sizer.Add(self._minimize_btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 16)
            sizer.Add(self._maximize_btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 16)
            sizer.Add(self._close_btn, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 16)

            self.SetSizer(sizer)
            self.SetMinSize(wx.Size(-1, 36))
            frame._rtl_targets.append(self)

            # Drag-to-move on the bar background, the title label, and the icon.
            for w in (self, self.title_label):
                self._bind_drag(w)
            if self.title_icon is not None:
                self._bind_drag(self.title_icon)

        def _make_chrome_btn(self, label, tooltip_key, handler):
            b = wx.StaticText(self, label=label)
            b.SetCursor(wx.Cursor(wx.CURSOR_HAND))
            # _tr_tooltip both sets the tooltip now and registers it for live
            # retranslation on language change.
            self._frame._tr_tooltip(b, tooltip_key)
            b.Bind(wx.EVT_LEFT_DOWN, lambda e: handler())
            chrome_font = wx.Font(self._frame.font_size + 2, wx.FONTFAMILY_DEFAULT,
                                  wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
            b.SetFont(chrome_font)
            return b

        def _toggle_maximize(self):
            # The frame owns maximize (emulated — WMs refuse the hint for
            # borderless windows); fall back to wx for exotic embeddings.
            toggle = getattr(self._frame, "toggle_maximize", None)
            if toggle is not None:
                toggle()
            else:
                self._frame.Maximize(not self._frame.IsMaximized())
            self.update_maximize_glyph()

        def update_maximize_glyph(self):
            """Reflect the frame's maximized state in the chrome glyph.

            Also called from the frame's EVT_SIZE handler, since a restore
            (or a WM-initiated maximize) doesn't come through our button.
            """
            is_max = getattr(self._frame, "is_app_maximized",
                             self._frame.IsMaximized)()
            self._maximize_btn.SetLabel("❐" if is_max else "□")

        def _bind_drag(self, w):
            w.Bind(wx.EVT_LEFT_DOWN, self._on_press)
            w.Bind(wx.EVT_MOTION, self._on_drag)
            w.Bind(wx.EVT_LEFT_UP, self._on_release)
            # Platform-conventional: double-click the bar toggles maximize.
            w.Bind(wx.EVT_LEFT_DCLICK, lambda e: self._toggle_maximize())

        def _on_press(self, event):
            self._dragger.begin()
            w = event.GetEventObject()
            if not w.HasCapture():
                w.CaptureMouse()

        def _on_drag(self, event):
            if event.Dragging() and event.LeftIsDown():
                self._dragger.drag()

        def _on_release(self, event):
            w = event.GetEventObject()
            if w.HasCapture():
                w.ReleaseMouse()
            self._dragger.end()

else:  # pragma: no cover - exercised only in wx-less test environments
    TitleBar = None
