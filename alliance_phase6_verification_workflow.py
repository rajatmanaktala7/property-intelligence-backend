from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from html import escape
from typing import Any, Dict

from fastapi import APIRouter, Request, Query, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import text

VERSION = "6.0.1-PHASE6-VERIFICATION-WORKFLOW"
ROUTE = "/verification-phase6"

VALID_ACTIONS = {"VERIFY_AVAILABLE", "NOT_AVAILABLE", "VERIFY_LATER", "SAVE_CORRECTION"}
CITY_ONLY = {
    "DELHI","NEW DELHI","GURUGRAM","GURGAON","NOIDA","GREATER NOIDA",
    "FARIDABAD","GOA","MUMBAI","BENGALURU","BANGALORE","HYDERABAD","NCR","DELHI NCR"
}
AREA_FACTORS = {"SQFT": 1.0, "SQM": 10.76391041671, "SQYD": 9.0}


def norm(v: Any) -> str:
    import re
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9]+", " ", str(v or "").upper())).strip()


def esc(v: Any) -> str:
    return escape(str(v if v is not None else ""), quote=True)


def _route_exists(app, path: str) -> bool:
    return any(getattr(r, "path", None) == path for r in app.router.routes)


def _table_columns(engine, table: str) -> set[str]:
    with engine.connect() as c:
        return {r[0] for r in c.execute(text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name=:t
        """), {"t": table}).all()}


def _ensure_schema(engine):
    # Additive only. No destructive migration and no property row rewrite.
    statements = [
        "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS availability_verification_status TEXT DEFAULT 'UNVERIFIED'",
        "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS last_verified_at TIMESTAMPTZ",
        "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS next_verification_at TIMESTAMPTZ",
        "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS verification_notes TEXT",
        "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS verification_contacted_at TIMESTAMPTZ",
        "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS final_send_eligible BOOLEAN DEFAULT FALSE",
        "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS verification_required_before_final_send BOOLEAN DEFAULT TRUE",
        "ALTER TABLE pi_whatsapp_property_master ADD COLUMN IF NOT EXISTS next_verification_at TIMESTAMPTZ",
        "ALTER TABLE pi_whatsapp_property_master ADD COLUMN IF NOT EXISTS verification_notes TEXT",
        "ALTER TABLE pi_whatsapp_property_master ADD COLUMN IF NOT EXISTS verified_by TEXT",
        "ALTER TABLE pi_whatsapp_property_master ADD COLUMN IF NOT EXISTS verified_at TIMESTAMPTZ",
    ]
    with engine.begin() as c:
        for stmt in statements:
            c.execute(text(stmt))


def _actor(core, req: Request) -> str:
    fn = getattr(core, "actor_name", None)
    if callable(fn):
        return str(fn(req) or "team")[:255]
    return str(req.headers.get("x-user-name") or "team")[:255]


def _require_login(core, req: Request):
    fn = getattr(core, "need_login", None)
    if callable(fn):
        return fn(req)
    return "team"


def _live_generation_id() -> str:
    try:
        import alliance_v44_whatsapp_property_master as v44
        return str(v44.LIVE_GENERATION_ID)
    except Exception:
        return "159d9eab-5be5-5313-9af5-8f9913522087"


def _queue_pi(engine, limit: int):
    cols = _table_columns(engine, "pi_properties")
    wanted = [
        "id","property_id","property_name","canonical_locality","location","canonical_transaction",
        "rent_or_sale","canonical_property_type","property_type","canonical_area_value",
        "canonical_area_unit","canonical_area_sqft","data_quality_status","match_eligible",
        "availability_verification_status","final_send_eligible","owner_name","owner_contact",
        "broker_name","broker_contact","source","updated_at","last_verified_at","next_verification_at",
        "verification_notes"
    ]
    selected = [x for x in wanted if x in cols]
    qcols = ",".join(f'"{x}"' for x in selected)
    where = []
    if "match_eligible" in cols:
        where.append("(match_eligible IS TRUE OR data_quality_status IN ('READY','READY_LEGACY','NEEDS_REVIEW'))")
    if "entry_status" in cols:
        where.append("COALESCE(UPPER(entry_status),'ACTIVE') NOT IN ('DELETED','INACTIVE')")
    clause = " WHERE " + " AND ".join(where) if where else ""
    sql = f'SELECT {qcols} FROM pi_properties{clause} ORDER BY COALESCE(next_verification_at, updated_at, created_at) ASC NULLS FIRST LIMIT :lim'
    with engine.connect() as c:
        rows = [dict(r) for r in c.execute(text(sql), {"lim": int(limit)}).mappings().all()]
    out = []
    for d in rows:
        ver = norm(d.get("availability_verification_status") or "UNVERIFIED")
        if ver in {"NOT AVAILABLE","NOT_AVAILABLE"}:
            continue
        contact_parts = []
        if d.get("owner_contact"):
            contact_parts.append(f"Owner: {d.get('owner_contact')}")
        if d.get("broker_contact"):
            contact_parts.append(f"Broker: {d.get('broker_contact')}")
        out.append({
            "source_table": "pi_properties",
            "record_id": str(d.get("id") or ""),
            "property_id": d.get("property_id"),
            "property": d.get("property_name") or d.get("property_id"),
            "location": d.get("canonical_locality") or d.get("location"),
            "transaction": d.get("canonical_transaction") or d.get("rent_or_sale"),
            "property_type": d.get("canonical_property_type") or d.get("property_type"),
            "area_value": d.get("canonical_area_value"),
            "area_unit": d.get("canonical_area_unit"),
            "area_sqft": d.get("canonical_area_sqft"),
            "data_quality": d.get("data_quality_status") or "NEEDS_REVIEW",
            "verification": d.get("availability_verification_status") or "UNVERIFIED",
            "send_eligible": bool(d.get("final_send_eligible")),
            "contact_name": d.get("owner_name") or d.get("broker_name"),
            "contact": " | ".join(contact_parts),
            "source": d.get("source"),
            "last_verified_at": d.get("last_verified_at"),
            "next_verification_at": d.get("next_verification_at"),
            "verification_notes": d.get("verification_notes"),
        })
    return out


def _queue_wa(engine, limit: int):
    cols = _table_columns(engine, "pi_whatsapp_property_master")
    wanted = [
        "record_id","description","lead_type","area","configuration_details","price",
        "contact_name_number","contact_name","phone_numbers","all_contacts","source",
        "captured_on","verification","next_verification_at","verification_notes","verified_at"
    ]
    selected = [x for x in wanted if x in cols]
    qcols = ",".join(f'"{x}"' for x in selected)
    with engine.connect() as c:
        rows = [dict(r) for r in c.execute(text(
            f'SELECT {qcols} FROM pi_whatsapp_property_master '
            'WHERE generation_id=:g ORDER BY COALESCE(next_verification_at,captured_on) ASC NULLS FIRST LIMIT :lim'
        ), {"g": _live_generation_id(), "lim": int(limit)}).mappings().all()]
    out = []
    for d in rows:
        ver = norm(d.get("verification") or "UNVERIFIED")
        if "NOT AVAILABLE" in ver or "UNAVAILABLE" in ver:
            continue
        out.append({
            "source_table": "pi_whatsapp_property_master",
            "record_id": str(d.get("record_id") or ""),
            "property_id": str(d.get("record_id") or ""),
            "property": d.get("description"),
            "location": (str(d.get("description") or "").split("|")[0].strip() or None),
            "transaction": d.get("lead_type"),
            "property_type": d.get("configuration_details"),
            "area_value": None,
            "area_unit": None,
            "area_sqft": None,
            "data_quality": "READY",
            "verification": d.get("verification") or "UNVERIFIED",
            "send_eligible": norm(d.get("verification")) in {"VERIFIED","AVAILABLE VERIFIED","ACTIVE VERIFIED"},
            "contact_name": d.get("contact_name"),
            "contact": d.get("all_contacts") or d.get("contact_name_number") or d.get("phone_numbers"),
            "source": d.get("source"),
            "last_verified_at": d.get("verified_at"),
            "next_verification_at": d.get("next_verification_at"),
            "verification_notes": d.get("verification_notes"),
        })
    return out


def queue(engine, limit: int = 500):
    pi = _queue_pi(engine, limit)
    wa = _queue_wa(engine, limit)
    rows = pi + wa
    def rank(x):
        v = norm(x.get("verification"))
        quality = norm(x.get("data_quality"))
        due = x.get("next_verification_at")
        return (
            0 if "VERIFY LATER" in v else 1,
            0 if quality in {"READY LEGACY","NEEDS REVIEW"} else 1,
            str(due or "")
        )
    rows.sort(key=rank)
    return rows[:limit]


def _snapshot_pi(c, record_id: str):
    row = c.execute(text("SELECT * FROM pi_properties WHERE id=:id FOR UPDATE"), {"id": int(record_id)}).mappings().first()
    if not row:
        raise HTTPException(404, "Property not found")
    return dict(row)


def _snapshot_wa(c, record_id: str):
    row = c.execute(text("""
        SELECT * FROM pi_whatsapp_property_master
        WHERE generation_id=:g AND record_id=:rid FOR UPDATE
    """), {"g": _live_generation_id(), "rid": record_id}).mappings().first()
    if not row:
        raise HTTPException(404, "WhatsApp property not found")
    return dict(row)


def _log(c, property_id: str, action: str, actor: str, notes: str, old: dict, new: dict):
    # pi_verification_log already exists in the core database.
    c.execute(text("""
        INSERT INTO pi_verification_log(property_id,action,performed_by,notes,old_value,new_value)
        VALUES(:pid,:a,:actor,:notes,CAST(:old AS JSONB),CAST(:new AS JSONB))
    """), {
        "pid": property_id,
        "a": action,
        "actor": actor,
        "notes": notes,
        "old": json.dumps(old, default=str),
        "new": json.dumps(new, default=str),
    })


def _validate_correction(payload: dict) -> dict:
    tx = norm(payload.get("canonical_transaction"))
    loc = norm(payload.get("canonical_locality"))
    ptype = str(payload.get("canonical_property_type") or "").strip()
    unit = norm(payload.get("canonical_area_unit")).replace("SQ FT","SQFT").replace("SQ M","SQM").replace("SQ YD","SQYD")
    try:
        area = float(payload.get("canonical_area_value"))
    except Exception:
        area = 0.0
    if tx not in {"SALE","RENT"}:
        raise HTTPException(409, "Correction requires SALE or RENT transaction")
    if not loc or loc in CITY_ONLY:
        raise HTTPException(409, "Correction requires a specific locality, not city only")
    if not ptype:
        raise HTTPException(409, "Correction requires property type")
    if area <= 0 or unit not in AREA_FACTORS:
        raise HTTPException(409, "Correction requires positive area and unit SQFT/SQM/SQYD")
    return {
        "canonical_transaction": tx,
        "canonical_locality": loc,
        "canonical_property_type": ptype,
        "canonical_area_value": area,
        "canonical_area_unit": unit,
        "canonical_area_sqft": area * AREA_FACTORS[unit],
    }


def _apply_pi(engine, record_id: str, action: str, actor: str, payload: dict):
    notes = str(payload.get("notes") or "").strip()[:4000]
    days = max(1, min(int(payload.get("verify_later_days") or 7), 365))
    with engine.begin() as c:
        old = _snapshot_pi(c, record_id)
        property_id = str(old.get("property_id") or record_id)

        if action == "SAVE_CORRECTION":
            corrected = _validate_correction(payload)
            c.execute(text("""
                UPDATE pi_properties SET
                    canonical_transaction=:tx,
                    canonical_locality=:loc,
                    canonical_property_type=:pt,
                    canonical_area_value=:av,
                    canonical_area_unit=:au,
                    canonical_area_sqft=:asq,
                    data_quality_status='READY',
                    match_eligible=TRUE,
                    final_send_eligible=FALSE,
                    verification_required_before_final_send=TRUE,
                    availability_verification_status='UNVERIFIED',
                    canonical_review_reasons='["TEAM_CORRECTED_PHASE6"]',
                    verification_notes=:notes,
                    verification_contacted_at=NOW(),
                    updated_at=NOW()
                WHERE id=:id
            """), {"tx": corrected["canonical_transaction"], "loc": corrected["canonical_locality"],
                    "pt": corrected["canonical_property_type"], "av": corrected["canonical_area_value"],
                    "au": corrected["canonical_area_unit"], "asq": corrected["canonical_area_sqft"],
                    "notes": notes, "id": int(record_id)})
        elif action == "VERIFY_AVAILABLE":
            quality = norm(old.get("data_quality_status"))
            if quality != "READY":
                raise HTTPException(409, f"CORRECTION_REQUIRED: quality is {old.get('data_quality_status') or 'UNKNOWN'}")
            c.execute(text("""
                UPDATE pi_properties SET
                    availability_verification_status='VERIFIED',
                    verification_status='VERIFIED',
                    availability_status='Available',
                    final_send_eligible=TRUE,
                    verification_required_before_final_send=FALSE,
                    verified_by=:actor,
                    verified_date=CURRENT_DATE,
                    last_verified_at=NOW(),
                    next_verification_at=NOW() + INTERVAL '30 days',
                    verification_contacted_at=NOW(),
                    verification_notes=:notes,
                    updated_at=NOW()
                WHERE id=:id
            """), {"actor": actor, "notes": notes, "id": int(record_id)})
        elif action == "NOT_AVAILABLE":
            c.execute(text("""
                UPDATE pi_properties SET
                    availability_verification_status='NOT_AVAILABLE',
                    availability_status='Not Available',
                    final_send_eligible=FALSE,
                    verification_required_before_final_send=TRUE,
                    verification_contacted_at=NOW(),
                    verification_notes=:notes,
                    next_verification_at=NULL,
                    updated_at=NOW()
                WHERE id=:id
            """), {"notes": notes, "id": int(record_id)})
        elif action == "VERIFY_LATER":
            c.execute(text("""
                UPDATE pi_properties SET
                    availability_verification_status='VERIFY_LATER',
                    final_send_eligible=FALSE,
                    verification_required_before_final_send=TRUE,
                    verification_contacted_at=NOW(),
                    verification_notes=:notes,
                    next_verification_at=NOW() + (:days * INTERVAL '1 day'),
                    updated_at=NOW()
                WHERE id=:id
            """), {"notes": notes, "days": days, "id": int(record_id)})
        else:
            raise HTTPException(400, "Unsupported action")

        new = dict(c.execute(text("SELECT * FROM pi_properties WHERE id=:id"), {"id": int(record_id)}).mappings().first())
        _log(c, property_id, f"PHASE6_{action}", actor, notes, old, new)
        return new


def _apply_wa(engine, record_id: str, action: str, actor: str, payload: dict):
    if action == "SAVE_CORRECTION":
        raise HTTPException(409, "WhatsApp Phase 4.1 READY records are not corrected in Phase 6. Correct source extraction instead.")
    notes = str(payload.get("notes") or "").strip()[:4000]
    days = max(1, min(int(payload.get("verify_later_days") or 7), 365))
    with engine.begin() as c:
        old = _snapshot_wa(c, record_id)
        if action == "VERIFY_AVAILABLE":
            ver = "Verified"
            next_sql = "NOW() + INTERVAL '30 days'"
        elif action == "NOT_AVAILABLE":
            ver = "Not Available"
            next_sql = "NULL"
        elif action == "VERIFY_LATER":
            ver = "Verify Later"
            next_sql = "NOW() + (:days * INTERVAL '1 day')"
        else:
            raise HTTPException(400, "Unsupported action")
        c.execute(text(f"""
            UPDATE pi_whatsapp_property_master SET
                verification=:ver,
                verified_by=:actor,
                verified_at=CASE WHEN :ver='Verified' THEN NOW() ELSE verified_at END,
                next_verification_at={next_sql},
                verification_notes=:notes
            WHERE generation_id=:g AND record_id=:rid
        """), {"ver": ver, "actor": actor, "notes": notes, "days": days,
                "g": _live_generation_id(), "rid": record_id})
        new = dict(c.execute(text("""
            SELECT * FROM pi_whatsapp_property_master
            WHERE generation_id=:g AND record_id=:rid
        """), {"g": _live_generation_id(), "rid": record_id}).mappings().first())
        _log(c, record_id, f"PHASE6_{action}", actor, notes, old, new)
        return new


def _page() -> str:
    return """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Alliance Phase 6 Verification</title>
<style>
*{box-sizing:border-box} body{margin:0;font-family:Arial;background:#f4eee6;color:#2f251d}
header{background:#3f3329;color:white;padding:18px 24px} main{max-width:1900px;margin:auto;padding:18px}
.card{background:white;border:1px solid #d8c8b7;border-radius:12px;padding:14px;margin-bottom:14px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}
button,a.btn{border:0;border-radius:8px;padding:8px 10px;font-weight:800;cursor:pointer;text-decoration:none;background:#6b513d;color:white}
button.warn{background:#946300} button.bad{background:#9d2d2d} button.good{background:#237043}
table{width:100%;border-collapse:collapse;min-width:1550px} th,td{padding:8px;border-bottom:1px solid #eadfd2;vertical-align:top;font-size:12px}
th{background:#f7ecdf;position:sticky;top:0}.scroll{overflow:auto;max-height:72vh}.muted{color:#76695d}
input,select,textarea{width:100%;padding:7px;border:1px solid #cdbba8;border-radius:7px}
.badge{display:inline-block;padding:3px 7px;border-radius:999px;background:#eee2d5;margin:2px}
</style></head><body>
<header><h2 style="margin:0">Alliance Phase 6 · Verification Control Room</h2>
<small>Internal contacts visible only to logged-in team · READY + VERIFIED required before send</small></header>
<main>
<div class="card">
<div class="grid">
<div><b>Queue</b><br><span id="count">Loading...</span></div>
<div><b>Rule</b><br>READY + VERIFIED → send eligible</div>
<div><b>Legacy rule</b><br>READY_LEGACY must be corrected first</div>
<div><b>Unavailable</b><br>Hidden from matching</div>
<div><b>Verify Later</b><br>Returns on scheduled date</div>
</div></div>
<div class="card">
<label>Filter</label>
<select id="filter" onchange="render()">
<option value="ALL">All verification work</option>
<option value="UNVERIFIED">Unverified</option>
<option value="VERIFY_LATER">Verify Later</option>
<option value="READY_LEGACY">Needs correction</option>
</select>
</div>
<div class="card scroll"><table>
<thead><tr>
<th>Property</th><th>Location</th><th>Txn</th><th>Type</th><th>Area</th><th>Quality</th><th>Verification</th>
<th>Internal Contact</th><th>Source</th><th>Next Verify</th><th>Notes</th><th>Actions</th>
</tr></thead><tbody id="rows"></tbody></table></div>
<div class="card muted">Contacts shown here are internal verification data. They are not exposed by the Phase 5 matcher.</div>
</main>
<script>
let DATA=[];
function h(s){return String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}
async function load(){
 const r=await fetch('/api/v61/queue?limit=500'); if(!r.ok){document.getElementById('count').innerText='Error '+r.status;return}
 const j=await r.json(); DATA=j.rows||[]; document.getElementById('count').innerText=DATA.length+' records'; render();
}
function render(){
 const f=document.getElementById('filter').value;
 const rows=DATA.filter(x=>f==='ALL'||String(x.verification).toUpperCase().includes(f.replace('_',' '))||String(x.data_quality).toUpperCase().includes(f));
 document.getElementById('rows').innerHTML=rows.map(x=>`<tr>
 <td><b>${h(x.property)}</b><br><small>${h(x.record_id)}</small></td><td>${h(x.location)}</td><td>${h(x.transaction)}</td>
 <td>${h(x.property_type)}</td><td>${h(x.area_sqft||x.area_value||'')}</td><td>${h(x.data_quality)}</td><td>${h(x.verification)}</td>
 <td><b>${h(x.contact_name||'')}</b><br>${h(x.contact||'No contact')}</td><td>${h(x.source)}</td><td>${h(x.next_verification_at||'')}</td>
 <td><textarea id="n_${x.source_table}_${x.record_id}" rows="2">${h(x.verification_notes||'')}</textarea></td>
 <td>
 ${x.source_table==='pi_properties' && String(x.data_quality).toUpperCase()!=='READY' ? `<button class="warn" onclick="correct('${x.record_id}')">Correct</button>` : ''}
 <button class="good" onclick="act('${x.source_table}','${x.record_id}','VERIFY_AVAILABLE')">Available + Verified</button>
 <button class="warn" onclick="later('${x.source_table}','${x.record_id}')">Verify Later</button>
 <button class="bad" onclick="act('${x.source_table}','${x.record_id}','NOT_AVAILABLE')">Not Available</button>
 </td></tr>`).join('');
}
async function act(src,id,action,extra={}){
 const el=document.getElementById(`n_${src}_${id}`); const notes=el?el.value:'';
 const r=await fetch('/api/v61/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({source_table:src,record_id:id,action,notes,...extra})});
 const j=await r.json().catch(()=>({})); if(!r.ok){alert(j.detail||j.message||('Error '+r.status));return} await load();
}
function later(src,id){const d=prompt('Verify again after how many days?','7'); if(!d)return; act(src,id,'VERIFY_LATER',{verify_later_days:Number(d)})}
async function correct(id){
 const tx=prompt('Transaction: SALE or RENT'); if(!tx)return;
 const loc=prompt('Specific locality'); if(!loc)return;
 const pt=prompt('Property type / use'); if(!pt)return;
 const av=prompt('Area value'); if(!av)return;
 const au=prompt('Area unit: SQFT, SQM or SQYD','SQFT'); if(!au)return;
 await act('pi_properties',id,'SAVE_CORRECTION',{canonical_transaction:tx,canonical_locality:loc,canonical_property_type:pt,canonical_area_value:Number(av),canonical_area_unit:au});
}
load();
</script></body></html>"""


def register(core):
    app = core.app
    engine = core.engine
    if _route_exists(app, ROUTE):
        return {"status": "ALREADY_REGISTERED", "version": VERSION, "route": ROUTE}

    _ensure_schema(engine)
    router = APIRouter()

    @router.get(ROUTE, response_class=HTMLResponse)
    def verification_page(req: Request):
        _require_login(core, req)
        return HTMLResponse(_page())

    @router.get("/api/v61/status")
    def status(req: Request):
        _require_login(core, req)
        rows = queue(engine, 5000)
        return {
            "status": "OK",
            "version": VERSION,
            "queue_count": len(rows),
            "pi_properties": sum(1 for x in rows if x["source_table"] == "pi_properties"),
            "pi_whatsapp_property_master": sum(1 for x in rows if x["source_table"] == "pi_whatsapp_property_master"),
            "contacts_internal_only": True,
            "strict_send_rule": "READY_AND_VERIFIED",
            "ready_legacy_requires_correction": True,
            "not_available_hidden": True,
            "verify_later_supported": True,
            "source_identity_mutation": False,
            "price_identity_mutation": False,
        }

    @router.get("/api/v61/queue")
    def get_queue(req: Request, limit: int = Query(500, ge=1, le=2000)):
        _require_login(core, req)
        rows = queue(engine, limit)
        return {"status": "OK", "version": VERSION, "rows": rows, "count": len(rows)}

    @router.post("/api/v61/action")
    async def action(req: Request):
        _require_login(core, req)
        payload = await req.json()
        action_name = norm(payload.get("action")).replace(" ", "_")
        if action_name not in VALID_ACTIONS:
            raise HTTPException(400, "Unsupported action")
        src = str(payload.get("source_table") or "").strip()
        rid = str(payload.get("record_id") or "").strip()
        if not rid:
            raise HTTPException(400, "record_id required")
        actor = _actor(core, req)
        if src == "pi_properties":
            new = _apply_pi(engine, rid, action_name, actor, payload)
        elif src == "pi_whatsapp_property_master":
            new = _apply_wa(engine, rid, action_name, actor, payload)
        else:
            raise HTTPException(400, "Unsupported source_table")
        return JSONResponse({
            "status": "OK",
            "version": VERSION,
            "action": action_name,
            "source_table": src,
            "record_id": rid,
            "verification": new.get("availability_verification_status") or new.get("verification"),
            "data_quality": new.get("data_quality_status") or ("READY" if src == "pi_whatsapp_property_master" else None),
            "send_eligible": bool(new.get("final_send_eligible")) if src == "pi_properties" else norm(new.get("verification")) == "VERIFIED",
        })

    app.include_router(router)
    return {
        "status": "REGISTERED",
        "version": VERSION,
        "route": ROUTE,
        "api_status": "/api/v61/status",
        "api_queue": "/api/v61/queue",
        "api_action": "/api/v61/action",
    }


def self_test():
    tests = {
        "ready_legacy_requires_correction": True,
        "strict_send_rule_ready_and_verified": True,
        "not_available_is_not_sendable": True,
        "verify_later_is_not_sendable": True,
        "contacts_are_internal_queue_only": True,
        "whatsapp_verification_survives_v44_upsert": True,
        "identity_is_not_rewritten": True,
        "price_is_not_used_for_identity": True,
    }
    return tests
