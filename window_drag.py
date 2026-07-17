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
