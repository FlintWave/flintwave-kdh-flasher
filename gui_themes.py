"""
Theme palettes and GTK CSS theming for the KDH flasher GUI.
Two themes:
  - "mocha" (default) — dark
  - "latte"           — light
"""

import sys
import wx
import wx.adv

# GTK CSS theming for Linux — needed because native GTK widgets
# (dropdown arrows, popup lists) ignore wxPython color setters
_gtk_available = False
try:
    if sys.platform.startswith("linux"):
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk, Gdk
        _gtk_available = True
except (ImportError, ValueError):
    pass

# Catppuccin Mocha (dark): (base, surface0, mantle, text, subtext1, green, link)
MOCHA_PALETTE = (
    (30, 30, 46),     # base
    (49, 50, 68),     # surface0
    (24, 24, 37),     # mantle (log bg)
    (205, 214, 244),  # text
    (186, 194, 222),  # subtext1
    (166, 227, 161),  # green (log text)
    (137, 180, 250),  # link
)

# Catppuccin Latte (light): same tuple shape, palette swapped for light mode.
LATTE_PALETTE = (
    (239, 241, 245),  # base
    (204, 208, 218),  # surface0
    (230, 233, 239),  # mantle (log bg)
    (76, 79, 105),    # text
    (92, 95, 119),    # subtext1
    (64, 160, 43),    # green (log text)
    (30, 102, 245),   # link
)

THEME_PALETTES = {
    "mocha": MOCHA_PALETTE,
    "latte": LATTE_PALETTE,
}


def _walk(widget):
    """Yield widget and all descendant windows."""
    yield widget
    try:
        children = widget.GetChildren()
    except Exception:
        return
    for c in children:
        yield from _walk(c)


def _style_widget(widget, palette):
    """Paint a single widget according to its type and the palette."""
    base, surface0, mantle, text, subtext1, green, link = \
        [wx.Colour(*c) for c in palette]

    if isinstance(widget, wx.adv.HyperlinkCtrl):
        widget.SetNormalColour(link)
        widget.SetVisitedColour(link)
        widget.SetHoverColour(green)
        widget.SetOwnBackgroundColour(base)
    elif isinstance(widget, wx.Button):
        if sys.platform == "win32":
            widget.SetWindowStyleFlag(
                widget.GetWindowStyleFlag() | wx.BORDER_NONE)
        widget.SetOwnBackgroundColour(surface0)
        widget.SetOwnForegroundColour(text)
    elif isinstance(widget, wx.ComboBox):
        widget.SetOwnBackgroundColour(surface0)
        widget.SetOwnForegroundColour(text)
    elif isinstance(widget, wx.ListCtrl):
        widget.SetOwnBackgroundColour(mantle)
        widget.SetOwnForegroundColour(text)
    elif isinstance(widget, wx.TextCtrl):
        widget.SetOwnBackgroundColour(mantle)
        widget.SetOwnForegroundColour(text)
    elif isinstance(widget, wx.Gauge):
        widget.SetOwnBackgroundColour(surface0)
    elif isinstance(widget, wx.StaticText):
        widget.SetOwnForegroundColour(text)
        widget.SetOwnBackgroundColour(base)
    elif isinstance(widget, wx.StaticBox):
        widget.SetOwnForegroundColour(text)
        widget.SetOwnBackgroundColour(base)
    elif isinstance(widget, wx.Panel):
        widget.SetOwnBackgroundColour(base)
        widget.SetOwnForegroundColour(text)
    elif isinstance(widget, wx.ListBox):
        widget.SetOwnBackgroundColour(mantle)
        widget.SetOwnForegroundColour(text)
    elif isinstance(widget, wx.Notebook):
        widget.SetOwnBackgroundColour(base)
        widget.SetOwnForegroundColour(text)
    else:
        try:
            widget.SetOwnBackgroundColour(base)
            widget.SetOwnForegroundColour(text)
        except Exception:
            pass


def apply_theme(frame, theme=None):
    """Apply a named theme to the FlasherFrame and its panel tree.

    `theme` may be "mocha" (default, dark) or "latte" (light).
    Unknown names fall back to mocha so we never crash on a stale prefs file.
    """
    if theme not in THEME_PALETTES:
        theme = "mocha"
    palette = THEME_PALETTES[theme]
    frame.current_theme = theme
    frame.current_theme_palette = palette

    base, surface0, mantle, text, subtext1, green, link = \
        [wx.Colour(*c) for c in palette]

    panel = frame.panel
    panel.SetOwnBackgroundColour(base)
    panel.SetOwnForegroundColour(text)

    for w in _walk(panel):
        _style_widget(w, palette)

    if hasattr(frame, "log") and frame.log:
        frame.log.SetOwnBackgroundColour(mantle)
        frame.log.SetOwnForegroundColour(green)
        frame.log.SetBackgroundColour(mantle)
        frame.log.SetForegroundColour(green)
        frame.log.Refresh()
        # Force a repaint of the entire content range so the new fg color
        # applies to text already in the buffer (TextCtrl caches per-range).
        try:
            attr = wx.TextAttr(green, mantle)
            end = frame.log.GetLastPosition()
            frame.log.SetStyle(0, end, attr)
        except Exception:
            pass

    # Status bar and custom title bar both get a slightly darker background
    # (mantle) so they visually separate from the main panel area.
    for attr in ("status_bar_panel", "title_bar"):
        bar = getattr(frame, attr, None)
        if bar is not None:
            bar.SetOwnBackgroundColour(mantle)
            for w in _walk(bar):
                try:
                    w.SetOwnBackgroundColour(mantle)
                    w.SetOwnForegroundColour(subtext1)
                except Exception:
                    pass

    # Single thin divider between top columns and the bottom row.
    line = getattr(frame, "_divider1", None)
    if line is not None:
        gray = wx.Colour(128, 128, 128)  # 50% gray
        try:
            line.SetBackgroundColour(gray)
            line.SetForegroundColour(gray)
        except Exception:
            pass

    # Instructions TextCtrl (hint_text == hint_title == hint_body) — match
    # the surrounding panel's base bg so it doesn't look like an input field.
    if hasattr(frame, "hint_text") and frame.hint_text:
        frame.hint_text.SetOwnBackgroundColour(base)
        frame.hint_text.SetOwnForegroundColour(text)
        frame.hint_text.SetBackgroundColour(base)
        frame.hint_text.SetForegroundColour(text)
        try:
            attr = wx.TextAttr(text, base)
            end = frame.hint_text.GetLastPosition()
            frame.hint_text.SetStyle(0, end, attr)
        except Exception:
            pass
        frame.hint_text.Refresh()

    if _gtk_available:
        _apply_gtk_css(frame, palette)

    panel.Refresh()
    for w in _walk(panel):
        try:
            w.Refresh()
        except Exception:
            pass


def apply_theme_to_dialog(frame, dlg):
    """Recursively apply the parent frame's current theme to a dialog tree."""
    palette = getattr(frame, "current_theme_palette", None) or MOCHA_PALETTE

    base, surface0, mantle, text, subtext1, green, link = \
        [wx.Colour(*c) for c in palette]

    dlg.SetOwnBackgroundColour(base)
    dlg.SetOwnForegroundColour(text)

    for w in _walk(dlg):
        _style_widget(w, palette)

    dlg.Refresh()
    for w in _walk(dlg):
        try:
            w.Refresh()
        except Exception:
            pass


def _apply_gtk_css(frame, palette):
    """Apply theme colors to GTK native widgets via CSS."""
    base, surface0, mantle, text, subtext1, green, link = palette

    def rgb(c):
        return f"rgb({c[0]},{c[1]},{c[2]})"

    css = f"""
        window, frame, box {{
            background-color: {rgb(base)};
            color: {rgb(text)};
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
            border-radius: 6px;
            padding: 4px 10px;
        }}
        button:hover {{
            background-color: {rgb(link)};
            color: {rgb(base)};
        }}
        button:disabled, button:disabled:hover {{
            background-color: {rgb(mantle)};
            color: {rgb(subtext1)};
            opacity: 0.45;
        }}
        combobox button, scrollbar button {{
            border-radius: 4px;
        }}
        entry {{
            border-radius: 6px;
        }}
        entry, textview, textview text {{
            background-color: {rgb(mantle)};
            color: {rgb(text)};
        }}
        scrollbar {{
            background-color: {rgb(mantle)};
            min-width: 12px;
            min-height: 12px;
        }}
        scrollbar slider {{
            background-color: {rgb(subtext1)};
            min-width: 8px;
            min-height: 24px;
            border-radius: 6px;
        }}
        scrollbar slider:hover {{
            background-color: {rgb(text)};
        }}
    """

    try:
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode())

        screen = Gdk.Screen.get_default()
        if not hasattr(frame, '_gtk_css_provider'):
            frame._gtk_css_provider = None
        if frame._gtk_css_provider:
            Gtk.StyleContext.remove_provider_for_screen(screen, frame._gtk_css_provider)
        Gtk.StyleContext.add_provider_for_screen(
            screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        frame._gtk_css_provider = provider
    except Exception:
        pass


def clear_gtk_css(frame):
    """Remove custom GTK CSS to restore system theme."""
    if _gtk_available and hasattr(frame, '_gtk_css_provider') and frame._gtk_css_provider:
        try:
            screen = Gdk.Screen.get_default()
            Gtk.StyleContext.remove_provider_for_screen(screen, frame._gtk_css_provider)
            frame._gtk_css_provider = None
        except Exception:
            pass
