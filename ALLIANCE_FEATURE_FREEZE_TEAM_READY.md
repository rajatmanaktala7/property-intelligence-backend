# ALLIANCE FEATURE FREEZE — TEAM READY CERTIFICATION V1

Status: ACTIVE
Baseline: 5f587fe
Purpose: Freeze feature expansion while Alliance is certified for daily team use.

## Frozen production contracts
- Production runtime must remain READY.
- Matcher authority remains MASTER_ONLY.
- Primary matcher property source remains pi_master_properties_v711.
- Raw/parallel WhatsApp records must never become matcher candidates.
- Human-verified requirement decisions remain protected.
- AI must never auto-enable matcher eligibility.
- Opaque WhatsApp IDs must never be guessed into phone numbers.
- Contacts remain staff-only.
- Existing WhatsApp and master business data must not be destructively rewritten by certification tooling.

## Allowed changes during freeze
Only defect fixes, security/privacy fixes, observability, tests, gold-label/review tooling, evidence-backed data-quality corrections, and certification work required to reach TEAM_READY.

## Blocked changes during freeze
No new product features, new matcher inventories, new raw-source matching paths, speculative contact recovery, unrelated UI expansion, or changes that bypass master property/requirement authorities.

## TEAM_READY release gates
1. Runtime and core health PASS.
2. WhatsApp end-to-end reconciliation PASS.
3. Requirement gold benchmark >= 98% overall.
4. Critical semantic fields >= 99%.
5. Matcher benchmark PASS on representative real requirements.
6. Privacy/authentication PASS.
7. Zero invented contacts, budgets, locations, or phone identities.
8. Regression suite PASS.
9. Human-review queue operational for uncertain cases.
10. Final read-only production certification PASS.

Until every gate passes, status is CERTIFICATION_IN_PROGRESS, not TEAM_READY.
