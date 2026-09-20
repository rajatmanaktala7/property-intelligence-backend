from __future__ import annotations
import json,re
from datetime import datetime,timezone
from sqlalchemy import text

VERSION="1.1.0-CANONICAL-DATABASE-HYGIENE-LINK-REPAIR"
STATE={"status":"NOT_RUN","result":None,"error":None}

BAD_EXACT={"tara","royal construction","royal constructions"}
BAD_WORDS=re.compile(r"\b(construction|constructions|builder|builders|developer|developers|realty|properties|infra|infrastructure|owner|broker|dealer)\b",re.I)
PHONEISH=re.compile(r"(?:\+?91[-\s]?)?[6-9]\d{9}")
EMAILISH=re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")

def _dict(v):
    if isinstance(v,dict): return v
    if isinstance(v,str):
        try:
            x=json.loads(v); return x if isinstance(x,dict) else {}
        except Exception:return {}
    return {}

def _flatten(v):
    d=_dict(v).copy()
    for k in ("manual_operational","magazine","newspaper","whatsapp","source_record","raw_record"):
        x=d.get(k)
        if isinstance(x,dict):
            for a,b in x.items():
                if d.get(a) in (None,"",[],{}): d[a]=b
    return d

def _norm(v): return " ".join(str(v or "").split()).strip()

def _bad_location(v,cr):
    s=_norm(v); lo=s.lower()
    if not s:return True
    if lo in BAD_EXACT:return True
    if PHONEISH.search(s) or EMAILISH.search(s):return True
    if BAD_WORDS.search(s):return True
    for k in ("contact_name","owner_name","broker_name","owner_broker_name","company_name","sender_name"):
        x=_norm(cr.get(k))
        if x and len(x)>=3 and x.lower()==lo:return True
    return False

def _recover(cr,current=""):
    for k in ("micro_market","area_name","locality","location","address","exact_address","property_address","city"):
        x=_norm(cr.get(k))
        if x and x.lower()!=_norm(current).lower() and not _bad_location(x,{}):
            return x
    return None

def audit(engine):
    out={}
    with engine.connect() as c:
        out["master_properties"]=int(c.execute(text("SELECT COUNT(*) FROM pi_master_properties_v711")).scalar() or 0)
        out["source_links"]=int(c.execute(text("SELECT COUNT(*) FROM pi_master_source_links_v711 WHERE master_entity_type='PROPERTY'")).scalar() or 0)
        out["master_without_source_link"]=int(c.execute(text("""SELECT COUNT(*) FROM pi_master_properties_v711 p
          WHERE NOT EXISTS(SELECT 1 FROM pi_master_source_links_v711 l WHERE l.master_entity_type='PROPERTY' AND l.canonical_id=p.canonical_id)""")).scalar() or 0)
        out["orphan_property_links"]=int(c.execute(text("""SELECT COUNT(*) FROM pi_master_source_links_v711 l
          WHERE l.master_entity_type='PROPERTY' AND NOT EXISTS(SELECT 1 FROM pi_master_properties_v711 p WHERE p.canonical_id=l.canonical_id)""")).scalar() or 0)
        out["manual_source_rows"]=int(c.execute(text("""SELECT COUNT(*) FROM pi_operational_properties
          WHERE COALESCE(entry_source,'MANUAL')='MANUAL'""")).scalar() or 0)
        out["unbridged_manual"]=int(c.execute(text("""SELECT COUNT(*) FROM pi_operational_properties o
          WHERE COALESCE(o.entry_source,'MANUAL')='MANUAL'
          AND NOT EXISTS(SELECT 1 FROM pi_master_source_links_v711 l WHERE l.master_entity_type='PROPERTY'
            AND l.source_table='pi_operational_properties' AND l.source_pk=o.property_code)""")).scalar() or 0)
        rows=c.execute(text("""SELECT canonical_id,locality,city,clean_record,promotion_status,source_type
          FROM pi_master_properties_v711""")).mappings().all()
    suspicious=[]
    for r in rows:
        cr=_flatten(r.get("clean_record"))
        if _bad_location(r.get("locality"),cr):
            suspicious.append({"canonical_id":r.get("canonical_id"),"location":r.get("locality"),
              "source_type":r.get("source_type"),"recoverable":_recover(cr,r.get("locality"))})
    out["suspicious_locations"]=len(suspicious)
    out["suspicious_sample"]=suspicious[:100]
    out["quarantined"]=sum(1 for r in rows if str(r.get("promotion_status") or "").upper()=="QUARANTINED")
    return out

def repair(engine):
    before=audit(engine)
    bridged=[]; bridge_errors=[]
    try:
        import alliance_operational_master_bridge_v12426 as bridge
        with engine.connect() as c:
            codes=[r[0] for r in c.execute(text("""SELECT o.property_code FROM pi_operational_properties o
              WHERE COALESCE(o.entry_source,'MANUAL')='MANUAL'
              AND NOT EXISTS(SELECT 1 FROM pi_master_source_links_v711 l WHERE l.master_entity_type='PROPERTY'
                AND l.source_table='pi_operational_properties' AND l.source_pk=o.property_code)
              ORDER BY o.id""")).all()]
        for code in codes:
            try:
                bridge.sync_property(engine,code,"database-hygiene")
                bridged.append(code)
            except Exception as exc:
                bridge_errors.append({"property_code":code,"error":f"{type(exc).__name__}: {exc}"})
    except Exception as exc:
        bridge_errors.append({"bridge":"bootstrap","error":f"{type(exc).__name__}: {exc}"})

    repaired=[]; quarantined=[]
    with engine.connect() as c:
        rows=c.execute(text("""SELECT canonical_id,locality,clean_record FROM pi_master_properties_v711""")).mappings().all()
    for r in rows:
        cr=_flatten(r.get("clean_record"))
        if not _bad_location(r.get("locality"),cr): continue
        alt=_recover(cr,r.get("locality"))
        with engine.begin() as c:
            if alt:
                c.execute(text("""UPDATE pi_master_properties_v711 SET locality=:loc,
                  promotion_status=CASE WHEN promotion_status='QUARANTINED' THEN 'NEEDS_VERIFICATION' ELSE promotion_status END,
                  updated_at=NOW() WHERE canonical_id=:cid"""),{"loc":alt,"cid":r["canonical_id"]})
                repaired.append({"canonical_id":r["canonical_id"],"from":r.get("locality"),"to":alt})
            else:
                c.execute(text("""UPDATE pi_master_properties_v711 SET promotion_status='QUARANTINED',updated_at=NOW()
                  WHERE canonical_id=:cid"""),{"cid":r["canonical_id"]})
                c.execute(text("""UPDATE pi_master_workflow_v720 SET verification_status='UNVERIFIED',
                  availability_status='UNKNOWN',updated_at=NOW() WHERE canonical_id=:cid"""),{"cid":r["canonical_id"]})
                quarantined.append({"canonical_id":r["canonical_id"],"location":r.get("locality")})
    # Remove orphan source-link rows only. They point to no canonical Master
    # property and can make source filters lie. Source property rows themselves
    # remain untouched.
    with engine.begin() as c:
        orphan_links_deleted=c.execute(text("""DELETE FROM pi_master_source_links_v711 l
          WHERE l.master_entity_type='PROPERTY'
          AND NOT EXISTS(SELECT 1 FROM pi_master_properties_v711 p WHERE p.canonical_id=l.canonical_id)""")).rowcount
    after=audit(engine)
    return {"version":VERSION,"bridged_manual":len(bridged),"bridge_errors":bridge_errors,
      "locations_recovered":len(repaired),"locations_quarantined":len(quarantined),
      "orphan_links_deleted":int(orphan_links_deleted or 0),
      "repaired_sample":repaired[:100],"quarantined_sample":quarantined[:100],
      "before":before,"after":after,
      "safety":"Raw/source rows are never modified; uncertain Master locations are quarantined from matcher."}

def run(engine):
    try:
        result=repair(engine)
        STATE.update(status="COMPLETE",result=result,error=None)
    except Exception as exc:
        STATE.update(status="ERROR",error=f"{type(exc).__name__}: {exc}")
    return STATE

def register(core):
    app=getattr(core,"app",None) or core; engine=core.engine
    @app.on_event("startup")
    def canonical_database_hygiene_startup():
        run(engine)
    @app.get("/api/alliance/database-hygiene")
    def database_hygiene(req):
        if hasattr(core,"need_login"): core.need_login(req)
        return {"state":STATE,"audit":audit(engine)}
    @app.post("/api/alliance/database-hygiene/run")
    def database_hygiene_run(req):
        if hasattr(core,"need_login"): core.need_login(req)
        return run(engine)
    return {"status":"REGISTERED","version":VERSION,"audit":"/api/alliance/database-hygiene"}
