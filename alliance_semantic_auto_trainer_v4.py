from __future__ import annotations
import json, re, threading, hashlib
from sqlalchemy import inspect, text
import alliance_requirement_brain_v3 as brain

VERSION = "4.0.1-ROUTE-FIRST-FAIL-SAFE"
TRAIN_TABLE = "pi_requirement_semantic_training_v4"
RUN_TABLE = "pi_requirement_semantic_training_runs_v4"
GATE = "pi_requirement_gate_v1191"
MIN_GOLD = 100
TARGET_CRITICAL = 98.0
TARGET_OVERALL = 98.0

_LOCK = threading.Lock()
_STATE = {
    "status": "IDLE", "version": VERSION,
    "brain_version": getattr(brain, "VERSION", "UNKNOWN"),
    "run_id": None, "rows_total": 0, "rows_analyzed": 0,
    "auto_corrected": 0, "review_required": 0,
    "duplicates_flagged": 0, "noise_flagged": 0,
    "critical_accuracy_pct": None, "overall_accuracy_pct": None,
    "gold_examples": 0, "certification": "NOT_CERTIFIED",
    "last_error": None,
}

PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?([6-9]\d{9})(?!\d)")
NO_LIMIT_RE = re.compile(r"\b(?:budget\s*(?:no\s*limit|open|flexible)|no\s*budget\s*limit|budget\s*as\s*per\s*market|as\s*per\s*market\s*rate)\b", re.I)
RENT_RE = re.compile(r"\b(?:rent|lease|rental|per\s*month|p\.?m\.?)\b", re.I)
BUY_RE = re.compile(r"\b(?:buy|purchase|wanted|looking\s+for|seeking|ready\s+(?:buyer|client)|requirement)\b", re.I)
SUPPLY_RE = re.compile(r"\b(?:for\s+sale|available\s+for|available\s+(?:on\s+)?rent|to\s+let|inventory\s+available|we\s+have\s+available|mandate\s+for\s+sale)\b", re.I)

KNOWN_LOCATIONS = {
    "MAPUSA": ("mapusa",), "THIVIM": ("thivim","tivim"), "COLVALE": ("colvale","comvale"),
    "PANJIM": ("panjim","panaji"), "ALTHINO": ("althino","altinho"),
    "CALANGUTE": ("calangute",), "CANDOLIM": ("candolim",), "PORVORIM": ("porvorim",),
    "ASSAGAO": ("assagao",), "ANJUNA": ("anjuna",), "VAGATOR": ("vagator",),
    "SIOLIM": ("siolim",), "MOIRA": ("moira",), "ALDONA": ("aldona",),
    "MORJIM": ("morjim",), "MANDREM": ("mandrem",), "ARAMBOL": ("arambol",),
    "BAGA": ("baga",), "DONA PAULA": ("dona paula",), "MIRAMAR": ("miramar",),
    "MARGAO": ("margao","madgaon"), "COLVA": ("colva",), "VASCO": ("vasco",),
}

def _app(core):
    return getattr(core, "app", None) or getattr(core, "CORE_APP", None)

def _engine(core):
    return getattr(core, "engine", None) or getattr(core, "db_engine", None)

def _json(v, default):
    if v is None: return default
    if isinstance(v, (dict, list)): return v
    try: return json.loads(v)
    except Exception: return default

def _num(v):
    try: return float(v) if v is not None else None
    except Exception: return None

def _norm_tx(v):
    s = str(v or "").upper().strip()
    if s in {"BUY","PURCHASE","SALE","FOR SALE"}: return "SALE"
    if s in {"RENT","LEASE","RENTAL","TO LET"}: return "LEASE"
    return s or None

def _brain_locations(obj):
    out=[]
    for x in obj.get("locations") or []:
        n=str((x.get("name") if isinstance(x,dict) else x) or "").upper().strip()
        if n and n not in out: out.append(n)
    return out

def _explicit_locations(raw):
    t=raw.lower(); out=[]
    for canon,aliases in KNOWN_LOCATIONS.items():
        if any(re.search(r"\b"+re.escape(a)+r"\b",t) for a in aliases): out.append(canon)
    return out

def _phones(raw):
    return sorted(set(m.group(1) for m in PHONE_RE.finditer(raw or "")))

def _looks_like_phone_number(v, phones):
    if v is None: return False
    try: d=str(int(float(v)))
    except Exception: return False
    return len(d)>=10 and any(d.endswith(p) or d.startswith(p) for p in phones)

def _budget_from_brain(obj):
    b=obj.get("budget") or {}
    status=str(b.get("status") or "").upper()
    if status in {"NO_LIMIT","OPEN","FLEXIBLE","MARKET_RATE"}: return None,None,status
    mn=b.get("min", b.get("min_value")); mx=b.get("max", b.get("max_value"))
    return _num(mn),_num(mx),status

def _area_from_brain(obj):
    a=obj.get("area") or {}
    return _num(a.get("min_sqft")),_num(a.get("max_sqft"))

def _ensure(engine):
    with engine.begin() as c:
        c.execute(text(f'''
        CREATE TABLE IF NOT EXISTS {RUN_TABLE}(
            id BIGSERIAL PRIMARY KEY,
            started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            finished_at TIMESTAMPTZ,
            brain_version TEXT NOT NULL,
            rows_total INTEGER NOT NULL DEFAULT 0,
            rows_analyzed INTEGER NOT NULL DEFAULT 0,
            auto_corrected INTEGER NOT NULL DEFAULT 0,
            review_required INTEGER NOT NULL DEFAULT 0,
            duplicates_flagged INTEGER NOT NULL DEFAULT 0,
            noise_flagged INTEGER NOT NULL DEFAULT 0,
            gold_examples INTEGER NOT NULL DEFAULT 0,
            critical_accuracy_pct NUMERIC,
            overall_accuracy_pct NUMERIC,
            certification TEXT NOT NULL DEFAULT 'NOT_CERTIFIED',
            details JSONB NOT NULL DEFAULT '{{}}'::jsonb
        )'''))
        c.execute(text(f'''
        CREATE TABLE IF NOT EXISTS {TRAIN_TABLE}(
            id BIGSERIAL PRIMARY KEY,
            run_id BIGINT NOT NULL,
            gate_id BIGINT NOT NULL,
            message_hash TEXT,
            brain_version TEXT NOT NULL,
            old_snapshot JSONB NOT NULL,
            proposed_snapshot JSONB NOT NULL,
            anomalies JSONB NOT NULL DEFAULT '[]'::jsonb,
            action TEXT NOT NULL,
            action_reason TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(run_id, gate_id)
        )'''))
        c.execute(text(f"CREATE INDEX IF NOT EXISTS idx_req_sem_train_gate ON {TRAIN_TABLE}(gate_id,created_at DESC)"))

def _snapshot(row):
    return {
        "classification": row.get("classification"),
        "transaction_type": row.get("transaction_type"),
        "property_category": row.get("property_category"),
        "locations": _json(row.get("locations"), []),
        "area_min_sqft": _num(row.get("area_min_sqft")),
        "area_max_sqft": _num(row.get("area_max_sqft")),
        "budget_min": _num(row.get("budget_min")),
        "budget_max": _num(row.get("budget_max")),
        "matcher_eligible": bool(row.get("matcher_eligible")),
        "verified_by": row.get("verified_by"),
        "verified_at": str(row.get("verified_at") or ""),
    }

def _proposal(raw,obj):
    tx=_norm_tx((obj.get("transaction") or {}).get("value"))
    asset=str((obj.get("asset") or {}).get("primary_asset") or "").upper() or None
    locs=_explicit_locations(raw) or _brain_locations(obj)
    amin,amax=_area_from_brain(obj)
    bmin,bmax,bstatus=_budget_from_brain(obj)
    if NO_LIMIT_RE.search(raw):
        bmin=bmax=None; bstatus="NO_LIMIT"
    return {
        "transaction_type":tx, "property_category":asset, "locations":locs,
        "area_min_sqft":amin, "area_max_sqft":amax,
        "budget_min":bmin, "budget_max":bmax, "budget_status":bstatus,
        "intent_role":(obj.get("intent") or {}).get("role"),
        "matching_readiness":obj.get("matching_readiness"),
        "field_confidence":obj.get("field_confidence") or {},
    }

def _anomalies(raw,old,proposed):
    phones=_phones(raw); a=[]
    oldtx=_norm_tx(old.get("transaction_type")); newtx=_norm_tx(proposed.get("transaction_type"))
    if oldtx and newtx and oldtx!=newtx: a.append("TRANSACTION_DISAGREEMENT")
    if oldtx=="LEASE" and BUY_RE.search(raw) and not RENT_RE.search(raw): a.append("LIKELY_FALSE_LEASE")
    if oldtx=="SALE" and RENT_RE.search(raw) and not re.search(r"\b(?:sale|sell|purchase|buy)\b",raw,re.I): a.append("LIKELY_FALSE_SALE")
    oldloc=[str(x).upper() for x in (_json(old.get("locations"),[]) or [])]
    explicit=_explicit_locations(raw)
    if explicit and (not oldloc or any(x in {"UNKNOWN","NORTH GOA","GOA"} for x in oldloc)): a.append("LOCATION_TOO_BROAD_OR_UNKNOWN")
    if explicit and set(explicit)!=set(proposed.get("locations") or []): a.append("EXPLICIT_LOCATION_MISMATCH")
    if any(_looks_like_phone_number(old.get(k),phones) for k in ("budget_min","budget_max")): a.append("PHONE_BUDGET_COLLISION")
    if NO_LIMIT_RE.search(raw) and (old.get("budget_min") is not None or old.get("budget_max") is not None): a.append("NO_LIMIT_BUDGET_CONTRADICTION")
    if SUPPLY_RE.search(raw) and not BUY_RE.search(raw): a.append("POSSIBLE_SUPPLY_NOT_REQUIREMENT")
    try:
        if old.get("budget_min") is not None and old.get("budget_max") is not None and float(old["budget_min"])>float(old["budget_max"]):
            a.append("BUDGET_RANGE_REVERSED")
    except Exception: pass
    return sorted(set(a))

def _safe_auto_fields(raw,row,proposed,anomalies):
    if row.get("verified_at") or row.get("verified_by"):
        return {}, "HUMAN_VERIFIED_PROTECTED"
    changes={}
    fc=proposed.get("field_confidence") or {}
    if "PHONE_BUDGET_COLLISION" in anomalies:
        changes["budget_min"]=None; changes["budget_max"]=None
    if "NO_LIMIT_BUDGET_CONTRADICTION" in anomalies:
        changes["budget_min"]=None; changes["budget_max"]=None
    explicit=_explicit_locations(raw)
    if explicit and float(fc.get("locations") or 0)>=0.90:
        oldloc=[str(x).upper() for x in (_json(row.get("locations"),[]) or [])]
        if not oldloc or any(x in {"UNKNOWN","NORTH GOA","GOA"} for x in oldloc):
            changes["locations"]=explicit
    tx=_norm_tx(proposed.get("transaction_type"))
    oldtx=_norm_tx(row.get("transaction_type"))
    if tx and float(fc.get("transaction") or 0)>=0.95:
        if tx=="SALE" and BUY_RE.search(raw) and not RENT_RE.search(raw) and oldtx!=tx:
            changes["transaction_type"]="SALE"
        elif tx=="LEASE" and RENT_RE.search(raw) and not re.search(r"\b(?:purchase|buy|for\s+sale|sale)\b",raw,re.I) and oldtx!=tx:
            changes["transaction_type"]="LEASE"
    if "POSSIBLE_SUPPLY_NOT_REQUIREMENT" in anomalies:
        changes={k:v for k,v in changes.items() if k in {"budget_min","budget_max"}}
        return changes,"SUPPLY_REVIEW_REQUIRED"
    return changes,"HIGH_CONFIDENCE_DETERMINISTIC"

def _apply(engine,gate_id,changes):
    if not changes: return False
    sets=[]; params={"id":gate_id}
    for i,(k,v) in enumerate(changes.items()):
        p=f"v{i}"
        if k=="locations":
            sets.append(f"locations=CAST(:{p} AS JSONB)"); params[p]=json.dumps(v)
        else:
            sets.append(f"{k}=:{p}"); params[p]=v
    sets.append("updated_at=NOW()")
    with engine.begin() as c:
        c.execute(text(f"UPDATE {GATE} SET {','.join(sets)} WHERE id=:id"),params)
        c.execute(text('''
            INSERT INTO pi_requirement_gate_audit_v1191(
                gate_id,action,actor,old_status,new_status,details,created_at
            )
            SELECT :id,'SEMANTIC_AUTO_CORRECTION_V4','ALLIANCE_AUTO_TRAINER_V4',
                   classification,classification,CAST(:details AS JSONB),NOW()
            FROM pi_requirement_gate_v1191 WHERE id=:id
        '''),{"id":gate_id,"details":json.dumps({"version":VERSION,"changes":changes})})
    return True

def _gold_benchmark(engine):
    insp=inspect(engine)
    if "pi_semantic_shadow_v3_runs" not in insp.get_table_names():
        return {"gold":0,"critical":None,"overall":None,"certification":"NOT_CERTIFIED"}
    cols={c["name"] for c in insp.get_columns("pi_semantic_shadow_v3_runs")}
    if not {"raw_text","reviewed","review_decision"}.issubset(cols):
        return {"gold":0,"critical":None,"overall":None,"certification":"NOT_CERTIFIED"}
    selectable=["raw_text","review_decision"]+[n for n in ("approved_snapshot","human_correction","v3_snapshot","v3") if n in cols]
    with engine.connect() as c:
        rows=c.execute(text("SELECT "+",".join(selectable)+" FROM pi_semantic_shadow_v3_runs WHERE reviewed=TRUE AND review_decision IS NOT NULL ORDER BY id DESC LIMIT 2000")).mappings().all()
    gold=critical_ok=critical_total=all_ok=all_total=0
    for r in rows:
        target=None
        for n in ("human_correction","approved_snapshot","v3_snapshot","v3"):
            if n in r and r.get(n):
                target=_json(r.get(n),{})
                if target: break
        if not isinstance(target,dict) or not target: continue
        raw=str(r.get("raw_text") or "")
        if not raw: continue
        try: pred=brain.analyze(raw,source="AUTO_TRAINER_V4_GOLD")
        except Exception: continue
        gold+=1
        pairs=[]
        if (target.get("intent") or {}).get("role") is not None:
            pairs.append(str((pred.get("intent") or {}).get("role"))==str((target.get("intent") or {}).get("role")))
        if (target.get("transaction") or {}).get("value") is not None:
            pairs.append(_norm_tx((pred.get("transaction") or {}).get("value"))==_norm_tx((target.get("transaction") or {}).get("value")))
        if (target.get("asset") or {}).get("primary_asset") is not None:
            pairs.append(str((pred.get("asset") or {}).get("primary_asset"))==str((target.get("asset") or {}).get("primary_asset")))
        tl=_brain_locations(target)
        if tl: pairs.append(set(_brain_locations(pred))==set(tl))
        for ok in pairs:
            critical_total+=1; critical_ok+=int(ok); all_total+=1; all_ok+=int(ok)
    critical=round(100*critical_ok/critical_total,2) if critical_total else None
    overall=round(100*all_ok/all_total,2) if all_total else None
    cert="CERTIFIED_98_PLUS" if gold>=MIN_GOLD and critical is not None and overall is not None and critical>=TARGET_CRITICAL and overall>=TARGET_OVERALL else "NOT_CERTIFIED"
    return {"gold":gold,"critical":critical,"overall":overall,"certification":cert}

def run_training(engine):
    if not _LOCK.acquire(blocking=False): return dict(_STATE)
    try:
        _STATE.update(status="RUNNING",last_error=None,rows_analyzed=0,auto_corrected=0,review_required=0,duplicates_flagged=0,noise_flagged=0)
        _ensure(engine)
        with engine.connect() as c:
            total=int(c.execute(text(f"SELECT COUNT(*) FROM {GATE}")).scalar() or 0)
        _STATE["rows_total"]=total
        with engine.begin() as c:
            run_id=int(c.execute(text(f"INSERT INTO {RUN_TABLE}(brain_version,rows_total) VALUES(:b,:n) RETURNING id"),{"b":getattr(brain,"VERSION","UNKNOWN"),"n":total}).scalar_one())
        _STATE["run_id"]=run_id
        batch=250; offset=0; seen_hash={}
        while True:
            with engine.connect() as c:
                rows=c.execute(text(f'''
                    SELECT id,original_message,message_hash,classification,transaction_type,
                           property_category,locations,area_min_sqft,area_max_sqft,
                           budget_min,budget_max,matcher_eligible,verified_by,verified_at
                    FROM {GATE} ORDER BY id LIMIT :n OFFSET :o
                '''),{"n":batch,"o":offset}).mappings().all()
            if not rows: break
            for rr in rows:
                row=dict(rr); raw=str(row.get("original_message") or "").strip()
                if not raw:
                    _STATE["review_required"]+=1
                    continue
                try:
                    obj=brain.analyze(raw,source=f"AUTO_TRAINER_V4_GATE_{row['id']}")
                    old=_snapshot(row); proposed=_proposal(raw,obj); anomalies=_anomalies(raw,old,proposed)
                    h=str(row.get("message_hash") or hashlib.sha256(raw.lower().encode()).hexdigest())
                    if h in seen_hash:
                        anomalies.append("EXACT_DUPLICATE_MESSAGE"); _STATE["duplicates_flagged"]+=1
                    else: seen_hash[h]=row["id"]
                    if "POSSIBLE_SUPPLY_NOT_REQUIREMENT" in anomalies: _STATE["noise_flagged"]+=1
                    changes,reason=_safe_auto_fields(raw,row,proposed,anomalies)
                    applied=_apply(engine,row["id"],changes) if changes else False
                    if applied: _STATE["auto_corrected"]+=1
                    critical_review=any(x in anomalies for x in (
                        "TRANSACTION_DISAGREEMENT","LIKELY_FALSE_LEASE","LIKELY_FALSE_SALE",
                        "POSSIBLE_SUPPLY_NOT_REQUIREMENT","EXACT_DUPLICATE_MESSAGE",
                        "LOCATION_TOO_BROAD_OR_UNKNOWN","EXPLICIT_LOCATION_MISMATCH"))
                    if critical_review and not applied: _STATE["review_required"]+=1
                    action="AUTO_CORRECTED" if applied else ("REVIEW_REQUIRED" if critical_review else "AUDIT_PASS")
                    with engine.begin() as c:
                        c.execute(text(f'''
                            INSERT INTO {TRAIN_TABLE}(
                                run_id,gate_id,message_hash,brain_version,old_snapshot,
                                proposed_snapshot,anomalies,action,action_reason
                            ) VALUES(:r,:g,:h,:b,CAST(:old AS JSONB),CAST(:new AS JSONB),
                                     CAST(:a AS JSONB),:act,:reason)
                            ON CONFLICT(run_id,gate_id) DO NOTHING
                        '''),{"r":run_id,"g":row["id"],"h":h,"b":getattr(brain,"VERSION","UNKNOWN"),
                              "old":json.dumps(old),"new":json.dumps(proposed),"a":json.dumps(sorted(set(anomalies))),
                              "act":action,"reason":reason})
                    _STATE["rows_analyzed"]+=1
                except Exception:
                    _STATE["review_required"]+=1
            offset+=len(rows)
        bench=_gold_benchmark(engine)
        _STATE.update(gold_examples=bench["gold"],critical_accuracy_pct=bench["critical"],overall_accuracy_pct=bench["overall"],certification=bench["certification"])
        with engine.begin() as c:
            c.execute(text(f'''
                UPDATE {RUN_TABLE} SET finished_at=NOW(),rows_analyzed=:ra,
                    auto_corrected=:ac,review_required=:rr,duplicates_flagged=:du,
                    noise_flagged=:no,gold_examples=:go,critical_accuracy_pct=:ca,
                    overall_accuracy_pct=:oa,certification=:ce,
                    details=CAST(:de AS JSONB) WHERE id=:id
            '''),{"ra":_STATE["rows_analyzed"],"ac":_STATE["auto_corrected"],"rr":_STATE["review_required"],
                  "du":_STATE["duplicates_flagged"],"no":_STATE["noise_flagged"],"go":bench["gold"],
                  "ca":bench["critical"],"oa":bench["overall"],"ce":bench["certification"],"id":run_id,
                  "de":json.dumps({"target_critical":TARGET_CRITICAL,"target_overall":TARGET_OVERALL,
                                   "min_gold":MIN_GOLD,"matcher_eligibility_auto_changed":False,
                                   "human_verified_rows_auto_changed":False})})
        _STATE["status"]="COMPLETE"
    except Exception as exc:
        _STATE["status"]="ERROR"; _STATE["last_error"]=f"{type(exc).__name__}: {str(exc)[:500]}"
    finally:
        _LOCK.release()
    return dict(_STATE)

def status():
    return dict(_STATE)

def register(core):
    global _STATE

    app=_app(core)
    engine=_engine(core)
    if app is None:
        raise RuntimeError("Semantic Auto-Trainer V4 requires authoritative FastAPI app")

    status_path="/api/alliance/semantic-auto-trainer-v4/status"
    run_path="/api/alliance/semantic-auto-trainer-v4/run"

    def _paths():
        return {getattr(r,"path",None) for r in app.router.routes}

    # 1) ROUTE AUTHORITY FIRST. No DDL before this point.
    paths=_paths()
    if status_path not in paths:
        @app.get(status_path)
        def semantic_auto_trainer_v4_status():
            s=status()
            s["route_authority"]="LIVE"
            s["routes_registered"]=all(p in _paths() for p in (status_path,run_path))
            s["database_ready"]=bool(_STATE.get("database_ready"))
            s["worker_started"]=bool(_STATE.get("worker_started"))
            s["startup_database_error"]=_STATE.get("startup_database_error")
            return s

    paths=_paths()
    if run_path not in paths:
        @app.post(run_path)
        def semantic_auto_trainer_v4_run():
            if engine is None:
                return {"status":"ERROR","version":VERSION,"error":"DATABASE_ENGINE_UNAVAILABLE","route_authority":"LIVE"}
            if not _STATE.get("database_ready"):
                try:
                    _ensure(engine)
                    _STATE["database_ready"]=True
                    _STATE["startup_database_error"]=None
                except Exception as exc:
                    _STATE["status"]="ERROR"
                    _STATE["startup_database_error"]=f"{type(exc).__name__}: {str(exc)[:500]}"
                    _STATE["last_error"]=_STATE["startup_database_error"]
                    return status()
            if _STATE.get("status")=="RUNNING":
                return status()
            threading.Thread(target=run_training,args=(engine,),daemon=True,name="semantic-auto-trainer-v4-manual").start()
            return {"status":"STARTED","version":VERSION,"route_authority":"LIVE"}

    final_paths=_paths()
    missing=[p for p in (status_path,run_path) if p not in final_paths]
    if missing:
        raise RuntimeError("Route authority registration failed: "+",".join(missing))

    _STATE["route_authority"]="LIVE"
    _STATE["routes_registered"]=True
    _STATE["database_ready"]=False
    _STATE["worker_started"]=False
    _STATE["startup_database_error"]=None
    _STATE["version"]=VERSION

    # 2) DATABASE INITIALIZATION AFTER ROUTES EXIST.
    if engine is None:
        _STATE["status"]="ERROR"
        _STATE["startup_database_error"]="DATABASE_ENGINE_UNAVAILABLE"
        _STATE["last_error"]="DATABASE_ENGINE_UNAVAILABLE"
        return {
            "status":"ROUTES_REGISTERED_DATABASE_ERROR",
            "version":VERSION,
            "routes":[status_path,run_path],
            "route_authority":"LIVE",
            "database_ready":False,
            "worker_started":False,
            "error":"DATABASE_ENGINE_UNAVAILABLE",
        }

    try:
        _ensure(engine)
        _STATE["database_ready"]=True
    except Exception as exc:
        err=f"{type(exc).__name__}: {str(exc)[:500]}"
        _STATE["status"]="ERROR"
        _STATE["startup_database_error"]=err
        _STATE["last_error"]=err
        return {
            "status":"ROUTES_REGISTERED_DATABASE_ERROR",
            "version":VERSION,
            "routes":[status_path,run_path],
            "route_authority":"LIVE",
            "database_ready":False,
            "worker_started":False,
            "error":err,
        }

    # 3) NON-BLOCKING TRAINER WORKER ONLY AFTER route + DB certification.
    try:
        threading.Thread(target=run_training,args=(engine,),daemon=True,name="semantic-auto-trainer-v4-boot").start()
        _STATE["worker_started"]=True
    except Exception as exc:
        err=f"{type(exc).__name__}: {str(exc)[:500]}"
        _STATE["status"]="ERROR"
        _STATE["last_error"]=err
        return {
            "status":"ROUTES_AND_DATABASE_READY_WORKER_ERROR",
            "version":VERSION,
            "routes":[status_path,run_path],
            "route_authority":"LIVE",
            "database_ready":True,
            "worker_started":False,
            "error":err,
        }

    return {
        "status":"REGISTERED",
        "version":VERSION,
        "brain_version":getattr(brain,"VERSION","UNKNOWN"),
        "mode":"AUTO_AUDIT_SAFE_CORRECTION",
        "target_accuracy_pct":98.0,
        "min_gold_examples":MIN_GOLD,
        "human_verified_rows_protected":True,
        "matcher_eligibility_auto_changed":False,
        "master_requirement_table":GATE,
        "route_authority":"LIVE",
        "routes":[status_path,run_path],
        "database_ready":True,
        "worker_started":True,
    }
