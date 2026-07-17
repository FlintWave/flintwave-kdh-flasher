# Pin Firmware Hashes — Proposal

## Why

Firmware vendors (BaofengTech, Radtel) silently repack bundles at unchanged URLs without updating file names or version strings. On 2026-07-10, BaofengTech repacked a bundle, breaking downloads for a week before detection. Null hashes in `firmware_manifest.json` are blind spots — the downloader silently accepts any repacked bundle, whether intentional or compromised.

**Pinning is also a safety surface for old clients**: released versions fetch the manifest remotely from master, so pinning hashes means:
- Any vendor repack requires a manifest update to restore functionality
- Users see a clear error rather than silently using unexpected firmware
- Old clients (e.g., v26.05) still benefit if a pin catches a repack after a later version is released

Treating firmware bundles as immutable (via hash verification) is standard practice in package managers, OS vendors, and deployment tooling. The infrastructure to verify is already in place (`firmware_download.py` lines 268-282); only the pinned hashes are missing.

## What Changes

1. **Manifest**: Add SHA-256 hashes for all firmware entries with non-null URLs
   - `uv-25-pro`: download bundle, verify contents, record hash
   - `rt-470`: download bundle, verify contents, record hash
   - `rt-490`: download bundle, verify contents, record hash
   - `bf-f8hp-pro` and `bf-f8hp-pro-nrfb`: already pinned ✓

2. **Tests**: Add assertion that every manifest entry with a non-null `firmware_url` MUST have a non-null `firmware_sha256`
   - Prevents future entries from shipping unpinned

3. **Specification**: Document that firmware bundles MUST be verified against pinned hashes, with actionable error messages on mismatch

## Impact

- **Functional**: No user-facing change; downloads are already verified against hashes if present. Adds verification where it was missing.
- **Correctness**: Detects silent repacks (the exact incident that occurred 2026-07-10).
- **Backwards compatible**: Existing verified downloads (bf-f8hp-pro entries) unchanged. Unverified downloads (uv-25-pro, rt-470, rt-490) become verified.
- **Future-proofing**: Test gate ensures new entries can't ship unpinned.

---

**Related incident**: BaofengTech bundle repack 2026-07-10; undetected for 7 days despite null hashes in manifest.
