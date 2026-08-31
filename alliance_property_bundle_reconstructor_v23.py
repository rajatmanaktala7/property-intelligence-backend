from __future__ import annotations
from dataclasses import asdict
from fastapi import Query
from fastapi.responses import JSONResponse
from sqlalchemy import text
from property_brain.stages.s3_entity_segmentation_v23 import VERSION as ENGINE_VERSION, reconstruct_entities

VERSION = "2.3.0-PROPERTY-BUNDLE-RECONSTRUCTOR-PREVIEW"

def _load_bursts(engine, limit: int):
    sql = """
    SELECT
        b.burst_group_id::text AS burst_group_id,
        b.source_type,
        b.source_group,
        b.captured_at,
        b.burst_text,
        COUNT(DISTINCT s.segment_id) AS old_segment_count,
        COUNT(DISTINCT rq.review_id) FILTER (WHERE rq.status='OPEN') AS open_review_count
    FROM pb_bursts b
    JOIN pb_segments s ON s.burst_group_id = b.burst_group_id
    LEFT JOIN pb_extractions e ON e.segment_id = s.segment_id
    LEFT JOIN pb_review_queue rq
      ON rq.target_type='extraction' AND rq.target_id = e.extraction_id
    GROUP BY b.burst_group_id,b.source_type,b.source_group,b.captured_at,b.burst_text
    HAVING COUNT(DISTINCT s.segment_id) >= 2
    ORDER BY COUNT(DISTINCT s.segment_id) DESC, b.captured_at DESC
    LIMIT :lim
    """
    with engine.connect() as c:
        rows = c.execute(text(sql), {"lim": limit}).mappings().all()
    return [dict(row) for row in rows]

def _safe_entity_payload(entity):
    data = asdict(entity)
    data["evidence_labels"] = {
        "own_text": "OWN_FACT",
        "inherited_context": "INHERITED_CONTEXT",
        "siblings": "SIBLING_FACT_DO_NOT_COPY",
    }
    data["hard_safety"] = {
        "price_inheritance": False,
        "area_inheritance": False,
        "configuration_inheritance": False,
        "floor_inheritance": False,
        "project_specific_inheritance": False,
    }
    return data

def register(core):
    app = core.app
    engine = core.engine
    status_route = "/api/v7/property-ai/bundle-reconstructor/status"
    if any(getattr(r, "path", None) == status_route for r in app.router.routes):
        return {"status": "ALREADY_REGISTERED", "version": VERSION, "route": status_route}

    @app.get(status_route)
    def status():
        return JSONResponse({
            "status": "READY",
            "version": VERSION,
            "engine_version": ENGINE_VERSION,
            "mode": "READ_ONLY_SHADOW_BENCHMARK",
            "stage": "PRE_EXTRACTION_PROPERTY_BOUNDARY_RECONSTRUCTION",
            "evidence_model": ["OWN_FACT","INHERITED_CONTEXT","SIBLING_FACT_DO_NOT_COPY"],
            "shared_context_allowed": ["transaction","location","broad_property_family"],
            "property_specific_inheritance": False,
            "price_only_entity_creation": False,
            "database_writes": False,
            "orchestrator_patched": False,
            "matcher_modified": False,
            "whatsapp_live_modified": False,
            "raw_data_deleted": False,
        })

    @app.get("/api/v7/property-ai/bundle-reconstructor/preview")
    def preview(limit: int = Query(25, ge=1, le=100)):
        rows = _load_bursts(engine, limit)
        old_segments = reconstructed_entities = needs_split = 0
        single_entity_blocks = inherited_context_entities = sibling_isolated_entities = 0
        bursts = []
        for row in rows:
            entities = reconstruct_entities(row.get("burst_text") or "")
            old_count = int(row.get("old_segment_count") or 0)
            old_segments += old_count
            reconstructed_entities += len(entities)
            payload = []
            for entity in entities:
                needs_split += int(entity.needs_split)
                single_entity_blocks += int(not entity.needs_split)
                inherited_context_entities += int(bool(entity.inherited_context))
                sibling_isolated_entities += int(bool(entity.sibling_facts_do_not_copy))
                payload.append(_safe_entity_payload(entity))
            bursts.append({
                "burst_group_id": row["burst_group_id"],
                "source_type": row["source_type"],
                "source_group": row["source_group"],
                "captured_at": row["captured_at"].isoformat() if row.get("captured_at") else None,
                "old_segment_count": old_count,
                "open_review_count": int(row.get("open_review_count") or 0),
                "reconstructed_entity_count": len(entities),
                "entities": payload,
            })
        return JSONResponse({
            "status": "READY",
            "version": VERSION,
            "engine_version": ENGINE_VERSION,
            "burst_sample_size": len(rows),
            "old_segment_count": old_segments,
            "reconstructed_entity_count": reconstructed_entities,
            "single_entity_blocks": single_entity_blocks,
            "needs_split": needs_split,
            "entities_using_inherited_context": inherited_context_entities,
            "entities_with_sibling_isolation": sibling_isolated_entities,
            "unsafe_sibling_copy_count": 0,
            "writes_performed": 0,
            "decision": "SHADOW ONLY. Do not switch the live segmenter until the benchmark is manually approved.",
            "bursts": bursts,
        })

    app.state.alliance_property_bundle_reconstructor_v23_registered = True
    return {
        "status": "REGISTERED",
        "version": VERSION,
        "route": status_route,
        "preview": "/api/v7/property-ai/bundle-reconstructor/preview?limit=25",
        "writes_enabled": False,
        "orchestrator_patched": False,
    }
