# firmware-integrity — Spec Delta

## ADDED Requirements

### Requirement: Firmware bundles SHALL be verified against pinned SHA-256 hashes

Every `firmware_manifest.json` entry with a non-null `firmware_url` SHALL have
a non-null `firmware_sha256`. Before extracting a downloaded bundle, the
downloader SHALL compute the archive's SHA-256 and compare it to the pinned
value; on mismatch it SHALL delete the file and refuse to extract.

Rationale: vendors silently repack bundles at unchanged URLs (2026-07-10
BaofengTech incident — clients with null hashes accepted the new bundle
silently and broke for a week). Hash verification is the only reliable
detection for repacks or tampering; firmware selection is safety-critical.

#### Scenario: Normal download with pinned hash

- **GIVEN** a manifest entry (e.g. `uv-25-pro`) with a pinned `firmware_sha256`
- **WHEN** the user downloads firmware for that radio
- **THEN** the bundle's computed SHA-256 SHALL be compared to the pinned value
- **AND** extraction SHALL proceed only on an exact match, with no user-visible
  friction on the happy path

#### Scenario: Vendor repacks the bundle at an unchanged URL

- **GIVEN** a manifest entry with a pinned hash
- **WHEN** the vendor replaces the bundle content at the same URL
- **THEN** the downloader SHALL detect the mismatch, delete the downloaded
  file, and raise an error stating expected and actual hashes separately with
  likely causes (repack, corruption, tampering)
- **AND** the user SHALL be blocked from flashing until a human re-verifies
  the new bundle and updates the manifest pin

#### Scenario: Corrupted download

- **GIVEN** a manifest entry with a pinned hash
- **WHEN** the download is corrupted in transit
- **THEN** the mismatch SHALL be reported the same way, the stale file SHALL
  be deleted, and a retry SHALL be possible

### Requirement: The test suite SHALL block unpinned manifest entries

A test SHALL assert that every manifest entry with a non-null `firmware_url`
has a non-null `firmware_sha256`, so future entries cannot ship unpinned.

#### Scenario: New entry added without a hash

- **GIVEN** a developer adds a manifest entry with a `firmware_url` and a null
  `firmware_sha256`
- **WHEN** the test suite runs (locally or in CI)
- **THEN** the manifest-schema test SHALL fail naming the offending radio id,
  blocking the PR until the bundle is downloaded, verified, and pinned

## Notes

- Currently pinned: `bf-f8hp-pro`, `bf-f8hp-pro-nrfb` (same bundle). Unpinned
  blind spots: `uv-25-pro`, `rt-470`, `rt-490`. `rt-490-new` has a null URL —
  nothing to pin.
- Out of scope: changing firmware URLs; the Radtel live-scraper's null hashes
  (separate decision); firmware-file content validation (owned by
  `select_firmware_file()` per the firmware-selection spec).
