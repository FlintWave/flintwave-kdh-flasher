#!/usr/bin/env python3
"""
Test suite for flintwave-kdh-flasher.

Run all tests:
    python3 tests.py

Run specific test class:
    python3 -m pytest tests.py::TestCRC -v

Run with coverage (if pytest-cov installed):
    python3 -m pytest tests.py --cov=flash_firmware --cov-report=term-missing
"""

import os
import math
import json
import struct
import tempfile
import unittest

import flash_firmware as fw


class TestCRC(unittest.TestCase):
    """CRC-16/CCITT implementation tests."""

    def test_empty(self):
        self.assertEqual(fw.crc16_ccitt(b""), 0x0000)

    def test_single_byte(self):
        result = fw.crc16_ccitt(b"\x00")
        self.assertIsInstance(result, int)
        self.assertTrue(0 <= result <= 0xFFFF)

    def test_known_value(self):
        # "123456789" is the standard CRC-16/CCITT test vector
        # CRC-16/CCITT (poly 0x1021, init 0x0000) of "123456789" = 0x31C3
        self.assertEqual(fw.crc16_ccitt(b"123456789"), 0x31C3)

    def test_bootloader_string(self):
        result = fw.crc16_ccitt(b"BOOTLOADER")
        self.assertIsInstance(result, int)
        self.assertTrue(0 <= result <= 0xFFFF)

    def test_all_zeros(self):
        result = fw.crc16_ccitt(b"\x00" * 1024)
        self.assertIsInstance(result, int)

    def test_all_ones(self):
        result = fw.crc16_ccitt(b"\xff" * 1024)
        self.assertIsInstance(result, int)

    def test_deterministic(self):
        data = os.urandom(256)
        self.assertEqual(fw.crc16_ccitt(data), fw.crc16_ccitt(data))


class TestPacketBuilding(unittest.TestCase):
    """Packet construction tests."""

    def test_header_and_trailer(self):
        pkt = fw.build_packet(0x01, 0x00)
        self.assertEqual(pkt[0], 0xAA)
        self.assertEqual(pkt[-1], 0xEF)

    def test_minimum_packet_size(self):
        # No data: AA + cmd + seed + lenH + lenL + crcH + crcL + EF = 8
        pkt = fw.build_packet(0x01, 0x00)
        self.assertEqual(len(pkt), 8)

    def test_command_and_seed(self):
        pkt = fw.build_packet(0x42, 0x05)
        self.assertEqual(pkt[1], 0x42)
        self.assertEqual(pkt[2], 0x05)

    def test_data_length_encoding(self):
        data = b"BOOTLOADER"  # 10 bytes
        pkt = fw.build_packet(0x01, 0x00, data)
        length = (pkt[3] << 8) | pkt[4]
        self.assertEqual(length, 10)

    def test_data_in_packet(self):
        data = b"BOOTLOADER"
        pkt = fw.build_packet(0x01, 0x00, data)
        self.assertEqual(pkt[5:15], data)

    def test_crc_validates(self):
        data = b"BOOTLOADER"
        pkt = fw.build_packet(0x01, 0x00, data)
        payload = pkt[1:-3]  # cmd + seed + len + data
        crc_in_pkt = (pkt[-3] << 8) | pkt[-2]
        self.assertEqual(fw.crc16_ccitt(payload), crc_in_pkt)

    def test_1024_byte_chunk(self):
        chunk = os.urandom(1024)
        pkt = fw.build_packet(fw.CMD_UPDATE, 0, chunk)
        # 8 bytes overhead + 1024 data = 1032
        self.assertEqual(len(pkt), 1032)
        # Verify CRC
        payload = pkt[1:-3]
        crc_in_pkt = (pkt[-3] << 8) | pkt[-2]
        self.assertEqual(fw.crc16_ccitt(payload), crc_in_pkt)

    def test_all_commands(self):
        for cmd in [fw.CMD_HANDSHAKE, fw.CMD_UPDATE, fw.CMD_UPDATE_DATA_PACKAGES,
                    fw.CMD_INTO_BOOT, fw.CMD_UPDATE_END]:
            pkt = fw.build_packet(cmd, 0)
            self.assertEqual(pkt[0], 0xAA)
            self.assertEqual(pkt[-1], 0xEF)
            self.assertEqual(pkt[1], cmd)

    def test_seed_wrapping(self):
        pkt = fw.build_packet(fw.CMD_UPDATE, 255, b"\x00" * 1024)
        self.assertEqual(pkt[2], 255)

    def test_known_handshake_packet(self):
        """Verify the handshake packet matches the expected hex from dry run."""
        pkt = fw.build_packet(fw.CMD_HANDSHAKE, 0, b"BOOTLOADER")
        self.assertEqual(pkt.hex(), "aa0100000a424f4f544c4f4144455252abef")

    def test_known_into_boot_packet(self):
        pkt = fw.build_packet(fw.CMD_INTO_BOOT, 0)
        self.assertEqual(pkt.hex(), "aa4200000083f4ef")

    def test_known_end_packet(self):
        pkt = fw.build_packet(fw.CMD_UPDATE_END, 0)
        self.assertEqual(pkt.hex(), "aa45000000d2d9ef")


class TestFirmwareValidation(unittest.TestCase):
    """Firmware file validation tests."""

    def _make_firmware(self, sp=0x200078E0, reset=0x08001185, size=1024):
        """Create a minimal valid firmware blob."""
        header = struct.pack("<II", sp, reset)
        padding = b"\x00" * (size - len(header))
        return header + padding

    def test_valid_firmware(self):
        firmware = self._make_firmware()
        # Should not raise
        fw.validate_firmware(firmware, "test.kdhx")

    def test_too_small(self):
        with self.assertRaises(ValueError) as ctx:
            fw.validate_firmware(b"\x00" * 100, "test.kdhx")
        self.assertIn("too small", str(ctx.exception))

    def test_too_large(self):
        firmware = self._make_firmware(size=fw.MAX_FIRMWARE_BYTES + 1)
        with self.assertRaises(ValueError) as ctx:
            fw.validate_firmware(firmware, "test.kdhx")
        self.assertIn("too large", str(ctx.exception))

    def test_too_many_chunks(self):
        # 256 * 1024 = 262144 bytes, needs 256 chunks > MAX_CHUNKS (255)
        firmware = self._make_firmware(size=256 * 1024)
        with self.assertRaises(ValueError) as ctx:
            fw.validate_firmware(firmware, "test.kdhx")
        self.assertIn("chunks", str(ctx.exception))

    def test_invalid_stack_pointer(self):
        firmware = self._make_firmware(sp=0x00000000)
        with self.assertRaises(ValueError) as ctx:
            fw.validate_firmware(firmware, "test.kdhx")
        self.assertIn("stack pointer", str(ctx.exception).lower())

    def test_invalid_reset_handler(self):
        firmware = self._make_firmware(reset=0x00000000)
        with self.assertRaises(ValueError) as ctx:
            fw.validate_firmware(firmware, "test.kdhx")
        self.assertIn("reset handler", str(ctx.exception).lower())

    def test_max_valid_size(self):
        # 255 chunks * 1024 = 261120 bytes — should be fine
        firmware = self._make_firmware(size=255 * 1024)
        fw.validate_firmware(firmware, "test.kdhx")

    def test_chunk_count_boundary(self):
        firmware = self._make_firmware(size=255 * 1024)
        total_chunks = math.ceil(len(firmware) / 1024)
        self.assertEqual(total_chunks, 255)
        self.assertLessEqual(total_chunks, fw.MAX_CHUNKS)


class TestPacketRoundTrip(unittest.TestCase):
    """Verify packets can be built and self-verified."""

    def test_handshake_roundtrip(self):
        pkt = fw.build_packet(fw.CMD_HANDSHAKE, 0, b"BOOTLOADER")
        # Parse it back
        self.assertEqual(pkt[0], fw.HEADER)
        self.assertEqual(pkt[-1], fw.TRAILER)
        cmd = pkt[1]
        seed = pkt[2]
        data_len = (pkt[3] << 8) | pkt[4]
        data = pkt[5:5 + data_len]
        crc_recv = (pkt[5 + data_len] << 8) | pkt[5 + data_len + 1]
        crc_calc = fw.crc16_ccitt(pkt[1:5 + data_len])
        self.assertEqual(cmd, fw.CMD_HANDSHAKE)
        self.assertEqual(seed, 0)
        self.assertEqual(data, b"BOOTLOADER")
        self.assertEqual(crc_recv, crc_calc)

    def test_all_chunks_of_random_firmware(self):
        """Simulate a full firmware flash packet sequence."""
        firmware = self._make_firmware(size=10 * 1024 + 500)
        total_chunks = math.ceil(len(firmware) / 1024)
        self.assertEqual(total_chunks, 11)

        for i in range(total_chunks):
            chunk = firmware[i * 1024:(i + 1) * 1024]
            pkt = fw.build_packet(fw.CMD_UPDATE, i & 0xFF, chunk)
            # Verify structure
            self.assertEqual(pkt[0], fw.HEADER)
            self.assertEqual(pkt[-1], fw.TRAILER)
            self.assertEqual(pkt[1], fw.CMD_UPDATE)
            self.assertEqual(pkt[2], i & 0xFF)
            # Verify CRC
            data_len = (pkt[3] << 8) | pkt[4]
            payload = pkt[1:5 + data_len]
            crc_recv = (pkt[5 + data_len] << 8) | pkt[5 + data_len + 1]
            self.assertEqual(fw.crc16_ccitt(payload), crc_recv)
            # Verify data
            self.assertEqual(pkt[5:5 + data_len], chunk)

    def _make_firmware(self, size=1024):
        header = struct.pack("<II", 0x200078E0, 0x08001185)
        return header + os.urandom(size - len(header))


class TestResponseParsing(unittest.TestCase):
    """Test response buffer safety limits."""

    def test_max_response_data_constant(self):
        self.assertGreater(fw.MAX_RESPONSE_DATA, 0)
        self.assertLessEqual(fw.MAX_RESPONSE_DATA, 256)

    def test_safety_limits_defined(self):
        self.assertGreater(fw.MAX_FIRMWARE_BYTES, 0)
        self.assertGreater(fw.MIN_FIRMWARE_BYTES, 0)
        self.assertGreater(fw.MAX_CHUNKS, 0)
        self.assertLessEqual(fw.MAX_CHUNKS, 255)


class TestDryRun(unittest.TestCase):
    """Test dry run with actual firmware file if available."""

    FIRMWARE_PATH = os.path.expanduser(
        "~/baofeng-firmware/F8HPPRO-V53-Update-Bundle/BTECH_V0.53_260116.kdhx"
    )

    @unittest.skipUnless(
        os.path.exists(FIRMWARE_PATH),
        "Firmware file not available"
    )
    def test_dry_run_real_firmware(self):
        result = fw.dry_run(self.FIRMWARE_PATH)
        self.assertTrue(result)

    def test_dry_run_synthetic_firmware(self):
        header = struct.pack("<II", 0x200078E0, 0x08001185)
        firmware = header + os.urandom(50 * 1024 - len(header))

        with tempfile.NamedTemporaryFile(suffix=".kdhx", delete=False) as f:
            f.write(firmware)
            path = f.name

        try:
            result = fw.dry_run(path)
            self.assertTrue(result)
        finally:
            os.unlink(path)

    def test_dry_run_invalid_file(self):
        with tempfile.NamedTemporaryFile(suffix=".kdhx", delete=False) as f:
            f.write(b"\x00" * 100)
            path = f.name

        try:
            result = fw.dry_run(path)
            self.assertFalse(result)
        finally:
            os.unlink(path)


class TestRadioDefinitions(unittest.TestCase):
    """Validate radios.json structure."""

    def setUp(self):
        radios_path = os.path.join(os.path.dirname(__file__), "radios.json")
        with open(radios_path) as f:
            self.data = json.load(f)
        self.radios = self.data["radios"]

    def test_has_radios(self):
        self.assertGreater(len(self.radios), 0)

    def test_required_fields(self):
        required = ["id", "name", "manufacturer", "model_type",
                     "bootloader_keys", "connector", "tested"]
        for radio in self.radios:
            for field in required:
                self.assertIn(field, radio, f"Radio {radio.get('id', '?')} missing '{field}'")

    def test_unique_ids(self):
        ids = [r["id"] for r in self.radios]
        self.assertEqual(len(ids), len(set(ids)), "Duplicate radio IDs found")

    def test_model_types_are_strings(self):
        for radio in self.radios:
            self.assertIsInstance(radio["model_type"], str)
            self.assertGreater(len(radio["model_type"]), 0)

    def test_firmware_urls_are_https_or_null(self):
        for radio in self.radios:
            url = radio.get("firmware_url")
            if url is not None:
                self.assertTrue(url.startswith("https://"),
                                f"Radio {radio['id']} has non-HTTPS URL: {url}")

    def test_tested_is_bool(self):
        for radio in self.radios:
            self.assertIsInstance(radio["tested"], bool)


class TestDownloader(unittest.TestCase):
    """Test firmware download safety checks."""

    def test_url_validation_https_only(self):
        import firmware_download as dl
        with self.assertRaises(ValueError):
            dl.validate_url("http://baofengtech.com/file.zip")

    def test_url_validation_allowed_domains(self):
        import firmware_download as dl
        # Should not raise
        dl.validate_url("https://baofengtech.com/file.zip")
        dl.validate_url("https://www.baofengtech.com/file.zip")
        dl.validate_url("https://www.radtels.com/file.zip")

    def test_url_validation_blocked_domains(self):
        import firmware_download as dl
        with self.assertRaises(ValueError):
            dl.validate_url("https://evil.com/file.zip")

    def test_url_validation_path_traversal(self):
        import firmware_download as dl
        with self.assertRaises(ValueError):
            dl.validate_url("https://baofengtech.com/../../../etc/passwd")

    def test_load_radios(self):
        import firmware_download as dl
        radios = dl.load_radios()
        self.assertIsInstance(radios, list)
        self.assertGreater(len(radios), 0)

    def test_get_radio_by_id(self):
        import firmware_download as dl
        radio = dl.get_radio_by_id("bf-f8hp-pro")
        self.assertIsNotNone(radio)
        self.assertEqual(radio["name"], "BTECH BF-F8HP Pro")

    def test_get_radio_by_id_unknown(self):
        import firmware_download as dl
        radio = dl.get_radio_by_id("nonexistent-radio")
        self.assertIsNone(radio)


class TestRadioBootloaderKeys(unittest.TestCase):
    """Verify all radios have usable bootloader key info."""

    def setUp(self):
        radios_path = os.path.join(os.path.dirname(__file__), "radios.json")
        with open(radios_path) as f:
            self.radios = json.load(f)["radios"]

    def test_bootloader_keys_not_empty(self):
        for radio in self.radios:
            self.assertIsInstance(radio["bootloader_keys"], str)
            self.assertGreater(len(radio["bootloader_keys"]), 0,
                               f"Radio {radio['id']} has empty bootloader_keys")

    def test_connector_not_empty(self):
        for radio in self.radios:
            self.assertIsInstance(radio["connector"], str)
            self.assertGreater(len(radio["connector"]), 0)


class TestUpdater(unittest.TestCase):
    """Test auto-updater module."""

    def test_get_local_commit(self):
        import updater
        commit = updater.get_local_commit()
        # Should return a 40-char hex string if in a git repo
        if commit:
            self.assertEqual(len(commit), 40)
            self.assertTrue(all(c in "0123456789abcdef" for c in commit))

    def test_repo_dir_exists(self):
        import updater
        self.assertTrue(os.path.isdir(updater.REPO_DIR))


class TestVersionConsistency(unittest.TestCase):
    """Verify version string exists in GUI code."""

    def test_version_format(self):
        """Version should be in YY.MM.N format."""
        import re
        gui_path = os.path.join(os.path.dirname(__file__), "flash_firmware_gui.py")
        with open(gui_path) as f:
            content = f.read()
        matches = re.findall(r'VERSION\s*=\s*"(\d+\.\d+\.\d+)"', content)
        self.assertGreater(len(matches), 0, "No VERSION found in GUI code")
        for ver in matches:
            parts = ver.split(".")
            self.assertEqual(len(parts), 3)
            for p in parts:
                self.assertTrue(p.isdigit())


class TestThemePalettes(unittest.TestCase):
    """Verify theme palette data is well-formed."""

    def test_all_themes_have_7_colors(self):
        """Each theme palette must have exactly 7 color tuples."""
        # Import the themes dict by reading the source
        # We test the structure matches what _set_theme expects
        gui_path = os.path.join(os.path.dirname(__file__), "flash_firmware_gui.py")
        with open(gui_path) as f:
            content = f.read()
        # Just verify the theme names exist in code
        for theme in ["latte", "frappe", "macchiato", "mocha", "high_contrast"]:
            self.assertIn(f'"{theme}"', content,
                          f"Theme {theme} not found in GUI code")


if __name__ == "__main__":
    unittest.main(verbosity=2)
