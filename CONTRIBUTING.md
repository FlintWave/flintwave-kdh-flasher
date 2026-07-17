# Contributing to FlintWave Flash

Contributions welcome — new radio definitions, bug fixes, test reports from
real hardware, and translation review. This document currently covers the
translation review process; for radio definitions see the `_comment` header in
`radios.json`.

## Translations

### How translation works

FlintWave Flash ships with English bundled and 7 downloadable languages:

| Language | Code | Notes |
|---|---|---|
| Chinese (Simplified) | `zh-CN` | |
| French | `fr` | |
| German | `de` | |
| Italian | `it` | |
| Spanish | `es` | |
| Arabic | `ar` | right-to-left layout |
| Russian | `ru` | |

English (`translations/en.json`) is the source of truth for UI strings;
per-radio strings (bootloader keys, connector, notes) have their English
source in `radios.json` and translated overrides keyed
`radio.<id>.<field>` in each catalog. Non-English catalogs download from
GitHub the first time you pick the language and are cached locally.

All 7 non-English catalogs started as **machine translations** and carry
`"reviewed": false` in their `_meta` block until a native speaker has checked
them. The app marks unreviewed languages in the language picker
("machine translated, help review").

### Safety-critical strings — review these first

Some strings carry real user-safety weight. Mistranslation here can make a
user fail to enter bootloader mode, or worse, flash the wrong firmware and
**permanently brick their radio**:

1. **`radio.<id>.bootloader_keys`** — the key-press sequence to enter
   bootloader mode. The intent must survive translation; keep button names
   like "SK1"/"PTT" as-is and translate the surrounding instructions.
2. **`radio.<id>.notes`** — hardware-variant warnings (e.g. the BF-F8HP Pro
   NRF/NRFB split, the RT-490 old/new PCB split). The "these firmware files
   are NOT interchangeable" warning must stay unmistakably clear.
3. **`dialog.confirm_title` / `dialog.confirm_single` /
   `dialog.confirm_batch_*`** — the final pre-flash confirmation dialogs.
   Preserve urgency and the "do not disconnect during flash" instruction.
4. **`dialog.untested_*`** — warnings shown when flashing a radio no one has
   confirmed with this tool yet. The risk must come through.

### How to review

1. **Find your language's tracking issue** — each language has one, labeled
   [`translation-review`](https://github.com/FlintWave/flintwave-kdh-flasher/issues?q=is%3Aissue+label%3Atranslation-review).
   Its checklist shows which key groups still need review. Comment to claim a
   group so work isn't duplicated.
2. **Edit locally** — clone the repo and edit `translations/<code>.json` in a
   text editor. Change only string values, never keys. For `radio.*` fields,
   the English source is in `radios.json`.
3. **Run the gate tests** (optional but recommended):
   ```bash
   python3 tests.py
   ```
   Two tests gate every translation PR: **completeness** (every per-radio
   field must have a key in every catalog) and **no-echo** (a "translation"
   identical to the English source is rejected — don't copy-paste English).
   If a technical term has no translation, keep the term and translate the
   text around it.
4. **Open a PR** — branch `translate/<code>`, link the tracking issue, and
   list the key groups you reviewed in the PR description.
5. **Maintainer closes the loop** — once a language's CRITICAL and IMPORTANT
   groups are native-speaker reviewed, a maintainer sets
   `_meta.reviewed: true`, the picker hint disappears, and the tracking issue
   is closed.

### Catalog format

```json
{
  "_meta": {
    "language": "de",
    "language_label": "Deutsch",
    "reviewed": false,
    "source": "machine"
  },
  "app.title": "FlintWave Flash",
  "radio.bf-f8hp-pro.bootloader_keys": "SK1 + SK2 …"
}
```

`_meta` is housekeeping (stripped at load time). `reviewed: true` asserts a
native speaker checked all strings, the safety-critical ones above included.
