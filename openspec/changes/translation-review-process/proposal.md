# Translation Review Process — Proposal

## Why

All 7 non-English catalogs (translations/zh-CN.json, fr.json, de.json, it.json, es.json, ar.json, ru.json) are machine-translated stubs marked `_meta.reviewed: false`. Many translation keys are innocuous (button labels, hints), but safety-critical strings demand native-speaker review:

- **radio.*.bootloader_keys**: Step-by-step instructions to enter bootloader mode. Errors here cause users to fail to flash, or worse, enter a wrong mode on their radio.
- **radio.*.notes**: Hardware-variant warnings (BF-F8HP Pro NRF vs NRFB; RT-490 old vs new PCB) that prevent bricking. Wrong interpretation of these notes can brick a radio permanently.
- **dialog.confirm_***: Confirmation dialogs that repeat bootloader instructions and warn about safety (power off, hold keys, screen blank, green LED). Poor translation here undermines the warning's urgency.
- **dialog.untested_***: Warnings about untested radios. Translation must convey risk.

The app has 162 non-radio translation strings organized in logical groups (button, tooltip, dialog, log, hint, etc.). The 7 required languages add ~40 dynamic radio-specific strings per radio definition. Tests already enforce completeness (`TestRadioStringTranslations`) and reject English-echo translations, but there is no process to surface review work to the community or track review progress.

## What Changes

1. **Community review tracking**: Create per-language GitHub issues (7 total) with a prioritized checklist of key groups. Each language issue tracks which key groups have been reviewed and fixed by native speakers.

2. **CONTRIBUTING.md**: Add a new "Translations" section documenting:
   - The review process: how to identify untranslated or echo strings, fix them, and test locally
   - Safety-critical priorities (radio.* fields, dialog strings)
   - Test expectations: the no-echo rule (translated strings must differ from English), completeness rule (all keys present), and how tests gate PRs
   - Where to find review tracking (language-specific issues)

3. **_meta.reviewed convention**: Mark each catalog `_meta.reviewed: true` only after a native speaker has reviewed and fixed all safety-critical strings. Whole-file bool chosen for simplicity: if a catalog is marked reviewed, all its strings (especially bootloader keys and hardware warnings) have been vetted by someone who speaks the language fluently. The test suite ensures no partial states slip through (completeness and no-echo tests catch drift).

4. **Optional in-app hint**: Annotate the language picker with "Machine translated — help review" for any language where `_meta.reviewed: false`. Helps users select a language knowing it may need improvement, and encourages contribution.

## Impact

- **For contributors**: Clear, trackable process to review translations. Tests give immediate feedback on quality. No new tools or infrastructure needed.
- **For users**: Unreviewed languages remain usable (fallback to English for missing keys), but they know which languages are native-reviewed. Reviewed languages have reliable safety-critical strings.
- **For safety**: Bootloader instructions and hardware warnings in 7 languages can now be reviewed by people who actually speak them. Issues are public, inviting community participation.
- **For maintenance**: Low overhead — just issues and a CONTRIBUTING section. The existing test suite already enforces the quality gates.

---

**Related context**: Current README mentions "community review PRs welcome" but provides no process. Safety-critical strings (bootloader sequences, hardware-variant notes) carry risk if mistranslated.
