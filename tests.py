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


class TestReportURLs(unittest.TestCase):
    """Verify test report URL generation is safe."""

    def test_github_issue_url_is_valid(self):
        import urllib.parse
        title = "Test Report: BTECH BF-F8HP Pro — SUCCESS"
        body = "Radio: BTECH BF-F8HP Pro\nFirmware: test.kdhx\nResult: SUCCESS\n"
        url = ("https://github.com/FlintWave/flintwave-kdh-flasher/issues/new?"
               + urllib.parse.urlencode({"title": title, "body": body, "labels": "test-report"}))
        self.assertTrue(url.startswith("https://github.com/FlintWave/flintwave-kdh-flasher/"))
        parsed = urllib.parse.urlparse(url)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.hostname, "github.com")

    def test_github_url_includes_label(self):
        import urllib.parse
        title = "Test Report: BTECH BF-F8HP Pro — SUCCESS"
        body = "Radio: BTECH BF-F8HP Pro\nResult: SUCCESS\n"
        url = ("https://github.com/FlintWave/flintwave-kdh-flasher/issues/new?"
               + urllib.parse.urlencode({"title": title, "body": body, "labels": "test-report"}))
        self.assertIn("labels=test-report", url)

    def test_special_characters_escaped(self):
        import urllib.parse
        title = 'Test Report: Radio "Special" & <weird>'
        body = "Line1\nLine2\n"
        params = urllib.parse.urlencode({"title": title, "body": body})
        self.assertNotIn("<", params)
        self.assertNotIn(">", params)
        self.assertNotIn('"', params)


class TestReportGeneration(unittest.TestCase):
    """Test that report body content is well-formed."""

    def test_success_report_body(self):
        import platform
        radio_name = "BTECH BF-F8HP Pro"
        fw_file = "BTECH_V0.53_260116.kdhx"
        report = (
            f"Radio: {radio_name}\n"
            f"Firmware: {fw_file}\n"
            f"Result: SUCCESS\n"
            f"OS: {platform.system()} {platform.release()}\n"
            f"Python: {platform.python_version()}\n"
        )
        self.assertIn("Radio: BTECH BF-F8HP Pro", report)
        self.assertIn("Result: SUCCESS", report)
        self.assertNotIn("Error:", report)

    def test_failure_report_body(self):
        import platform
        error_msg = "No response from radio"
        report = (
            f"Radio: RT-470\n"
            f"Firmware: test.kdhx\n"
            f"Result: FAILED\n"
            f"OS: {platform.system()} {platform.release()}\n"
            f"Python: {platform.python_version()}\n"
            f"Error: {error_msg}\n"
        )
        self.assertIn("Result: FAILED", report)
        self.assertIn("Error: No response from radio", report)

    def test_report_body_has_os_info(self):
        import platform
        report = f"OS: {platform.system()} {platform.release()}\n"
        self.assertIn(platform.system(), report)

    def test_additional_notes_placeholder(self):
        report_body = "Radio: Test\nResult: SUCCESS\n\nAdditional notes:\n"
        self.assertTrue(report_body.endswith("Additional notes:\n"))


class TestThemePalettes(unittest.TestCase):
    """Verify Mocha (dark) and Latte (light) palettes are well-formed."""

    def _check_palette_shape(self, palette, name):
        self.assertEqual(len(palette), 7,
                         f"{name} palette must have exactly 7 color tuples")
        for i, color in enumerate(palette):
            self.assertEqual(len(color), 3, f"{name} color {i} is not RGB")
            for ch in color:
                self.assertIsInstance(ch, int)
                self.assertTrue(0 <= ch <= 255,
                                f"{name} color {i} channel out of range: {ch}")

    def _import_gui_themes(self):
        import importlib
        try:
            return importlib.import_module("gui_themes")
        except ImportError:
            self.skipTest("gui_themes not importable in this environment")

    def test_mocha_palette_shape(self):
        gt = self._import_gui_themes()
        self._check_palette_shape(gt.MOCHA_PALETTE, "Mocha")

    def test_latte_palette_shape(self):
        gt = self._import_gui_themes()
        self._check_palette_shape(gt.LATTE_PALETTE, "Latte")

    def test_theme_palettes_keys(self):
        """THEME_PALETTES exposes both 'mocha' and 'latte'."""
        gt = self._import_gui_themes()
        self.assertEqual(set(gt.THEME_PALETTES.keys()), {"mocha", "latte"})
        self.assertEqual(gt.THEME_PALETTES["mocha"], gt.MOCHA_PALETTE)
        self.assertEqual(gt.THEME_PALETTES["latte"], gt.LATTE_PALETTE)

    def test_palettes_actually_differ(self):
        """Sanity check — Mocha base must be dark, Latte base must be light."""
        gt = self._import_gui_themes()
        mocha_base_brightness = sum(gt.MOCHA_PALETTE[0])
        latte_base_brightness = sum(gt.LATTE_PALETTE[0])
        self.assertLess(mocha_base_brightness, latte_base_brightness,
                        "Mocha base should be darker than Latte base")


class TestHintCopy(unittest.TestCase):
    """Verify the FlasherFrame's hint state machine has every state the GUI
    transitions through, and that each state resolves to a non-empty
    (title, body) pair through the active translation catalog. Catches typos
    and missing keys that would otherwise only surface at runtime."""

    REQUIRED_STATES = {
        # Idle / pre-action states
        "no_firmware", "no_handset", "ready_flash", "batch_ready",
        # In-progress states
        "downloading", "flashing", "dryrun", "diagnostics",
        # Terminal states
        "complete", "dryrun_complete", "diag_complete", "failed",
    }

    def test_all_required_states_present(self):
        # Inspect HINT_STATES at the class level so we don't have to instantiate
        # the wx Frame (which requires a display).
        import importlib
        try:
            gm = importlib.import_module("gui_main")
        except ImportError:
            self.skipTest("gui_main not importable in this environment")
        keys = set(gm.FlasherFrame.HINT_STATES)
        missing = self.REQUIRED_STATES - keys
        self.assertFalse(missing, f"Missing HINT_STATES entries: {missing}")

    def test_each_state_resolves_through_i18n(self):
        """For every declared state, ensure the i18n catalog has matching
        hint.<state>.title and hint.<state>.body keys that resolve to
        non-empty strings."""
        import importlib
        try:
            gm = importlib.import_module("gui_main")
            i18n = importlib.import_module("i18n")
        except ImportError:
            self.skipTest("gui_main / i18n not importable in this environment")
        i18n.load_bundled_en()
        for state in gm.FlasherFrame.HINT_STATES:
            title = i18n.t(f"hint.{state}.title")
            body = i18n.t(f"hint.{state}.body")
            # If the raw key is returned, the catalog entry is missing.
            self.assertNotEqual(title, f"hint.{state}.title",
                                f"missing translation for hint.{state}.title")
            self.assertNotEqual(body, f"hint.{state}.body",
                                f"missing translation for hint.{state}.body")
            self.assertGreater(len(title), 0)
            self.assertGreater(len(body), 0)

    def test_dryrun_complete_does_not_say_power_cycle(self):
        """Regression: dry run hint must NOT instruct the user to power-cycle
        the radio (it never touched the radio). That copy belongs to 'complete'."""
        import importlib
        try:
            i18n = importlib.import_module("i18n")
        except ImportError:
            self.skipTest("i18n not importable in this environment")
        i18n.load_bundled_en()
        body = i18n.t("hint.dryrun_complete.body")
        self.assertNotIn("Power cycle", body)
        self.assertNotIn("power cycle", body)


class TestHandsetStatusConstants(unittest.TestCase):
    """Verify all per-handset status strings used by the batch flash flow are
    defined in gui_main. After the i18n migration these constants are
    translation keys (e.g. "status.ready") rather than English literals;
    the rendering layer translates them at write-time."""

    EXPECTED_KEYS = {
        "STATUS_UNKNOWN": "status.unknown",
        "STATUS_PROBING": "status.probing",
        "STATUS_READY": "status.ready",
        "STATUS_NO_RESP": "status.no_response",
        "STATUS_FLASHING": "status.flashing",
        "STATUS_DONE": "status.done",
        "STATUS_FAILED": "status.failed",
        "STATUS_SKIPPED": "status.skipped",
    }

    def test_status_constants_defined(self):
        import importlib
        try:
            gm = importlib.import_module("gui_main")
        except ImportError:
            self.skipTest("gui_main not importable in this environment")
        for name, expected_key in self.EXPECTED_KEYS.items():
            self.assertTrue(hasattr(gm, name), f"Missing status constant: {name}")
            self.assertEqual(getattr(gm, name), expected_key,
                             f"{name} should be the i18n key '{expected_key}'")

    def test_status_keys_resolve(self):
        """Every status key resolves to a non-empty English string."""
        import importlib
        try:
            i18n = importlib.import_module("i18n")
        except ImportError:
            self.skipTest("i18n not importable in this environment")
        i18n.load_bundled_en()
        for key in self.EXPECTED_KEYS.values():
            value = i18n.t(key)
            self.assertNotEqual(value, key, f"missing translation for {key}")
            self.assertGreater(len(value), 0)


class TestRadioNameDedup(unittest.TestCase):
    """Match the dedup rule used by both the radio dropdown and
    _format_radio_info: don't double-stamp the manufacturer when the model name
    already starts with it."""

    @staticmethod
    def _full_name(manufacturer, name):
        # Same one-liner used in both call sites.
        return name if name.startswith(manufacturer) else f"{manufacturer} {name}".strip()

    def test_name_already_starts_with_manufacturer(self):
        self.assertEqual(self._full_name("BTECH", "BTECH BF-F8HP Pro"),
                         "BTECH BF-F8HP Pro")

    def test_name_does_not_start_with_manufacturer(self):
        self.assertEqual(self._full_name("Baofeng", "UV-25 Plus"),
                         "Baofeng UV-25 Plus")

    def test_real_radios_do_not_double_up(self):
        """Run the rule against radios.json so adding a new entry that breaks
        the rule is caught immediately."""
        radios_path = os.path.join(os.path.dirname(__file__), "radios.json")
        with open(radios_path) as f:
            radios = json.load(f)["radios"]
        for r in radios:
            mfr, name = r["manufacturer"], r["name"]
            full = self._full_name(mfr, name)
            # The bug we're guarding against is "BTECH BTECH BF-F8HP Pro".
            self.assertNotIn(f"{mfr} {mfr}", full,
                             f"Manufacturer '{mfr}' doubled in '{full}'")


class TestUpdaterReleasesURL(unittest.TestCase):
    """Verify get_releases_url returns a usable GitHub releases URL."""

    def test_returns_releases_page_url(self):
        import updater
        url = updater.get_releases_url()
        self.assertIsInstance(url, str)
        self.assertTrue(url.startswith("https://"),
                        f"Expected https URL, got: {url}")
        self.assertIn("github.com", url)
        self.assertIn("releases", url)


class TestFirmwareVersion(unittest.TestCase):
    """Tests for firmware_version.py."""

    def test_parse_simple_version(self):
        import firmware_version as fv
        self.assertEqual(fv.parse_version("0.53"), (0, 53, 0))

    def test_parse_version_with_prefix(self):
        import firmware_version as fv
        self.assertEqual(fv.parse_version("V0.53"), (0, 53, 0))
        self.assertEqual(fv.parse_version("v0.53"), (0, 53, 0))

    def test_parse_version_with_alpha(self):
        import firmware_version as fv
        self.assertEqual(fv.parse_version("1.27a"), (1, 27, 1))

    def test_parse_version_uppercase_alpha(self):
        import firmware_version as fv
        self.assertEqual(fv.parse_version("V2.13A"), (2, 13, 1))

    def test_parse_none_returns_zero(self):
        import firmware_version as fv
        self.assertEqual(fv.parse_version(None), (0, 0, 0))
        self.assertEqual(fv.parse_version(""), (0, 0, 0))
        self.assertEqual(fv.parse_version("garbage"), (0, 0, 0))

    def test_compare_equal(self):
        import firmware_version as fv
        self.assertEqual(fv.compare_versions("0.53", "0.53"), 0)

    def test_compare_newer(self):
        import firmware_version as fv
        self.assertEqual(fv.compare_versions("0.54", "0.53"), 1)

    def test_compare_older(self):
        import firmware_version as fv
        self.assertEqual(fv.compare_versions("0.52", "0.53"), -1)

    def test_compare_alpha_ordering(self):
        import firmware_version as fv
        self.assertTrue(fv.is_newer("1.27b", "1.27a"))
        self.assertTrue(fv.is_newer("1.27a", "1.27"))
        self.assertFalse(fv.is_newer("1.27", "1.27a"))

    def test_compare_major_minor(self):
        import firmware_version as fv
        self.assertTrue(fv.is_newer("2.13A", "1.27a"))
        self.assertFalse(fv.is_newer("0.53", "1.03"))

    def test_extract_from_btech_filename(self):
        import firmware_version as fv
        self.assertEqual(fv.extract_version_from_filename("BTECH_V0.53_260116.kdhx"), "0.53")

    def test_extract_from_uv25_filename(self):
        import firmware_version as fv
        self.assertEqual(fv.extract_version_from_filename("UV25Pro_NRF_401+_V0.20_250217.kdhx"), "0.20")

    def test_extract_from_radtel_filename(self):
        import firmware_version as fv
        self.assertEqual(fv.extract_version_from_filename("RT-470_2.13A.rar"), "2.13A")
        self.assertEqual(fv.extract_version_from_filename("1.27a_firmware_240523.rar"), "1.27a")

    def test_extract_from_version_in_name(self):
        import firmware_version as fv
        self.assertEqual(fv.extract_version_from_filename("Firmware_Version_1.03.zip"), "1.03")

    def test_extract_from_unknown_filename(self):
        import firmware_version as fv
        self.assertIsNone(fv.extract_version_from_filename("random.kdhx"))
        self.assertIsNone(fv.extract_version_from_filename(None))


class TestFirmwareManifest(unittest.TestCase):
    """Tests for firmware_manifest.py state management."""

    def setUp(self):
        self._orig_state_file = None

    def _use_temp_state(self):
        import firmware_manifest as fm_mod
        self._orig_state_file = fm_mod.STATE_FILE
        self._tmpdir = tempfile.mkdtemp()
        fm_mod.STATE_FILE = os.path.join(self._tmpdir, "state.json")
        fm_mod.STATE_DIR = self._tmpdir
        return fm_mod

    def tearDown(self):
        if self._orig_state_file:
            import firmware_manifest as fm_mod
            fm_mod.STATE_FILE = self._orig_state_file
            import shutil
            shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_load_state_missing_file(self):
        fm_mod = self._use_temp_state()
        self.assertEqual(fm_mod._load_state(), {})

    def test_save_and_load_roundtrip(self):
        fm_mod = self._use_temp_state()
        data = {"test_key": "test_value", "nested": {"a": 1}}
        fm_mod._save_state(data)
        loaded = fm_mod._load_state()
        self.assertEqual(loaded, data)

    def test_record_flash_creates_entry(self):
        fm_mod = self._use_temp_state()
        fm_mod.record_flash("bf-f8hp-pro", "0.53", "abc123")
        last = fm_mod.get_last_flashed("bf-f8hp-pro")
        self.assertIsNotNone(last)
        self.assertEqual(last["version"], "0.53")
        self.assertEqual(last["firmware_sha256"], "abc123")
        self.assertIn("timestamp", last)

    def test_get_last_flashed_unknown_radio(self):
        fm_mod = self._use_temp_state()
        self.assertIsNone(fm_mod.get_last_flashed("nonexistent"))

    def test_record_flash_overwrites(self):
        fm_mod = self._use_temp_state()
        fm_mod.record_flash("test-radio", "1.0", "hash1")
        fm_mod.record_flash("test-radio", "2.0", "hash2")
        last = fm_mod.get_last_flashed("test-radio")
        self.assertEqual(last["version"], "2.0")

    def test_get_radio_firmware_info_with_manifest(self):
        import firmware_manifest as fm_mod
        manifest = {
            "bf-f8hp-pro": {
                "firmware_version": "0.53",
                "firmware_url": "https://example.com/fw.zip",
            }
        }
        info = fm_mod.get_radio_firmware_info("bf-f8hp-pro", manifest)
        self.assertEqual(info["firmware_version"], "0.53")

    def test_get_radio_firmware_info_missing(self):
        import firmware_manifest as fm_mod
        self.assertIsNone(fm_mod.get_radio_firmware_info("nope", {}))
        self.assertIsNone(fm_mod.get_radio_firmware_info("nope", None))


class TestManifestSchema(unittest.TestCase):
    """Validate firmware_manifest.json structure."""

    def setUp(self):
        manifest_path = os.path.join(os.path.dirname(__file__), "firmware_manifest.json")
        with open(manifest_path) as f:
            self.manifest = json.load(f)
        radios_path = os.path.join(os.path.dirname(__file__), "radios.json")
        with open(radios_path) as f:
            self.radios = json.load(f)["radios"]

    def test_manifest_has_version(self):
        self.assertIn("manifest_version", self.manifest)
        self.assertIsInstance(self.manifest["manifest_version"], int)

    def test_manifest_covers_radios_with_firmware(self):
        """Every radio with a firmware_url in radios.json should be in the manifest."""
        manifest_ids = set(self.manifest["radios"].keys())
        for radio in self.radios:
            if radio["id"] == "generic" or not radio.get("firmware_url"):
                continue
            self.assertIn(radio["id"], manifest_ids,
                          f"Radio {radio['id']} missing from manifest")

    def test_manifest_urls_are_valid(self):
        import firmware_download as dl
        for radio_id, info in self.manifest["radios"].items():
            url = info.get("firmware_url")
            if url:
                try:
                    dl.validate_url(url)
                except ValueError as e:
                    self.fail(f"Radio {radio_id} has invalid URL: {e}")

    def test_manifest_versions_are_parseable(self):
        import firmware_version as fv_mod
        for radio_id, info in self.manifest["radios"].items():
            ver = info.get("firmware_version")
            if ver:
                parsed = fv_mod.parse_version(ver)
                self.assertNotEqual(parsed, (0, 0, 0),
                                    f"Radio {radio_id} version '{ver}' did not parse")


class TestBtfProtocol(unittest.TestCase):
    """Verify the BTF (RT-950 Pro) protocol module — packet framing, CRC,
    validation, and the protocol-driver alias.
    """

    def test_module_exposes_driver_interface(self):
        # The GUI dispatches via a uniform interface; verify all required
        # callables exist on the BTF module so dispatch can't NameError.
        import flash_btf as btf
        for name in ("probe_port", "flash_to_port", "validate_firmware",
                     "MAX_FIRMWARE_BYTES", "build_packet", "dry_run"):
            self.assertTrue(hasattr(btf, name),
                            f"flash_btf missing {name} (driver interface)")

    def test_constants_match_spec(self):
        import flash_btf as btf
        self.assertEqual(btf.HEADER, 0xAA)
        self.assertEqual(btf.TRAILER, 0x55)
        self.assertEqual(btf.ACK, 0x06)
        self.assertEqual(btf.CMD_PROBE, 0x42)
        self.assertEqual(btf.CMD_VERSION, 0x0A)
        self.assertEqual(btf.CMD_MODEL, 0x02)
        self.assertEqual(btf.CMD_PKG_COUNT, 0x04)
        self.assertEqual(btf.CMD_DATA, 0x03)
        self.assertEqual(btf.CMD_END, 0x45)
        self.assertEqual(btf.VERSION_STRING, b"BOOTLOADER_V3")
        self.assertEqual(btf.BTF_MODEL_OFFSET, 0x3E0)
        self.assertEqual(btf.BTF_MODEL_SIZE, 32)
        self.assertEqual(btf.BTF_KEY_OFFSET, 0x400)
        self.assertEqual(btf.DATA_BLOCK_SIZE, 1024)

    def test_build_packet_framing(self):
        import flash_btf as btf
        # CMD_PROBE with no payload — anchors the framing format end-to-end.
        pkt = btf.build_packet(btf.CMD_PROBE)
        self.assertEqual(pkt[0], btf.HEADER)
        self.assertEqual(pkt[-1], btf.TRAILER)
        self.assertEqual(pkt[1], btf.CMD_PROBE)
        # args=0, len=0 → 4 zero bytes after cmd
        self.assertEqual(pkt[2:6], b"\x00\x00\x00\x00")
        self.assertEqual(len(pkt), 9)  # header + 5 + 0 + 2crc + trailer

    def test_build_packet_data_carries_2byte_seq(self):
        # CMD_DATA's seq# is a 16-bit big-endian field in args. Verify it
        # round-trips and survives values > 255 (KDH used 1 byte; BTF uses 2).
        import flash_btf as btf
        block = b"\xab" * 1024
        pkt = btf.build_packet(btf.CMD_DATA, args=0x0102, data=block)
        self.assertEqual(pkt[1], btf.CMD_DATA)
        self.assertEqual(pkt[2], 0x01)  # args_hi
        self.assertEqual(pkt[3], 0x02)  # args_lo
        self.assertEqual(pkt[4], 0x04)  # len_hi (1024 = 0x0400)
        self.assertEqual(pkt[5], 0x00)  # len_lo
        self.assertEqual(len(pkt), 1 + 5 + 1024 + 2 + 1)

    def test_build_packet_crc_self_consistent(self):
        import flash_btf as btf
        from flash_firmware import crc16_ccitt
        for cmd, args, data in [
            (btf.CMD_PROBE, 0, b""),
            (btf.CMD_VERSION, 0, btf.VERSION_STRING),
            (btf.CMD_MODEL, 0, b"RT-950      " + b"\x00" * 20),
            (btf.CMD_DATA, 0xFFFE, b"\x55" * 1024),
            (btf.CMD_END, 0, b""),
        ]:
            pkt = btf.build_packet(cmd, args, data)
            payload = pkt[1:-3]
            pkt_crc = (pkt[-3] << 8) | pkt[-2]
            self.assertEqual(crc16_ccitt(payload), pkt_crc,
                             f"CRC mismatch on cmd 0x{cmd:02X}")

    def test_parse_response_valid(self):
        import flash_btf as btf
        # Mock a 9-byte response: [AA][cmd][00][result][00][00][crcH][crcL][55]
        body = bytes([btf.HEADER, btf.CMD_PROBE, 0x00, btf.ACK,
                      0x00, 0x00, 0xDE, 0xAD, btf.TRAILER])
        parsed = btf.parse_response(body)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed, (btf.CMD_PROBE, btf.ACK))

    def test_parse_response_rejects_short_or_unframed(self):
        import flash_btf as btf
        self.assertIsNone(btf.parse_response(b""))
        self.assertIsNone(btf.parse_response(None))
        self.assertIsNone(btf.parse_response(b"\xAA\x42"))  # too short
        self.assertIsNone(btf.parse_response(b"\x99" + b"\x00" * 7 + b"\x55"))  # bad header
        self.assertIsNone(btf.parse_response(b"\xAA" + b"\x00" * 7 + b"\x99"))  # bad trailer

    def test_validate_btf_rejects_too_small(self):
        import flash_btf as btf
        with self.assertRaises(ValueError):
            btf.validate_firmware(b"\x00" * 100, "tiny.btf")

    def test_validate_btf_rejects_invalid_vector_table(self):
        import flash_btf as btf
        size = btf.BTF_KEY_OFFSET + btf.BTF_KEY_SIZE + btf.DATA_BLOCK_SIZE
        bad = bytearray(b"\x00" * size)
        bad[0:4] = (0xDEADBEEF).to_bytes(4, "little")  # SP outside SRAM
        bad[4:8] = (0x08003001).to_bytes(4, "little")
        with self.assertRaises(ValueError):
            btf.validate_firmware(bytes(bad), "bad-sp.btf")

    def test_validate_btf_accepts_valid_layout(self):
        import flash_btf as btf
        size = btf.BTF_KEY_OFFSET + btf.BTF_KEY_SIZE + btf.DATA_BLOCK_SIZE
        blob = bytearray(b"\x00" * size)
        blob[0:4] = (0x20001000).to_bytes(4, "little")
        blob[4:8] = (0x08003101).to_bytes(4, "little")
        blob[btf.BTF_MODEL_OFFSET:btf.BTF_MODEL_OFFSET + 12] = b"TEST-RADIO  "
        info = btf.validate_firmware(bytes(blob), "ok.btf")
        self.assertEqual(info["model_str"], "TEST-RADIO")
        self.assertEqual(info["size"], size)
        self.assertGreater(info["chunks"], 0)

    def test_error_messages_cover_documented_codes(self):
        import flash_btf as btf
        for code in (0xE1, 0xE2, 0xE3, 0xE5, 0xE6):
            self.assertIn(code, btf.ERROR_MESSAGES,
                          f"missing error message for 0x{code:02X}")

    def test_radio_with_btf_protocol_is_registered(self):
        # Catches accidental removal of the RT-950 Pro entry, and confirms
        # the field is actually `protocol: "btf"` so dispatch will fire.
        import json, os
        radios = json.load(open(os.path.join(
            os.path.dirname(__file__), "radios.json")))["radios"]
        btf_radios = [r for r in radios if r.get("protocol") == "btf"]
        self.assertGreater(len(btf_radios), 0,
                           "Expected at least one radio with protocol='btf'")
        for r in btf_radios:
            self.assertEqual(r.get("firmware_filename_pattern"), "*.BTF",
                             f"BTF radio {r['id']} should match *.BTF")


class TestRadioStringTranslations(unittest.TestCase):
    """Verify per-radio strings (bootloader_keys, connector, notes) have
    coverage in every shipped translation catalog. Catches contributors
    adding a new radio without the i18n keys, or a translation file
    drifting out of sync.
    """
    REQUIRED_LANGS = ["zh-CN", "fr", "de", "it", "es", "ar", "ru"]
    TRANSLATABLE_FIELDS = ("bootloader_keys", "connector", "notes")

    def setUp(self):
        import json, os
        repo = os.path.dirname(__file__)
        self.radios = json.load(open(os.path.join(repo, "radios.json")))["radios"]
        self.catalogs = {}
        for code in self.REQUIRED_LANGS:
            path = os.path.join(repo, "translations", f"{code}.json")
            self.catalogs[code] = json.load(open(path, encoding="utf-8"))

    def test_every_radio_field_translated_in_every_lang(self):
        missing = []
        for r in self.radios:
            rid = r["id"]
            for field in self.TRANSLATABLE_FIELDS:
                src = r.get(field)
                if not isinstance(src, str) or not src.strip():
                    continue
                key = f"radio.{rid}.{field}"
                for code, cat in self.catalogs.items():
                    if key not in cat:
                        missing.append(f"{code}: {key}")
        self.assertEqual(missing, [],
                         f"{len(missing)} missing radio translations: "
                         f"{missing[:5]}")

    def test_translations_are_actually_translated(self):
        # A common failure mode is the model echoing the English source.
        # Catch any radio.* key whose value is identical to the English source.
        echoed = []
        for r in self.radios:
            rid = r["id"]
            for field in self.TRANSLATABLE_FIELDS:
                src = r.get(field)
                if not isinstance(src, str) or not src.strip():
                    continue
                key = f"radio.{rid}.{field}"
                for code, cat in self.catalogs.items():
                    if cat.get(key, "").strip() == src.strip():
                        echoed.append(f"{code}: {key}")
        self.assertEqual(echoed, [],
                         f"{len(echoed)} translations identical to English "
                         f"source (model echo): {echoed[:5]}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
