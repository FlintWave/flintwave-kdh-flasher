#!/usr/bin/env python3
"""
Native Linux firmware flasher for the BTECH BF-F8HP Pro.

Implements the BTECH bootloader protocol for flashing .kdhx firmware files
over serial. Developed by observing the serial communication between the
official CPS software and the radio using a logic analyzer, and referencing
publicly available documentation on the BTECH bootloader packet format.

Protocol overview (Manual Download — radio in bootloader mode via SK1+SK2):
  1. CMD_HANDSHAKE (0x01) with "BOOTLOADER" payload
  2. CMD_UPDATE_DATA_PACKAGES (0x04) with total chunk count
  3. CMD_UPDATE (0x03) x N with 1024-byte firmware chunks
  4. CMD_UPDATE_END (0x45)

Packet format:
  [0xAA][cmd][seed][lenH][lenL][data...][crcH][crcL][0xEF]
  CRC-16/CCITT (poly 0x1021, init 0x0000) over cmd+seed+len+data

Serial config: 115200 baud, 8N1, DTR enabled, RTS enabled

Usage:
    python3 flash_firmware.py /dev/ttyUSB0 BTECH_V0.53_260116.kdhx
    python3 flash_firmware.py --dry-run none BTECH_V0.53_260116.kdhx
    python3 flash_firmware.py --diag /dev/ttyUSB0
"""

import os
import re
import sys
import time
import math
import hashlib
import argparse

try:
    import serial
except ImportError:
    serial = None

# Protocol constants
CMD_HANDSHAKE = 0x01
CMD_UPDATE = 0x03
CMD_UPDATE_DATA_PACKAGES = 0x04
CMD_INTO_BOOT = 0x42
CMD_UPDATE_END = 0x45

HEADER = 0xAA
TRAILER = 0xEF
ACK = 0x06

# Safety limits
MAX_RESPONSE_DATA = 64
MAX_FIRMWARE_BYTES = 1 * 1024 * 1024  # 1 MB
MIN_FIRMWARE_BYTES = 256
MAX_CHUNKS = 255

ERROR_MESSAGES = {
    0xE1: "Handshake code error",
    0xE2: "Data verification error (retryable)",
    0xE3: "Incorrect address error",
    0xE4: "Flash write error",
    0xE5: "Command error",
}


def crc16_ccitt(data: bytes) -> int:
    """CRC-16/CCITT, polynomial 0x1021, initial value 0x0000."""
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc = crc << 1
            crc &= 0xFFFF
    return crc


def build_packet(cmd: int, seed: int, data: bytes = b"") -> bytes:
    """Build a framed packet with header, payload, CRC, and trailer."""
    data_len = len(data)
    payload = bytes([cmd, seed, (data_len >> 8) & 0xFF, data_len & 0xFF]) + data
    crc = crc16_ccitt(payload)
    return bytes([HEADER]) + payload + bytes([(crc >> 8) & 0xFF, crc & 0xFF, TRAILER])


def read_response_polling(ser, timeout_s=2.0):
    """Read a response packet using non-blocking polling.

    Reads in three stages matching the bootloader's response format:
      1. Wait for header byte (0xAA)
      2. Read 4 header bytes (cmd, args, lenH, lenL)
      3. Read data + CRC + trailer
    """
    buf = bytearray()
    deadline = time.time() + timeout_s

    # Stage 1: wait for header byte 0xAA
    while time.time() < deadline:
        if ser.in_waiting >= 1:
            b = ser.read(1)
            if b[0] == HEADER:
                buf.append(b[0])
                break
        time.sleep(0.001)
    else:
        return None

    # Stage 2: read 4 bytes (cmd, cmdArgs, lenH, lenL)
    while time.time() < deadline:
        if ser.in_waiting >= 4:
            buf.extend(ser.read(4))
            break
        time.sleep(0.001)
    else:
        return None

    data_len = (buf[3] << 8) | buf[4]

    if data_len > MAX_RESPONSE_DATA:
        raise ValueError(f"Oversized response data_len={data_len}, max={MAX_RESPONSE_DATA}")

    # Stage 3: read data + CRC(2) + trailer(1)
    remain = data_len + 3
    while time.time() < deadline:
        if ser.in_waiting >= remain:
            buf.extend(ser.read(remain))
            break
        time.sleep(0.001)
    else:
        return None

    # Validate CRC
    crc_calc = crc16_ccitt(bytes(buf[1:5 + data_len]))
    crc_recv = (buf[5 + data_len] << 8) | buf[5 + data_len + 1]
    if crc_calc != crc_recv:
        raise ValueError(f"CRC mismatch: calc=0x{crc_calc:04X} recv=0x{crc_recv:04X}")

    if buf[5 + data_len + 2] != TRAILER:
        raise ValueError(f"Bad trailer: 0x{buf[5 + data_len + 2]:02X}")

    cmd = buf[1]
    cmd_args = buf[2]
    data = bytes(buf[5:5 + data_len])
    return cmd, cmd_args, data


def send_command(ser, cmd, seed, data=b"", retries=5):
    """Send command and wait for ACK. Retry on recoverable errors."""
    packet = build_packet(cmd, seed, data)
    for attempt in range(retries):
        ser.reset_input_buffer()
        ser.write(packet)
        ser.flush()

        resp = read_response_polling(ser, timeout_s=2.0)
        if resp is None:
            if attempt < retries - 1:
                print(f"  Timeout, retrying ({attempt + 1}/{retries})...")
                time.sleep(0.1)
                continue
            raise TimeoutError("No response from radio")

        resp_cmd, resp_args, resp_data = resp

        # Check for error codes in both cmd and args fields.
        # The radio may return errors as either:
        #   cmd=error_code, args=anything (error in command field)
        #   cmd=echoed_cmd, args=error_code (error in args field)
        error_code = None
        if resp_cmd in ERROR_MESSAGES:
            error_code = resp_cmd
        elif resp_args in ERROR_MESSAGES:
            error_code = resp_args

        if error_code is not None:
            error_msg = ERROR_MESSAGES[error_code]
            # 0xE3 (address) and 0xE4 (flash write) are fatal
            if error_code in (0xE3, 0xE4):
                raise RuntimeError(f"Radio error: {error_msg}")
            # All other errors are retryable
            if attempt < retries - 1:
                print(f"  Error 0x{error_code:02X}: {error_msg}, retrying ({attempt + 1}/{retries})...")
                time.sleep(0.1)
                continue
            raise RuntimeError(f"Radio error: {error_msg}")

        if resp_args != ACK:
            if attempt < retries - 1:
                print(f"  Unexpected response (cmd=0x{resp_cmd:02X} args=0x{resp_args:02X}), retrying ({attempt + 1}/{retries})...")
                time.sleep(0.1)
                continue
            raise RuntimeError(f"Unexpected response: cmd=0x{resp_cmd:02X} args=0x{resp_args:02X}")

        return resp_cmd, resp_args, resp_data

    raise RuntimeError("Max retries exceeded")


def validate_firmware(firmware: bytes, path: str):
    """Validate firmware before flashing. Aborts on failure."""
    fw_size = len(firmware)
    total_chunks = math.ceil(fw_size / 1024)

    if fw_size < MIN_FIRMWARE_BYTES:
        raise ValueError(f"Firmware too small ({fw_size} bytes). Not a valid firmware file.")

    if fw_size > MAX_FIRMWARE_BYTES:
        raise ValueError(f"Firmware too large ({fw_size} bytes, max {MAX_FIRMWARE_BYTES}).")

    if total_chunks > MAX_CHUNKS:
        raise ValueError(
            f"Firmware requires {total_chunks} chunks, exceeds protocol limit of {MAX_CHUNKS}."
        )

    # ARM vector table sanity check
    sp = int.from_bytes(firmware[0:4], "little")
    reset = int.from_bytes(firmware[4:8], "little")
    if not (0x20000000 <= sp <= 0x20100000):
        raise ValueError(
            f"Invalid ARM stack pointer 0x{sp:08X} — not a valid firmware file."
        )
    if not (0x08000000 <= reset <= 0x08100000):
        raise ValueError(
            f"Invalid ARM reset handler 0x{reset:08X} — not a valid firmware file."
        )

    sha256 = hashlib.sha256(firmware).hexdigest()
    print(f"Firmware: {path}")
    print(f"Size: {fw_size} bytes, {total_chunks} chunks")
    print(f"SHA-256: {sha256}")


def flash_firmware(port: str, firmware_path: str):
    fw_size = os.path.getsize(firmware_path)
    if fw_size > MAX_FIRMWARE_BYTES:
        raise ValueError(f"File too large ({fw_size} bytes, max {MAX_FIRMWARE_BYTES}).")

    with open(firmware_path, "rb") as f:
        firmware = f.read()

    validate_firmware(firmware, firmware_path)
    total_chunks = math.ceil(len(firmware) / 1024)
    print(f"Port: {port}")
    print()

    with serial.Serial(
        port=port, baudrate=115200, bytesize=8,
        parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
        timeout=2.0, write_timeout=2.0
    ) as ser:
        ser.dtr = True
        ser.rts = True
        time.sleep(0.1)
        ser.reset_input_buffer()
        ser.reset_output_buffer()

        print("[1/3] Bootloader handshake...")
        send_command(ser, CMD_HANDSHAKE, 0, b"BOOTLOADER")
        print("  OK")

        print(f"[2/3] Sending firmware ({total_chunks} chunks)...")
        send_command(ser, CMD_UPDATE_DATA_PACKAGES, 0, bytes([total_chunks]))

        seq = 0
        for i in range(total_chunks):
            offset = i * 1024
            chunk = firmware[offset:offset + 1024]
            # Pad last chunk to 1024 bytes with zeros (bootloader expects fixed size)
            if len(chunk) < 1024:
                chunk = chunk + b'\x00' * (1024 - len(chunk))
            send_command(ser, CMD_UPDATE, seq & 0xFF, chunk)
            seq += 1
            time.sleep(0.02)  # Brief delay for flash write
            pct = ((i + 1) / total_chunks) * 100
            bar_len = 30
            filled = int(bar_len * (i + 1) / total_chunks)
            bar = "\u2588" * filled + "\u2591" * (bar_len - filled)
            print(f"\r  [{bar}] {pct:5.1f}% ({i + 1}/{total_chunks})", end="", flush=True)

        print()

        print("[3/3] Finalizing...")
        send_command(ser, CMD_UPDATE_END, 0)

    print("  OK")
    print()
    print("Firmware update complete! Power cycle the radio and check Menu > Radio Info.")


def run_diagnostics(port: str):
    """Test serial communication with the radio in bootloader mode."""
    print(f"Running diagnostics on {port}...")
    print()

    with serial.Serial(
        port=port, baudrate=115200, bytesize=8,
        parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
        timeout=1.0
    ) as ser:
        ser.dtr = True
        ser.rts = True
        time.sleep(0.1)

        print(f"  Port opened: {ser.name}")
        print(f"  Baud: {ser.baudrate}, DTR: {ser.dtr}, RTS: {ser.rts}")
        print(f"  CTS: {ser.cts}, DSR: {ser.dsr}, CD: {ser.cd}, RI: {ser.ri}")
        print()

        # FTDI latency timer (Linux only, safe path construction)
        dev = os.path.basename(port)
        if re.fullmatch(r'tty[A-Za-z0-9]+', dev):
            sysfs = f"/sys/bus/usb-serial/devices/{dev}/latency_timer"
            try:
                with open(sysfs) as f:
                    print(f"  FTDI latency timer: {f.read().strip()}ms")
            except OSError:
                print("  FTDI latency timer: unknown")
        print()

        # Test 1: CMD_HANDSHAKE (manual mode)
        print("Test 1: Sending CMD_HANDSHAKE...")
        packet = build_packet(CMD_HANDSHAKE, 0, b"BOOTLOADER")
        print(f"  TX: {packet.hex()}")
        ser.reset_input_buffer()
        ser.write(packet)
        ser.flush()

        time.sleep(1.0)
        avail = ser.in_waiting
        if avail:
            data = ser.read(min(avail, 128))
            print(f"  RX ({avail} bytes): {data.hex()}")
        else:
            print("  RX: no data")

        # Test 2: CMD_INTO_BOOT (auto mode)
        print()
        print("Test 2: Sending CMD_INTO_BOOT...")
        packet = build_packet(CMD_INTO_BOOT, 0)
        print(f"  TX: {packet.hex()}")
        ser.reset_input_buffer()
        ser.write(packet)
        ser.flush()

        time.sleep(1.0)
        avail = ser.in_waiting
        if avail:
            data = ser.read(min(avail, 128))
            print(f"  RX ({avail} bytes): {data.hex()}")
        else:
            print("  RX: no data")

        # Test 3: Poll for any response
        print()
        print("Test 3: Polling for any data over 3 seconds...")
        ser.reset_input_buffer()
        start = time.time()
        got_data = False
        while time.time() - start < 3.0:
            if ser.in_waiting:
                b = ser.read(min(ser.in_waiting, 128))
                print(f"  RX at {time.time() - start:.2f}s: {b.hex()}")
                got_data = True
            time.sleep(0.001)
        if not got_data:
            print("  No data received")

        print()
        print("Test 4: Line status check...")
        print(f"  CTS: {ser.cts}, DSR: {ser.dsr}, CD: {ser.cd}, RI: {ser.ri}")

    print()
    if not got_data:
        print("RESULT: Radio is not responding on RX.")
        print("Possible causes:")
        print("  - Cable RX line may be faulty")
        print("  - Radio not in bootloader mode (need SK1+SK2 held during power on)")
        print("  - Wrong serial device (check dmesg for USB disconnect/reconnect)")
    else:
        print("RESULT: Radio is responding! The flash should work.")


def dry_run(firmware_path: str):
    """Verify packet construction without touching serial port."""
    fw_size = os.path.getsize(firmware_path)
    if fw_size > MAX_FIRMWARE_BYTES:
        print(f"FAIL: File too large ({fw_size} bytes, max {MAX_FIRMWARE_BYTES})")
        return False

    with open(firmware_path, "rb") as f:
        firmware = f.read()

    fw_size = len(firmware)
    total_chunks = math.ceil(fw_size / 1024)

    if fw_size < MIN_FIRMWARE_BYTES:
        print(f"FAIL: Firmware too small ({fw_size} bytes)")
        return False

    if total_chunks > MAX_CHUNKS:
        print(f"FAIL: Too many chunks ({total_chunks}, max {MAX_CHUNKS})")
        return False

    sha256 = hashlib.sha256(firmware).hexdigest()
    print(f"Firmware: {firmware_path}")
    print(f"Size: {fw_size} bytes, {total_chunks} chunks")
    print(f"SHA-256: {sha256}")
    print()

    sp = int.from_bytes(firmware[0:4], "little")
    reset = int.from_bytes(firmware[4:8], "little")
    print("ARM vector table check:")
    print(f"  Stack pointer:  0x{sp:08X}", end="")
    ok_sp = 0x20000000 <= sp <= 0x20100000
    print(" (valid SRAM)" if ok_sp else " (INVALID)")
    print(f"  Reset handler:  0x{reset:08X}", end="")
    ok_reset = 0x08000000 <= reset <= 0x08100000
    print(" (valid Flash)" if ok_reset else " (INVALID)")
    if not ok_sp or not ok_reset:
        print("\nFAIL: Invalid ARM vector table")
        return False
    print()

    print("Building all packets (manual download flow)...")

    p = build_packet(CMD_HANDSHAKE, 0, b"BOOTLOADER")
    print(f"  CMD_HANDSHAKE:            {p.hex()}")

    p = build_packet(CMD_UPDATE_DATA_PACKAGES, 0, bytes([total_chunks]))
    print(f"  CMD_UPDATE_DATA_PACKAGES: {p.hex()} (chunks={total_chunks})")

    print(f"  CMD_UPDATE:               building {total_chunks} data packets...")
    for i in range(total_chunks):
        offset = i * 1024
        chunk = firmware[offset:offset + 1024]
        p = build_packet(CMD_UPDATE, i & 0xFF, chunk)
        assert p[0] == HEADER and p[-1] == TRAILER
        payload = p[1:-3]
        pkt_crc = (p[-3] << 8) | p[-2]
        assert crc16_ccitt(payload) == pkt_crc, f"Chunk {i}: CRC self-check failed"

    p = build_packet(CMD_UPDATE_END, 0)
    print(f"  CMD_UPDATE_END:           {p.hex()}")

    print()
    print(f"Total packets: {total_chunks + 3}")
    print("All CRC self-checks passed")
    last_chunk_size = fw_size - (total_chunks - 1) * 1024
    print(f"Last chunk: {last_chunk_size} bytes")
    print()
    print("DRY RUN PASSED")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BTECH BF-F8HP Pro Firmware Flasher")
    parser.add_argument("port", nargs="?", default=None,
                        help="Serial port (e.g. /dev/ttyUSB0)")
    parser.add_argument("firmware", nargs="?", default=None,
                        help="Path to .kdhx firmware file")
    parser.add_argument("--dry-run", action="store_true",
                        help="Verify packets without serial communication")
    parser.add_argument("--diag", action="store_true",
                        help="Run serial diagnostics")
    args = parser.parse_args()

    print("=" * 50)
    print("  BTECH BF-F8HP Pro Firmware Flasher")
    print("=" * 50)
    print()

    if args.dry_run:
        if not args.firmware:
            parser.error("--dry-run requires firmware file")
        print("*** DRY RUN MODE ***")
        print()
        ok = dry_run(args.firmware)
        sys.exit(0 if ok else 1)

    if args.diag:
        if not args.port:
            parser.error("--diag requires port")
        if not serial:
            print("ERROR: pyserial not installed")
            sys.exit(1)
        run_diagnostics(args.port)
        sys.exit(0)

    if not args.port or not args.firmware:
        parser.error("port and firmware file required")

    if not serial:
        print("ERROR: pyserial not installed. Run: pip install pyserial")
        sys.exit(1)

    print("WARNING: Do not disconnect the radio or cable")
    print("         during the update process!")
    print()

    try:
        answer = input("Ready to flash? (yes/no): ")
        if answer.lower() != "yes":
            print("Aborted.")
            sys.exit(0)
        print()
        flash_firmware(args.port, args.firmware)
    except KeyboardInterrupt:
        print("\n\nAborted by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\n\nERROR: {e}")
        print("The radio may need to be power cycled and put back in bootloader mode.")
        sys.exit(1)
