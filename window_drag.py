#!/usr/bin/env python3
"""
Drag-to-move helper for borderless windows.

The app hides the OS title bar and provides its own, so moving the window is
done by dragging the custom title bar. The offset bookkeeping — capture where
the mouse grabbed the window, then keep the window at (mouse - offset) as the
mouse moves — is pure arithmetic with no wxWidgets in it, so it lives here where
it can be unit-tested without a display.

``WindowDragger`` talks to the window only through ``GetPosition()`` / ``Move()``
and gets the pointer location from an injected callable, so tests can drive it
with plain vector objects. The wx-specific bits (mouse capture, event filtering)
stay in the ``TitleBar`` component that owns the dragger.
"""


class WindowDragger:
    """Tracks a drag-to-move gesture for ``window``.

    Args:
        window: object with ``GetPosition()`` and ``Move(pos)`` (a wx.Frame in
            production). Positions are whatever ``get_mouse_pos`` returns and
            ``window.GetPosition()`` yields — they only need ``+`` / ``-``.
        get_mouse_pos: zero-arg callable returning the current pointer position
            (``wx.GetMousePosition`` in production).
    """

    def __init__(self, window, get_mouse_pos):
        self._window = window
        self._get_mouse_pos = get_mouse_pos
        self._offset = None

    def begin(self):
        """Record the mouse-to-window offset at the start of a drag."""
        self._offset = self._get_mouse_pos() - self._window.GetPosition()

    def drag(self):
        """Move the window so the grab point stays under the pointer.

        No-op if a drag isn't in progress, so it's safe to call on every mouse
        motion event.
        """
        if self._offset is not None:
            self._window.Move(self._get_mouse_pos() - self._offset)

    def end(self):
        """Finish the drag; subsequent ``drag()`` calls do nothing until begin()."""
        self._offset = None

    @property
    def active(self):
        """True while a drag is in progress."""
        return self._offset is not None


RESIZE_MARGIN = 8

# Resize zones. Composite names contain their component edges on purpose:
# resize_geometry() substring-matches ("right" in "bottomright") so corners
# reuse the edge arithmetic.
_ZONES = ("left", "right", "top", "bottom",
          "topleft", "topright", "bottomleft", "bottomright")


def hit_test_edge(x, y, width, height, margin=RESIZE_MARGIN):
    """Return the resize zone under (x, y) in a width x height window.

    One of the ``_ZONES`` strings, or None outside the margin band. Corners
    win over plain edges so diagonal resizing is reachable. Pure arithmetic —
    unit-tested without a display.
    """
    left = x < margin
    right = x >= width - margin
    top = y < margin
    bottom = y >= height - margin
    if top and left:
        return "topleft"
    if top and right:
        return "topright"
    if bottom and left:
        return "bottomleft"
    if bottom and right:
        return "bottomright"
    if left:
        return "left"
    if right:
        return "right"
    if top:
        return "top"
    if bottom:
        return "bottom"
    return None


def resize_geometry(zone, start_pos, start_size, start_mouse, mouse, min_size):
    """New ``(pos, size)`` for dragging ``zone`` from start_mouse to mouse.

    All arguments are (x, y) tuples. Dragging a left/top edge moves the
    origin so the opposite edge stays anchored; sizes clamp to ``min_size``
    (and the anchored edge absorbs the clamp, so the window never slides).
    Pure arithmetic — unit-tested without a display.
    """
    dx = mouse[0] - start_mouse[0]
    dy = mouse[1] - start_mouse[1]
    x, y = start_pos
    w, h = start_size
    min_w, min_h = min_size

    if "right" in zone:
        w = max(min_w, w + dx)
    if "bottom" in zone:
        h = max(min_h, h + dy)
    if "left" in zone:
        new_w = max(min_w, w - dx)
        x += w - new_w
        w = new_w
    if "top" in zone:
        new_h = max(min_h, h - dy)
        y += h - new_h
        h = new_h
    return (x, y), (w, h)


try:
    import wx
except ImportError:  # pragma: no cover - wx-less test environments
    wx = None

if wx is not None:
    _ZONE_CURSORS = {
        "left": wx.CURSOR_SIZEWE, "right": wx.CURSOR_SIZEWE,
        "top": wx.CURSOR_SIZENS, "bottom": wx.CURSOR_SIZENS,
        "topleft": wx.CURSOR_SIZENWSE, "bottomright": wx.CURSOR_SIZENWSE,
        "topright": wx.CURSOR_SIZENESW, "bottomleft": wx.CURSOR_SIZENESW,
    }

    class EdgeResizeController:
        """Manual edge-resize for a borderless frame.

        Compositors often ignore wx.RESIZE_BORDER on undecorated windows, so
        this reimplements it: attach() binds mouse handlers on the widgets
        that touch the frame's edges; a press inside the margin band starts a
        resize driven by the pure resize_geometry(). Handlers Skip() outside
        the band so drag-to-move and normal widget behavior are unaffected.
        """

        def __init__(self, frame, margin=RESIZE_MARGIN):
            self._frame = frame
            self._margin = margin
            self._zone = None
            self._start_rect = None
            self._start_mouse = None
            self._capture_widget = None

        def attach(self, *widgets):
            for w in widgets:
                w.Bind(wx.EVT_MOTION, self._on_motion)
                w.Bind(wx.EVT_LEFT_DOWN, self._on_down)
                w.Bind(wx.EVT_LEFT_UP, self._on_up)
                w.Bind(wx.EVT_LEAVE_WINDOW, self._on_leave)

        def _zone_at_pointer(self):
            is_max = getattr(self._frame, "is_app_maximized",
                             self._frame.IsMaximized)()
            if is_max:
                return None  # edges of a maximized window aren't resizable
            mouse = wx.GetMousePosition()
            rect = self._frame.GetScreenRect()
            return hit_test_edge(mouse.x - rect.x, mouse.y - rect.y,
                                 rect.width, rect.height, self._margin)

        def _on_motion(self, event):
            if self._zone is not None:
                mouse = wx.GetMousePosition()
                min_w, min_h = self._frame.GetMinSize()
                pos, size = resize_geometry(
                    self._zone,
                    (self._start_rect.x, self._start_rect.y),
                    (self._start_rect.width, self._start_rect.height),
                    self._start_mouse, (mouse.x, mouse.y),
                    (max(1, min_w), max(1, min_h)))
                self._frame.SetSize(pos[0], pos[1], size[0], size[1])
                return
            zone = self._zone_at_pointer()
            widget = event.GetEventObject()
            if zone is not None:
                widget.SetCursor(wx.Cursor(_ZONE_CURSORS[zone]))
            else:
                widget.SetCursor(wx.NullCursor)
                event.Skip()

        def _on_down(self, event):
            zone = self._zone_at_pointer()
            if zone is None:
                event.Skip()
                return
            self._zone = zone
            self._start_rect = self._frame.GetScreenRect()
            mouse = wx.GetMousePosition()
            self._start_mouse = (mouse.x, mouse.y)
            widget = event.GetEventObject()
            if not widget.HasCapture():
                widget.CaptureMouse()
                self._capture_widget = widget

        def _on_up(self, event):
            if self._zone is None:
                event.Skip()
                return
            if (self._capture_widget is not None
                    and self._capture_widget.HasCapture()):
                self._capture_widget.ReleaseMouse()
            self._zone = None
            self._start_rect = None
            self._start_mouse = None
            self._capture_widget = None

        def _on_leave(self, event):
            if self._zone is None:
                event.GetEventObject().SetCursor(wx.NullCursor)
            event.Skip()
else:  # pragma: no cover
    EdgeResizeController = None
