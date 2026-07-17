#!/usr/bin/env python3
"""
In-memory mock bootloaders for end-to-end flash testing without hardware.

The real flashers (:mod:`flash_firmware` for the KDH protocol, :mod:`flash_btf`
for the .BTF / BOOTLOADER_V3 protocol) open a ``serial.Serial`` port and drive a
full handshake → announce → stream → finalize sequence over the wire. Until now
the test suite only exercised packet *framing* in isolation; the actual
send/receive state machine — chunk counting, sequence numbers, retry-on-error,
ordering enforcement — was only ever validated against a physical radio.

This module provides a fake serial endpoint that speaks each protocol back to
the flasher, so the whole sequence can be driven in a unit test:

    engine = KDHBootloader()
    with patch_serial(flash_firmware, engine=engine):
        flash_firmware.flash_to_port("/dev/mock", firmware)
    assert engine.finished
    assert engine.reassembled_firmware() == expected_padded_bytes

The engines are deliberately strict state machines: they answer 0xE5
("command error") when the flasher sends a command out of order, so a flasher
regression that streamed data before announcing the chunk count would be caught
here rather than on someone's radio. They also support fault injection
(``chunk_nak_once``, ``chunk_fatal``, ``bad_handshake``, ``model_mismatch``) so
the retryable/fatal error branches can be exercised deterministically.

Nothing here imports :mod:`serial` — the fake module shim supplies the handful
of attributes the flashers touch (``Serial``, ``PARITY_NONE``, ``STOPBITS_ONE``,
``SerialException``), so these tests run on any machine, in CI, with no pyserial
and no hardware.
"""

import contextlib

import flash_firmware as _kdh
import flash_btf as _btf

crc16_ccitt = _kdh.crc16_ccitt

ACK = 0x06


# --------------------------------------------------------------------------- #
# Fake serial transport
# --------------------------------------------------------------------------- #
class MockSerial:
    """A minimal drop-in for ``serial.Serial`` backed by a bootloader engine.

    Bytes written by the flasher are fed to ``engine`` as soon as a complete
    packet is buffered; the engine's framed response is queued for the flasher
    to read back. Everything is synchronous — by the time ``write()`` returns,
    the response is already sitting in the read buffer — so no threads or real
    timing are involved.
    """

    def __init__(self, engine, port=None, **kwargs):
        self.engine = engine
        self.port = port
        self._rx = bytearray()   # bytes waiting for the flasher to read()
        self._tx = bytearray()   # partial inbound packet from the flasher
        self.is_open = True
        # Modem-control lines the flashers set; stored so assignment works.
        self.dtr = False
        self.rts = False
        self.name = port

    # -- context manager -------------------------------------------------- #
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def close(self):
        self.is_open = False

    # -- buffer management ------------------------------------------------ #
    @property
    def in_waiting(self):
        return len(self._rx)

    def reset_input_buffer(self):
        self._rx.clear()

    def reset_output_buffer(self):
        self._tx.clear()

    def flush(self):
        pass

    # -- I/O -------------------------------------------------------------- #
    def write(self, data):
        self._tx.extend(data)
        self._pump()
        return len(data)

    def read(self, n=1):
        out = bytes(self._rx[:n])
        del self._rx[:n]
        return out

    def _pump(self):
        """Extract whole packets from the tx buffer and queue engine replies."""
        while self._tx:
            kind, size = self.engine.try_extract(self._tx)
            if kind is None:
                break                       # need more bytes
            if kind == "drop":
                del self._tx[:size]         # resync past a stray byte
                continue
            packet = bytes(self._tx[:size])
            del self._tx[:size]
            resp = self.engine.handle(packet)
            if resp:
                self._rx.extend(resp)


class _FakeSerialModule:
    """Stand-in for the ``serial`` module the flashers import.

    A ``registry`` maps port name → engine (for batch/multi-port flows); a bare
    ``engine`` is used for any port not in the registry.
    """

    PARITY_NONE = "N"
    STOPBITS_ONE = 1
    EIGHTBITS = 8

    class SerialException(Exception):
        pass

    def __init__(self, engine=None, registry=None):
        self.engine = engine
        self.registry = dict(registry or {})
        self.opened = []               # (port, MockSerial) for assertions

    def Serial(self, port=None, **kwargs):
        eng = self.registry.get(port, self.engine)
        if eng is None:
            raise self.SerialException(f"no mock bootloader registered for {port!r}")
        ser = MockSerial(eng, port=port, **kwargs)
        self.opened.append((port, ser))
        return ser


@contextlib.contextmanager
def patch_serial(*modules, engine=None, registry=None):
    """Temporarily point ``module.serial`` at a fake backed by mock engines.

    Usage::

        with patch_serial(flash_firmware, engine=my_engine):
            flash_firmware.flash_to_port(...)

    Restores the original ``serial`` attribute (often ``None`` in a
    pyserial-free environment) on exit.
    """
    fake = _FakeSerialModule(engine=engine, registry=registry)
    saved = [(m, getattr(m, "serial", None)) for m in modules]
    try:
        for m in modules:
            m.serial = fake
        yield fake
    finally:
        for m, old in saved:
            m.serial = old


# --------------------------------------------------------------------------- #
# KDH bootloader (flash_firmware protocol)
# --------------------------------------------------------------------------- #
class KDHBootloader:
    """Simulates a radio in KDH bootloader mode.

    State machine: idle → handshaked → counted → done. Each response is a real
    framed packet whose ``args`` byte is ACK (0x06) on success or an error code
    (0xE1..0xE5) on failure, matching what ``flash_firmware.send_command``
    inspects.

    Fault injection:
      * ``bad_handshake``     — answer 0xE1 to CMD_HANDSHAKE.
      * ``chunk_nak_once``    — iterable of seq numbers that NAK (0xE2,
                                retryable) on their first attempt then ACK.
      * ``chunk_fatal``       — ``(seq, code)`` returning a fatal error (0xE4)
                                for that chunk on every attempt.
    """

    HEADER = 0xAA
    TRAILER = 0xEF

    def __init__(self, *, bad_handshake=False, chunk_nak_once=None,
                 chunk_fatal=None):
        self.bad_handshake = bad_handshake
        self.chunk_nak_once = set(chunk_nak_once or ())
        self.chunk_fatal = chunk_fatal
        self.state = "idle"
        self.expected_chunks = None
        self.received_chunks = []      # payloads ACK'd, in order
        self.transcript = []           # (cmd, seed, data_len) per received pkt
        self.finished = False
        self._naked = set()

    # -- framing ---------------------------------------------------------- #
    def try_extract(self, buf):
        if not buf:
            return None, 0
        if buf[0] != self.HEADER:
            return "drop", 1
        if len(buf) < 5:
            return None, 0
        data_len = (buf[3] << 8) | buf[4]
        total = data_len + 8           # 0xAA + 4 hdr + data + crc(2) + trailer
        if len(buf) < total:
            return None, 0
        return "packet", total

    def _resp(self, cmd, status):
        # args byte carries ACK or the error code; flash_firmware reads buf[2].
        return _kdh.build_packet(cmd, status)

    def handle(self, packet):
        cmd = packet[1]
        seed = packet[2]
        data_len = (packet[3] << 8) | packet[4]
        data = packet[5:5 + data_len]
        self.transcript.append((cmd, seed, data_len))

        # Integrity check — a corrupt frame is a retryable verification error.
        crc_recv = (packet[5 + data_len] << 8) | packet[6 + data_len]
        crc_calc = crc16_ccitt(packet[1:5 + data_len])
        if packet[-1] != self.TRAILER or crc_recv != crc_calc:
            return self._resp(cmd, 0xE2)

        if cmd == _kdh.CMD_HANDSHAKE:
            if self.bad_handshake or data != b"BOOTLOADER":
                return self._resp(cmd, 0xE1)
            self.state = "handshaked"
            return self._resp(cmd, ACK)

        if cmd == _kdh.CMD_UPDATE_DATA_PACKAGES:
            if self.state not in ("handshaked", "counted"):
                return self._resp(cmd, 0xE5)
            self.expected_chunks = data[0] if data else 0
            self.received_chunks = []
            self.state = "counted"
            return self._resp(cmd, ACK)

        if cmd == _kdh.CMD_UPDATE:
            if self.state != "counted":
                return self._resp(cmd, 0xE5)
            seq = seed
            if self.chunk_fatal and seq == self.chunk_fatal[0]:
                return self._resp(cmd, self.chunk_fatal[1])
            if seq in self.chunk_nak_once and seq not in self._naked:
                self._naked.add(seq)
                return self._resp(cmd, 0xE2)   # retryable; chunk not stored
            self.received_chunks.append(data)
            return self._resp(cmd, ACK)

        if cmd == _kdh.CMD_UPDATE_END:
            if self.state != "counted":
                return self._resp(cmd, 0xE5)
            self.state = "done"
            self.finished = True
            return self._resp(cmd, ACK)

        return self._resp(cmd, 0xE5)           # unknown command

    # -- assertions helpers ---------------------------------------------- #
    def reassembled_firmware(self):
        return b"".join(self.received_chunks)

    def commands_seen(self):
        return [t[0] for t in self.transcript]


# --------------------------------------------------------------------------- #
# BTF bootloader (flash_btf / BOOTLOADER_V3 protocol)
# --------------------------------------------------------------------------- #
class BTFBootloader:
    """Simulates a radio in .BTF (AT32F403A) bootloader mode.

    State machine: idle → probed → versioned → modeled → counted → done.
    Responses are the fixed 9-byte form
    ``[0xAA][cmd][0x00][result][0x00][0x00][crcH][crcL][0x55]`` that
    ``flash_btf.parse_response`` decodes (result at index 3).

    Fault injection:
      * ``model_mismatch`` — answer 0xE6 to CMD_MODEL.
      * ``chunk_nak_once`` — seqs that NAK (0xE2, retryable) once then ACK.
      * ``chunk_fatal``    — ``(seq, code)`` fatal error for that chunk.
      * ``end_status``     — override the CMD_END result (default ACK).
    """

    HEADER = 0xAA
    TRAILER = 0x55

    def __init__(self, *, model_mismatch=False, chunk_nak_once=None,
                 chunk_fatal=None, end_status=ACK):
        self.model_mismatch = model_mismatch
        self.chunk_nak_once = set(chunk_nak_once or ())
        self.chunk_fatal = chunk_fatal
        self.end_status = end_status
        self.state = "idle"
        self.expected_chunks = None
        self.received_chunks = []
        self.transcript = []           # (cmd, args, data_len)
        self.model_sig = None
        self.finished = False
        self._naked = set()

    def try_extract(self, buf):
        if not buf:
            return None, 0
        if buf[0] != self.HEADER:
            return "drop", 1
        if len(buf) < 6:
            return None, 0
        data_len = (buf[4] << 8) | buf[5]
        total = data_len + 9           # 0xAA + 5 hdr + data + crc(2) + trailer
        if len(buf) < total:
            return None, 0
        return "packet", total

    def _resp(self, cmd, result):
        payload = bytes([cmd, 0x00, result, 0x00, 0x00])
        crc = crc16_ccitt(payload)
        return bytes([self.HEADER]) + payload + bytes([(crc >> 8) & 0xFF,
                                                       crc & 0xFF]) + bytes([self.TRAILER])

    def handle(self, packet):
        cmd = packet[1]
        args = (packet[2] << 8) | packet[3]
        data_len = (packet[4] << 8) | packet[5]
        data = packet[6:6 + data_len]
        self.transcript.append((cmd, args, data_len))

        crc_recv = (packet[6 + data_len] << 8) | packet[7 + data_len]
        crc_calc = crc16_ccitt(packet[1:6 + data_len])
        if packet[-1] != self.TRAILER or crc_recv != crc_calc:
            return self._resp(cmd, 0xE2)

        if cmd == _btf.CMD_PROBE:
            self.state = "probed"
            return self._resp(cmd, ACK)

        if cmd == _btf.CMD_VERSION:
            if data != _btf.VERSION_STRING:
                return self._resp(cmd, 0xE5)
            self.state = "versioned"
            return self._resp(cmd, ACK)

        if cmd == _btf.CMD_MODEL:
            if self.state not in ("versioned", "probed"):
                return self._resp(cmd, 0xE5)
            if self.model_mismatch:
                return self._resp(cmd, 0xE6)
            self.model_sig = data
            self.state = "modeled"
            return self._resp(cmd, ACK)

        if cmd == _btf.CMD_PKG_COUNT:
            if self.state != "modeled":
                return self._resp(cmd, 0xE5)
            self.expected_chunks = ((data[0] << 8) | data[1]) + 1 if len(data) >= 2 else 0
            self.received_chunks = []
            self.state = "counted"
            return self._resp(cmd, ACK)

        if cmd == _btf.CMD_DATA:
            if self.state != "counted":
                return self._resp(cmd, 0xE5)
            seq = args
            if self.chunk_fatal and seq == self.chunk_fatal[0]:
                return self._resp(cmd, self.chunk_fatal[1])
            if seq in self.chunk_nak_once and seq not in self._naked:
                self._naked.add(seq)
                return self._resp(cmd, 0xE2)
            self.received_chunks.append(data)
            return self._resp(cmd, ACK)

        if cmd == _btf.CMD_END:
            self.state = "done"
            self.finished = True
            return self._resp(cmd, self.end_status)

        return self._resp(cmd, 0xE5)

    def reassembled_firmware(self):
        return b"".join(self.received_chunks)

    def commands_seen(self):
        return [t[0] for t in self.transcript]
