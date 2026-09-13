from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from html import escape
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import inspect, text
import alliance_master_inventory_role_firewall_v1 as _inventory_firewall

VERSION = "1.0.0-EXPLAINABLE-MASTER-MATCHER"
MARKER = "ALLIANCE_EXPLAINABLE_MATCHER_V1"
MASTER_TABLE = "pi_master_properties_v711"
REQUIREMENT_TABLE = "pi_requirement_gate_v1191"
SOURCE_CONTRACT = "MASTER_ONLY"
MAX_MASTER_ROWS = 15000

STATE: Dict[str, Any] = {
    "status": "INIT",
    "version": VERSION,
    "marker": MARKER,
    "source_contract": SOURCE_CONTRACT,
    "master_table": MASTER_TABLE,
    "contacts_exposed": False,
    "automatic_send": False,
    "last_run_at": None,
    "last_error": None,
}

def _app(core):
    return getattr(core, "app", core)

def _engine(core):
    try:
        import alliance_property_brain_foundation_v1 as foundation
        return foundation._engine_from_core(core)
    except Exception:
        return None

def _need_login(core, request: Request):
    fn = getattr(core, "need_login", None)
    if callable(fn):
        return fn(request)
    return True

def _norm(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "").strip().upper())

def _digits(v: Any) -> Optional[float]:
    try:
        if v is None or str(v).strip() == "":
            return None
        return float(str(v).replace(",", "").strip())
    except Exception:
        return None

def _money_to_rupees(num: str, unit: str) -> Optional[float]:
    try:
        n = float(num.replace(",", ""))
    except Exception:
        return None
    u = _norm(unit)
    if "CR" in u or "CRORE" in u:
        return n * 10_000_000
    if "LAC" in u or "LAKH" in u or u == "L":
        return n * 100_000
    if u in ("K", "THOUSAND"):
        return n * 1_000
    return n

def _extract_money(text_raw: str) -> List[Tuple[int, float]]:
    t = text_raw or ""
    out: List[Tuple[int, float]] = []

    # First capture ranges where the unit is written once at the end:
    # "2.05 to 2.10 cr", "90-100 lakh".
    range_rx = re.compile(
        r"(?i)(?<!\d)(\d+(?:\.\d+)?)\s*(?:-|–|—|TO)\s*"
        r"(\d+(?:\.\d+)?)\s*(CR(?:ORE)?S?|LAC(?:S)?|LAKHS?|L|K|THOUSAND)\b"
    )
    covered = []
    for rm in range_rx.finditer(t):
        v1 = _money_to_rupees(rm.group(1), rm.group(3))
        v2 = _money_to_rupees(rm.group(2), rm.group(3))
        if v1 is not None:
            out.append((rm.start(1), v1))
        if v2 is not None:
            out.append((rm.start(2), v2))
        covered.append((rm.start(), rm.end()))

    # Then capture ordinary single money values, skipping spans already handled above.
    single_rx = re.compile(r"(?i)(?<!\d)(\d+(?:\.\d+)?)\s*(CR(?:ORE)?S?|LAC(?:S)?|LAKHS?|L|K|THOUSAND)\b")
    for sm in single_rx.finditer(t):
        if any(a <= sm.start() < b for a, b in covered):
            continue
        v = _money_to_rupees(sm.group(1), sm.group(2))
        if v is not None:
            out.append((sm.start(), v))

    out.sort(key=lambda x: x[0])
    return out

def _extract_area(raw: str) -> Dict[str, Any]:
    t = raw or ""
    m = re.search(r"(?i)\b(\d[\d,]*(?:\.\d+)?)\s*(SQ\s*FT|SQFT|SFT|SQ\s*M|SQM|SQMT|SQ\s*MT)\b", t)
    if not m:
        return {"value": None, "unit": None}
    value = float(m.group(1).replace(",", ""))
    u = _norm(m.group(2)).replace(" ", "")
    if u in ("SQM", "SQMT"):
        return {"value": value, "unit": "SQM"}
    return {"value": value, "unit": "SQFT"}

def _extract_location(raw: str) -> Optional[str]:
    t = raw or ""
    m = re.search(r"(?i)\b(?:SEC(?:TOR)?)[\s\-]*([0-9]{1,3}[A-Z]?)\b", t)
    if m:
        return f"SECTOR {m.group(1).upper()}"
    # conservative locality cues: preserve only explicit "in/at/near <place>" phrases
    m = re.search(r"(?i)\b(?:IN|AT|NEAR)\s+([A-Z][A-Za-z .'-]{2,40}?)(?=\s+(?:FOR|BUDGET|TARGET|TOWER|FLOOR|AREA|WITH)\b|[,.;]|$)", t)
    if m:
        return _norm(m.group(1))
    return None

def _extract_project(raw: str) -> Optional[str]:
    t = re.sub(r"(?i)^\s*\*?REQUIREMENT\*?\s*", "", raw or "").strip()
    # Strong project pattern before a sector delimiter.
    m = re.match(r"(?is)^(.{3,80}?)\s*[-–—,:]\s*(?:SEC(?:TOR)?\b)", t)
    if m:
        p = re.sub(r"[*_]+", "", m.group(1)).strip(" -–—,:")
        if p:
            return _norm(p)
    # Named project cue.
    m = re.search(r"(?i)\b(?:PROJECT|SOCIETY)\s*[:\-]?\s*([A-Z][A-Za-z0-9 &'().-]{2,70})", t)
    if m:
        return _norm(m.group(1))
    return None

def _extract_floor(raw: str) -> Dict[str, Optional[int]]:
    m = re.search(r"(?i)\bFLOOR\s*[:\-]?\s*(\d{1,3})\s*(?:-|TO)\s*(\d{1,3})\b", raw or "")
    if not m:
        return {"min": None, "max": None}
    a, b = int(m.group(1)), int(m.group(2))
    return {"min": min(a,b), "max": max(a,b)}

def _extract_branches(raw: str) -> List[Dict[str, Any]]:
    t = raw or ""
    towers = list(re.finditer(r"(?i)\bTOWER\s*([A-Z0-9-]+)\b", t))
    money = _extract_money(t)
    branches: List[Dict[str, Any]] = []
    if not towers:
        return branches
    for i, tm in enumerate(towers):
        start = tm.start()
        end = towers[i+1].start() if i + 1 < len(towers) else len(t)
        seg = t[start:end]
        floor = _extract_floor(seg)
        unit = None
        um = re.search(r"(?i)\bUNIT\s*[:\-]?\s*([A-Z0-9-]+)\b", seg)
        if um:
            unit = _norm(um.group(1))
        vals = [v for pos, v in money if start <= pos < end]
        bmin = min(vals) if len(vals) >= 2 else None
        bmax = max(vals) if vals else None
        branches.append({
            "tower": _norm(tm.group(1)),
            "floor_min": floor["min"],
            "floor_max": floor["max"],
            "unit_preference": unit,
            "budget_min": bmin,
            "budget_max": bmax,
        })
    return branches

def _transaction(raw: str) -> Tuple[Optional[str], str]:
    t = _norm(raw)
    if re.search(r"\b(RENT|RENTAL|LEASE|LEASING)\b", t):
        return "LEASE", "explicit"
    if re.search(r"\b(BUY|BUYING|PURCHASE|PURCHASING|SALE|FOR SALE|READY BUYER)\b", t):
        return "SALE", "explicit"
    return None, "not_identified"

def parse_requirement(raw: str) -> Dict[str, Any]:
    area = _extract_area(raw)
    location = _extract_location(raw)
    project = _extract_project(raw)
    branches = _extract_branches(raw)
    transaction, tx_conf = _transaction(raw)
    t = _norm(raw)
    family = None
    family_conf = "not_identified"
    if re.search(r"\b(TOWER|UNIT|FLOOR|APARTMENT|FLAT)\b", t):
        family = "RESIDENTIAL_APARTMENT"
        family_conf = "inferred_from_structure"
    monies = _extract_money(raw)
    budget_max = max((v for _, v in monies), default=None)
    return {
        "raw": raw,
        "project": project,
        "location": location,
        "transaction": transaction,
        "transaction_confidence": tx_conf,
        "property_family": family,
        "property_family_confidence": family_conf,
        "area_requested": area["value"],
        "area_unit": area["unit"],
        "area_exact_tolerance_pct": 3.0,
        "area_strong_tolerance_pct": 5.0,
        "area_alternative_tolerance_pct": 10.0,
        "budget_max": budget_max,
        "branches": branches,
    }

FIELD_ALIASES = {
    "id": ["record_id","property_id","id","master_id","canonical_id"],
    "project": ["project_name","project","society_name","building_name","scheme_name"],
    "location": ["location","locality","primary_location","micro_market","area_name","address","city"],
    "family": ["property_family","asset_family","category","property_type","asset_type","subtype"],
    "transaction": ["transaction","transaction_type","deal_type","listing_type"],
    "area": ["builtup_area","built_up_area","area_sqft","area","size_sqft","super_area","carpet_area"],
    "price": ["asking_price","price","price_value","budget","amount","sale_price","expected_price"],
    "tower": ["tower","tower_name","block","building"],
    "floor": ["floor","floor_no","floor_number"],
    "unit": ["unit","unit_no","unit_number","flat_no"],
    "verified": ["is_verified","verified","verification_status","status"],
    "availability": ["availability_status","availability","workflow_status","status"],
    "updated": ["updated_at","last_verified_at","created_at"],
    "raw_text": ["raw_text","original_message","message_text","description","property_text","text","source_text"],
}

def _pick(cols: List[str], key: str) -> Optional[str]:
    low = {c.lower(): c for c in cols}
    for a in FIELD_ALIASES[key]:
        if a.lower() in low:
            return low[a.lower()]
    return None

def _schema(engine) -> Dict[str, Optional[str]]:
    cols = [c["name"] for c in inspect(engine).get_columns(MASTER_TABLE)]
    return {k: _pick(cols, k) for k in FIELD_ALIASES}

def _select_master(engine, smap: Dict[str, Optional[str]]) -> List[Dict[str, Any]]:
    selected = []
    for logical, col in smap.items():
        if col:
            selected.append(f'"{col}" AS "{logical}"')
    if not selected:
        return []
    sql = f'SELECT {", ".join(selected)} FROM "{MASTER_TABLE}" LIMIT {int(MAX_MASTER_ROWS)}'
    with engine.connect() as c:
        return [dict(r._mapping) for r in c.execute(text(sql)).fetchall()]

def _match_text(req: Optional[str], prop: Any) -> str:
    if not req:
        return "UNKNOWN"
    p = _norm(prop)
    if not p:
        return "UNKNOWN"
    r = _norm(req)
    if r in p or p in r:
        return "MATCH"
    return "CONFLICT"

def _area_state(req: Dict[str, Any], prop_area: Any) -> Tuple[str, Optional[float]]:
    target = _digits(req.get("area_requested"))
    actual = _digits(prop_area)
    if target is None or actual is None or target <= 0:
        return "UNKNOWN", None
    pct = abs(actual - target) / target * 100
    if pct <= req["area_exact_tolerance_pct"]:
        return "MATCH", pct
    if pct <= req["area_alternative_tolerance_pct"]:
        return "NEAR", pct
    return "CONFLICT", pct

def _price_state(req: Dict[str, Any], prop_price: Any) -> Tuple[str, Optional[float]]:
    limit = _digits(req.get("budget_max"))
    price = _digits(prop_price)
    if limit is None or price is None or limit <= 0:
        return "UNKNOWN", None
    over = (price - limit) / limit * 100
    if price <= limit:
        return "MATCH", over
    if over <= 10:
        return "NEAR", over
    return "CONFLICT", over

def _branch_fit(branches: List[Dict[str, Any]], row: Dict[str, Any]) -> Tuple[str, List[str]]:
    if not branches:
        return "UNKNOWN", []
    ptower = _norm(row.get("tower"))
    pfloor = _digits(row.get("floor"))
    punit = _norm(row.get("unit"))
    pprice = _digits(row.get("price"))
    reasons: List[str] = []
    best = "CONFLICT"
    for b in branches:
        tower = "UNKNOWN" if not ptower else ("MATCH" if _norm(b["tower"]) in ptower or ptower in _norm(b["tower"]) else "CONFLICT")
        floor = "UNKNOWN"
        if pfloor is not None and b.get("floor_min") is not None and b.get("floor_max") is not None:
            floor = "MATCH" if b["floor_min"] <= pfloor <= b["floor_max"] else "CONFLICT"
        unit = "UNKNOWN"
        if b.get("unit_preference") and punit:
            unit = "MATCH" if _norm(b["unit_preference"]) == punit else "CONFLICT"
        budget = "UNKNOWN"
        if pprice is not None and b.get("budget_max") is not None:
            budget = "MATCH" if pprice <= b["budget_max"] else ("NEAR" if pprice <= b["budget_max"] * 1.10 else "CONFLICT")
        states = [tower, floor, budget]
        conflicts = states.count("CONFLICT")
        matches = states.count("MATCH")
        if conflicts == 0 and matches >= 2:
            best = "MATCH"
            reasons = [f"branch Tower {b['tower']} fits"]
            if unit == "MATCH":
                reasons.append("preferred unit matches")
            break
        if conflicts <= 1 and matches >= 1 and best != "MATCH":
            best = "NEAR"
            reasons = [f"near branch Tower {b['tower']}"]
    return best, reasons

def _rank_one(req: Dict[str, Any], row: Dict[str, Any]) -> Dict[str, Any]:
    project = _match_text(req.get("project"), row.get("project"))
    location = _match_text(req.get("location"), row.get("location"))
    family = _match_text(req.get("property_family"), row.get("family"))
    transaction = _match_text(req.get("transaction"), row.get("transaction"))
    area, area_delta = _area_state(req, row.get("area"))
    price, price_delta = _price_state(req, row.get("price"))
    branch, branch_reasons = _branch_fit(req.get("branches") or [], row)

    # UNKNOWN is uncertainty, never an automatic contradiction.
    weights = {
        "project": 30, "location": 20, "family": 12, "transaction": 8,
        "area": 12, "price": 10, "branch": 8,
    }
    states = {
        "project": project, "location": location, "family": family,
        "transaction": transaction, "area": area, "price": price, "branch": branch,
    }
    score = 0.0
    unknown = []
    conflicts = []
    matched = []
    near = []
    for k, st in states.items():
        w = weights[k]
        if st == "MATCH":
            score += w
            matched.append(k)
        elif st == "NEAR":
            score += w * 0.60
            near.append(k)
        elif st == "UNKNOWN":
            score += w * 0.25
            unknown.append(k)
        else:
            conflicts.append(k)

    # Genuine blockers only when explicit requirement and known property contradict.
    hard_conflicts = []
    if req.get("location") and location == "CONFLICT":
        hard_conflicts.append("location")
    if req.get("transaction") and transaction == "CONFLICT":
        hard_conflicts.append("transaction")
    if req.get("property_family") and family == "CONFLICT":
        hard_conflicts.append("property_family")

    if hard_conflicts:
        category = "REJECTED_CONFLICT"
    elif project == "MATCH" and area == "MATCH" and price in ("MATCH","UNKNOWN") and branch in ("MATCH","UNKNOWN"):
        category = "EXACT_OR_VERIFY"
    elif score >= 72:
        category = "STRONG"
    elif score >= 58:
        category = "ALTERNATIVE"
    else:
        category = "LOW"

    reasons = []
    if project == "MATCH":
        reasons.append("project matches")
    elif project == "UNKNOWN" and req.get("project"):
        reasons.append("project needs verification")
    if location == "MATCH":
        reasons.append("location matches")
    if area == "MATCH":
        reasons.append("area within exact tolerance")
    elif area == "NEAR":
        reasons.append(f"area near target ({area_delta:.1f}% difference)")
    elif area == "UNKNOWN" and req.get("area_requested"):
        reasons.append("area needs verification")
    if price == "MATCH":
        reasons.append("within budget")
    elif price == "NEAR":
        reasons.append(f"price {price_delta:.1f}% above target")
    elif price == "UNKNOWN" and req.get("budget_max"):
        reasons.append("price needs verification")
    reasons.extend(branch_reasons)

    return {
        "record_id": row.get("id"),
        "score": round(score, 1),
        "category": category,
        "matched": matched,
        "near": near,
        "unknown": unknown,
        "conflicts": conflicts,
        "hard_conflicts": hard_conflicts,
        "why": reasons[:8],
        "property": {
            "project": row.get("project"),
            "location": row.get("location"),
            "family": row.get("family"),
            "transaction": row.get("transaction"),
            "area": row.get("area"),
            "price": row.get("price"),
            "tower": row.get("tower"),
            "floor": row.get("floor"),
            "unit": row.get("unit"),
            "verified": row.get("verified"),
            "availability": row.get("availability"),
            "updated": row.get("updated"),
        },
    }

def run_match(engine, raw: str, limit: int = 30) -> Dict[str, Any]:
    req = parse_requirement(raw)
    smap = _schema(engine)
    rows = _select_master(engine, smap)

    role_counts = {"PROPERTY_SUPPLY": 0, "PROPERTY_DEMAND": 0, "AMBIGUOUS": 0, "NOISE": 0}
    eligible_rows = []
    for row in rows:
        role = _inventory_firewall.classify_inventory_role(
            row.get("raw_text"),
            row.get("family"),
            row.get("transaction"),
        )
        role_name = role.get("role") or "AMBIGUOUS"
        role_counts[role_name] = role_counts.get(role_name, 0) + 1
        if role.get("eligible_for_property_matcher") is True:
            eligible_rows.append(row)

    ranked = [_rank_one(req, r) for r in eligible_rows]
    useful = [r for r in ranked if r["category"] not in ("REJECTED_CONFLICT","LOW")]
    useful.sort(key=lambda x: (-x["score"], len(x["unknown"]), len(x["conflicts"])))

    exact = [r for r in useful if r["category"] == "EXACT_OR_VERIFY"][:limit]
    strong = [r for r in useful if r["category"] == "STRONG"][:limit]
    alt = [r for r in useful if r["category"] == "ALTERNATIVE"][:limit]

    rejected = len([r for r in ranked if r["category"] == "REJECTED_CONFLICT"])
    STATE["status"] = "PASS"
    STATE["last_run_at"] = datetime.now(timezone.utc).isoformat()
    STATE["last_error"] = None
    return {
        "status": "PASS",
        "version": VERSION,
        "source_contract": SOURCE_CONTRACT,
        "master_table": MASTER_TABLE,
        "master_rows_considered": len(rows),
        "master_supply_rows_considered": len(eligible_rows),
        "inventory_role_firewall": {
            "version": _inventory_firewall.VERSION,
            "marker": _inventory_firewall.MARKER,
            "role_counts": role_counts,
            "requirements_excluded_from_property_matches": role_counts.get("PROPERTY_DEMAND", 0),
            "ambiguous_excluded_from_property_matches": role_counts.get("AMBIGUOUS", 0),
            "noise_excluded_from_property_matches": role_counts.get("NOISE", 0),
            "supply_only": True,
        },
        "requirement": req,
        "results": {
            "exact_or_verify": exact,
            "strong": strong,
            "alternatives": alt,
        },
        "explainability": {
            "unknown_is_not_conflict": True,
            "exact_area_preserved": True,
            "preference_branches_preserved": True,
            "inventory_role_gate": "PROPERTY_SUPPLY_ONLY",
            "hard_conflict_rejections": rejected,
            "contacts_exposed": False,
            "automatic_send": False,
        },
    }

def _page():
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Alliance · Smart Match</title>
<style>
body{{margin:0;background:#f5f7fb;color:#172033;font:15px/1.5 Inter,system-ui,sans-serif}}.w{{max-width:1080px;margin:auto;padding:26px 18px 70px}}
a{{color:#3157d5;text-decoration:none}}h1{{margin:8px 0 4px;font-size:32px}}.sub{{color:#687386;margin-bottom:22px}}
.card{{background:#fff;border:1px solid #e5e9f0;border-radius:18px;padding:18px;margin:14px 0;box-shadow:0 8px 28px rgba(25,35,55,.06)}}
textarea{{width:100%;min-height:130px;resize:vertical;border:1px solid #cfd6e2;border-radius:14px;padding:14px;font:inherit;box-sizing:border-box}}
button{{border:0;border-radius:12px;padding:12px 18px;background:#1f4fd5;color:white;font-weight:800;cursor:pointer}}button:disabled{{opacity:.5}}
.badge{{display:inline-block;padding:5px 9px;border-radius:999px;background:#eef3ff;font-size:12px;font-weight:800;margin:3px}}
.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}.metric{{background:#f8fafc;border-radius:12px;padding:12px}}
.res{{border-top:1px solid #edf0f4;padding:13px 0}}.score{{font-weight:900;font-size:18px}}.why{{color:#596579;font-size:13px}}
.warn{{background:#fff7e6;border:1px solid #f3d087;border-radius:12px;padding:12px;margin-top:12px}}.ok{{background:#ecfdf3;border:1px solid #a7e0ba;border-radius:12px;padding:12px}}
@media(max-width:760px){{.grid{{grid-template-columns:1fr}}}}
</style></head><body><div class="w">
<a href="/alliance/primary">← Dashboard</a>
<h1>Smart Match</h1>
<div class="sub">Master Property Database only · exact facts preserved · preferences ranked · missing data sent to verification, not silently rejected.</div>
<div class="card"><textarea id="q" placeholder="Paste the client's requirement exactly as received..."></textarea><br><br>
<button id="run" onclick="go()">Run Smart Match</button><div id="msg"></div></div>
<div id="out"></div>
<script>
function esc(v){{return String(v??'—').replace(/[&<>"]/g,s=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[s]))}}
function block(title,arr){{
 let h='<div class="card"><h2>'+esc(title)+' <span class="badge">'+arr.length+'</span></h2>';
 if(!arr.length)return h+'<div class="why">No candidate in this category.</div></div>';
 arr.forEach(x=>{{
   let p=x.property||{{}};
   h+='<div class="res"><div class="score">'+esc(x.score)+'% · '+esc(p.project||p.location||x.record_id)+'</div>'+
      '<div>'+esc(p.location)+' · '+esc(p.family)+' · Area '+esc(p.area)+' · ₹ '+esc(p.price)+'</div>'+
      '<div class="why">'+esc((x.why||[]).join(' · '))+'</div>'+
      (x.record_id?'<div><a href="/alliance/primary/property/'+encodeURIComponent(x.record_id)+'">Open full property →</a></div>':'')+
      '</div>';
 }});
 return h+'</div>';
}}
async function go(){{
 const q=document.getElementById('q').value.trim(); if(!q)return;
 const b=document.getElementById('run'); b.disabled=true; document.getElementById('msg').innerHTML='<div class="warn">Searching Master Property Database…</div>';
 try{{
  const r=await fetch('/api/alliance/explainable-match-v1/run',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{requirement:q}})}});
  const d=await r.json(); if(!r.ok)throw new Error(d.detail||d.message||'Match failed');
  const x=d.requirement||{{}}, z=d.results||{{}};
  let h='<div class="card"><h2>Requirement Intelligence</h2><div class="grid">'+
   '<div class="metric"><b>Project</b><br>'+esc(x.project)+'</div>'+
   '<div class="metric"><b>Location</b><br>'+esc(x.location)+'</div>'+
   '<div class="metric"><b>Exact Area</b><br>'+esc(x.area_requested)+' '+esc(x.area_unit)+'</div>'+
   '<div class="metric"><b>Transaction</b><br>'+esc(x.transaction)+' · '+esc(x.transaction_confidence)+'</div>'+
   '<div class="metric"><b>Property Family</b><br>'+esc(x.property_family)+'</div>'+
   '<div class="metric"><b>Budget Max</b><br>₹ '+esc(x.budget_max)+'</div></div>'+
   '<div class="ok">Searched '+esc(d.master_rows_considered)+' master rows. Missing property fields are treated as verification needs, not contradictions.</div></div>';
  h+=block('A. Exact / Verify',z.exact_or_verify||[]);
  h+=block('B. Strong Matches',z.strong||[]);
  h+=block('C. Approved Alternatives',z.alternatives||[]);
  document.getElementById('out').innerHTML=h; document.getElementById('msg').innerHTML='';
 }}catch(e){{document.getElementById('msg').innerHTML='<div class="warn">'+esc(e.message)+'</div>';}}
 finally{{b.disabled=false}}
}}
</script></div></body></html>"""

def register(core):
    app = _app(core)
    eng = _engine(core)
    existing = {getattr(r, "path", None) for r in app.router.routes}

    if "/alliance/primary/smart-match" not in existing:
        @app.get("/alliance/primary/smart-match")
        def smart_match_page(request: Request):
            try:
                _need_login(core, request)
            except Exception:
                return JSONResponse({"detail":"Login required"}, status_code=401)
            return HTMLResponse(_page(), headers={"Cache-Control":"no-store"})

    if "/api/alliance/explainable-match-v1/run" not in existing:
        @app.post("/api/alliance/explainable-match-v1/run")
        async def smart_match_run(request: Request):
            try:
                _need_login(core, request)
            except Exception:
                return JSONResponse({"detail":"Login required"}, status_code=401)
            if eng is None:
                return JSONResponse({"detail":"Database engine unavailable"}, status_code=503)
            try:
                payload = await request.json()
                raw = str(payload.get("requirement") or "").strip()
                if not raw:
                    return JSONResponse({"detail":"Requirement is required"}, status_code=400)
                return JSONResponse(run_match(eng, raw, limit=30))
            except Exception as exc:
                STATE["status"] = "ERROR"
                STATE["last_error"] = f"{type(exc).__name__}: {exc}"
                return JSONResponse({"detail":"Smart Match failed safely","error_type":type(exc).__name__}, status_code=500)

    if "/api/alliance/explainable-match-v1/public-status" not in existing:
        @app.get("/api/alliance/explainable-match-v1/public-status")
        def public_status():
            return {
                "status": "READY" if eng is not None else "NO_ENGINE",
                "version": VERSION,
                "marker": MARKER,
                "source_contract": SOURCE_CONTRACT,
                "master_table": MASTER_TABLE,
                "unknown_is_not_conflict": True,
                "exact_area_preserved": True,
                "preference_branches_preserved": True,
                "inventory_role_firewall": "PROPERTY_SUPPLY_ONLY",
                "inventory_role_firewall_version": _inventory_firewall.VERSION,
                "contacts_exposed": False,
                "automatic_send": False,
                "data_exposed": False,
            }

    STATE["status"] = "READY" if eng is not None else "NO_ENGINE"
    return dict(STATE)
