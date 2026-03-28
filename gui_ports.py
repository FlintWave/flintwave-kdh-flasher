"""
Serial port detection and programming cable identification.
"""

import serial
import serial.tools.list_ports

# Known USB VID:PID pairs for compatible programming cables
KNOWN_CABLES = {
    (0x0403, 0x6015): "FTDI FT231X (PC03)",
    (0x0403, 0x6001): "FTDI FT232R",
    (0x0403, 0x6010): "FTDI FT2232",
    (0x0403, 0x6014): "FTDI FT232H",
    (0x067B, 0x2303): "Prolific PL2303",
    (0x067B, 0x23A3): "Prolific PL2303GS",
    (0x1A86, 0x7523): "CH340",
    (0x1A86, 0x55D4): "CH9102",
    (0x10C4, 0xEA60): "CP2102",
}

FTDI_VID_PID = (0x0403, 0x6015)  # PC03 cable


def list_serial_ports():
    """List serial ports with descriptions. Cross-platform."""
    ports = []
    for p in serial.tools.list_ports.comports():
        vid_pid = (p.vid, p.pid) if p.vid and p.pid else None
        cable = KNOWN_CABLES.get(vid_pid, "")
        if cable:
            label = f"{p.device} - {cable} [{p.serial_number or ''}]"
        elif p.description and p.description != "n/a":
            label = f"{p.device} - {p.description}"
        else:
            label = p.device
        ports.append((p.device, label.strip(), vid_pid))
    return ports


def find_programming_cable():
    """Auto-detect the BTECH PC03 cable or other FTDI cables."""
    ports = list_serial_ports()
    # Prefer exact PC03 match
    for device, label, vid_pid in ports:
        if vid_pid == FTDI_VID_PID:
            return device, label
    # Fall back to any known cable
    for device, label, vid_pid in ports:
        if vid_pid in KNOWN_CABLES:
            return device, label
    return None, None
