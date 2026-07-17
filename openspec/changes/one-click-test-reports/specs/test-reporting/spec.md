## ADDED Requirements

### Requirement: Post-flash report offer
The system SHALL offer to file a community test report after every
terminal flash outcome (success or failure), unless suppressed per the
nag-suppression requirement below.

#### Scenario: Successful flash on a never-reported radio+firmware
- **WHEN** a flash completes successfully for a radio id and firmware
  version that have no prior `submitted` or `skipped` record in
  `state.json`
- **THEN** the app shows the test report dialog prefilled for a success
  report

#### Scenario: Failed flash on a never-reported radio+firmware
- **WHEN** a flash fails (any exception during the flash thread) for a
  radio id and firmware version that have no prior `submitted` or
  `skipped` record in `state.json`
- **THEN** the app shows the test report dialog prefilled for a failure
  report, including the error message

### Requirement: Prefilled report content
The system SHALL prefill the report title and body from data already
available in-app, and SHALL construct the GitHub issue URL via a pure
function independent of any GUI widget so it is unit-testable without a
display.

#### Scenario: Report body fields
- **WHEN** the report body is built for a flash outcome
- **THEN** it includes, in order: radio name, firmware filename (basename
  only), result (SUCCESS or FAILED), OS name and release, Python version,
  the error message if the flash failed, and (if a log was captured) the
  last 2000 characters of the in-app log under a log header

#### Scenario: URL stays within a safe length budget
- **WHEN** the report body includes a 2000-character log tail composed
  entirely of characters that expand under percent-encoding (e.g.
  newlines)
- **THEN** the fully encoded `issues/new` URL (title + body + labels)
  SHALL remain under 8000 characters

#### Scenario: Special characters are safely escaped
- **WHEN** the radio name, firmware filename, or log content contains
  characters with special meaning in a URL or HTML context (`<`, `>`,
  `"`, `&`)
- **THEN** the constructed URL SHALL NOT contain those raw characters
  unescaped

### Requirement: Nag suppression
The system SHALL remember, per radio id and firmware version, whether a
report was already submitted or explicitly skipped, and SHALL NOT offer
the report dialog again for that same combination.

#### Scenario: Repeat flash of the same radio+firmware after submitting
- **WHEN** a report was previously submitted for a given radio id and
  firmware version
- **AND** the same radio id and firmware version is flashed again
- **THEN** the app does not show the report dialog

#### Scenario: Repeat flash after explicit "don't ask again"
- **WHEN** the user checked "don't ask again for this radio+firmware" and
  dismissed the dialog for a given radio id and firmware version
- **AND** the same radio id and firmware version is flashed again
- **THEN** the app does not show the report dialog

#### Scenario: Plain Skip does not suppress
- **WHEN** the user dismisses the report dialog via the existing Skip
  button without checking "don't ask again"
- **AND** the same radio id and firmware version is flashed again
- **THEN** the app shows the report dialog again

#### Scenario: New firmware version on a previously-reported radio
- **WHEN** a report was previously submitted or suppressed for radio id
  `X` at firmware version `1.0`
- **AND** radio id `X` is flashed with firmware version `2.0`
- **THEN** the app shows the report dialog for the new version

#### Scenario: Unknown firmware version degrades gracefully
- **WHEN** the firmware version cannot be determined from the filename
- **THEN** nag suppression keys on radio id alone using a fallback
  version sentinel, and does not raise an error

### Requirement: Report content privacy visibility
The system SHALL show the user the complete, final prefilled report body
before any data leaves the machine, and SHALL NOT transmit report data
automatically.

#### Scenario: User reviews before submitting
- **WHEN** the report dialog is shown
- **THEN** the full prefilled body is displayed in an editable, readable
  text control before the user can submit

#### Scenario: Submission requires explicit user action in the browser
- **WHEN** the user clicks Submit in the report dialog
- **THEN** the app opens the prefilled GitHub issue page in the user's
  default browser and does not itself send any network request
  containing report data; final submission still requires the user to
  click GitHub's own submit control
