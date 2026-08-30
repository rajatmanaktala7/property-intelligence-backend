ALLIANCE PHASE 6 - VERIFICATION WORKFLOW
Version: 6.0.0-PHASE6-VERIFICATION-WORKFLOW

PURPOSE
Turn Phase 5 VERIFY FIRST inventory into an operational team workflow without weakening canonical matching.

ROUTES
/verification-phase6
/api/v61/status
/api/v61/queue
/api/v61/action

BUSINESS RULES
1. Contacts are visible only inside the login-protected verification queue.
2. Phase 5 matcher continues to hide contacts.
3. pi_properties can become send-eligible only when data_quality_status=READY and team action VERIFY_AVAILABLE succeeds.
4. READY_LEGACY / NEEDS_REVIEW cannot be made send-ready by clicking Verify. They require SAVE_CORRECTION first.
5. NOT_AVAILABLE is hidden from the matcher by availability status.
6. VERIFY_LATER is never send-eligible and receives next_verification_at.
7. WhatsApp Phase 4.1 READY records can be verified directly; V4.4 preserves the verification field on later upserts.
8. Existing fingerprint/canonical identity is never changed here.
9. Price is never used or changed as identity.

FILES
alliance_phase6_verification_workflow.py  NEW
alliance_module_registry.py               COMPLETE REPLACEMENT
phase6_local_self_test.py                 TEST ONLY

DEPLOYMENT PLAN
- Replace/add files locally.
- Compile and self-test.
- Do not commit until diff is inspected.
- Deploy as separate reversible commit.
- Verify Railway import and /healthz /readyz.
- Verify /api/v61/status while logged in.
- Test one READY record with Verify Later first.
- Test one READY_LEGACY record to confirm direct Verify is rejected.
- Only then test a real READY record as Verified Available.
