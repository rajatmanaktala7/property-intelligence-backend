from __future__ import annotations
import json,re,unicodedata,uuid
from collections import Counter
from fastapi import Query
from fastapi.responses import HTMLResponse
from sqlalchemy import text
import alliance_property_brain_foundation_v1 as foundation

VERSION="2.9.0-INFRASTRUCTURE-FIRST-GEOGRAPHY-TRANSACTION"
MODE="DETERMINISTIC_GAZETTEER_PLUS_CANONICAL_TRANSACTION_OCCUPANCY"
ENGINE_VERSION="ALLIANCE_INFRASTRUCTURE_FIRST_V29"
GAZETTEER_VERSION="ALLIANCE_GAZETTEER_DNCR_GOA_V1"
ONTOLOGY_VERSION="ALLIANCE_CANONICAL_TRANSACTION_V1"

DDL=[
"""CREATE TABLE IF NOT EXISTS alliance_geography_gazetteer_v29(
place_id UUID PRIMARY KEY,canonical_name TEXT NOT NULL,place_type TEXT NOT NULL,
city TEXT,state TEXT,country TEXT NOT NULL DEFAULT 'India',market TEXT,micro_market TEXT,
approved BOOLEAN NOT NULL DEFAULT TRUE,confidence NUMERIC(5,2) NOT NULL DEFAULT 100,
version TEXT NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
UNIQUE(canonical_name,place_type,version))""",
"""CREATE TABLE IF NOT EXISTS alliance_geography_alias_v29(
alias_id UUID PRIMARY KEY,place_id UUID NOT NULL REFERENCES alliance_geography_gazetteer_v29(place_id),
alias TEXT NOT NULL,alias_norm TEXT NOT NULL,approved BOOLEAN NOT NULL DEFAULT TRUE,
source TEXT NOT NULL DEFAULT 'SEED',version TEXT NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
UNIQUE(alias_norm,version))""",
"""CREATE TABLE IF NOT EXISTS alliance_geography_candidate_v29(
candidate_id UUID PRIMARY KEY,literal_location TEXT NOT NULL,literal_norm TEXT NOT NULL,
seen_count INTEGER NOT NULL DEFAULT 1,sample_entity_id TEXT,sample_message_id TEXT,
status TEXT NOT NULL DEFAULT 'NEEDS_REVIEW',version TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
UNIQUE(literal_norm,version))""",
"""CREATE TABLE IF NOT EXISTS alliance_ontology_enum_v29(
dimension TEXT NOT NULL,value TEXT NOT NULL,active BOOLEAN NOT NULL DEFAULT TRUE,
description TEXT,version TEXT NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
PRIMARY KEY(dimension,value,version))""",
"""CREATE TABLE IF NOT EXISTS alliance_infrastructure_resolution_v29(
resolution_id UUID PRIMARY KEY,entity_id TEXT NOT NULL UNIQUE,message_id TEXT,
literal_location JSONB NOT NULL DEFAULT '[]'::jsonb,
normalized_geography JSONB NOT NULL DEFAULT '{}'::jsonb,
canonical_transaction JSONB NOT NULL DEFAULT '{}'::jsonb,
field_provenance JSONB NOT NULL DEFAULT '{}'::jsonb,
review_flags JSONB NOT NULL DEFAULT '[]'::jsonb,
resolution_score NUMERIC(6,2),engine_version TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now(),updated_at TIMESTAMPTZ NOT NULL DEFAULT now())"""
]

PLACES=[
("Greater Kailash 1","LOCALITY","Delhi","Delhi","DELHI_NCR","South Delhi",["gk-1","gk 1","g.k.-1","g.k. 1","greater kailash 1","greater kailash-i","greater kailash i"]),
("Greater Kailash 2","LOCALITY","Delhi","Delhi","DELHI_NCR","South Delhi",["gk-2","gk 2","g.k.-2","g.k. 2","greater kailash 2","greater kailash-ii","greater kailash ii"]),
("Saket","LOCALITY","Delhi","Delhi","DELHI_NCR","South Delhi",["saket"]),
("Vasant Kunj","LOCALITY","Delhi","Delhi","DELHI_NCR","South Delhi",["vasant kunj"]),
("Vasant Vihar","LOCALITY","Delhi","Delhi","DELHI_NCR","South Delhi",["vasant vihar"]),
("Defence Colony","LOCALITY","Delhi","Delhi","DELHI_NCR","South Delhi",["defence colony","def colony"]),
("Hauz Khas","LOCALITY","Delhi","Delhi","DELHI_NCR","South Delhi",["hauz khas"]),
("Green Park","LOCALITY","Delhi","Delhi","DELHI_NCR","South Delhi",["green park"]),
("South Extension","LOCALITY","Delhi","Delhi","DELHI_NCR","South Delhi",["south extension","south ex","south ex-1","south ex 1","south ex-2","south ex 2"]),
("Jangpura Extension","LOCALITY","Delhi","Delhi","DELHI_NCR","South Delhi",["jangpura extension","jangpura extn","jangpura ext"]),
("Paschim Vihar","LOCALITY","Delhi","Delhi","DELHI_NCR","West Delhi",["paschim vihar"]),
("Punjabi Bagh","LOCALITY","Delhi","Delhi","DELHI_NCR","West Delhi",["punjabi bagh"]),
("Rajouri Garden","LOCALITY","Delhi","Delhi","DELHI_NCR","West Delhi",["rajouri garden"]),
("Dwarka","LOCALITY","Delhi","Delhi","DELHI_NCR","South West Delhi",["dwarka"]),
("Pushpanjali","LOCALITY","Delhi","Delhi","DELHI_NCR","South West Delhi",["pushpanjali","pushpanjali delhi"]),
("Gurugram","CITY","Gurugram","Haryana","DELHI_NCR",None,["gurugram","gurgaon","ggn"]),
("DLF Phase 1","LOCALITY","Gurugram","Haryana","DELHI_NCR","Golf Course Road",["dlf phase 1","dlf phase-i","dlf phase i"]),
("DLF Phase 2","LOCALITY","Gurugram","Haryana","DELHI_NCR","MG Road",["dlf phase 2","dlf phase-ii","dlf phase ii"]),
("DLF Phase 4","LOCALITY","Gurugram","Haryana","DELHI_NCR","Golf Course Road",["dlf phase 4","dlf phase-iv","dlf phase iv"]),
("Golf Course Road","MICRO_MARKET","Gurugram","Haryana","DELHI_NCR","Golf Course Road",["golf course road","gcr"]),
("Golf Course Extension Road","MICRO_MARKET","Gurugram","Haryana","DELHI_NCR","Golf Course Extension",["golf course extension road","golf course ext road","gcx"]),
("Noida","CITY","Noida","Uttar Pradesh","DELHI_NCR",None,["noida"]),
("Greater Noida","CITY","Greater Noida","Uttar Pradesh","DELHI_NCR",None,["greater noida"]),
("Faridabad","CITY","Faridabad","Haryana","DELHI_NCR",None,["faridabad"]),
("Ghaziabad","CITY","Ghaziabad","Uttar Pradesh","DELHI_NCR",None,["ghaziabad"]),
("Siolim","LOCALITY",None,"Goa","GOA","North Goa",["siolim"]),
("Assagao","LOCALITY",None,"Goa","GOA","North Goa",["assagao","assagaon"]),
("Anjuna","LOCALITY",None,"Goa","GOA","North Goa",["anjuna"]),
("Vagator","LOCALITY",None,"Goa","GOA","North Goa",["vagator"]),
("Morjim","LOCALITY",None,"Goa","GOA","North Goa",["morjim"]),
("Parra","LOCALITY",None,"Goa","GOA","North Goa",["parra"]),
("Mapusa","LOCALITY",None,"Goa","GOA","North Goa",["mapusa"]),
("Porvorim","LOCALITY",None,"Goa","GOA","North Goa",["porvorim"]),
("Candolim","LOCALITY",None,"Goa","GOA","North Goa",["candolim"]),
("Calangute","LOCALITY",None,"Goa","GOA","North Goa",["calangute"]),
("Arpora","LOCALITY",None,"Goa","GOA","North Goa",["arpora"]),
("Saligao","LOCALITY",None,"Goa","GOA","North Goa",["saligao"]),
("Reis Magos","LOCALITY",None,"Goa","GOA","North Goa",["reis magos","reis-magos"]),
("Caranzalem","LOCALITY",None,"Goa","GOA","Tiswadi",["caranzalem"]),
("Dona Paula","LOCALITY",None,"Goa","GOA","Tiswadi",["dona paula","dona-paula"]),
("Taleigao","LOCALITY",None,"Goa","GOA","Tiswadi",["taleigao"]),
("Santa Cruz","LOCALITY",None,"Goa","GOA","Tiswadi",["st cruz","st. cruz","santa cruz"]),
("Thivim","LOCALITY",None,"Goa","GOA","North Goa",["thivim","tivim"]),
("Cansa","LOCALITY",None,"Goa","GOA","North Goa",["cansa"]),
("Dhargal","LOCALITY",None,"Goa","GOA","North Goa",["dhargal"]),
("Querim","LOCALITY",None,"Goa","GOA","North Goa",["querim","keri","querim beach"]),
]

ONTOLOGY={
"transaction_type":["SALE","RENT","LEASE","REQUIREMENT_PURCHASE","REQUIREMENT_LEASE","BUSINESS_TRANSFER","REVENUE_SHARE","PARTNERSHIP"],
"occupancy_status":["VACANT","TENANTED","OWNER_OCCUPIED","UNKNOWN"],
"investment_status":["INCOME_PRODUCING","NON_INCOME","UNKNOWN"]
}

def _engine(c):return foundation._engine_from_core(c)
def _app(c):return getattr(c,"app",None) or c
def _loads(v,d):
    if v is None:return d
    if isinstance(v,(dict,list)):return v
    try:return json.loads(v)
    except Exception:return d
def _norm(v):
    x=unicodedata.normalize("NFKC",str(v or "")).casefold()
    x=x.replace("&"," and ")
    x=re.sub(r"[^a-z0-9]+"," ",x)
    return re.sub(r"\s+"," ",x).strip()

def _install(e):
    with e.begin() as c:
        for q in DDL:c.execute(text(q))
    _seed(e)

def _seed(e):
    with e.begin() as c:
        for canonical,ptype,city,state,market,micro,aliases in PLACES:
            row=c.execute(text("""SELECT place_id FROM alliance_geography_gazetteer_v29
                WHERE canonical_name=:n AND place_type=:t AND version=:v"""),
                {"n":canonical,"t":ptype,"v":GAZETTEER_VERSION}).first()
            pid=str(row[0]) if row else str(uuid.uuid4())
            if not row:
                c.execute(text("""INSERT INTO alliance_geography_gazetteer_v29
                (place_id,canonical_name,place_type,city,state,country,market,micro_market,approved,confidence,version)
                VALUES(:id,:n,:t,:city,:state,'India',:market,:micro,TRUE,100,:v)"""),
                {"id":pid,"n":canonical,"t":ptype,"city":city,"state":state,"market":market,"micro":micro,"v":GAZETTEER_VERSION})
            all_aliases=list(dict.fromkeys([canonical]+aliases))
            for a in all_aliases:
                an=_norm(a)
                c.execute(text("""INSERT INTO alliance_geography_alias_v29(alias_id,place_id,alias,alias_norm,approved,source,version)
                VALUES(:id,:pid,:a,:an,TRUE,'SEED',:v) ON CONFLICT(alias_norm,version) DO NOTHING"""),
                {"id":str(uuid.uuid4()),"pid":pid,"a":a,"an":an,"v":GAZETTEER_VERSION})
        for dim,vals in ONTOLOGY.items():
            for val in vals:
                c.execute(text("""INSERT INTO alliance_ontology_enum_v29(dimension,value,active,description,version)
                VALUES(:d,:v,TRUE,:desc,:ver) ON CONFLICT(dimension,value,version) DO NOTHING"""),
                {"d":dim,"v":val,"desc":"Canonical 2.9 value","ver":ONTOLOGY_VERSION})

def _aliases(e):
    with e.connect() as c:
        rows=c.execute(text("""SELECT a.alias_norm,a.alias,g.canonical_name,g.place_type,g.city,g.state,g.country,g.market,g.micro_market
        FROM alliance_geography_alias_v29 a JOIN alliance_geography_gazetteer_v29 g ON g.place_id=a.place_id
        WHERE a.version=:v AND a.approved=TRUE AND g.approved=TRUE"""),{"v":GAZETTEER_VERSION}).mappings().all()
    return [dict(r) for r in rows]

def _literal_locations(tutor_answer):
    locs=tutor_answer.get("locations") or []
    out=[]
    for x in locs:
        if isinstance(x,dict) and x.get("value"):
            out.append({"value":str(x["value"]),"field":x.get("field"),"evidence":x.get("evidence"),"scope":x.get("scope")})
    return out

def _resolve_location(literals, aliases):
    hits=[]
    for item in literals:
        raw=item["value"];rn=_norm(raw)
        candidates=[]
        for a in aliases:
            an=a["alias_norm"]
            if not an:continue
            if rn==an:
                candidates=[(100,len(an),a)];break
            # Deterministic phrase match, longest alias wins. Avoid very short aliases.
            if len(an)>=5 and re.search(r"(^| )"+re.escape(an)+r"($| )",rn):
                candidates.append((95,len(an),a))
        if candidates:
            candidates.sort(key=lambda z:(z[0],z[1]),reverse=True)
            top=candidates[0][2]
            hits.append({
              "literal":raw,"normalized_locality":top["canonical_name"],
              "place_type":top["place_type"],"city":top["city"],"state":top["state"],
              "country":top["country"],"market":top["market"],"micro_market":top["micro_market"],
              "quality":"ENRICHED","confidence":candidates[0][0],
              "gazetteer_version":GAZETTEER_VERSION,"matched_alias":top["alias"]
            })
    # Deduplicate canonical destinations.
    unique=[];seen=set()
    for h in hits:
        k=(h["normalized_locality"],h.get("city"),h.get("state"))
        if k not in seen:seen.add(k);unique.append(h)
    return unique

def _unknown_candidates(e,literals,resolved,entity_id,message_id):
    known={_norm(x["literal"]) for x in resolved}
    with e.begin() as c:
        for item in literals:
            ln=_norm(item["value"])
            if not ln or ln in known:continue
            c.execute(text("""INSERT INTO alliance_geography_candidate_v29
            (candidate_id,literal_location,literal_norm,seen_count,sample_entity_id,sample_message_id,status,version)
            VALUES(:id,:lit,:ln,1,:eid,:mid,'NEEDS_REVIEW',:v)
            ON CONFLICT(literal_norm,version) DO UPDATE SET seen_count=alliance_geography_candidate_v29.seen_count+1,
            sample_entity_id=EXCLUDED.sample_entity_id,sample_message_id=EXCLUDED.sample_message_id,updated_at=now()"""),
            {"id":str(uuid.uuid4()),"lit":item["value"],"ln":ln,"eid":entity_id,"mid":message_id,"v":GAZETTEER_VERSION})

def _transaction(raw,source_class,legacy_tx=None):
    low=unicodedata.normalize("NFKC",str(raw or "")).casefold()
    sale=bool(re.search(r"\b(for\s*sale|sale\b|selling\b|asking(?:\s+price)?|demand\s*[:\-]|reserve\s*price)\b",low))
    lease=bool(re.search(r"\b(for\s*lease|lease\s*available|on\s*lease)\b",low))
    rent=bool(re.search(r"\b(for\s*rent|rent\s*[:@\-]|rental\s*[:@\-])\b",low))
    tenanted=bool(re.search(r"\b(already\s+rented|rented\s+out|currently\s+rented|tenanted|tenant\s+paying|leased\s+out|rental\s+income|rent\s+income)\b",low))
    vacant=bool(re.search(r"\b(vacant|ready\s+to\s+move|immediate\s+possession)\b",low))
    owner_occ=bool(re.search(r"\b(owner\s+occupied|self\s+occupied)\b",low))
    requirement=str(source_class or "").upper()=="REQUIREMENT" or bool(re.search(r"\b(requirement|wanted|looking\s+for|client\s+requires?|we\s+need)\b",low))
    business_transfer=bool(re.search(r"\b(running\s+business\s+for\s+sale|business\s+transfer|setup\s+for\s+sale|restaurant\s+setup\s+for\s+sale)\b",low))
    revenue_share=bool(re.search(r"\brevenue\s+share\b",low))
    partnership=bool(re.search(r"\b(partnership|partner\s+required|equity\s+partner)\b",low))

    if business_transfer:tx="BUSINESS_TRANSFER"
    elif revenue_share:tx="REVENUE_SHARE"
    elif partnership:tx="PARTNERSHIP"
    elif requirement and (sale or re.search(r"\b(buy|purchase|purchase\s+requirement)\b",low)):tx="REQUIREMENT_PURCHASE"
    elif requirement and (lease or rent):tx="REQUIREMENT_LEASE"
    elif sale:tx="SALE"
    elif lease:tx="LEASE"
    elif rent:tx="RENT"
    else:tx=None

    if tenanted:occupancy="TENANTED"
    elif owner_occ:occupancy="OWNER_OCCUPIED"
    elif vacant:occupancy="VACANT"
    else:occupancy="UNKNOWN"
    investment="INCOME_PRODUCING" if tenanted and (sale or tx=="SALE") else ("NON_INCOME" if occupancy=="VACANT" else "UNKNOWN")

    flags=[]
    if str(legacy_tx or "").upper()=="BOTH":
        flags.append("LEGACY_BOTH_RETIRED_CANONICALLY")
    if tenanted and tx in ("RENT","LEASE") and sale:
        flags.append("SALE_AND_TENANCY_RESOLVED_AS_SALE_PLUS_TENANTED")
    return {
      "transaction_type":tx,"occupancy_status":occupancy,"investment_status":investment,
      "legacy_transaction_type":legacy_tx,"quality":"EXPLICIT_ATOMIC" if tx else "MISSING",
      "ontology_version":ONTOLOGY_VERSION
    },flags

def _resolve_one(e,row,aliases):
    ta=_loads(row.get("tutor_answer"),{})
    literals=_literal_locations(ta)
    geo=_resolve_location(literals,aliases)
    _unknown_candidates(e,literals,geo,row["entity_id"],row.get("message_id"))
    src=_loads(row.get("source_truth"),{})
    legacy=((src.get("transaction_type") or {}).get("value") if isinstance(src.get("transaction_type"),dict) else None)
    if not legacy:
        t28=ta.get("transaction_type")
        if isinstance(t28,dict):legacy=t28.get("value")
        elif isinstance(t28,str):legacy=t28
    source_class=((src.get("source_class") or {}).get("value") if isinstance(src.get("source_class"),dict) else row.get("source_class"))
    tx,flags=_transaction(row.get("raw_text"),source_class,legacy)
    prov={
      "literal_location":{"quality":"EXPLICIT_ATOMIC" if literals else "MISSING","source":"Foundation 2.8 tutor/raw evidence"},
      "normalized_geography":{"quality":"ENRICHED" if geo else "MISSING","source":GAZETTEER_VERSION},
      "canonical_transaction":{"quality":tx["quality"],"source":ONTOLOGY_VERSION},
      "gold_v1_mutated":False,"whatsapp_live_mutated":False,"production_mutated":False
    }
    if literals and not geo:flags.append("LOCATION_UNKNOWN_TO_GAZETTEER")
    if not tx["transaction_type"]:flags.append("TRANSACTION_ABSTAINED")
    supported=int(bool(literals))+int(bool(geo))+int(bool(tx["transaction_type"]))+int(tx["occupancy_status"]!="UNKNOWN")
    score=round(100*supported/4,2)
    return literals,geo,tx,prov,sorted(set(flags)),score

def run(e,limit=1000):
    _install(e);aliases=_aliases(e)
    with e.connect() as c:
        rows=[dict(x) for x in c.execute(text("""SELECT m.entity_id,m.message_id,m.source_class,m.source_truth,
        v.raw_text,t.tutor_answer
        FROM alliance_magic_examiner_v26 m
        JOIN alliance_topper_availability_v24 v ON v.entity_id=m.entity_id
        LEFT JOIN alliance_intensive_tutor_v28 t ON t.entity_id=m.entity_id
        WHERE m.engine_version='ALLIANCE_MAGIC_EXAMINER_V1'
        ORDER BY m.updated_at DESC LIMIT :n"""),{"n":int(limit)}).mappings().all()]
    failed=[];stats=Counter();samples=[]
    for r in rows:
        try:
            literals,geo,tx,prov,flags,score=_resolve_one(e,r,aliases)
            stats["seen"]+=1
            if literals:stats["literal_location"]+=1
            if geo:stats["normalized_geography"]+=1
            if tx["transaction_type"]:stats["canonical_transaction"]+=1
            if tx["occupancy_status"]!="UNKNOWN":stats["occupancy_resolved"]+=1
            if "LEGACY_BOTH_RETIRED_CANONICALLY" in flags:stats["legacy_both_seen"]+=1
            if flags:stats["review_cases"]+=1
            with e.begin() as c:
                c.execute(text("""INSERT INTO alliance_infrastructure_resolution_v29
                (resolution_id,entity_id,message_id,literal_location,normalized_geography,canonical_transaction,
                field_provenance,review_flags,resolution_score,engine_version)
                VALUES(:id,:eid,:mid,CAST(:lit AS jsonb),CAST(:geo AS jsonb),CAST(:tx AS jsonb),CAST(:prov AS jsonb),
                CAST(:flags AS jsonb),:score,:ver)
                ON CONFLICT(entity_id) DO UPDATE SET message_id=EXCLUDED.message_id,literal_location=EXCLUDED.literal_location,
                normalized_geography=EXCLUDED.normalized_geography,canonical_transaction=EXCLUDED.canonical_transaction,
                field_provenance=EXCLUDED.field_provenance,review_flags=EXCLUDED.review_flags,
                resolution_score=EXCLUDED.resolution_score,engine_version=EXCLUDED.engine_version,updated_at=now()"""),
                {"id":str(uuid.uuid4()),"eid":r["entity_id"],"mid":r.get("message_id"),
                 "lit":json.dumps(literals,ensure_ascii=False),"geo":json.dumps(geo,ensure_ascii=False),
                 "tx":json.dumps(tx,ensure_ascii=False),"prov":json.dumps(prov,ensure_ascii=False),
                 "flags":json.dumps(flags,ensure_ascii=False),"score":score,"ver":ENGINE_VERSION})
            if flags and len(samples)<20:
                samples.append({"entity_id":r["entity_id"],"score":score,"flags":flags,"literal_location":literals,"normalized_geography":geo,"canonical_transaction":tx})
        except Exception as x:failed.append(f"{r.get('entity_id')}:{type(x).__name__}:{x}"[:500])
    return {"status":"PASS" if not failed else "PARTIAL","version":VERSION,"engine_version":ENGINE_VERSION,
      "seen":len(rows),"resolved":len(rows)-len(failed),"failed":len(failed),"stats":dict(stats),
      "review_samples":samples,"errors":failed[:10],"production_writes":0,"whatsapp_live_writes":0,"gold_v1_mutations":0}

def status(e):
    _install(e)
    with e.connect() as c:
        s=c.execute(text("""SELECT count(*) n,avg(resolution_score) score,
        count(*) FILTER(WHERE jsonb_array_length(literal_location)>0) literal_n,
        count(*) FILTER(WHERE normalized_geography<>'{}'::jsonb AND normalized_geography<>'[]'::jsonb) geo_n,
        count(*) FILTER(WHERE canonical_transaction->>'transaction_type' IS NOT NULL) tx_n,
        count(*) FILTER(WHERE canonical_transaction->>'occupancy_status'<>'UNKNOWN') occ_n,
        count(*) FILTER(WHERE review_flags ? 'LEGACY_BOTH_RETIRED_CANONICALLY') both_n
        FROM alliance_infrastructure_resolution_v29 WHERE engine_version=:v"""),{"v":ENGINE_VERSION}).mappings().first()
        cand=[dict(x) for x in c.execute(text("""SELECT literal_location,seen_count,status FROM alliance_geography_candidate_v29
        WHERE version=:v ORDER BY seen_count DESC,updated_at DESC LIMIT 25"""),{"v":GAZETTEER_VERSION}).mappings().all()]
        seeds=c.execute(text("SELECT count(*) FROM alliance_geography_gazetteer_v29 WHERE version=:v"),{"v":GAZETTEER_VERSION}).scalar() or 0
        aliases=c.execute(text("SELECT count(*) FROM alliance_geography_alias_v29 WHERE version=:v"),{"v":GAZETTEER_VERSION}).scalar() or 0
        review=[dict(x) for x in c.execute(text("""SELECT entity_id,resolution_score,review_flags,literal_location,normalized_geography,canonical_transaction
        FROM alliance_infrastructure_resolution_v29 WHERE engine_version=:v AND jsonb_array_length(review_flags)>0
        ORDER BY resolution_score ASC,updated_at DESC LIMIT 20"""),{"v":ENGINE_VERSION}).mappings().all()]
    n=int(s["n"] or 0)
    pct=lambda x:round(100*int(x or 0)/max(n,1),2)
    return foundation._json_safe({"status":"PASS","version":VERSION,"mode":MODE,"engine_version":ENGINE_VERSION,
      "gazetteer_version":GAZETTEER_VERSION,"ontology_version":ONTOLOGY_VERSION,"resolved_profiles":n,
      "average_resolution_score":round(float(s["score"] or 0),2),"coverage":{
        "literal_location":pct(s["literal_n"]),"normalized_or_enriched_geography":pct(s["geo_n"]),
        "canonical_transaction":pct(s["tx_n"]),"occupancy_status_resolved":pct(s["occ_n"])},
      "legacy_both_cases_canonically_retired":int(s["both_n"] or 0),
      "gazetteer_places":int(seeds),"gazetteer_aliases":int(aliases),"unknown_location_candidates":cand,
      "review_queue":review,
      "canonical_rule":"Historical BOTH is preserved as legacy evidence; canonical output never uses BOTH.",
      "field_quality_rule":"EXPLICIT_ATOMIC is source truth; ENRICHED is deterministic gazetteer knowledge; they are never collapsed.",
      "production_writes":0,"whatsapp_live_writes":0,"gold_v1_mutations":0})

DASH="""<!doctype html><html><body style='font-family:Arial;background:#081018;color:#eef5ff;max-width:1280px;margin:28px auto'>
<h1>🏗️ Foundation 2.9 Infrastructure First</h1>
<p>Deterministic geography gazetteer + canonical transaction/occupancy split. Historical source truth remains untouched.</p>
<button onclick='go()' style='padding:14px 20px;background:#f7d66a;border:0;border-radius:9px;font-weight:bold'>Resolve Latest 1000</button>
<button onclick='st()' style='padding:14px 20px'>Refresh</button>
<h2>Infrastructure Scoreboard</h2><pre id=s></pre><h2>Resolution Result</h2><pre id=r>No run yet.</pre>
<script>async function a(p,m='GET'){let x=await fetch(p,{method:m}),t=await x.text(),d;try{d=JSON.parse(t)}catch(e){d={raw:t}}if(!x.ok)throw Error(d.detail||d.raw||('HTTP '+x.status));return d}
async function st(){try{s.textContent=JSON.stringify(await a('/api/property-brain/infrastructure-v29/status'),null,2)}catch(e){s.textContent='ERROR '+e.message}}
async function go(){r.textContent='Resolving...';try{r.textContent=JSON.stringify(await a('/api/property-brain/infrastructure-v29/run?limit=1000','POST'),null,2);await st()}catch(e){r.textContent='ERROR '+e.message}}st()</script></body></html>"""

def register(core):
    e=_engine(core);app=_app(core);_install(e)
    if not foundation._route_exists(app,"/api/property-brain/infrastructure-v29/status"):
        @app.get("/api/property-brain/infrastructure-v29/status")
        def _s():return status(e)
    if not foundation._route_exists(app,"/api/property-brain/infrastructure-v29/run"):
        @app.post("/api/property-brain/infrastructure-v29/run")
        def _r(limit:int=Query(default=1000,ge=1,le=5000)):return run(e,limit)
    if not foundation._route_exists(app,"/property-brain/infrastructure-v29"):
        @app.get("/property-brain/infrastructure-v29",response_class=HTMLResponse)
        def _d():return HTMLResponse(DASH)
    return {"status":"REGISTERED","version":VERSION,"dashboard":"/property-brain/infrastructure-v29",
      "production_writes":0,"whatsapp_live_writes":0,"gold_v1_mutations":0}
