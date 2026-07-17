#!/usr/bin/env python3
"""
Pure workflow-state logic for the flasher GUI.

The three-column BalenaEtcher-style workflow (Firmware → Handset → Flash) has
two decision points that used to live as methods on the 2,300-line
``FlasherFrame`` god-class, tangled together with wxWidgets calls:

  * which *hint* to show the user (the instructions panel copy), and
  * which column controls should be *enabled* (workflow gating).

Both are pure functions of a handful of booleans/counts — no wx involved — so
they live here where they can be unit-tested without a display or wxPython.
``gui_main`` imports these and keeps only the widget wiring (reading values off
controls, calling ``.Enable()`` / rendering text).
"""

from collections import namedtuple

# Terminal states are sticky: once a flash/dry-run/diagnostics run finishes, the
# hint stays on the outcome until the user changes something.
TERMINAL_STATES = ("complete", "failed", "dryrun_complete", "diag_complete")

# Every hint key the instructions panel knows how to render (matches the
# hint.<state>.title / hint.<state>.body entries in the i18n catalogs).
HINT_STATES = frozenset({
    "no_firmware", "no_handset", "batch_ready", "ready_dryrun",
    "ready_flash", "downloading", "flashing", "dryrun", "diagnostics",
    "complete", "dryrun_complete", "diag_complete", "failed",
})

# States during which it's useful to also show the per-radio info (bootloader
# keys, connector type, notes from radios.json) beneath the hint body.
RADIO_INFO_STATES = frozenset({
    "no_firmware", "no_handset", "ready_flash", "ready_dryrun", "batch_ready",
})

# Which column controls should be enabled at a given moment. The frame maps
# these booleans onto the actual widgets.
WorkflowGates = namedtuple("WorkflowGates", ["download", "handset", "flash"])


def compute_hint_state(terminal_state, busy, firmware_ready, handset_count,
                       busy_state="flashing"):
    """Return the hint-state key for the current workflow situation.

    Priority order (highest first):
      1. A sticky terminal outcome (complete/failed/…) — show it until cleared.
      2. An in-progress operation — show ``busy_state`` (downloading/flashing/…).
      3. No firmware chosen yet — ``no_firmware``.
      4. No handset checked — ``no_handset``.
      5. More than one handset checked — ``batch_ready``.
      6. Otherwise ready for a single flash — ``ready_flash``.
    """
    if terminal_state in TERMINAL_STATES:
        return terminal_state
    if busy:
        return busy_state
    if not firmware_ready:
        return "no_firmware"
    if handset_count == 0:
        return "no_handset"
    if handset_count > 1:
        return "batch_ready"
    return "ready_flash"


def compute_gates(radio_chosen, firmware_ready, handset_ready):
    """Return which column controls should be enabled.

    Workflow tiers:
      * Download needs a real radio selected (Browse is always available).
      * The Handset column unlocks once a firmware file exists.
      * The Flash column unlocks once firmware exists AND a handset is checked.
    """
    return WorkflowGates(
        download=bool(radio_chosen),
        handset=bool(firmware_ready),
        flash=bool(firmware_ready) and bool(handset_ready),
    )
