from __future__ import annotations
from fastapi import Request
from sqlalchemy import text

VERSION="12.4.26C-TEAM-READINESS-GUARD"

CRITICAL_TABLES=[
    "pi_master_properties_v711","pi_master_requirements_v711","pi_master_source_links_v711",
    "pi_master_workflow_v720","pi_master_matches_v720","pi_match_reviews_v730",
    "pi_operational_properties","pi_operational_requirements",
]
CRITICAL_ROUTES=[
    "/alliance/primary","/alliance/primary/properties","/alliance/primary/requirements",
    "/alliance/primary/availability","/alliance/primary/matcher","/alliance/primary/followups",
    "/property-manual","/requirement-manual",
    "/api/alliance/canonical-bridge/audit",
]

def _routes(app):
    return {getattr(r,"path",None) for r in getattr(app.router,"routes",[]) if getattr(r,"path",None)}

def snapshot(core):
    app=getattr(core,"app",None) or core
    engine=core.engine
    routes=_routes(app)
    with engine.connect() as c:
        tables={t:bool(c.execute(text("SELECT to_regclass(:t) IS NOT NULL"),{"t":t}).scalar()) for t in CRITICAL_TABLES}
        counts={}
        queries={
            "master_properties":"SELECT COUNT(*) FROM pi_master_properties_v711",
            "master_requirements":"SELECT COUNT(*) FROM pi_master_requirements_v711",
            "verified_requirements":"""SELECT COUNT(*) FROM pi_master_requirements_v711 r
              JOIN pi_master_workflow_v720 w ON w.canonical_id=r.canonical_id
              WHERE r.promotion_status='PROMOTED_VALIDATED' AND w.verification_status='VERIFIED'""",
            "available_properties":"""SELECT COUNT(*) FROM pi_master_properties_v711 p
              JOIN pi_master_workflow_v720 w ON w.canonical_id=p.canonical_id
              WHERE p.promotion_status='PROMOTED_VALIDATED' AND w.verification_status='VERIFIED'
                AND w.availability_status='AVAILABLE'""",
            "archived_properties":"SELECT COUNT(*) FROM pi_master_properties_v711 WHERE promotion_status='MANUAL_ARCHIVED'",
            "archived_requirements":"SELECT COUNT(*) FROM pi_master_requirements_v711 WHERE promotion_status='MANUAL_ARCHIVED'",
            "unsafe_client_matches":"""SELECT COUNT(*) FROM pi_master_matches_v720 m
              JOIN pi_match_reviews_v730 rv ON rv.requirement_canonical_id=m.requirement_canonical_id
                AND rv.property_canonical_id=m.property_canonical_id
              JOIN pi_master_properties_v711 p ON p.canonical_id=m.property_canonical_id
              LEFT JOIN pi_master_workflow_v720 w ON w.canonical_id=p.canonical_id
              WHERE rv.review_status='APPROVED'
                AND NOT (p.promotion_status='PROMOTED_VALIDATED'
                         AND COALESCE(w.verification_status,'UNVERIFIED')='VERIFIED'
                         AND COALESCE(w.availability_status,'UNKNOWN')='AVAILABLE')""",
        }
        for k,q in queries.items():
            try: counts[k]=int(c.execute(text(q)).scalar() or 0)
            except Exception as e: counts[k]={"error":f"{type(e).__name__}: {e}"}
        try:
            p=engine.pool
            pool={"class":type(p).__name__,"status":p.status() if hasattr(p,"status") else "unknown",
                  "checked_out":p.checkedout() if hasattr(p,"checkedout") else None,
                  "checked_in":p.checkedin() if hasattr(p,"checkedin") else None,
                  "overflow":p.overflow() if hasattr(p,"overflow") else None}
        except Exception as e:
            pool={"error":f"{type(e).__name__}: {e}"}
    route_status={p:p in routes for p in CRITICAL_ROUTES}
    missing_tables=[k for k,v in tables.items() if not v]
    missing_routes=[k for k,v in route_status.items() if not v]
    blockers=[]
    if missing_tables: blockers.append("missing_tables")
    if missing_routes: blockers.append("missing_routes")
    return {"status":"PASS" if not blockers else "FAIL","version":VERSION,
            "blockers":blockers,"tables":tables,"routes":route_status,"counts":counts,"db_pool":pool,
            "policy":{"property_verification_separate_from_availability":True,
                      "client_draft_requires_approved_verified_available":True,
                      "matcher_rejects_unavailable_and_inactive":True,
                      "historical_backfill":"NOT_AUTHORIZED"}}

def register(core):
    app=getattr(core,"app",None) or core
    app.router.routes[:]=[r for r in list(app.router.routes)
                          if getattr(r,"path",None)!="/api/alliance/team-readiness-12426c"]
    @app.get("/api/alliance/team-readiness-12426c")
    def readiness(req:Request):
        fn=getattr(core,"need_login",None)
        if fn: fn(req)
        return snapshot(core)
    return {"status":"REGISTERED","version":VERSION,"route":"/api/alliance/team-readiness-12426c"}
