# Property Intelligence Unified Workspace V11 - Edit + Verification
Version 6.3.0

## New controls
Team can:
- load a property by Property ID
- edit property details
- update photo/video/brochure links
- save changes
- verify a property

Every edit is written to pi_verification_log with:
- property ID
- action
- performed by
- old JSON
- new JSON
- timestamp

## Last Verified
Every property gets a visible Last Verified indicator.

Default:
VERIFICATION_DUE_DAYS=30

Display:
- Never Verified -> red
- Verification Due / older than configured days -> red
- Recently verified -> green

The Database Properties view also shows:
- last_verified
- verification_due

## Railway variable
VERIFICATION_DUE_DAYS=30

Keep all previous variables and deploy to the SAME backend repository/service/Postgres/domain.

After deployment /health must show version 6.3.0.
