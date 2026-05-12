#!/usr/bin/env python3
"""
Native flasher for the Radtel RT-950 Pro (and other AT32F403A radios that
use the .BTF firmware container with the BOOTLOADER_V3 protocol).

Implements the .BTF on-the-wire protocol reverse-engineered from
RT-950_EnUPDATE.exe and the OEM bootloader at flash 0x08000000-0x08003000.
The .BTF container is sent to the radio AS-IS (1024-byte chunks); the MCU
bootloader decrypts it internally using the 16-byte XOR key embedded at
.BTF offset 0x400.

Protocol overview (radio in bootloader mode via PB12 + PE5 held at power on):
  1. CMD_PROBE  (0x42) — confirm bootloader is listening
  2. CMD_VERSION(0x0A) with payload "BOOTLOADER_V3"
  3. CMD_MODEL  (0x02) with 32 bytes from .BTF offset 0x3E0
  4. CMD_PKG_COUNT(0x04) with big-endian (total_chunks - 1) as 2 bytes
  5. CMD_DATA   (0x03) x N — each chunk carries a 16-bit BE seq# in args
  6. CMD_END    (0x45) — finalize, radio reboots into the new firmware

Packet format:
  [0xAA][cmd][argH][argL][lenH][lenL][data...][crcH][crcL][0x55]
  CRC-16/CCITT (poly 0x1021, init 0x0000) over cmd+args+len+data

Response packets are fixed 9 bytes:
  [0xAA][echoed cmd][0x00][result][0x00][0x00][crcH][crcL][0x55]

Serial config: 115200 baud, 8N1, DTR enabled, RTS enabled

Reference: github.com/Hertzz58/Radtel-RT950-Pro-Firmware (GPL-3.0).
"""

import math
import struct
import sys
import time
import hashlib

try:
    import serial
except ImportError:
    serial = None

# Reuse the shared CRC implementation; the polynomial and init match exactly.
from flash_firmware import crc16_ccitt

# Protocol constants
HEADER = 0xAA
TRAILER = 0x55
ACK = 0x06

CMD_PROBE = 0x42
CMD_VERSION = 0x0A
CMD_MODEL = 0x02
CMD_PKG_COUNT = 0x04
CMD_DATA = 0x03
CMD_END = 0x45

VERSION_STRING = b"BOOTLOADER_V3"

# .BTF wrapper offsets — set by the BTF encoder, consumed by the bootloader.
BTF_MODEL_OFFSET = 0x3E0  # 32-byte model signature (for CMD_MODEL)
BTF_MODEL_SIZE = 32
BTF_KEY_OFFSET = 0x400    # 16-byte XOR key seed (the bootloader decrypts with this)
BTF_KEY_SIZE = 16

DATA_BLOCK_SIZE = 1024

# Safety limits
MAX_FIRMWARE_BYTES = 1 * 1024 * 1024  # 1 MB
MIN_FIRMWARE_BYTES = BTF_KEY_OFFSET + BTF_KEY_SIZE + DATA_BLOCK_SIZE
MAX_CHUNKS = 0xFFFF  # 16-bit seq# room

ERROR_MESSAGES = {
    0xE1: "Wrong data length",
    0xE2: "Data verification error (retryable)",
    0xE3: "Flash write error",
    0xE5: "Command error / not applicable in this mode",
    0xE6: "Model mismatch (this firmware is for a different radio)",
}


def build_packet(cmd: int, args: int = 0, data: bytes = b"") -> bytes:
    """Build a 0xAA-framed BTF packet.

    args is a 16-bit big-endian field (KDH uses 1 byte; BTF uses 2 — for
    CMD_DATA it carries the chunk's sequence number).
    """
    args_hi = (args >> 8) & 0xFF
    args_lo = args & 0xFF
    dlen = len(data)
    payload = bytes([cmd, args_hi, args_lo, (dlen >> 8) & 0xFF, dlen & 0xFF]) + data
    crc = crc16_ccitt(payload)
    return bytes([HEADER]) + payload + struct.pack(">H", crc) + bytes([TRAILER])


def parse_response(buf: bytes):
    """Parse a fixed-length BTF response packet. Returns (cmd, result) or None."""
    if buf is None or len(buf) < 9 or buf[0] != HEADER or buf[-1] != TRAILER:
        return None
    return (buf[1], buf[3])


def _read_until_footer(ser, timeout_s: float = 5.0) -> bytes:
    """Read bytes until the BTF 0x55 footer is seen on a packet of length >= 9.

    BTF responses are always 9 bytes ([0xAA] + 7 bytes + [0x55]). We poll
    rather than block-read to allow short timeouts on quick commands.
    """
    deadline = time.time() + timeout_s
    buf = bytearray()
    while time.time() < deadline:
        b = ser.read(1)
        if not b:
            continue
        buf.extend(b)
        if b[0] == TRAILER and len(buf) >= 9:
            break
    return bytes(buf)


def send_command(ser, cmd: int, args: int = 0, data: bytes = b"",
                 timeout: float = 5.0, retries: int = 3):
    """Send a BTF packet and read the response.

    Retries on response-not-ACK with status 0xE2 (the radio's documented
    retryable verification error). Other errors are returned immediately so
    the caller can raise a precise message.
    """
    pkt = build_packet(cmd, args, data)
    for attempt in range(retries):
        ser.reset_input_buffer()
        ser.write(pkt)
        ser.flush()
        resp_bytes = _read_until_footer(ser, timeout_s=timeout)
        parsed = parse_response(resp_bytes)
        if parsed is None:
            if attempt + 1 == retries:
                return None, b""
            time.sleep(0.05)
            continue
        _resp_cmd, result = parsed
        if result == ACK:
            return result, resp_bytes
        if result == 0xE2 and attempt + 1 < retries:
            time.sleep(0.05)
            continue
        return result, resp_bytes
    return None, b""


def validate_btf(btf_bytes: bytes, path: str) -> dict:
    """Validate a .BTF blob before flashing. Aborts on failure.

    Returns a small dict with size, chunks, model_str, sha256 for logging.
    """
    fw_size = len(btf_bytes)

    if fw_size < MIN_FIRMWARE_BYTES:
        raise ValueError(
            f"BTF file too small ({fw_size} bytes). Not a valid .BTF firmware."
        )
    if fw_size > MAX_FIRMWARE_BYTES:
        raise ValueError(
            f"BTF file too large ({fw_size} bytes, max {MAX_FIRMWARE_BYTES})."
        )

    total_chunks = math.ceil(fw_size / DATA_BLOCK_SIZE)
    if total_chunks > MAX_CHUNKS:
        raise ValueError(
            f"BTF requires {total_chunks} chunks, exceeds protocol limit of {MAX_CHUNKS}."
        )

    # ARM Cortex-M vector table sanity check — the .BTF starts with a
    # plaintext vector table for the bootloader's SP/PC validation step.
    sp = int.from_bytes(btf_bytes[0:4], "little")
    reset = int.from_bytes(btf_bytes[4:8], "little")
    if not (0x20000000 <= sp <= 0x2001FFFF):
        raise ValueError(
            f"Invalid ARM stack pointer 0x{sp:08X} — not a valid .BTF firmware."
        )
    # Application code lives above the bootloader region (0x08000000-0x08003000).
    if not (0x08003000 <= reset <= 0x080FFFFF):
        raise ValueError(
            f"Invalid ARM reset handler 0x{reset:08X} — not a valid .BTF firmware."
        )

    model_sig = btf_bytes[BTF_MODEL_OFFSET:BTF_MODEL_OFFSET + BTF_MODEL_SIZE]
    model_str = model_sig[:12].rstrip(b"\x00 ").decode("ascii", errors="replace")

    sha256 = hashlib.sha256(btf_bytes).hexdigest()

    print(f"BTF firmware: {path}")
    print(f"Size: {fw_size} bytes, {total_chunks} chunks")
    print(f"Model signature: \"{model_str}\"")
    print(f"SHA-256: {sha256}")

    return {
        "size": fw_size,
        "chunks": total_chunks,
        "model_str": model_str,
        "model_sig": model_sig,
        "sha256": sha256,
    }


def probe_port(port: str, timeout: float = 1.5) -> bool:
    """Send CMD_PROBE on `port` and check for a BTF bootloader reply.

    Returns True iff the radio is in BTF bootloader mode and answered with a
    valid 0xAA-headed packet ending with 0x55.
    Re-raises PermissionError so the GUI can surface a dialout-group hint.
    """
    if serial is None:
        return False
    try:
        with serial.Serial(
            port=port, baudrate=115200, bytesize=8,
            parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
            timeout=timeout, write_timeout=timeout,
        ) as ser:
            ser.dtr = True
            ser.rts = True
            time.sleep(0.1)
            ser.reset_input_buffer()
            ser.reset_output_buffer()

            ser.write(build_packet(CMD_PROBE))
            ser.flush()

            resp = _read_until_footer(ser, timeout_s=timeout)
            parsed = parse_response(resp)
            if parsed is None:
                return False
            # Bootloader in update mode responds 0xE5 to CMD_PROBE — that's
            # the "ready, command-not-applicable-here" status. Either ACK or
            # E5 confirms a BTF bootloader.
            _, result = parsed
            return result in (ACK, 0xE5)
    except PermissionError:
        raise
    except Exception as e:
        msg = str(e)
        if "[Errno 13]" in msg or "Permission denied" in msg:
            raise PermissionError(msg) from e
        return False


def flash_to_port(port: str, btf_bytes: bytes,
                  log_cb=None, progress_cb=None) -> None:
    """Flash an already-loaded .BTF file to a single radio on `port`.

    Mirrors flash_firmware.flash_to_port so the GUI can dispatch with the
    same callable shape. Raises on any unrecoverable error.
    """
    def _log(msg):
        if log_cb:
            log_cb(msg)
        else:
            print(msg)

    def _progress(p):
        if progress_cb:
            progress_cb(p)

    info = validate_btf(btf_bytes, port)
    total_chunks = info["chunks"]
    model_sig = info["model_sig"]

    if serial is None:
        raise RuntimeError("pyserial not available")

    with serial.Serial(
        port=port, baudrate=115200, bytesize=8,
        parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
        timeout=2.0, write_timeout=2.0,
    ) as ser:
        ser.dtr = True
        ser.rts = True
        time.sleep(0.1)
        ser.reset_input_buffer()
        ser.reset_output_buffer()

        # Step 1: probe — confirm bootloader is up
        _log("Probing BTF bootloader…")
        result, _ = send_command(ser, CMD_PROBE, timeout=2.0)
        if result is None:
            raise RuntimeError("No response to BTF probe — radio not in bootloader mode?")
        # E5 (cmd not applicable) is also a valid "I'm here" reply.
        if result not in (ACK, 0xE5):
            raise RuntimeError(
                f"BTF probe rejected: 0x{result:02X} ({ERROR_MESSAGES.get(result, 'unknown')})"
            )

        # Step 2: version handshake
        _log("Version handshake (BOOTLOADER_V3)…")
        result, _ = send_command(ser, CMD_VERSION, data=VERSION_STRING, timeout=3.0)
        if result != ACK:
            raise RuntimeError(
                f"Version handshake failed: 0x{result:02X} "
                f"({ERROR_MESSAGES.get(result, 'unknown')})"
            )

        # Step 3: model verification
        _log(f"Verifying model signature: \"{info['model_str']}\"…")
        result, _ = send_command(ser, CMD_MODEL, data=model_sig, timeout=3.0)
        if result != ACK:
            raise RuntimeError(
                f"Model verification failed: 0x{result:02X} "
                f"({ERROR_MESSAGES.get(result, 'unknown')}) — wrong firmware for this radio?"
            )

        # Step 4: announce package count (radio expects total_chunks - 1)
        _log(f"Announcing {total_chunks} chunks…")
        count_data = struct.pack(">H", total_chunks - 1)
        result, _ = send_command(ser, CMD_PKG_COUNT, data=count_data, timeout=3.0)
        if result != ACK:
            raise RuntimeError(
                f"Package count rejected: 0x{result:02X} "
                f"({ERROR_MESSAGES.get(result, 'unknown')})"
            )

        # Step 5: stream the data
        _log(f"Flashing {total_chunks} × {DATA_BLOCK_SIZE}-byte chunks…")
        for seq in range(total_chunks):
            offset = seq * DATA_BLOCK_SIZE
            block = btf_bytes[offset:offset + DATA_BLOCK_SIZE]
            if len(block) < DATA_BLOCK_SIZE:
                block = block + b"\x00" * (DATA_BLOCK_SIZE - len(block))

            result, _ = send_command(ser, CMD_DATA, args=seq, data=block, timeout=10.0)
            if result != ACK:
                raise RuntimeError(
                    f"Chunk {seq + 1}/{total_chunks} rejected: 0x{result:02X} "
                    f"({ERROR_MESSAGES.get(result, 'unknown')})"
                )
            _progress((seq + 1) * 100.0 / total_chunks)

        # Step 6: end
        _log("Finalizing update (CMD_END)…")
        result, _ = send_command(ser, CMD_END, timeout=10.0)
        # The radio sometimes resets immediately and the end-ack never arrives;
        # treat a missing reply as success after-the-fact, but log it.
        if result is None:
            _log("No reply to CMD_END — radio likely already rebooting.")
        elif result != ACK:
            _log(f"CMD_END returned 0x{result:02X} "
                 f"({ERROR_MESSAGES.get(result, 'unknown')}) — usually still successful.")
        _log("BTF flash complete. Radio should reboot with new firmware.")


def flash_btf(port: str, btf_path: str) -> None:
    """CLI entry point — load a .BTF file and flash it to `port`."""
    with open(btf_path, "rb") as f:
        btf_bytes = f.read()
    flash_to_port(port, btf_bytes)


def dry_run(btf_path: str, log_cb=None) -> None:
    """Validate a .BTF and build all packets without sending — checks every
    CRC self-test passes. log_cb(str) overrides print() for GUI integration."""
    def _log(msg=""):
        if log_cb:
            log_cb(msg)
        else:
            print(msg)

    with open(btf_path, "rb") as f:
        btf_bytes = f.read()

    # validate_btf uses print() directly; capture its output if a log_cb is
    # supplied. Cheap enough to mirror the work here in the GUI path.
    info = validate_btf(btf_bytes, btf_path)
    total_chunks = info["chunks"]
    model_sig = info["model_sig"]

    _log()
    _log("Building all BTF packets (dry run)…")
    p = build_packet(CMD_PROBE)
    assert p[0] == HEADER and p[-1] == TRAILER, "framing wrong"
    _log(f"  CMD_PROBE:           {p.hex()}")

    p = build_packet(CMD_VERSION, data=VERSION_STRING)
    _log(f"  CMD_VERSION:         {p.hex()}")

    p = build_packet(CMD_MODEL, data=model_sig)
    assert len(p) == 1 + 5 + 32 + 2 + 1, "model packet wrong size"
    _log(f"  CMD_MODEL:           {p.hex()}")

    p = build_packet(CMD_PKG_COUNT, data=struct.pack(">H", total_chunks - 1))
    _log(f"  CMD_PKG_COUNT:       {p.hex()} (total_chunks={total_chunks})")

    _log(f"  CMD_DATA:            building {total_chunks} packets…")
    for seq in range(total_chunks):
        offset = seq * DATA_BLOCK_SIZE
        block = btf_bytes[offset:offset + DATA_BLOCK_SIZE]
        if len(block) < DATA_BLOCK_SIZE:
            block = block + b"\x00" * (DATA_BLOCK_SIZE - len(block))
        pkt = build_packet(CMD_DATA, args=seq, data=block)
        # CRC self-check
        payload = pkt[1:-3]
        pkt_crc = (pkt[-3] << 8) | pkt[-2]
        assert crc16_ccitt(payload) == pkt_crc, f"chunk {seq}: CRC self-check failed"

    p = build_packet(CMD_END)
    _log(f"  CMD_END:             {p.hex()}")

    _log()
    _log(f"Total packets: {1 + 1 + 1 + 1 + total_chunks + 1}")
    _log("All CRC self-checks passed")
    _log("DRY RUN PASSED")


# --- Protocol-driver interface aliases ----------------------------------
# These make flash_btf interchangeable with flash_firmware in the GUI's
# dispatch helper. The GUI calls driver.validate_firmware / .probe_port /
# .flash_to_port without caring which protocol it is.
validate_firmware = validate_btf


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)

    if sys.argv[1] == "--dry-run":
        if len(sys.argv) < 3:
            print("Usage: flash_btf.py --dry-run <firmware.BTF>")
            sys.exit(2)
        dry_run(sys.argv[2])
        return

    if len(sys.argv) < 3:
        print("Usage: flash_btf.py <port> <firmware.BTF>")
        print("       flash_btf.py --dry-run <firmware.BTF>")
        sys.exit(2)

    flash_btf(sys.argv[1], sys.argv[2])


if __name__ == "__main__":
    main()
