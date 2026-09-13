from __future__ import annotations
import json, re
from collections import defaultdict
from sqlalchemy import inspect, text

VERSION="1.0.0-UPSTREAM-WHATSAPP-IDENTITY-BRIDGE"
REGISTRY_TABLE="pi_whatsapp_sender_identity_registry_v1"
LID_HINTS=("lid","sender_lid","participant_lid","author_lid","user_lid","remote_lid")
PHONE_HINTS=("phone","phone_number","sender_phone","sender_number","author_phone","contact_phone","mobile","pn","phone_jid")
JSON_HINTS=("payload","event","data","raw","json","metadata","body","message")

def _norm(v): return str(v or "").strip()

def _phone(v):
    s=_norm(v)
    if not s: return ""
    if "@" in s:
        local,domain=s.split("@",1)
        if domain.lower() in ("s.whatsapp.net","c.us"): s=local
        elif domain.lower()=="lid": return ""
    d=re.sub(r"\D+","",s)
    if d.startswith("00"): d=d[2:]
    if len(d)==12 and d.startswith("91"): d=d[-10:]
    if len(d)==11 and d.startswith("0"): d=d[-10:]
    return d if len(d)==10 and d[0] in "6789" else ""

def _lid(v):
    s=_norm(v)
    if not s: return ""
    local=s.split("@",1)[0]
    domain=s.split("@",1)[1].lower() if "@" in s else ""
    digits=re.sub(r"\D+","",local)
    if domain=="lid": return local
    return digits if len(digits)>=13 else ""

def _tables(engine):
    ins=inspect(engine)
    names=set(ins.get_table_names())
    try: names.update(ins.get_view_names())
    except Exception: pass
    return sorted(n for n in names if str(n).lower().startswith(("wa_","wai_","pi_whatsapp")))

def _columns(engine,table):
    return [str(c["name"]) for c in inspect(engine).get_columns(table)]

def _flatten(obj,path=""):
    out=[]
    if isinstance(obj,dict):
        for k,v in obj.items():
            p=f"{path}.{k}" if path else str(k)
            out.append((p,v)); out.extend(_flatten(v,p))
    elif isinstance(obj,list):
        for i,v in enumerate(obj[:200]): out.extend(_flatten(v,f"{path}[{i}]"))
    return out

def _pairs_from_object(obj):
    lids=[]; phones=[]
    for path,v in _flatten(obj):
        key=path.lower().split(".")[-1]
        lv=_lid(v); pv=_phone(v)
        if lv and ("lid" in key or (isinstance(v,str) and "@lid" in v.lower())): lids.append((lv,path))
        if pv and any(h in key for h in ("phone","mobile","number","pn","jid")): phones.append((pv,path))
    return lids,phones

def _collect_same_row(engine,table,limit=100000):
    cols=_columns(engine,table)
    lid_cols=[c for c in cols if any(h==c.lower() or h in c.lower() for h in LID_HINTS)]
    phone_cols=[c for c in cols if any(h==c.lower() or h in c.lower() for h in PHONE_HINTS)]
    json_cols=[c for c in cols if any(h in c.lower() for h in JSON_HINTS)]
    selected=sorted(set(lid_cols+phone_cols+json_cols))
    if not selected: return [],0,0
    quoted=",".join(f'"{c}"' for c in selected)
    q=text(f'SELECT {quoted} FROM "{table}" LIMIT :lim')
    with engine.connect() as c:
        rows=[dict(r) for r in c.execute(q,{"lim":int(limit)}).mappings()]
    evidence=[]; json_objects=0
    for r in rows:
        lids=[(_lid(r.get(c)),c) for c in lid_cols if _lid(r.get(c))]
        phones=[(_phone(r.get(c)),c) for c in phone_cols if _phone(r.get(c))]
        if len({x[0] for x in lids})==1 and len({x[0] for x in phones})==1:
            evidence.append((lids[0][0],phones[0][0],f"{table}:STRUCTURED_COLUMNS"))
        for c in json_cols:
            raw=r.get(c)
            if raw in (None,""): continue
            obj=raw
            if isinstance(raw,str):
                txt=raw.strip()
                if not txt or txt[0] not in "[{": continue
                try: obj=json.loads(txt)
                except Exception: continue
            if not isinstance(obj,(dict,list)): continue
            json_objects+=1
            jl,jp=_pairs_from_object(obj)
            ul={x[0] for x in jl}; up={x[0] for x in jp}
            if len(ul)==1 and len(up)==1:
                evidence.append((next(iter(ul)),next(iter(up)),f"{table}:{c}:JSON_OBJECT"))
    return evidence,len(rows),json_objects

def run(engine):
    from alliance_whatsapp_sender_identity_registry_v1 import ensure_registry
    ensure_registry(engine)
    ev=defaultdict(lambda:{"phones":defaultdict(int),"sources":set()})
    tables=_tables(engine)
    rows_scanned=0; json_objects=0; evidence_pairs=0
    for table in tables:
        if table==REGISTRY_TABLE: continue
        try: pairs,nrows,njson=_collect_same_row(engine,table)
        except Exception: continue
        rows_scanned+=nrows; json_objects+=njson
        for lid,phone,source in pairs:
            if not lid or not phone: continue
            evidence_pairs+=1
            ev[lid]["phones"][phone]+=1
            ev[lid]["sources"].add(source)

    resolved=[]; ambiguous=[]
    for lid,e in ev.items():
        phones=dict(e["phones"])
        if len(phones)==1:
            phone=next(iter(phones))
            resolved.append((lid,phone,sum(phones.values()),sorted(e["sources"])))
        else:
            ambiguous.append((lid,phones,sorted(e["sources"])))

    upsert=text(f"INSERT INTO {REGISTRY_TABLE} (opaque_id,resolved_phone,resolution_status,confidence,evidence_count,conflicting_phones,source_tables,evidence_json,updated_at) VALUES(:opaque_id,:resolved_phone,:resolution_status,:confidence,:evidence_count,:conflicting_phones,:source_tables,:evidence_json,NOW()) ON CONFLICT (opaque_id) DO UPDATE SET resolved_phone=EXCLUDED.resolved_phone,resolution_status=EXCLUDED.resolution_status,confidence=EXCLUDED.confidence,evidence_count=EXCLUDED.evidence_count,conflicting_phones=EXCLUDED.conflicting_phones,source_tables=EXCLUDED.source_tables,evidence_json=EXCLUDED.evidence_json,updated_at=NOW()")
    with engine.begin() as c:
        for lid,phone,count,sources in resolved:
            c.execute(upsert,{"opaque_id":lid,"resolved_phone":phone,"resolution_status":"RESOLVED_UNIQUE_EXACT_EVIDENCE","confidence":100,"evidence_count":count,"conflicting_phones":0,"source_tables":json.dumps(sources),"evidence_json":json.dumps({"method":"UPSTREAM_SAME_ROW_IDENTITY","sources":sources})})
        for lid,phones,sources in ambiguous:
            c.execute(upsert,{"opaque_id":lid,"resolved_phone":None,"resolution_status":"AMBIGUOUS_CONFLICT","confidence":0,"evidence_count":sum(phones.values()),"conflicting_phones":len(phones),"source_tables":json.dumps(sources),"evidence_json":json.dumps({"method":"UPSTREAM_SAME_ROW_IDENTITY","phones":phones,"sources":sources})})

    return {"status":"PASS","version":VERSION,"tables_discovered":len(tables),"rows_scanned":rows_scanned,"json_objects_scanned":json_objects,"evidence_pairs":evidence_pairs,"resolved_unique":len(resolved),"ambiguous":len(ambiguous),"registry_rows_written":len(resolved)+len(ambiguous),"business_table_mutations":0}
