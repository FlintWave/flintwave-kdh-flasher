"""
Theme palettes and GTK CSS theming for the KDH flasher GUI.
Catppuccin color scheme support plus high-contrast accessibility theme.
"""

import sys
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

# Catppuccin palettes: (base, surface0, mantle, text, subtext1, green, link)
# Using surface0 for interactive elements (buttons, dropdowns) for contrast
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


def apply_theme(frame, theme):
    """Apply a named theme to the FlasherFrame and its panel."""
    panel = frame.panel

    if theme not in THEME_PALETTES:
        # System default — reset everything
        frame.current_theme = "system"
        frame.current_theme_palette = None
        panel.SetOwnBackgroundColour(wx.NullColour)
        panel.SetOwnForegroundColour(wx.NullColour)
        frame.log.SetOwnBackgroundColour(wx.NullColour)
        frame.log.SetOwnForegroundColour(wx.NullColour)
        frame.radio_info.SetOwnForegroundColour(wx.Colour(80, 80, 80))
        for child in panel.GetChildren():
            child.SetOwnBackgroundColour(wx.NullColour)
            child.SetOwnForegroundColour(wx.NullColour)
        clear_gtk_css(frame)
        panel.Refresh()
        for child in panel.GetChildren():
            child.Refresh()
        return

    frame.current_theme = theme
    frame.current_theme_palette = THEME_PALETTES[theme]

    base, surface0, mantle, text, subtext1, green, link = \
        [wx.Colour(*c) for c in THEME_PALETTES[theme]]

    panel.SetOwnBackgroundColour(base)
    panel.SetOwnForegroundColour(text)
    frame.log.SetOwnBackgroundColour(mantle)
    frame.log.SetOwnForegroundColour(green)
    frame.radio_info.SetOwnForegroundColour(subtext1)

    for child in panel.GetChildren():
        if isinstance(child, wx.adv.HyperlinkCtrl):
            child.SetNormalColour(link)
            child.SetVisitedColour(link)
            child.SetHoverColour(green)
            child.SetOwnBackgroundColour(base)
        elif isinstance(child, wx.Button):
            if sys.platform == "win32":
                child.SetWindowStyleFlag(
                    child.GetWindowStyleFlag() | wx.BORDER_NONE)
            child.SetOwnBackgroundColour(surface0)
            child.SetOwnForegroundColour(text)
        elif isinstance(child, wx.ComboBox):
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
    frame.log.SetOwnForegroundColour(green)

    # Apply GTK CSS for native widgets (menus, dropdowns, arrows)
    if _gtk_available:
        _apply_gtk_css(frame, THEME_PALETTES[theme])

    panel.Refresh()
    for child in panel.GetChildren():
        child.Refresh()


def apply_theme_to_dialog(frame, dlg, widgets):
    """Apply the current theme palette to a dialog's widgets.

    widgets is a dict with keys:
        panels      - list of panels/notebook to set base colors
        about_children - children of the about panel to theme
        copy_text   - the copyright StaticText (gets subtext1 color)
        license_text - the license TextCtrl (gets mantle bg, green fg)
        close_btn   - the close button (gets surface0 bg)
    """
    palette = frame.current_theme_palette
    if not palette:
        return

    base, surface0, mantle, text, subtext1, green, link_c = \
        [wx.Colour(*c) for c in palette]

    for w in widgets["panels"]:
        w.SetOwnBackgroundColour(base)
        w.SetOwnForegroundColour(text)

    for child in widgets.get("about_children", []):
        if isinstance(child, wx.adv.HyperlinkCtrl):
            child.SetNormalColour(link_c)
            child.SetVisitedColour(link_c)
            child.SetOwnBackgroundColour(base)
        elif isinstance(child, wx.StaticText):
            child.SetOwnForegroundColour(text)

    if "copy_text" in widgets:
        widgets["copy_text"].SetOwnForegroundColour(subtext1)
    if "license_text" in widgets:
        widgets["license_text"].SetOwnBackgroundColour(mantle)
        widgets["license_text"].SetOwnForegroundColour(green)
    if "close_btn" in widgets:
        widgets["close_btn"].SetOwnBackgroundColour(surface0)
        widgets["close_btn"].SetOwnForegroundColour(text)


def _apply_gtk_css(frame, palette):
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
