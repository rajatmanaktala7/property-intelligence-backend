from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional

from fastapi import Body
from fastapi.responses import JSONResponse
from sqlalchemy import inspect, text

import alliance_property_evidence_grammar_v257b as v257b
import alliance_property_offers_tutor_v258 as v258

VERSION = "2.5.9A-CONTROLLED-WRITE-GATE"
MODE = "EXPLICIT_APPROVAL_PROPERTY_AND_OFFER_WRITER"

DDL = [
    """
    CREATE TABLE IF NOT EXISTS alliance_v259_write_candidates(
        candidate_id BIGSERIAL PRIMARY KEY,
        source_label TEXT,
        source_candidate JSONB NOT NULL,
        canonical_payload JSONB NOT NULL,
        offer_payload JSONB NOT NULL,
        gate_result JSONB NOT NULL DEFAULT '{}'::jsonb,
        status TEXT NOT NULL DEFAULT 'STAGED',
        approved_by TEXT,
        property_id UUID,
        offer_id UUID,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        committed_at TIMESTAMPTZ
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_v259_write_candidates_status
    ON alliance_v259_write_candidates(status)
    """,
    """
    CREATE TABLE IF NOT EXISTS alliance_v259_write_audit(
        audit_id BIGSERIAL PRIMARY KEY,
        candidate_id BIGINT,
        action TEXT NOT NULL,
        actor TEXT,
        payload JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
]

BENIGN_REASONS = {
    "PROPERTY_SPECIFIC_FACT_MISSING",
    "LOCATION_RECOVERED_FROM_OWN_TEXT_V25",
    "LOCATION_RECOVERED_FROM_PARENT_SCOPE_V255B",
    "LOCATION_RECOVERED_FROM_PROJECT_REGISTRY_V256A",
    "ASSET_FAMILY_CORRECTED_FROM_INTENDED_USE",
}

def _table_exists(engine, name: str) -> bool:
    try:
        return inspect(engine).has_table(name)
    except Exception:
        return False

def _norm(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()

def _dec(value: Any) -> Optional[Decimal]:
    if value in (None, ""):
        return None
    try:
        d = Decimal(str(value).replace(",", "").strip())
        return d if d > 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None

def _fingerprint(canonical: Dict[str, Any]) -> str:
    parts = [
        _norm(canonical.get("city")).upper(),
        _norm(canonical.get("locality")).upper(),
        _norm(canonical.get("project_name")).upper(),
        _norm(canonical.get("property_family")).upper(),
        _norm(canonical.get("property_subtype")).upper(),
        _norm(canonical.get("configuration")).upper(),
        _norm(canonical.get("area_sqft")).upper(),
    ]
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def _tutor_gate(engine) -> Dict[str, Any]:
    exam = v258._run_curriculum(engine, persist=False)
    return {
        "score": exam.get("score"),
        "critical_failures": exam.get("critical_failures"),
        "production_gate_passed": bool(exam.get("production_gate_passed")),
    }

def _candidate_gate(
    source_candidate: Dict[str, Any],
    canonical: Dict[str, Any],
    offer: Dict[str, Any],
    tutor_ok: bool = True,
) -> Dict[str, Any]:
    analyzed = v257b._analyze_candidate_v257b(dict(source_candidate))
    meta = analyzed["v257b"]
    reasons = set(source_candidate.get("review_reasons") or [])

    blockers = []

    if not tutor_ok:
        blockers.append("TUTOR_PRODUCTION_GATE_NOT_PASSED")

    if source_candidate.get("classification") != "AVAILABILITY":
        blockers.append("NOT_AVAILABILITY")

    tx = source_candidate.get("transaction")
    if tx not in ("SALE", "RENT"):
        blockers.append("TRANSACTION_NOT_EXPLICIT")

    if not source_candidate.get("property_family"):
        blockers.append("PROPERTY_FAMILY_MISSING")

    if not _norm(source_candidate.get("location")):
        blockers.append("LOCATION_MISSING")

    if meta["record_integrity"]["class"] != "SINGLE_PROPERTY_LIKELY":
        blockers.append("NOT_SINGLE_PROPERTY_LIKELY")

    if meta["hard_blockers"]:
        blockers.extend(meta["hard_blockers"])

    if meta.get("multiple_offer_values_preserved_unresolved"):
        blockers.append("MULTIPLE_OFFERS_UNRESOLVED")

    if not meta["fact_evidence"]["strong_property_fact_gate"]:
        blockers.append("STRONG_PROPERTY_FACT_GATE_FAILED")

    unsafe_reasons = sorted(r for r in reasons if r not in BENIGN_REASONS)
    if unsafe_reasons:
        blockers.extend(f"UNRESOLVED_REASON:{r}" for r in unsafe_reasons)

    c_family = _norm(canonical.get("property_family")).upper()
    c_locality = _norm(canonical.get("locality"))
    c_desc = _norm(canonical.get("clean_description"))
    if not c_family:
        blockers.append("CANONICAL_PROPERTY_FAMILY_MISSING")
    if not c_locality:
        blockers.append("CANONICAL_LOCALITY_MISSING")
    if not c_desc:
        blockers.append("CANONICAL_DESCRIPTION_MISSING")

    if c_family and c_family != _norm(source_candidate.get("property_family")).upper():
        blockers.append("CANONICAL_FAMILY_MISMATCH")

    src_loc = _norm(source_candidate.get("location")).upper()
    if c_locality and src_loc and c_locality.upper() != src_loc:
        blockers.append("CANONICAL_LOCATION_MISMATCH")

    identity_signals = [
        _norm(canonical.get("project_name")),
        _norm(canonical.get("configuration")),
        _norm(canonical.get("area_sqft")),
    ]
    if not any(identity_signals):
        blockers.append("INSUFFICIENT_PHYSICAL_IDENTITY")

    o_tx = _norm(offer.get("transaction_type")).upper()
    if o_tx != tx:
        blockers.append("OFFER_TRANSACTION_MISMATCH")

    rent_value = _dec(offer.get("rent_value"))
    sale_value = _dec(offer.get("sale_price_value"))

    if tx == "RENT":
        if rent_value is None:
            blockers.append("RENT_VALUE_REQUIRED")
        if sale_value is not None:
            blockers.append("SALE_VALUE_PRESENT_ON_RENT_OFFER")
    elif tx == "SALE":
        if sale_value is None:
            blockers.append("SALE_PRICE_REQUIRED")
        if rent_value is not None:
            blockers.append("RENT_VALUE_PRESENT_ON_SALE_OFFER")

    unique_blockers = sorted(set(blockers))
    return {
        "passed": len(unique_blockers) == 0,
        "blockers": unique_blockers,
        "tutor_gate_required": True,
        "single_property_required": True,
        "multiple_offers_auto_write_allowed": False,
        "rate_totalization_allowed": False,
        "transaction_inference_allowed": False,
        "source_analysis": {
            "record_integrity": meta["record_integrity"],
            "fact_evidence": meta["fact_evidence"],
            "hard_blockers": meta["hard_blockers"],
            "multiple_offer_values_preserved_unresolved": meta.get(
                "multiple_offer_values_preserved_unresolved"
            ),
        },
    }

def _status(engine) -> Dict[str, Any]:
    tutor = _tutor_gate(engine) if _table_exists(engine, "alliance_ai_training_cases") else {
        "score": None,
        "critical_failures": None,
        "production_gate_passed": False,
    }
    return {
        "status": "READY",
        "version": VERSION,
        "mode": MODE,
        "base_v257b_version": v257b.VERSION,
        "base_v258_version": v258.VERSION,
        "tables": {
            "pb_property_offers": _table_exists(engine, "pb_property_offers"),
            "alliance_v259_write_candidates": _table_exists(engine, "alliance_v259_write_candidates"),
            "alliance_v259_write_audit": _table_exists(engine, "alliance_v259_write_audit"),
        },
        "tutor_gate": tutor,
        "automatic_bulk_writes": False,
        "explicit_human_approval_required": True,
        "canonical_property_writes_enabled": True,
        "offer_writes_enabled": True,
        "matcher_modified": False,
        "whatsapp_live_modified": False,
        "raw_data_deleted": False,
    }

def _install(engine) -> Dict[str, Any]:
    if not _table_exists(engine, "pb_property_offers"):
        return {
            "status": "ERROR",
            "version": VERSION,
            "error": "V258 pb_property_offers is missing. Run V258 install-and-train first.",
        }
    executed = 0
    try:
        with engine.begin() as c:
            for ddl in DDL:
                c.execute(text(ddl))
                executed += 1
        return {
            "status": "INSTALLED",
            "version": VERSION,
            "ddl_statements": executed,
            "status_after": _status(engine),
        }
    except Exception as exc:
        return {
            "status": "ERROR",
            "version": VERSION,
            "error": f"{type(exc).__name__}: {exc}",
            "executed_before_error": executed,
        }

def _stage(engine, payload: Dict[str, Any]) -> Dict[str, Any]:
    if not _table_exists(engine, "alliance_v259_write_candidates"):
        return {"status": "INSTALL_REQUIRED", "version": VERSION}

    source_candidate = dict(payload.get("source_candidate") or {})
    canonical = dict(payload.get("canonical") or {})
    offer = dict(payload.get("offer") or {})
    source_label = _norm(payload.get("source_label")) or "MANUAL_CONTROLLED"

    tutor = _tutor_gate(engine)
    gate = _candidate_gate(
        source_candidate,
        canonical,
        offer,
        tutor_ok=bool(tutor["production_gate_passed"]),
    )
    status = "READY_FOR_APPROVAL" if gate["passed"] else "BLOCKED"

    with engine.begin() as c:
        candidate_id = c.execute(
            text("""
                INSERT INTO alliance_v259_write_candidates
                (source_label, source_candidate, canonical_payload, offer_payload,
                 gate_result, status, updated_at)
                VALUES
                (:source_label, CAST(:source_candidate AS jsonb),
                 CAST(:canonical_payload AS jsonb), CAST(:offer_payload AS jsonb),
                 CAST(:gate_result AS jsonb), :status, NOW())
                RETURNING candidate_id
            """),
            {
                "source_label": source_label,
                "source_candidate": json.dumps(source_candidate),
                "canonical_payload": json.dumps(canonical),
                "offer_payload": json.dumps(offer),
                "gate_result": json.dumps(gate),
                "status": status,
            },
        ).scalar_one()

        c.execute(
            text("""
                INSERT INTO alliance_v259_write_audit(candidate_id, action, actor, payload)
                VALUES(:candidate_id, 'STAGE', 'SYSTEM', CAST(:payload AS jsonb))
            """),
            {
                "candidate_id": candidate_id,
                "payload": json.dumps({"gate": gate, "status": status}),
            },
        )

    return {
        "status": status,
        "version": VERSION,
        "candidate_id": int(candidate_id),
        "gate": gate,
        "tutor_gate": tutor,
        "writes_performed": 0,
    }

def _commit(engine, candidate_id: int, approved_by: str) -> Dict[str, Any]:
    approved_by = _norm(approved_by)
    if not approved_by:
        return {"status": "ERROR", "error": "approved_by is required"}

    tutor = _tutor_gate(engine)
    if not tutor["production_gate_passed"]:
        return {
            "status": "BLOCKED",
            "reason": "TUTOR_PRODUCTION_GATE_NOT_PASSED",
            "tutor_gate": tutor,
        }

    try:
        with engine.begin() as c:
            row = c.execute(
                text("""
                    SELECT candidate_id, source_candidate, canonical_payload,
                           offer_payload, status, property_id, offer_id
                    FROM alliance_v259_write_candidates
                    WHERE candidate_id = :candidate_id
                    FOR UPDATE
                """),
                {"candidate_id": candidate_id},
            ).mappings().first()

            if not row:
                return {"status": "NOT_FOUND", "candidate_id": candidate_id}

            if row["status"] == "COMMITTED":
                return {
                    "status": "ALREADY_COMMITTED",
                    "candidate_id": candidate_id,
                    "property_id": str(row["property_id"]) if row["property_id"] else None,
                    "offer_id": str(row["offer_id"]) if row["offer_id"] else None,
                }

            source_candidate = dict(row["source_candidate"])
            canonical = dict(row["canonical_payload"])
            offer = dict(row["offer_payload"])

            gate = _candidate_gate(source_candidate, canonical, offer, tutor_ok=True)
            if not gate["passed"]:
                c.execute(
                    text("""
                        UPDATE alliance_v259_write_candidates
                        SET gate_result = CAST(:gate AS jsonb),
                            status = 'BLOCKED', updated_at = NOW()
                        WHERE candidate_id = :candidate_id
                    """),
                    {"candidate_id": candidate_id, "gate": json.dumps(gate)},
                )
                return {
                    "status": "BLOCKED",
                    "candidate_id": candidate_id,
                    "gate": gate,
                }

            fingerprint = _fingerprint(canonical)
            property_id = c.execute(
                text("""
                    SELECT property_id
                    FROM pb_canonical_properties
                    WHERE fingerprint = :fingerprint
                    ORDER BY created_at ASC
                    LIMIT 1
                """),
                {"fingerprint": fingerprint},
            ).scalar()

            property_created = False
            if property_id is None:
                property_id = c.execute(
                    text("""
                        INSERT INTO pb_canonical_properties(
                            property_id, fingerprint, transaction_type,
                            property_family, property_subtype, city, locality,
                            project_name, configuration, area_value, area_unit,
                            area_sqft, rent_value, rent_period, sale_price_value,
                            floor, furnishing, features, contact_name,
                            contact_numbers, clean_description, overall_confidence,
                            verification_status, current_status,
                            ai_understanding, data_quality_status, suitable_uses,
                            negotiability, source_evidence, entity_version,
                            created_at, updated_at
                        )
                        VALUES(
                            gen_random_uuid(), :fingerprint, NULL,
                            :property_family, :property_subtype, :city, :locality,
                            :project_name, :configuration, :area_value, :area_unit,
                            :area_sqft, NULL, NULL, NULL,
                            :floor, :furnishing, CAST(:features AS jsonb), NULL,
                            '[]'::jsonb, :clean_description, :overall_confidence,
                            'UNVERIFIED', 'ACTIVE',
                            CAST(:ai_understanding AS jsonb), 'CONTROLLED_WRITE',
                            CAST(:suitable_uses AS jsonb), :negotiability,
                            CAST(:source_evidence AS jsonb), 1,
                            NOW(), NOW()
                        )
                        RETURNING property_id
                    """),
                    {
                        "fingerprint": fingerprint,
                        "property_family": _norm(canonical.get("property_family")).upper(),
                        "property_subtype": _norm(canonical.get("property_subtype")) or None,
                        "city": _norm(canonical.get("city")) or None,
                        "locality": _norm(canonical.get("locality")),
                        "project_name": _norm(canonical.get("project_name")) or None,
                        "configuration": _norm(canonical.get("configuration")) or None,
                        "area_value": _dec(canonical.get("area_value")),
                        "area_unit": _norm(canonical.get("area_unit")) or None,
                        "area_sqft": _dec(canonical.get("area_sqft")),
                        "floor": _norm(canonical.get("floor")) or None,
                        "furnishing": _norm(canonical.get("furnishing")) or None,
                        "features": json.dumps(canonical.get("features") or []),
                        "clean_description": _norm(canonical.get("clean_description")),
                        "overall_confidence": _dec(canonical.get("overall_confidence")) or Decimal("0.95"),
                        "ai_understanding": json.dumps({
                            "writer_version": VERSION,
                            "source": "V259_CONTROLLED_WRITE",
                        }),
                        "suitable_uses": json.dumps(canonical.get("suitable_uses") or []),
                        "negotiability": canonical.get("negotiability"),
                        "source_evidence": json.dumps({
                            "v259_candidate_id": candidate_id,
                            "source_label": row.get("source_label") if "source_label" in row else None,
                        }),
                    },
                ).scalar_one()
                property_created = True

            offer_id = c.execute(
                text("""
                    SELECT offer_id
                    FROM pb_property_offers
                    WHERE property_id = :property_id
                      AND source_evidence->>'v259_candidate_id' = :candidate_id_text
                    LIMIT 1
                """),
                {
                    "property_id": property_id,
                    "candidate_id_text": str(candidate_id),
                },
            ).scalar()

            offer_created = False
            if offer_id is None:
                offer_id = c.execute(
                    text("""
                        INSERT INTO pb_property_offers(
                            property_id, transaction_type, offer_status,
                            rent_value, rent_period, sale_price_value,
                            rate_value, rate_unit, cam_value,
                            security_deposit_value, furnishing, negotiable,
                            possession, availability_status,
                            source_raw_ids, source_evidence, ai_understanding,
                            confidence, verification_status, created_at, updated_at
                        )
                        VALUES(
                            :property_id, :transaction_type, 'UNDER_REVIEW',
                            :rent_value, :rent_period, :sale_price_value,
                            :rate_value, :rate_unit, :cam_value,
                            :security_deposit_value, :furnishing, :negotiable,
                            :possession, :availability_status,
                            CAST(:source_raw_ids AS jsonb),
                            CAST(:source_evidence AS jsonb),
                            CAST(:ai_understanding AS jsonb),
                            :confidence, 'UNVERIFIED', NOW(), NOW()
                        )
                        RETURNING offer_id
                    """),
                    {
                        "property_id": property_id,
                        "transaction_type": _norm(offer.get("transaction_type")).upper(),
                        "rent_value": _dec(offer.get("rent_value")),
                        "rent_period": _norm(offer.get("rent_period")) or None,
                        "sale_price_value": _dec(offer.get("sale_price_value")),
                        "rate_value": _dec(offer.get("rate_value")),
                        "rate_unit": _norm(offer.get("rate_unit")) or None,
                        "cam_value": _dec(offer.get("cam_value")),
                        "security_deposit_value": _dec(offer.get("security_deposit_value")),
                        "furnishing": _norm(offer.get("furnishing")) or None,
                        "negotiable": offer.get("negotiable"),
                        "possession": _norm(offer.get("possession")) or None,
                        "availability_status": _norm(offer.get("availability_status")) or None,
                        "source_raw_ids": json.dumps(offer.get("source_raw_ids") or []),
                        "source_evidence": json.dumps({
                            "v259_candidate_id": candidate_id,
                            "approved_by": approved_by,
                        }),
                        "ai_understanding": json.dumps({
                            "writer_version": VERSION,
                            "gate_passed": True,
                        }),
                        "confidence": _dec(offer.get("confidence")) or Decimal("0.95"),
                    },
                ).scalar_one()
                offer_created = True

            c.execute(
                text("""
                    UPDATE alliance_v259_write_candidates
                    SET status = 'COMMITTED', approved_by = :approved_by,
                        property_id = :property_id, offer_id = :offer_id,
                        gate_result = CAST(:gate AS jsonb),
                        committed_at = NOW(), updated_at = NOW()
                    WHERE candidate_id = :candidate_id
                """),
                {
                    "approved_by": approved_by,
                    "property_id": property_id,
                    "offer_id": offer_id,
                    "gate": json.dumps(gate),
                    "candidate_id": candidate_id,
                },
            )

            c.execute(
                text("""
                    INSERT INTO alliance_v259_write_audit(candidate_id, action, actor, payload)
                    VALUES(:candidate_id, 'COMMIT', :actor, CAST(:payload AS jsonb))
                """),
                {
                    "candidate_id": candidate_id,
                    "actor": approved_by,
                    "payload": json.dumps({
                        "property_id": str(property_id),
                        "offer_id": str(offer_id),
                        "property_created": property_created,
                        "offer_created": offer_created,
                    }),
                },
            )

        return {
            "status": "COMMITTED",
            "version": VERSION,
            "candidate_id": candidate_id,
            "property_id": str(property_id),
            "offer_id": str(offer_id),
            "property_created": property_created,
            "offer_created": offer_created,
            "tutor_gate": tutor,
        }
    except Exception as exc:
        return {
            "status": "ERROR",
            "version": VERSION,
            "candidate_id": candidate_id,
            "error": f"{type(exc).__name__}: {exc}",
        }

def _regression_demo() -> Dict[str, Any]:
    safe_source = {
        "classification": "AVAILABILITY",
        "transaction": "RENT",
        "property_family": "RESIDENTIAL",
        "location": "DLF Phase 2",
        "review_reasons": [],
        "boundary_needs_split": False,
        "own_text_redacted": "DLF Phase 2 400 SYDS 4BHK rent 1.60 LAC",
    }
    safe_canonical = {
        "property_family": "RESIDENTIAL",
        "locality": "DLF Phase 2",
        "configuration": "4 BHK",
        "area_sqft": 3600,
        "clean_description": "4 BHK residential floor in DLF Phase 2",
    }
    safe_offer = {
        "transaction_type": "RENT",
        "rent_value": 160000,
        "rent_period": "MONTH",
    }

    multi = dict(safe_source)
    multi["own_text_redacted"] = "DLF Phase 2 400 SYDS 4BHK 1.60 LAC fully furnished 1.75 LAC"
    rate = {
        "classification": "AVAILABILITY",
        "transaction": "SALE",
        "property_family": "LAND",
        "location": "Noida",
        "review_reasons": ["AMBIGUOUS_RATE_NOT_TOTAL_PRICE"],
        "own_text_redacted": "Plot size 500 yards Rate is Rs 50000 per yard",
    }
    req = {
        "classification": "REQUIREMENT",
        "transaction": None,
        "property_family": "RESIDENTIAL",
        "location": "Vagator",
        "review_reasons": [],
        "own_text_redacted": "Looking for 4 BHK villa in Vagator budget 2 Lakh per month",
    }

    a = _candidate_gate(safe_source, safe_canonical, safe_offer, tutor_ok=True)
    b = _candidate_gate(multi, safe_canonical, safe_offer, tutor_ok=True)
    c = _candidate_gate(
        rate,
        {
            "property_family": "LAND",
            "locality": "Noida",
            "area_sqft": 4500,
            "clean_description": "Land in Noida",
        },
        {"transaction_type": "SALE", "sale_price_value": 25000000},
        tutor_ok=True,
    )
    d = _candidate_gate(req, safe_canonical, safe_offer, tutor_ok=True)
    e = _candidate_gate(safe_source, safe_canonical, safe_offer, tutor_ok=False)

    passed = bool(
        a["passed"]
        and not b["passed"]
        and "MULTIPLE_OFFERS_UNRESOLVED" in b["blockers"]
        and not c["passed"]
        and "AMBIGUOUS_RATE_NOT_TOTAL_PRICE" in c["blockers"]
        and not d["passed"]
        and "NOT_AVAILABILITY" in d["blockers"]
        and not e["passed"]
        and "TUTOR_PRODUCTION_GATE_NOT_PASSED" in e["blockers"]
    )
    return {
        "status": "PASS" if passed else "FAIL",
        "version": VERSION,
        "tests": {
            "safe_single_offer_passes": a["passed"],
            "multiple_offer_blocked": not b["passed"],
            "rate_totalization_blocked": not c["passed"],
            "requirement_blocked_from_property_writer": not d["passed"],
            "tutor_failure_blocks_writer": not e["passed"],
            "automatic_bulk_writes": False,
            "human_approval_required": True,
        },
        "writes_performed": 0,
    }

def register(core):
    app = core.app
    engine = core.engine
    status_route = "/api/v7/property-ai/controlled-writer-v259/status"

    if any(getattr(r, "path", None) == status_route for r in app.router.routes):
        return {"status": "ALREADY_REGISTERED", "version": VERSION, "route": status_route}

    @app.get(status_route)
    def status():
        return JSONResponse(_status(engine))

    @app.post("/api/v7/property-ai/controlled-writer-v259/install")
    def install():
        return JSONResponse(_install(engine))

    @app.get("/api/v7/property-ai/controlled-writer-v259/regression-test")
    def regression_test():
        return JSONResponse(_regression_demo())

    @app.post("/api/v7/property-ai/controlled-writer-v259/gate")
    def gate(payload: Dict[str, Any] = Body(...)):
        tutor = _tutor_gate(engine)
        result = _candidate_gate(
            dict(payload.get("source_candidate") or {}),
            dict(payload.get("canonical") or {}),
            dict(payload.get("offer") or {}),
            tutor_ok=bool(tutor["production_gate_passed"]),
        )
        return JSONResponse({"version": VERSION, "tutor_gate": tutor, "gate": result})

    @app.post("/api/v7/property-ai/controlled-writer-v259/stage")
    def stage(payload: Dict[str, Any] = Body(...)):
        return JSONResponse(_stage(engine, payload))

    @app.post("/api/v7/property-ai/controlled-writer-v259/commit/{candidate_id}")
    def commit(candidate_id: int, payload: Dict[str, Any] = Body(...)):
        return JSONResponse(_commit(engine, candidate_id, payload.get("approved_by")))

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "route": status_route,
        "install": "/api/v7/property-ai/controlled-writer-v259/install",
        "regression": "/api/v7/property-ai/controlled-writer-v259/regression-test",
        "gate": "/api/v7/property-ai/controlled-writer-v259/gate",
        "stage": "/api/v7/property-ai/controlled-writer-v259/stage",
        "commit": "/api/v7/property-ai/controlled-writer-v259/commit/{candidate_id}",
    }

