#!/usr/bin/env python3
"""
Handset-column behavior: port discovery, probing, polling, and selection.

This is the *controller* half of the handset column — the runtime logic that
was the deepest remaining coupling in FlasherFrame. It owns the model of
detected ports and drives the wx ListCtrl: enumerating USB serial ports,
a background thread that watches for plug/unplug, a probe thread that sends the
protocol-appropriate handshake to each port, and the per-row status / progress /
checkbox updates plus the selection summary.

``HandsetController`` collaborates with the owning frame for everything only the
frame can provide: the ListCtrl / summary / refresh widgets, the ``_busy`` and
``_closing`` flags, protocol dispatch (``_driver_for`` / ``_get_selected_radio``),
the dialout-permission hint, and the workflow feedback it triggers on selection
changes (``_compute_hint_state`` / ``_set_hint`` / ``_update_workflow_gating``).
The frame exposes thin delegators (``_set_handset_status`` etc.) so the flash
workers and gui_columns keep calling the same names.

``enumerate_serial_ports`` and ``poll_signature`` are pure (a ``comports``
callable is injectable) so they're unit-tested without pyserial or a display;
the threaded/widget parts are verified against real hardware.
"""

import os
import time
import threading

try:
    import wx
except ImportError:
    wx = None

import i18n
from i18n import t
from gui_ports import KNOWN_CABLES, FTDI_VID_PID

# Handset-list status values are i18n keys; the rendering layer calls t() on
# them when writing a status cell. Comparisons use these symbolic constants;
# only the on-screen text runs through the translation table. (Moved here from
# gui_main so the controller and the flash workers share one definition.)
STATUS_UNKNOWN = "status.unknown"
STATUS_PROBING = "status.probing"
STATUS_READY = "status.ready"
STATUS_NO_RESP = "status.no_response"
STATUS_FLASHING = "status.flashing"
STATUS_DONE = "status.done"
STATUS_FAILED = "status.failed"
STATUS_SKIPPED = "status.skipped"


def enumerate_serial_ports(comports=None):
    """Return dicts describing currently visible USB serial ports.

    Non-USB serial ports (motherboard 16550 ``/dev/ttyS*``) are filtered out —
    all known KDH programming cables are USB-attached, and showing 30+ unused
    UARTs would also balloon probe time (~1.5s per port).

    ``comports`` is injectable for testing; by default it uses
    ``serial.tools.list_ports.comports``.
    """
    if comports is None:
        import serial.tools.list_ports
        comports = serial.tools.list_ports.comports
    out = []
    for p in comports():
        if not (p.vid and p.pid):
            continue  # skip non-USB ports
        vid_pid = (p.vid, p.pid)
        cable = KNOWN_CABLES.get(vid_pid, p.description or "")
        out.append({
            "device": p.device,
            "cable": cable,
            "vid_pid": vid_pid,
            "status": STATUS_UNKNOWN,
            "progress": "",
            "is_pc03": vid_pid == FTDI_VID_PID,
        })
    return out


def poll_signature(ports):
    """Stable signature of the visible port set, for plug/unplug change detection."""
    return tuple(sorted(p["device"] for p in ports))


class HandsetController:
    """Owns the handset port model and drives the handset ListCtrl."""

    def __init__(self, frame):
        self.frame = frame
        self.ports = []              # list of dicts: device, cable, vid_pid, status, progress
        self._probing = False        # True while the probe thread is walking ports
        self._poll_signature = None  # last visible-port signature (change detection)

    # -- column headers --------------------------------------------------- #
    def apply_columns(self):
        """Insert / rebuild the handset table column headers in the active language.

        Column header alignment doesn't auto-mirror under SetLayoutDirection, so
        we re-create the columns with the right format whenever the language
        changes.
        """
        frame = self.frame
        if not hasattr(frame, "handset_list"):
            return
        fmt = (wx.LIST_FORMAT_RIGHT if i18n.is_rtl() else wx.LIST_FORMAT_LEFT)
        # Stash existing widths so we don't lose user resize state.
        had_columns = frame.handset_list.GetColumnCount() > 0
        widths = ([frame.handset_list.GetColumnWidth(i) for i in range(4)]
                  if had_columns else [110, 140, 110, 50])
        frame.handset_list.ClearAll()
        for idx, (key, width) in enumerate(zip(
                ("handset.col_port", "handset.col_cable",
                 "handset.col_status", "handset.col_percent"),
                widths)):
            frame.handset_list.InsertColumn(idx, t(key), width=width, format=fmt)

    # -- refresh / probe / poll ------------------------------------------ #
    def refresh_ports(self, probe=False, preserve_checks=False):
        """Re-enumerate ports and rebuild the handset list.

        If probe=True, sends a handshake to each port in a background thread and
        updates Status. PC03 cables are auto-checked unless preserve_checks is
        True (used by the polling loop so we don't fight the user).
        """
        frame = self.frame
        if frame._busy or self._probing:
            # Don't reshape the list while flashing, or while a probe thread is
            # posting status updates keyed by row index — a rebuild here would
            # invalidate those indices and land results on the wrong rows.
            return

        previously_checked = set()
        if preserve_checks:
            for i in range(frame.handset_list.GetItemCount()):
                if self.is_checked(i):
                    previously_checked.add(self.ports[i]["device"])

        new_ports = enumerate_serial_ports()
        frame.handset_list.DeleteAllItems()
        self.ports = new_ports

        for entry in new_ports:
            # Show only the device basename (e.g. "ttyUSB0") — the full path is
            # kept in entry["device"] for serial.Serial calls.
            display_port = os.path.basename(entry["device"]) or entry["device"]
            idx = frame.handset_list.InsertItem(
                frame.handset_list.GetItemCount(), display_port)
            frame.handset_list.SetItem(idx, 1, entry["cable"])
            frame.handset_list.SetItem(idx, 2, t(entry["status"]))
            frame.handset_list.SetItem(idx, 3, entry["progress"])

            should_check = (
                entry["device"] in previously_checked
                or (not preserve_checks and entry["is_pc03"])
            )
            if should_check:
                self.set_check(idx, True)

        self.refresh_summary()

        if probe and new_ports:
            frame.refresh_btn.Disable()
            self._probing = True
            threading.Thread(target=self._probe_thread, daemon=True).start()

    def _probe_thread(self):
        """Send the protocol-appropriate handshake to every listed port; update
        Status as we go. The handshake is dispatched on the selected radio's
        protocol — KDH ("BOOTLOADER" handshake) or BTF (cmd 0x42 probe).

        If the very first probe hits PermissionError (Linux dialout), surface the
        hint once and abort instead of marking every port "No response".
        """
        frame = self.frame
        driver = frame._driver_for(frame._get_selected_radio())
        permission_blocked = False
        try:
            for idx, entry in enumerate(list(self.ports)):
                wx.CallAfter(self.set_status, idx, STATUS_PROBING)
                try:
                    ready = driver.probe_port(entry["device"], timeout=1.5)
                except PermissionError:
                    permission_blocked = True
                    wx.CallAfter(frame._log_dialout_hint, entry["device"])
                    # Mark this port and all remaining ones as Unknown rather
                    # than No response — they may well be radios.
                    for remaining_idx in range(idx, len(self.ports)):
                        wx.CallAfter(self.set_status, remaining_idx, STATUS_UNKNOWN)
                    break
                except Exception:
                    # A non-permission failure (port vanished, busy, I/O error)
                    # still needs a terminal status, otherwise the row is left
                    # showing "Probing…" forever.
                    wx.CallAfter(self.set_status, idx, STATUS_NO_RESP)
                else:
                    new_status = STATUS_READY if ready else STATUS_NO_RESP
                    wx.CallAfter(self.set_status, idx, new_status)
                    if ready:
                        wx.CallAfter(self.set_check, idx, True)
        finally:
            # Always clear the in-flight flag, even on an unexpected error, so
            # the port-poll loop isn't blocked from refreshing forever.
            self._probing = False
        wx.CallAfter(frame.refresh_btn.Enable)
        if not permission_blocked:
            wx.CallAfter(lambda: frame._set_hint(frame._compute_hint_state()))

    def port_poll_loop(self):
        """Background thread: detect plug/unplug events and trigger refresh.

        Polls every 2 seconds; when the set of visible ports changes (and we
        aren't busy), refresh the list while preserving user-made checkbox state.
        """
        frame = self.frame
        while not frame._closing:
            time.sleep(2.0)
            if frame._closing:
                break
            try:
                ports = enumerate_serial_ports()
                signature = poll_signature(ports)
                if signature != self._poll_signature:
                    self._poll_signature = signature
                    if not frame._busy:
                        wx.CallAfter(self.refresh_ports, False, True)
            except Exception:
                pass

    # -- row / selection helpers ----------------------------------------- #
    def set_status(self, idx, status):
        frame = self.frame
        if 0 <= idx < len(self.ports):
            self.ports[idx]["status"] = status
            try:
                frame.handset_list.SetItem(idx, 2, t(status))
            except Exception:
                pass
            self.refresh_summary()

    def set_progress(self, idx, text):
        frame = self.frame
        if 0 <= idx < len(self.ports):
            self.ports[idx]["progress"] = text
            try:
                frame.handset_list.SetItem(idx, 3, text)
            except Exception:
                pass

    def set_check(self, idx, checked):
        frame = self.frame
        if not (0 <= idx < frame.handset_list.GetItemCount()):
            return
        if frame._handset_checkboxes_supported:
            frame.handset_list.CheckItem(idx, checked)
        else:
            frame.handset_list.Select(idx, on=1 if checked else 0)
        self.refresh_summary()

    def is_checked(self, idx):
        frame = self.frame
        if not (0 <= idx < frame.handset_list.GetItemCount()):
            return False
        if frame._handset_checkboxes_supported:
            return frame.handset_list.IsItemChecked(idx)
        return frame.handset_list.IsSelected(idx)

    def set_all_checked(self, checked):
        frame = self.frame
        for idx in range(frame.handset_list.GetItemCount()):
            self.set_check(idx, checked)

    def on_check_changed(self, event):
        frame = self.frame
        self.refresh_summary()
        frame._terminal_state = None
        frame._set_hint(frame._compute_hint_state())
        frame._update_workflow_gating()
        if event:
            event.Skip()

    def refresh_summary(self):
        frame = self.frame
        total = frame.handset_list.GetItemCount()
        sel = sum(1 for i in range(total) if self.is_checked(i))
        frame.handset_summary.SetLabel(
            t("handset.summary").format(selected=sel, total=total))

    def selected_indices(self):
        frame = self.frame
        return [i for i in range(frame.handset_list.GetItemCount())
                if self.is_checked(i)]

    def selected_devices(self):
        return [self.ports[i]["device"] for i in self.selected_indices()]
