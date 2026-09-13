from __future__ import annotations

import html, json, re, urllib.parse
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import inspect, text

import alliance_property_brain_foundation_v1 as foundation
import alliance_phase5_canonical_matcher as phase5

VERSION = "1.0.0-AUTOMATED-DEAL-DESK"
PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[\s\-]?)?([6-9]\d{9})(?!\d)")
OPAQUE_RE = re.compile(r"^\d{13,}$")

_STATE = {
    "status": "INIT",
    "version": VERSION,
    "route_authority": "INIT",
    "database_ready": False,
    "matcher_contract": "MASTER_ONLY",
    "contact_policy": "EVIDENCE_ONLY_NO_GUESSING",
    "draft_policy": "EVIDENCE_ONLY_TEAM_APPROVAL_REQUIRED",
    "startup_error": None,
}

class RunRequest(BaseModel):
    requirement_id: Optional[int] = None
    requirement_text: Optional[str] = None
    min_score: float = 70.0
    limit: int = 20

def _app(core): return getattr(core, "app", core)
def _engine(core): return foundation._engine_from_core(core)

def _auth(core, req: Request):
    try:
        core.need_login(req)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Login required") from exc

def _table_exists(e, name: str) -> bool:
    try:
        return name in set(inspect(e).get_table_names())
    except Exception:
        return False

def _cols(e, name: str) -> List[str]:
    try:
        return [x["name"] for x in inspect(e).get_columns(name)]
    except Exception:
        return []

def _qident(x: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(x or "")):
        raise ValueError("unsafe identifier")
    return '"' + x.replace('"','""') + '"'

def _phone(v: Any) -> Optional[str]:
    s = str(v or "").strip()
    if not s or "@lid" in s.lower() or OPAQUE_RE.fullmatch(re.sub(r"\D","",s) or ""):
        return None
    m = PHONE_RE.search(s)
    return "+91" + m.group(1) if m else None

def _pick_phone(row: Dict[str,Any], fields: List[str]) -> Tuple[Optional[str], Optional[str]]:
    for f in fields:
        if f in row:
            p = _phone(row.get(f))
            if p:
                return p, f
    return None, None

def _fetch_master(e, cid: str) -> Dict[str,Any]:
    if not _table_exists(e,"pi_master_properties_v711"): return {}
    cols=_cols(e,"pi_master_properties_v711")
    key="canonical_id" if "canonical_id" in cols else ("property_id" if "property_id" in cols else None)
    if not key: return {}
    with e.connect() as c:
        r=c.execute(text(f"SELECT to_jsonb(t) FROM pi_master_properties_v711 t WHERE CAST({_qident(key)} AS TEXT)=:id LIMIT 1"),{"id":cid}).scalar()
    return dict(r) if isinstance(r,dict) else {}

def _source_links(e,cid:str) -> List[Dict[str,Any]]:
    if not _table_exists(e,"pi_master_source_links_v711"): return []
    with e.connect() as c:
        rows=c.execute(text("""SELECT source_type,source_table,source_pk,created_at
                              FROM pi_master_source_links_v711
                              WHERE master_entity_type='PROPERTY'
                                AND CAST(canonical_id AS TEXT)=:cid
                              ORDER BY created_at DESC NULLS LAST,id DESC
                              LIMIT 20"""),{"cid":cid}).mappings().all()
    return [dict(x) for x in rows]

def _fetch_source(e,table_name:str,source_pk:str) -> Dict[str,Any]:
    if not table_name or not source_pk or not _table_exists(e,table_name): return {}
    cols=_cols(e,table_name)
    preferred=["wa_property_id","property_id","record_id","id","source_id","listing_id","canonical_id"]
    keys=[x for x in preferred if x in cols]+[x for x in cols if x.endswith("_id") and x not in preferred]
    for k in keys:
        try:
            with e.connect() as c:
                r=c.execute(text(f"SELECT to_jsonb(t) FROM {_qident(table_name)} t WHERE CAST({_qident(k)} AS TEXT)=:pk LIMIT 1"),{"pk":str(source_pk)}).scalar()
            if isinstance(r,dict): return dict(r)
        except Exception:
            continue
    return {}

def _contact_from_row(row:Dict[str,Any], origin:str) -> Optional[Dict[str,Any]]:
    p,f=_pick_phone(row,[
        "owner_phone","broker_phone","contact_phone","phone_number","mobile",
        "phone","sender_phone"
    ])
    if not p: return None
    role = (
        "OWNER" if f=="owner_phone" else
        "BROKER" if f=="broker_phone" else
        "WHATSAPP_SENDER" if f=="sender_phone" else
        "SOURCE_CONTACT"
    )
    name=None
    for nf in ("owner_name","broker_name","contact_name","sender_name","name"):
        if row.get(nf):
            name=str(row.get(nf)).strip(); break
    return {"phone":p,"role":role,"name":name,"method":f,"origin":origin,"verified":False}

def _resolve_contact(e,cid:str) -> Dict[str,Any]:
    evidence=[]
    master=_fetch_master(e,cid)
    c=_contact_from_row(master,"MASTER_PROPERTY")
    if c: evidence.append(c)

    for link in _source_links(e,cid):
        table_name=str(link.get("source_table") or "").strip()
        pk=str(link.get("source_pk") or "").strip()
        row=_fetch_source(e,table_name,pk)
        c=_contact_from_row(row,f"{table_name}:{pk}")
        if c: evidence.append(c)

        # Exact WhatsApp message sender fallback if source property has message_id.
        mid=str(row.get("message_id") or "").strip()
        if mid and _table_exists(e,"wa_messages"):
            cols=_cols(e,"wa_messages")
            if "message_id" in cols:
                try:
                    with e.connect() as db:
                        mr=db.execute(text("SELECT to_jsonb(t) FROM wa_messages t WHERE CAST(message_id AS TEXT)=:mid LIMIT 1"),{"mid":mid}).scalar()
                    if isinstance(mr,dict):
                        c2=_contact_from_row(dict(mr),f"wa_messages:{mid}")
                        if c2: evidence.append(c2)
                except Exception:
                    pass

    seen=set(); uniq=[]
    for x in evidence:
        if x["phone"] in seen: continue
        seen.add(x["phone"]); uniq.append(x)

    # Explicit owner/broker/source contact outranks sender provenance.
    priority={"OWNER":0,"BROKER":1,"SOURCE_CONTACT":2,"WHATSAPP_SENDER":3}
    uniq.sort(key=lambda x: priority.get(x.get("role"),9))
    primary=uniq[0] if uniq else None
    return {
        "status":"CONTACT_FOUND" if primary else "CONTACT_NOT_RECOVERABLE_FROM_SOURCE",
        "primary":primary,
        "evidence":uniq[:5],
        "opaque_id_guessing":False,
    }

def _requirement_text(e,rid:int) -> str:
    if not _table_exists(e,"pi_requirement_gate_v1191"):
        raise HTTPException(503,"Master requirement authority unavailable")
    cols=_cols(e,"pi_requirement_gate_v1191")
    candidates=[x for x in ("original_message","raw_text","requirement_text","description") if x in cols]
    if not candidates: raise HTTPException(500,"No requirement text column found")
    expr="COALESCE("+",".join(_qident(x) for x in candidates)+")"
    with e.connect() as c:
        raw=c.execute(text(f"SELECT {expr} FROM pi_requirement_gate_v1191 WHERE id=:id LIMIT 1"),{"id":rid}).scalar()
    if not raw: raise HTTPException(404,"Requirement not found")
    return str(raw)

def _safe_row(r:Dict[str,Any],bucket:str,e) -> Dict[str,Any]:
    rid=str(r.get("record_id") or r.get("canonical_id") or r.get("property_id") or "").strip()
    contact=_resolve_contact(e,rid) if rid else {"status":"CONTACT_NOT_RECOVERABLE_FROM_SOURCE","primary":None,"evidence":[],"opaque_id_guessing":False}
    return {
        "bucket":bucket,
        "record_id":rid,
        "detail_url":str(r.get("detail_url") or (f"/alliance/primary/property/{rid}" if rid else "")),
        "score":r.get("score"),
        "location":r.get("location") or r.get("locality") or r.get("city"),
        "property_type":r.get("property_type") or r.get("asset_type") or r.get("subtype"),
        "transaction":r.get("transaction_type") or r.get("transaction"),
        "price":r.get("price") or r.get("sale_price_inr") or r.get("rent_inr"),
        "area":r.get("area") or r.get("area_sqft") or r.get("available_area_sqft"),
        "verification":r.get("verification_status") or r.get("verified") or r.get("status"),
        "source":r.get("source_bucket") or r.get("source_table") or "MASTER",
        "why":r.get("why") or r.get("match_reason") or r.get("reason"),
        "contact":contact,
    }

def _draft(requirement:str, rows:List[Dict[str,Any]]) -> str:
    exact=[x for x in rows if x["bucket"] in ("EXACT_VERIFIED","EXACT_NEEDS_VERIFICATION")][:4]
    alt=[x for x in rows if x["bucket"]=="ALTERNATIVE"][:3]
    lines=["Hi, we have shortlisted property options for your requirement.",""]
    if exact:
        lines.append("Best matching options:")
        for i,x in enumerate(exact,1):
            bits=[f"{i}. {x.get('property_type') or 'Property'}"]
            if x.get("location"): bits.append(str(x["location"]))
            if x.get("price"): bits.append(f"Price/Rent: {x['price']}")
            if x.get("area"): bits.append(f"Area: {x['area']}")
            lines.append(" • ".join(bits))
    if alt:
        lines+=["","Alternate options:"]
        for i,x in enumerate(alt,1):
            bits=[f"{i}. {x.get('property_type') or 'Property'}"]
            if x.get("location"): bits.append(str(x["location"]))
            if x.get("price"): bits.append(f"Price/Rent: {x['price']}")
            lines.append(" • ".join(bits))
    if not exact and not alt:
        lines+=["We do not have a sufficiently strong verified match yet.","We are checking additional inventory and will update you."]
    lines+=["","Please tell us which options you would like to inspect. Availability and commercial terms will be reconfirmed before closure."]
    return "\n".join(lines)

def _run(e,payload:RunRequest):
    req=(payload.requirement_text or "").strip()
    if not req and payload.requirement_id is not None:
        req=_requirement_text(e,int(payload.requirement_id))
    if not req: raise HTTPException(400,"requirement_text or requirement_id required")
    result=phase5.run_match(e,requirement_text=req,min_score=float(payload.min_score),limit=max(1,min(int(payload.limit),50)))
    rows=[]
    for key,bucket in (("exact_verified","EXACT_VERIFIED"),("exact_needs_verification","EXACT_NEEDS_VERIFICATION"),("alternatives","ALTERNATIVE")):
        for r in (result.get(key) or []):
            if isinstance(r,dict): rows.append(_safe_row(r,bucket,e))
    return {
        "status":"OK","version":VERSION,"matcher_source_contract":"MASTER_ONLY",
        "requirement":req,"rows":rows,"draft":_draft(req,rows),
        "draft_requires_team_approval":True,"automatic_send":False,
        "contact_policy":"EVIDENCE_ONLY_NO_GUESSING",
        "counts":{
            "exact_verified":sum(1 for x in rows if x["bucket"]=="EXACT_VERIFIED"),
            "exact_needs_verification":sum(1 for x in rows if x["bucket"]=="EXACT_NEEDS_VERIFICATION"),
            "alternatives":sum(1 for x in rows if x["bucket"]=="ALTERNATIVE"),
            "contact_found":sum(1 for x in rows if x["contact"]["status"]=="CONTACT_FOUND"),
            "contact_missing":sum(1 for x in rows if x["contact"]["status"]!="CONTACT_FOUND"),
        }
    }

def _esc(v): return html.escape("" if v is None else str(v))

def _page():
    return """<!doctype html><meta charset='utf-8'><title>Alliance Automated Deal Desk</title>
<style>
body{font-family:Arial;margin:24px;max-width:1450px}textarea,input{width:100%;padding:10px;margin:5px 0 12px;box-sizing:border-box}
button{padding:11px 18px;margin-right:8px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}.card{border:1px solid #ddd;border-radius:10px;padding:14px;margin:10px 0}.muted{color:#666}.ok{font-weight:bold}.warn{font-weight:bold}
@media(max-width:900px){.grid{grid-template-columns:1fr}}
</style>
<h1>Alliance Automated Deal Desk</h1>
<p>Master-only matcher → evidence-based contact recovery → exact + alternate options → team-approved client draft.</p>
<label>Requirement ID (optional)</label><input id='rid' type='number'>
<label>Or paste requirement</label><textarea id='req' rows='5'></textarea>
<button onclick='run()'>Run Smart Matcher + Prepare Deal Desk</button>
<div id='summary'></div><div id='results'></div>
<h2>AI Assistant Draft</h2><textarea id='draft' rows='14'></textarea>
<button onclick='copyDraft()'>Copy Draft</button><button onclick='openWhatsApp()'>Open WhatsApp with Draft</button>
<p class='muted'>Team must verify properties and contacts before sending. Alliance never guesses missing phone numbers.</p>
<script>
let DATA=null;
window.addEventListener('DOMContentLoaded',()=>{const q=new URLSearchParams(location.search).get('requirement_id');if(q)document.getElementById('rid').value=q;});
function e(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function run(){
 const body={requirement_id:rid.value?Number(rid.value):null,requirement_text:req.value||null,min_score:70,limit:20};
 const r=await fetch('/api/alliance/deal-desk-v1/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
 const d=await r.json(); if(!r.ok){alert(JSON.stringify(d));return} DATA=d;
 draft.value=d.draft||'';
 summary.innerHTML='<p><b>Exact verified:</b> '+d.counts.exact_verified+' · <b>Needs verification:</b> '+d.counts.exact_needs_verification+' · <b>Alternates:</b> '+d.counts.alternatives+' · <b>Contacts found:</b> '+d.counts.contact_found+' · <b>Missing contacts:</b> '+d.counts.contact_missing+'</p>';
 results.innerHTML=(d.rows||[]).map(x=>{
   const p=x.contact&&x.contact.primary;
   const c=p?e(p.phone)+' · '+e(p.role)+' · '+e(p.name||''):'Contact not recoverable from source';
   return '<div class="card"><b>'+e(x.bucket)+'</b> · Score '+e(x.score)+'<br><b>'+e(x.property_type||'Property')+'</b> · '+e(x.location||'')+' · '+e(x.price||'')+' · '+e(x.area||'')+
   '<br>Contact: <span class="'+(p?'ok':'warn')+'">'+c+'</span><br>Source: '+e(x.source||'')+' · Verification: '+e(x.verification||'')+
   (x.why?'<br>Why: '+e(x.why):'')+(x.detail_url?'<br><a href="'+e(x.detail_url)+'" target="_blank">Open full property</a>':'')+'</div>'
 }).join('');
}
async function copyDraft(){await navigator.clipboard.writeText(draft.value);alert('Draft copied')}
function openWhatsApp(){window.open('https://wa.me/?text='+encodeURIComponent(draft.value),'_blank')}
</script>"""

def register(core):
    app=_app(core); e=_engine(core)
    if app is None: raise RuntimeError("APP_UNAVAILABLE")
    existing={getattr(r,"path",None) for r in app.router.routes}

    if "/api/alliance/deal-desk-v1/status" not in existing:
        @app.get("/api/alliance/deal-desk-v1/status")
        def deal_desk_status(req:Request):
            _auth(core,req)
            return dict(_STATE)

    if "/api/alliance/deal-desk-v1/run" not in existing:
        @app.post("/api/alliance/deal-desk-v1/run")
        def deal_desk_run(req:Request,payload:RunRequest):
            _auth(core,req)
            if e is None: raise HTTPException(503,"Database unavailable")
            return _run(e,payload)

    if "/alliance/primary/deal-desk" not in existing:
        @app.get("/alliance/primary/deal-desk",response_class=HTMLResponse)
        def deal_desk_page(req:Request):
            _auth(core,req)
            return HTMLResponse(_page())

    _STATE["route_authority"]="LIVE"
    if e is None:
        _STATE["status"]="ERROR"; _STATE["startup_error"]="DATABASE_ENGINE_UNAVAILABLE"; return dict(_STATE)
    try:
        if not _table_exists(e,"pi_master_properties_v711"): raise RuntimeError("MASTER_PROPERTY_AUTHORITY_MISSING")
        if not _table_exists(e,"pi_requirement_gate_v1191"): raise RuntimeError("MASTER_REQUIREMENT_AUTHORITY_MISSING")
        _STATE["database_ready"]=True; _STATE["status"]="READY"; _STATE["startup_error"]=None
    except Exception as exc:
        _STATE["status"]="ERROR"; _STATE["startup_error"]=f"{type(exc).__name__}: {exc}"
    return dict(_STATE)
