ALLIANCE PHASE 5 - CANONICAL MATCHER
Version: 5.0.2-PHASE5-CANONICAL-MATCHER

This package is deliberately NOT wired into the production route yet.

Files:
1. alliance_phase5_canonical_matcher.py
   - Reads pi_properties canonical match-eligible rows.
   - Reads only the fixed live Phase 4.1 WhatsApp master generation.
   - Strict location/transaction/area/type gates before scoring.
   - Uses price only when explicitly comparable.
   - Excludes price from identity/deduplication.
   - Never selects or emits contact fields.
   - Separates VERIFIED results from records requiring verification.
   - Smart locality alternatives are use-aware and appear only after an exact inventory gap.

2. phase5_canonical_matcher_dry_run.py
   - Production-data read-only validator.
   - Runs self-tests and representative matching queries.
   - Executes no database writes.

DEPLOYMENT POLICY:
Dry run first. Do not replace alliance_deal_match_ai_v60.py and do not change routes until the live dry-run output is reviewed and approved.

5.0.1 safety correction:
- Final send eligibility requires strict READY + VERIFIED.
- READY_LEGACY remains internal verification inventory even if marked VERIFIED.
- Smart alternatives can appear when there is no VERIFIED exact match, while unverified exact candidates remain in the verification queue.

5.0.2 matching-quality correction:
- Preserve specific canonical DB localities even when not present in the static alias map.
- Continue rejecting city-only locations.
- Smart alternatives try the use-specific subtype map first, then inherit the approved family map.
- No change to send safety: strict READY + VERIFIED remains mandatory.
