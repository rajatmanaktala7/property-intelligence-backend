from __future__ import annotations
import hashlib, json, re
from sqlalchemy import text

VERSION="1.0.1-LIVE-REQUEST-JSON-IDENTITY-CAPTURE"
EVENT_TABLE="pi_whatsapp_live_identity_events_v1"
REGISTRY_TABLE="pi_whatsapp_sender_identity_registry_v1"

IDENTITY_KEYS={
    "sender_phone","phone","phone_number","sender_number","author_phone","contact_phone","mobile","pn","phone_jid",
    "sender_jid","jid","author","participant","remote_jid","sender_id","lid","sender_lid","participant_lid","author_lid",
    "group_jid","chat_jid","group_id","group_name","chat_name",
    "message_id","external_message_id","id","key","push_name","sender_name","author_name"
}

def _norm(v):
    return str(v or "").strip()

def _phone(v):
    s=_norm(v)
    if not s:
        return ""
    if "@" in s:
        local,domain=s.split("@",1)
        if domain.lower() in ("s.whatsapp.net","c.us"):
            s=local
        elif domain.lower()=="lid":
            return ""
    d=re.sub(r"\D+","",s)
    if d.startswith("00"):
        d=d[2:]
    if len(d)==12 and d.startswith("91"):
        d=d[-10:]
    if len(d)==11 and d.startswith("0"):
        d=d[-10:]
    return d if len(d)==10 and d[0] in "6789" else ""

def _lid(v):
    s=_norm(v)
    if not s:
        return ""
    local=s.split("@",1)[0]
    domain=s.split("@",1)[1].lower() if "@" in s else ""
    digits=re.sub(r"\D+","",local)
    if domain=="lid":
        return local
    return digits if len(digits)>=13 else ""

def _walk(obj,path=""):
    if isinstance(obj,dict):
        for k,v in obj.items():
            p=f"{path}.{k}" if path else str(k)
            yield p,k,v
            yield from _walk(v,p)
    elif isinstance(obj,list):
        for i,v in enumerate(obj[:100]):
            yield from _walk(v,f"{path}[{i}]")

def identity_snapshot(payload):
    out={}
    for path,key,v in _walk(payload):
        lk=str(key).lower()
        if lk in IDENTITY_KEYS or any(tok in lk for tok in ("lid","jid","participant","sender","phone","mobile","group","message_id","external_message_id")):
            if isinstance(v,(dict,list)):
                continue
            sv=_norm(v)
            if sv:
                out[path]=sv[:500]
    return out

def _extract_pairs(snapshot):
    lids=[]; phones=[]
    for path,v in snapshot.items():
        lk=path.lower()
        l=_lid(v); p=_phone(v)
        if l and ("lid" in lk or "@lid" in _norm(v).lower() or len(re.sub(r"\D+","",_norm(v)))>=13):
            lids.append((l,path))
        if p and any(x in lk for x in ("phone","mobile","pn","jid","participant","sender")):
            phones.append((p,path))
    return lids,phones

def _ensure(engine):
    with engine.begin() as c:
        c.execute(text(f"""
        CREATE TABLE IF NOT EXISTS {EVENT_TABLE}(
            event_fingerprint TEXT PRIMARY KEY,
            captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            external_message_id TEXT,
            group_ref TEXT,
            sender_lid TEXT,
            sender_phone TEXT,
            mapping_status TEXT NOT NULL,
            identity_snapshot JSONB NOT NULL,
            bridge_version TEXT NOT NULL
        )
        """))
    from alliance_whatsapp_sender_identity_registry_v1 import ensure_registry
    ensure_registry(engine)

def capture_identity_event(payload,engine):
    if not isinstance(payload,dict) or engine is None:
        return {"status":"SKIPPED"}
    snap=identity_snapshot(payload)
    if not snap:
        return {"status":"NO_IDENTITY_FIELDS"}

    lids,phones=_extract_pairs(snap)
    ul=sorted({x[0] for x in lids})
    up=sorted({x[0] for x in phones})
    if len(ul)==1 and len(up)==1:
        mapping_status="RESOLVED_UNIQUE_EXACT_EVIDENCE"; lid=ul[0]; phone=up[0]
    elif ul and up:
        mapping_status="AMBIGUOUS_CONFLICT"; lid=ul[0] if len(ul)==1 else None; phone=None
    else:
        mapping_status="IDENTITY_CAPTURED_NO_PAIR"; lid=ul[0] if len(ul)==1 else None; phone=up[0] if len(up)==1 else None

    ext=next((_norm(payload.get(k)) for k in ("external_message_id","message_id","id") if payload.get(k)), "")
    group=next((_norm(payload.get(k)) for k in ("group_jid","group_id","group_name","chat_jid","chat_name") if payload.get(k)), "")
    raw=json.dumps(snap,sort_keys=True,ensure_ascii=False,separators=(",",":"))
    fp=hashlib.sha256((ext+"|"+group+"|"+raw).encode("utf-8")).hexdigest()

    _ensure(engine)
    with engine.begin() as c:
        c.execute(text(f"""
        INSERT INTO {EVENT_TABLE}
          (event_fingerprint,external_message_id,group_ref,sender_lid,sender_phone,mapping_status,identity_snapshot,bridge_version)
        VALUES
          (:fp,:ext,:grp,:lid,:phone,:status,CAST(:snap AS JSONB),:ver)
        ON CONFLICT (event_fingerprint) DO NOTHING
        """),{"fp":fp,"ext":ext or None,"grp":group or None,"lid":lid,"phone":phone,
              "status":mapping_status,"snap":raw,"ver":VERSION})

        if mapping_status=="RESOLVED_UNIQUE_EXACT_EVIDENCE":
            c.execute(text(f"""
            INSERT INTO {REGISTRY_TABLE}
              (opaque_id,resolved_phone,resolution_status,confidence,evidence_count,conflicting_phones,source_tables,evidence_json,updated_at)
            VALUES
              (:lid,:phone,'RESOLVED_UNIQUE_EXACT_EVIDENCE',100,1,0,:src,:ev,NOW())
            ON CONFLICT (opaque_id) DO UPDATE SET
              resolved_phone=CASE
                WHEN {REGISTRY_TABLE}.resolved_phone IS NULL THEN EXCLUDED.resolved_phone
                WHEN {REGISTRY_TABLE}.resolved_phone=EXCLUDED.resolved_phone THEN EXCLUDED.resolved_phone
                ELSE NULL END,
              resolution_status=CASE
                WHEN {REGISTRY_TABLE}.resolved_phone IS NULL OR {REGISTRY_TABLE}.resolved_phone=EXCLUDED.resolved_phone
                  THEN 'RESOLVED_UNIQUE_EXACT_EVIDENCE' ELSE 'AMBIGUOUS_CONFLICT' END,
              confidence=CASE
                WHEN {REGISTRY_TABLE}.resolved_phone IS NULL OR {REGISTRY_TABLE}.resolved_phone=EXCLUDED.resolved_phone
                  THEN 100 ELSE 0 END,
              evidence_count={REGISTRY_TABLE}.evidence_count+1,
              conflicting_phones=CASE
                WHEN {REGISTRY_TABLE}.resolved_phone IS NULL OR {REGISTRY_TABLE}.resolved_phone=EXCLUDED.resolved_phone
                  THEN {REGISTRY_TABLE}.conflicting_phones ELSE GREATEST({REGISTRY_TABLE}.conflicting_phones,2) END,
              source_tables=:src,
              evidence_json=:ev,
              updated_at=NOW()
            """),{"lid":lid,"phone":phone,
                  "src":json.dumps([EVENT_TABLE]),
                  "ev":json.dumps({"method":"LIVE_INGEST_SAME_PAYLOAD","event_fingerprint":fp})})

    return {"status":"CAPTURED","mapping_status":mapping_status,"lid_present":bool(lid),"phone_present":bool(phone)}
