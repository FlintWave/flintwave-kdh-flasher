# Pin Firmware Hashes — Implementation Tasks

## Pre-flight

- [ ] Verify `firmware_download.py` SHA-256 verification code path (lines 268-282) is working
- [ ] Confirm test suite runs cleanly: `python3 tests.py`
- [ ] Review current manifest state: `bf-f8hp-pro` and `bf-f8hp-pro-nrfb` are pinned; `uv-25-pro`, `rt-470`, `rt-490` are null

## For Each Unpinned Entry

### uv-25-pro (UV-25 Plus)

1. [ ] Download the bundle from `https://baofeng.s3.amazonaws.com/Baofeng_UV-25_Plus_Firware_Update_Guide_20250526.zip`
2. [ ] Extract and verify bundle contains expected firmware file (pattern: `*.kdhx`)
3. [ ] Inspect extracted firmware to confirm it matches UV-25 Plus (check header, validate against radios.json `firmware_filename_pattern`)
4. [ ] Calculate SHA-256 hash of the bundle file (use `sha256sum` or Python `hashlib`)
5. [ ] Record the hash and update `firmware_manifest.json` entry
6. [ ] Document the firmware version confirmed in `release_notes` (currently "V0.20")

### rt-470 (Radtel RT-470 series)

1. [ ] Download the bundle from `https://cdn.shopify.com/s/files/1/0564/8855/8800/files/RT-470_RT470X_RT470L_NEW_PCB_8.33KHZ_V2.13C_20240425.rar`
2. [ ] Extract and verify bundle contains expected firmware file (pattern: `*.kdhx`)
3. [ ] Inspect extracted firmware to confirm it matches RT-470 new PCB (radios.json specifies this variant)
4. [ ] Calculate SHA-256 hash of the bundle file
5. [ ] Record the hash and update `firmware_manifest.json` entry
6. [ ] Document the firmware version confirmed in `release_notes` (currently "V2.13C")

### rt-490 (Radtel RT-490 old PCB)

1. [ ] Download the bundle from `https://cdn.shopify.com/s/files/1/0564/8855/8800/files/Firmware_Version_1.03.zip`
2. [ ] Extract and verify bundle contains expected firmware file (pattern: `*.kdhx`)
3. [ ] Inspect extracted firmware to confirm it matches RT-490 old PCB variant
4. [ ] Calculate SHA-256 hash of the bundle file
5. [ ] Record the hash and update `firmware_manifest.json` entry
6. [ ] Document the firmware version confirmed in `release_notes` (currently "V1.03")

## Manifest Update

- [ ] Update `firmware_manifest.json` with the three new hashes
- [ ] Verify all non-null `firmware_url` entries now have non-null `firmware_sha256`
- [ ] Update `_updated` timestamp to reflect change date
- [ ] Verify `manifest_version` is consistent (currently 1)

## Test Coverage

- [ ] Add test `TestManifestSchema.test_all_entries_with_url_must_have_hash()`:
  - Assert: for every entry in manifest with non-null `firmware_url`, `firmware_sha256` is non-null
  - This gate prevents future unpinned entries from being merged
- [ ] Run test suite to confirm new test passes: `python3 tests.py TestManifestSchema`

## Documentation & Changelog

- [ ] Update `CHANGELOG.md` with entry in the target release section:
  ```
  - Pin SHA-256 hashes for all firmware downloads (uv-25-pro, rt-470, rt-490); 
    future unpinned entries blocked by test gate
  ```
- [ ] Verify no other documentation files reference the manifest schema
- [ ] (Optional) Update project context in `openspec/project.md` if domain notes need clarification on firmware safety

## Verification

- [ ] `python3 tests.py` passes all tests including new hash-pinning gate
- [ ] Manual spot-check: simulate a download with a pinned hash; verify correct hash is passed to `firmware_download.py`
- [ ] Manual spot-check: simulate hash mismatch; verify error message is actionable (current message: "The downloaded file may be corrupted or tampered with")
- [ ] No regressions in existing pinned entries (bf-f8hp-pro, bf-f8hp-pro-nrfb)

## Final Checklist

- [ ] All three bundles downloaded, inspected, and hashes recorded
- [ ] `firmware_manifest.json` updated with hashes
- [ ] Test added and passing
- [ ] `CHANGELOG.md` updated
- [ ] PR created with summary and test results
