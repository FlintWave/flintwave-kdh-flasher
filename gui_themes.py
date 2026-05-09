"""
Theme palettes and GTK CSS theming for the KDH flasher GUI.
Two themes: Latte (light) and Mocha (dark).
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

# Catppuccin palettes: (base, surface0, mantle, text, subtext1, green, link)
THEME_PALETTES = {
    "latte": (
        (239, 241, 245),  # base
        (204, 208, 218),  # surface0
        (230, 233, 239),  # mantle (log bg)
        (76, 79, 105),    # text
        (92, 95, 119),    # subtext1
        (64, 160, 43),    # green (log text)
        (30, 102, 245),   # link
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


def apply_theme(frame, theme):
    """Apply a named theme to the FlasherFrame and its panel tree."""
    if theme not in THEME_PALETTES:
        theme = "latte"

    frame.current_theme = theme
    frame.current_theme_palette = THEME_PALETTES[theme]
    palette = THEME_PALETTES[theme]

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

    if hasattr(frame, "radio_info") and frame.radio_info:
        frame.radio_info.SetOwnForegroundColour(subtext1)

    if hasattr(frame, "hint_body") and frame.hint_body:
        frame.hint_body.SetOwnForegroundColour(subtext1)
        frame.hint_body.SetOwnBackgroundColour(base)

    if hasattr(frame, "hint_title") and frame.hint_title:
        frame.hint_title.SetOwnForegroundColour(text)
        frame.hint_title.SetOwnBackgroundColour(base)

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
    palette = getattr(frame, "current_theme_palette", None)
    if not palette:
        return

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
