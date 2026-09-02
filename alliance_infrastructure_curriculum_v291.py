from __future__ import annotations

import json
import re
import unicodedata
import uuid
from collections import Counter, defaultdict

from fastapi import Query
from fastapi.responses import HTMLResponse
from sqlalchemy import text

import alliance_property_brain_foundation_v1 as foundation
import alliance_infrastructure_first_v29 as v29

VERSION = "2.9.1-GEOGRAPHY-TRANSACTION-CURRICULUM-REVISED"
MODE = "BOUNDARY_DIAGNOSTIC_CANDIDATE_VALIDATION_CONTEXT_DISAMBIGUATION_TRANSACTION_LINEAGE_EVAL2"
ENGINE_VERSION = "ALLIANCE_INFRASTRUCTURE_CURRICULUM_V291"
GAZETTEER_VERSION = "ALLIANCE_GAZETTEER_DNCR_GOA_V1_1"
ONTOLOGY_VERSION = "ALLIANCE_CANONICAL_DEAL_DIMENSIONS_V1"
EVAL_VERSION = "ALLIANCE_EVALUATION_2_V1"

DDL = [
"""CREATE TABLE IF NOT EXISTS alliance_location_diagnostic_v291(
diagnostic_id UUID PRIMARY KEY,
entity_id TEXT NOT NULL UNIQUE,
message_id TEXT,
diagnostic_class TEXT NOT NULL,
atomic_location_evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
parent_location_evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
owned_location_evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
notes JSONB NOT NULL DEFAULT '[]'::jsonb,
engine_version TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",

"""CREATE TABLE IF NOT EXISTS alliance_location_candidate_validation_v291(
validation_id UUID PRIMARY KEY,
entity_id TEXT NOT NULL UNIQUE,
message_id TEXT,
candidate_value TEXT,
candidate_class TEXT NOT NULL,
validation_evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
resolved_geography JSONB NOT NULL DEFAULT '{}'::jsonb,
provenance TEXT,
confidence NUMERIC(5,2),
engine_version TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",

"""CREATE TABLE IF NOT EXISTS alliance_project_geography_v291(
project_norm TEXT PRIMARY KEY,
project_name TEXT NOT NULL,
city TEXT,
state TEXT,
country TEXT NOT NULL DEFAULT 'India',
approved BOOLEAN NOT NULL DEFAULT FALSE,
approved_by TEXT,
source TEXT,
version TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",

"""CREATE TABLE IF NOT EXISTS alliance_deal_dimensions_v291(
deal_id UUID PRIMARY KEY,
entity_id TEXT NOT NULL UNIQUE,
message_id TEXT,
intent_direction TEXT,
transaction_mode TEXT,
occupancy_status TEXT NOT NULL DEFAULT 'UNKNOWN',
investment_status TEXT NOT NULL DEFAULT 'UNKNOWN',
source_provenance TEXT,
source_evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
legacy_transaction_type TEXT,
conflict_with_legacy BOOLEAN NOT NULL DEFAULT FALSE,
abstained BOOLEAN NOT NULL DEFAULT FALSE,
review_flags JSONB NOT NULL DEFAULT '[]'::jsonb,
ontology_version TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",

"""CREATE TABLE IF NOT EXISTS alliance_eval2_v291(
metric_key TEXT PRIMARY KEY,
metric_value NUMERIC(10,4),
numerator INTEGER NOT NULL DEFAULT 0,
denominator INTEGER NOT NULL DEFAULT 0,
metric_scope TEXT NOT NULL,
requires_gold BOOLEAN NOT NULL DEFAULT FALSE,
notes TEXT,
eval_version TEXT NOT NULL,
updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",

"""CREATE TABLE IF NOT EXISTS alliance_gold_v2_candidate_queue(
candidate_id UUID PRIMARY KEY,
entity_id TEXT NOT NULL,
message_id TEXT,
category TEXT NOT NULL,
priority_score NUMERIC(6,2) NOT NULL,
reason TEXT NOT NULL,
payload JSONB NOT NULL DEFAULT '{}'::jsonb,
status TEXT NOT NULL DEFAULT 'OPEN',
source_version TEXT NOT NULL,
created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
UNIQUE(entity_id,category,source_version))"""
]

FAST_TRACK_PLACES = [
    ("Delhi","CITY","Delhi","Delhi","DELHI_NCR",None,["delhi","new delhi"]),
    ("Panaji","CITY","Panaji","Goa","GOA",None,["panaji","panjim","panjim goa"]),
    ("Goa","STATE",None,"Goa","GOA",None,["goa"]),
    ("Mumbai","CITY","Mumbai","Maharashtra","OUTSIDE_CORE_MARKET",None,["mumbai","bombay"]),
    ("Jaipur","CITY","Jaipur","Rajasthan","OUTSIDE_CORE_MARKET",None,["jaipur"]),
    ("Tri Nagar","LOCALITY","Delhi","Delhi","DELHI_NCR","North West Delhi",["tri nagar","trinagar"]),
    ("Greater Kailash","LOCALITY","Delhi","Delhi","DELHI_NCR","South Delhi",["greater kailash","gk"]),
]

BROKER_FILLER = {
    "cheap","price","very","negotiable","urgent","urgently","available","contact","call",
    "best","deal","offer","only","excellent","prime","reasonable","final","immediate",
    "demand","asking","brokerage","direct","owner","owners","party","genuine"
}

LOCATION_SUFFIXES = (
    "nagar","vihar","enclave","colony","extension","extn","road","marg","phase",
    "sector","bagh","kunj","kailash","park","garden","village","estate","city"
)

def _engine(core):
    return foundation._engine_from_core(core)

def _app(core):
    return getattr(core, "app", None) or core

def _loads(v, default):
    if v is None:
        return default
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except Exception:
        return default

def _norm(v):
    s = unicodedata.normalize("NFKC", str(v or "")).casefold()
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def _install(engine):
    with engine.begin() as conn:
        for stmt in DDL:
            conn.execute(text(stmt))
    _seed_gazetteer(engine)

def _seed_gazetteer(engine):
    # Additive only: do not edit/delete v2.9 seed rows.
    with engine.begin() as conn:
        for canonical, ptype, city, state, market, micro, aliases in FAST_TRACK_PLACES:
            row = conn.execute(text("""
                SELECT place_id FROM alliance_geography_gazetteer_v29
                WHERE canonical_name=:n AND place_type=:t
                ORDER BY created_at ASC LIMIT 1
            """), {"n": canonical, "t": ptype}).first()
            pid = str(row[0]) if row else str(uuid.uuid4())
            if not row:
                conn.execute(text("""
                    INSERT INTO alliance_geography_gazetteer_v29
                    (place_id,canonical_name,place_type,city,state,country,market,micro_market,
                     approved,confidence,version)
                    VALUES(:id,:n,:t,:city,:state,'India',:market,:micro,TRUE,100,:v)
                """), {"id":pid,"n":canonical,"t":ptype,"city":city,"state":state,
                       "market":market,"micro":micro,"v":GAZETTEER_VERSION})
            for alias in list(dict.fromkeys([canonical] + aliases)):
                conn.execute(text("""
                    INSERT INTO alliance_geography_alias_v29
                    (alias_id,place_id,alias,alias_norm,approved,source,version)
                    VALUES(:id,:pid,:a,:an,TRUE,'V291_FAST_TRACK_APPROVED',:v)
                    ON CONFLICT(alias_norm,version) DO NOTHING
                """), {"id":str(uuid.uuid4()),"pid":pid,"a":alias,
                       "an":_norm(alias),"v":GAZETTEER_VERSION})

        # New compositional deal ontology. Old 2.9 enum rows remain historical.
        dims = {
            "intent_direction":["SUPPLY","DEMAND"],
            "transaction_mode":["SALE","RENT","LEASE","BUSINESS_TRANSFER","REVENUE_SHARE","PARTNERSHIP"],
            "occupancy_status":["VACANT","TENANTED","OWNER_OCCUPIED","UNKNOWN"],
            "investment_status":["INCOME_PRODUCING","NON_INCOME","UNKNOWN"],
        }
        for dim, values in dims.items():
            for value in values:
                conn.execute(text("""
                    INSERT INTO alliance_ontology_enum_v29(dimension,value,active,description,version)
                    VALUES(:d,:v,TRUE,'Foundation 2.9.1 compositional canonical deal dimension',:ver)
                    ON CONFLICT(dimension,value,version) DO NOTHING
                """), {"d":dim,"v":value,"ver":ONTOLOGY_VERSION})

def _gazetteer_aliases(engine):
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT a.alias_norm,a.alias,g.canonical_name,g.place_type,g.city,g.state,g.country,
                   g.market,g.micro_market,g.version AS place_version,a.version AS alias_version
            FROM alliance_geography_alias_v29 a
            JOIN alliance_geography_gazetteer_v29 g ON g.place_id=a.place_id
            WHERE a.approved=TRUE AND g.approved=TRUE
        """)).mappings().all()
    # longest first prevents "gk" winning over "greater kailash 1"
    out = [dict(x) for x in rows if x.get("alias_norm")]
    out.sort(key=lambda x: len(x["alias_norm"]), reverse=True)
    return out

def _scan_known_places(raw_text, aliases):
    rn = _norm(raw_text)
    hits, seen = [], set()
    for a in aliases:
        an = a["alias_norm"]
        if len(an) < 3:
            continue
        if re.search(r"(^| )" + re.escape(an) + r"($| )", rn):
            key = (a["canonical_name"], a.get("city"), a.get("state"))
            if key in seen:
                continue
            seen.add(key)
            hits.append({
                "value": a["canonical_name"],
                "matched_alias": a["alias"],
                "place_type": a["place_type"],
                "city": a.get("city"),
                "state": a.get("state"),
                "country": a.get("country"),
                "market": a.get("market"),
                "micro_market": a.get("micro_market"),
                "quality": "EXPLICIT_ATOMIC",
                "provenance": "ATOMIC_GAZETTEER_MENTION",
                "confidence": 100,
            })
    return hits[:12]

def _extract_atomic_location_candidates(raw_text, profile, aliases):
    atomic = (profile.get("atomic_explicit") or {})
    out, seen = [], set()

    for field in ("city","locality"):
        for item in atomic.get(field) or []:
            val = item.get("value") if isinstance(item, dict) else item
            if not val:
                continue
            k = _norm(val)
            if k and k not in seen:
                seen.add(k)
                out.append({
                    "value": str(val),
                    "field": field,
                    "evidence": item.get("evidence") if isinstance(item,dict) else str(val),
                    "provenance":"V24_ATOMIC_EXPLICIT"
                })

    # Reuse 2.8 pattern carefully, then validate semantically downstream.
    pats = [
        r"\b(?:location|located)\s*[:\-]\s*([^\n|]{2,80})",
        r"\b(?:for\s+sale|for\s+rent|for\s+lease|available)\s+(?:in|at)\s+([^\n|]{2,80})",
    ]
    for pat in pats:
        for m in re.finditer(pat, str(raw_text or ""), re.I):
            val = m.group(1).strip(" .-*")
            val = re.split(
                r"\s+(?:area|size|price|rent|demand|asking|contact|call|brokerage)\s*[:\-]",
                val, flags=re.I
            )[0].strip()
            k = _norm(val)
            if k and k not in seen:
                seen.add(k)
                out.append({
                    "value":val,
                    "field":"literal_location",
                    "evidence":m.group(0).strip(),
                    "provenance":"V291_ATOMIC_PATTERN"
                })

    # Key improvement: known-place mention in atomic text counts as recoverable literal evidence
    # even when the phrase is not preceded by "location:" or "for sale at".
    for hit in _scan_known_places(raw_text, aliases):
        k = _norm(hit["value"])
        if k and k not in seen:
            seen.add(k)
            out.append({
                "value":hit["value"],
                "field":"literal_location",
                "evidence":hit["matched_alias"],
                "provenance":"V291_ATOMIC_GAZETTEER_MENTION"
            })

    return out[:12]

def _validate_candidate(value, aliases):
    raw = str(value or "").strip()
    n = _norm(raw)
    tokens = n.split()
    ev = {"raw":raw,"normalized":n,"positive_signals":[],"negative_signals":[]}

    if not n:
        return "NOT_LOCATION", ev, 0.0

    # Exact or phrase gazetteer hit.
    matches = []
    for a in aliases:
        an = a["alias_norm"]
        if n == an:
            matches = [a]
            break
        if len(an) >= 4 and re.search(r"(^| )"+re.escape(an)+r"($| )", n):
            matches.append(a)
    if matches:
        ev["positive_signals"].append("GAZETTEER_MATCH")
        return "VALID_KNOWN_PLACE", ev, 100.0

    # Obvious broker-filler phrase.
    filler_count = sum(t in BROKER_FILLER for t in tokens)
    if tokens and filler_count >= max(1, len(tokens)-1):
        ev["negative_signals"].append("BROKER_FILLER_DOMINANT")
        return "NOT_LOCATION", ev, 99.0

    if re.search(r"\bsector\s*\d+[a-z]?\b", n):
        ev["positive_signals"].append("SECTOR_PATTERN")
        if re.search(r"\b(?:and|&|/|,)\b", raw, re.I):
            return "COMPOUND_LOCATION", ev, 90.0
        return "AMBIGUOUS_LOCATION", ev, 95.0

    if any(n.endswith(" "+s) or n==s or (" "+s+" ") in (" "+n+" ") for s in LOCATION_SUFFIXES):
        ev["positive_signals"].append("LOCATION_SUFFIX")

    if re.search(r"[,&/]", raw) or re.search(r"\band\b", n):
        # Compound requires at least two non-filler chunks.
        chunks = [c.strip() for c in re.split(r"[,&/]+|\band\b", raw, flags=re.I) if c.strip()]
        good = [c for c in chunks if not all(t in BROKER_FILLER for t in _norm(c).split())]
        if len(good) >= 2:
            ev["positive_signals"].append("MULTI_PLACE_SHAPE")
            return "COMPOUND_LOCATION", ev, 85.0

    # Conservative unknown-place acceptance: short alphabetic/proper-name-like phrases only.
    if 1 <= len(tokens) <= 4 and all(re.fullmatch(r"[a-z0-9]+", t) for t in tokens):
        if filler_count == 0 and (
            any(n.endswith(s) for s in LOCATION_SUFFIXES)
            or any(x.isalpha() and len(x) >= 4 for x in tokens)
        ):
            ev["positive_signals"].append("PLAUSIBLE_PLACE_NAME")
            return "VALID_UNKNOWN_PLACE", ev, 70.0

    ev["negative_signals"].append("NO_POSITIVE_LOCATION_SHAPE")
    return "NOT_LOCATION", ev, 80.0

def _resolve_known(value, aliases):
    n = _norm(value)
    candidates = []
    for a in aliases:
        an = a["alias_norm"]
        if n == an:
            candidates = [(100, len(an), a)]
            break
        if len(an) >= 4 and re.search(r"(^| )"+re.escape(an)+r"($| )", n):
            candidates.append((95, len(an), a))
    if not candidates:
        return {}
    candidates.sort(key=lambda z:(z[0],z[1]), reverse=True)
    score, _, a = candidates[0]
    return {
        "literal_location":value,
        "canonical_name":a["canonical_name"],
        "place_type":a["place_type"],
        "city":a.get("city"),
        "state":a.get("state"),
        "country":a.get("country"),
        "market":a.get("market"),
        "micro_market":a.get("micro_market"),
        "quality":"ENRICHED",
        "provenance":"DETERMINISTIC_GAZETTEER",
        "confidence":score,
        "matched_alias":a["alias"],
        "gazetteer_version":a.get("alias_version") or GAZETTEER_VERSION,
    }

def _owned_context(owned_fields):
    out = {}
    for field in ("city","locality","project_name","project","transaction_type"):
        item = owned_fields.get(field) or {}
        if item.get("status") in ("OWNED_ATOMIC","OWNED_PARENT_SCOPED"):
            vals = [str(x) for x in (item.get("values") or []) if str(x or "").strip()]
            if vals:
                out[field] = {
                    "values":vals,
                    "status":item.get("status"),
                    "scope_reason":item.get("scope_reason"),
                    "evidence":item.get("evidence"),
                }
    return out

def _contextual_sector_resolution(value, owned, engine):
    n = _norm(value)
    m = re.search(r"\bsector\s*(\d+[a-z]?)\b", n)
    if not m:
        return {}, None

    cities = []
    city_item = owned.get("city") or {}
    for x in city_item.get("values") or []:
        if str(x).strip():
            cities.append(str(x).strip())

    # Deterministic project->city mapping, approved rows only.
    project_vals = []
    for key in ("project_name","project"):
        project_vals.extend((owned.get(key) or {}).get("values") or [])
    project_cities = []
    if project_vals:
        with engine.connect() as conn:
            for p in project_vals:
                row = conn.execute(text("""
                    SELECT city,state,country,project_name FROM alliance_project_geography_v291
                    WHERE project_norm=:p AND approved=TRUE
                """), {"p":_norm(p)}).mappings().first()
                if row and row.get("city"):
                    project_cities.append(dict(row))

    city_unique = sorted({c.casefold():c for c in cities}.values(), key=str.casefold)
    project_unique = sorted({str(x["city"]).casefold():str(x["city"]) for x in project_cities}.values(), key=str.casefold)

    evidence_refs = []
    if len(city_unique) == 1:
        resolved_city = city_unique[0]
        evidence_refs.append({"type":"OWNED_PARENT_CITY","value":resolved_city,"ownership":city_item})
    elif len(city_unique) > 1:
        return {}, "AMBIGUOUS_PARENT_CITY"
    elif len(project_unique) == 1:
        resolved_city = project_unique[0]
        evidence_refs.append({"type":"APPROVED_PROJECT_CITY","value":resolved_city,"projects":project_cities})
    elif len(project_unique) > 1:
        return {}, "AMBIGUOUS_PROJECT_CITY"
    else:
        return {}, "SECTOR_CITY_UNPROVEN"

    if city_unique and project_unique and city_unique[0].casefold() != project_unique[0].casefold():
        return {}, "PARENT_PROJECT_CITY_CONFLICT"

    confidence = 92.0 if city_unique and project_unique else 85.0
    return {
        "literal_location":value,
        "canonical_name":f"Sector {m.group(1).upper()}",
        "place_type":"LOCALITY",
        "city":resolved_city,
        "state":None,
        "country":"India",
        "quality":"CONTEXTUAL",
        "provenance":"PROVEN_CONTEXT_OWNERSHIP",
        "confidence":confidence,
        "evidence_refs":evidence_refs,
    }, None

def _location_diagnostic(raw, parent_raw, profile, owned_fields, aliases):
    atomic = _extract_atomic_location_candidates(raw, profile, aliases)
    parent = (profile.get("parent_context_candidates") or {})
    parent_loc = []
    for f in ("city","locality"):
        for item in parent.get(f) or []:
            parent_loc.append({
                "field":f,
                "value":item.get("value") if isinstance(item,dict) else item,
                "evidence":item.get("evidence") if isinstance(item,dict) else str(item),
            })
    owned = _owned_context(owned_fields)

    if atomic:
        klass = "ATOMIC_LITERAL_OR_RECOVERABLE"
        notes = ["Location evidence exists in atomic span."]
    elif owned.get("city") or owned.get("locality"):
        klass = "OWNED_PARENT_LOCATION_AVAILABLE"
        notes = ["No atomic location, but context ownership already proves a parent geography value."]
    elif parent_loc:
        klass = "PARENT_LOCATION_UNOWNED"
        notes = ["Parent location candidate exists but is not safely owned by this atomic entity."]
    elif _scan_known_places(parent_raw, aliases):
        klass = "PARENT_KNOWN_PLACE_NOT_OWNED"
        notes = ["Parent text contains a known place but ownership is not proven."]
    else:
        klass = "NO_LOCATION_SIGNAL"
        notes = ["No supported atomic or owned parent location signal found."]

    return klass, atomic, parent_loc, owned, notes

def _deal_dimensions(raw, source_class, owned_fields, legacy_tx):
    low = unicodedata.normalize("NFKC", str(raw or "")).casefold()
    flags = []
    ev = {}

    is_requirement = (
        str(source_class or "").upper() == "REQUIREMENT"
        or bool(re.search(r"\b(requirement|wanted|looking\s+for|client\s+requires?|we\s+need|need\s+for)\b", low))
    )
    intent = "DEMAND" if is_requirement else "SUPPLY"

    sale = bool(re.search(r"\b(for\s*sale|sale\b|selling\b|asking(?:\s+price)?|demand\s*[:\-]|reserve\s*price|buy|purchase)\b", low))
    lease = bool(re.search(r"\b(for\s*lease|lease\s*available|on\s*lease|lease\s+requirement)\b", low))
    rent = bool(re.search(r"\b(for\s*rent|rent\s*[:@\-]|rental\s*[:@\-]|rent\s+requirement)\b", low))
    business_transfer = bool(re.search(r"\b(running\s+business\s+for\s+sale|business\s+transfer|setup\s+for\s+sale|restaurant\s+setup\s+for\s+sale)\b", low))
    revenue_share = bool(re.search(r"\brevenue\s+share\b", low))
    partnership = bool(re.search(r"\b(partnership|partner\s+required|equity\s+partner)\b", low))

    tx = None
    provenance = None
    if business_transfer:
        tx, provenance = "BUSINESS_TRANSFER", "ATOMIC_EXPLICIT"
    elif revenue_share:
        tx, provenance = "REVENUE_SHARE", "ATOMIC_EXPLICIT"
    elif partnership:
        tx, provenance = "PARTNERSHIP", "ATOMIC_EXPLICIT"
    elif sale:
        tx, provenance = "SALE", "ATOMIC_EXPLICIT"
    elif lease:
        tx, provenance = "LEASE", "ATOMIC_EXPLICIT"
    elif rent:
        tx, provenance = "RENT", "ATOMIC_EXPLICIT"
    else:
        owned_tx = (owned_fields.get("transaction_type") or {})
        vals = [str(x).upper() for x in (owned_tx.get("values") or []) if str(x or "").strip()]
        if owned_tx.get("status") == "OWNED_PARENT_SCOPED" and len(set(vals)) == 1:
            parent_val = vals[0]
            if parent_val in ("SALE","RENT"):
                tx = parent_val
                provenance = "OWNED_PARENT_TRANSACTION"
                ev["parent_scope_reason"] = owned_tx.get("scope_reason")
                ev["parent_evidence"] = owned_tx.get("evidence")
            elif parent_val == "BOTH":
                flags.append("OWNED_PARENT_BOTH_NOT_CANONICALIZED_WITHOUT_CHILD_PROOF")

    tenanted = bool(re.search(r"\b(already\s+rented|rented\s+out|currently\s+rented|tenanted|tenant\s+paying|leased\s+out|rental\s+income|rent\s+income)\b", low))
    owner_occ = bool(re.search(r"\b(owner\s+occupied|self\s+occupied)\b", low))
    vacant = bool(re.search(r"\b(vacant|ready\s+to\s+move|immediate\s+possession)\b", low))
    occupancy = "TENANTED" if tenanted else ("OWNER_OCCUPIED" if owner_occ else ("VACANT" if vacant else "UNKNOWN"))
    investment = "INCOME_PRODUCING" if tenanted and tx == "SALE" else ("NON_INCOME" if occupancy == "VACANT" else "UNKNOWN")

    ev.update({
        "atomic_sale_signal":sale,
        "atomic_rent_signal":rent,
        "atomic_lease_signal":lease,
        "requirement_signal":is_requirement,
        "occupancy_signal":"TENANTED" if tenanted else ("OWNER_OCCUPIED" if owner_occ else ("VACANT" if vacant else None)),
    })
    if not tx:
        flags.append("TRANSACTION_ABSTAINED")
    if str(legacy_tx or "").upper() == "BOTH":
        flags.append("LEGACY_BOTH_RETAINED_AUDIT_ONLY")

    legacy_norm = str(legacy_tx or "").upper() or None
    legacy_map = {"BOTH":None,"SALE":"SALE","RENT":"RENT","LEASE":"LEASE"}
    comparable_legacy = legacy_map.get(legacy_norm)
    conflict = bool(tx and comparable_legacy and tx != comparable_legacy)

    return {
        "intent_direction":intent,
        "transaction_mode":tx,
        "occupancy_status":occupancy,
        "investment_status":investment,
        "source_provenance":provenance,
        "source_evidence":ev,
        "legacy_transaction_type":legacy_tx,
        "conflict_with_legacy":conflict,
        "abstained":tx is None,
        "review_flags":sorted(set(flags)),
    }

def _queue_gold_candidate(engine, row, category, priority, reason, payload):
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO alliance_gold_v2_candidate_queue
            (candidate_id,entity_id,message_id,category,priority_score,reason,payload,status,source_version)
            VALUES(:id,:eid,:mid,:cat,:p,:reason,CAST(:payload AS jsonb),'OPEN',:v)
            ON CONFLICT(entity_id,category,source_version) DO UPDATE SET
              priority_score=GREATEST(alliance_gold_v2_candidate_queue.priority_score,EXCLUDED.priority_score),
              reason=EXCLUDED.reason,payload=EXCLUDED.payload
        """), {
            "id":str(uuid.uuid4()),"eid":row["entity_id"],"mid":row.get("message_id"),
            "cat":category,"p":priority,"reason":reason,
            "payload":json.dumps(foundation._json_safe(payload), ensure_ascii=False),
            "v":ENGINE_VERSION,
        })

def _write_metric(engine, key, num, den, scope, requires_gold=False, notes=None):
    val = round(100.0 * num / max(den,1), 4) if den else 0.0
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO alliance_eval2_v291(metric_key,metric_value,numerator,denominator,metric_scope,requires_gold,notes,eval_version)
            VALUES(:k,:v,:n,:d,:s,:g,:notes,:ev)
            ON CONFLICT(metric_key) DO UPDATE SET metric_value=EXCLUDED.metric_value,
              numerator=EXCLUDED.numerator,denominator=EXCLUDED.denominator,
              metric_scope=EXCLUDED.metric_scope,requires_gold=EXCLUDED.requires_gold,
              notes=EXCLUDED.notes,eval_version=EXCLUDED.eval_version,updated_at=now()
        """), {"k":key,"v":val,"n":num,"d":den,"s":scope,"g":requires_gold,"notes":notes,"ev":EVAL_VERSION})

def run(engine, limit=1000):
    _install(engine)
    aliases = _gazetteer_aliases(engine)

    with engine.connect() as conn:
        rows = [dict(x) for x in conn.execute(text("""
            SELECT m.entity_id,m.message_id,m.source_class,m.source_truth,
                   v.raw_text,v.parent_message_text,v.extracted_profile,
                   o.owned_fields,o.rejected_inheritance,o.sibling_context,
                   i.literal_location AS v29_literal_location,
                   i.normalized_geography AS v29_normalized_geography,
                   i.canonical_transaction AS v29_canonical_transaction
            FROM alliance_magic_examiner_v26 m
            JOIN alliance_topper_availability_v24 v ON v.entity_id=m.entity_id
            LEFT JOIN alliance_context_ownership_v25 o ON o.entity_id=m.entity_id
            LEFT JOIN alliance_infrastructure_resolution_v29 i ON i.entity_id=m.entity_id
            WHERE m.engine_version='ALLIANCE_MAGIC_EXAMINER_V1'
            ORDER BY m.updated_at DESC LIMIT :n
        """), {"n":int(limit)}).mappings().all()]

    failed = []
    counts = Counter()
    diag_counts = Counter()
    candidate_counts = Counter()
    samples = []

    for row in rows:
        try:
            raw = str(row.get("raw_text") or "")
            parent_raw = str(row.get("parent_message_text") or "")
            profile = _loads(row.get("extracted_profile"), {})
            owned_fields = _loads(row.get("owned_fields"), {})
            source_truth = _loads(row.get("source_truth"), {})
            legacy = None
            stx = source_truth.get("transaction_type")
            if isinstance(stx,dict):
                legacy = stx.get("value")
            if not legacy:
                old = _loads(row.get("v29_canonical_transaction"), {})
                legacy = old.get("legacy_transaction_type")

            diag_class, atomic_candidates, parent_candidates, owned_ctx, notes = _location_diagnostic(
                raw,parent_raw,profile,owned_fields,aliases
            )
            diag_counts[diag_class] += 1
            if atomic_candidates:
                counts["source_location_supported"] += 1

            with engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO alliance_location_diagnostic_v291
                    (diagnostic_id,entity_id,message_id,diagnostic_class,atomic_location_evidence,
                     parent_location_evidence,owned_location_evidence,notes,engine_version)
                    VALUES(:id,:eid,:mid,:dc,CAST(:a AS jsonb),CAST(:p AS jsonb),CAST(:o AS jsonb),
                           CAST(:notes AS jsonb),:v)
                    ON CONFLICT(entity_id) DO UPDATE SET message_id=EXCLUDED.message_id,
                      diagnostic_class=EXCLUDED.diagnostic_class,
                      atomic_location_evidence=EXCLUDED.atomic_location_evidence,
                      parent_location_evidence=EXCLUDED.parent_location_evidence,
                      owned_location_evidence=EXCLUDED.owned_location_evidence,
                      notes=EXCLUDED.notes,engine_version=EXCLUDED.engine_version,updated_at=now()
                """), {"id":str(uuid.uuid4()),"eid":row["entity_id"],"mid":row.get("message_id"),
                       "dc":diag_class,"a":json.dumps(atomic_candidates,ensure_ascii=False),
                       "p":json.dumps(parent_candidates,ensure_ascii=False),
                       "o":json.dumps(owned_ctx,ensure_ascii=False),
                       "notes":json.dumps(notes,ensure_ascii=False),"v":ENGINE_VERSION})

            # Candidate validation and resolution. Pick the best validated candidate.
            best = None
            for cand in atomic_candidates:
                cclass, vev, conf = _validate_candidate(cand["value"], aliases)
                candidate_counts[cclass] += 1
                resolved = _resolve_known(cand["value"], aliases) if cclass=="VALID_KNOWN_PLACE" else {}
                contextual_flag = None
                if cclass=="AMBIGUOUS_LOCATION" and re.search(r"\bsector\s*\d+", _norm(cand["value"])):
                    resolved, contextual_flag = _contextual_sector_resolution(cand["value"], owned_ctx, engine)
                    if resolved:
                        cclass = "VALID_KNOWN_PLACE"
                        conf = resolved.get("confidence", conf)
                    elif contextual_flag:
                        vev["contextual_flag"] = contextual_flag

                rank = {
                    "VALID_KNOWN_PLACE":5,
                    "VALID_UNKNOWN_PLACE":4,
                    "COMPOUND_LOCATION":3,
                    "AMBIGUOUS_LOCATION":2,
                    "NOT_LOCATION":1,
                }.get(cclass,0)
                item = (rank, conf, cand, cclass, vev, resolved)
                if best is None or (rank,conf) > (best[0],best[1]):
                    best = item

                if cclass=="NOT_LOCATION":
                    _queue_gold_candidate(engine,row,"NEGATIVE_LOCATION",95,
                        "Candidate should not be treated as geography",
                        {"candidate":cand,"validation":vev})
                elif cclass in ("AMBIGUOUS_LOCATION","COMPOUND_LOCATION"):
                    _queue_gold_candidate(engine,row,"HARD_GEOGRAPHY",90,
                        f"{cclass} needs human teaching",
                        {"candidate":cand,"validation":vev,"owned_context":owned_ctx})

            if best:
                _, conf, cand, cclass, vev, resolved = best
                if resolved:
                    counts["enriched_or_contextual_geography"] += 1
                with engine.begin() as conn:
                    conn.execute(text("""
                        INSERT INTO alliance_location_candidate_validation_v291
                        (validation_id,entity_id,message_id,candidate_value,candidate_class,validation_evidence,
                         resolved_geography,provenance,confidence,engine_version)
                        VALUES(:id,:eid,:mid,:cv,:cc,CAST(:ve AS jsonb),CAST(:rg AS jsonb),:prov,:conf,:v)
                        ON CONFLICT(entity_id) DO UPDATE SET message_id=EXCLUDED.message_id,
                          candidate_value=EXCLUDED.candidate_value,candidate_class=EXCLUDED.candidate_class,
                          validation_evidence=EXCLUDED.validation_evidence,resolved_geography=EXCLUDED.resolved_geography,
                          provenance=EXCLUDED.provenance,confidence=EXCLUDED.confidence,
                          engine_version=EXCLUDED.engine_version,updated_at=now()
                    """), {
                        "id":str(uuid.uuid4()),"eid":row["entity_id"],"mid":row.get("message_id"),
                        "cv":cand["value"],"cc":cclass,"ve":json.dumps(vev,ensure_ascii=False),
                        "rg":json.dumps(resolved,ensure_ascii=False),
                        "prov":resolved.get("provenance") if resolved else cand.get("provenance"),
                        "conf":conf,"v":ENGINE_VERSION
                    })

            # Deal dimensions.
            deal = _deal_dimensions(raw,row.get("source_class"),owned_fields,legacy)
            counts["deal_total"] += 1
            if deal["transaction_mode"]:
                counts["transaction_resolved"] += 1
            else:
                counts["transaction_abstained"] += 1
            if deal["occupancy_status"] != "UNKNOWN":
                counts["occupancy_resolved"] += 1
            if deal["conflict_with_legacy"]:
                counts["legacy_conflict"] += 1
            if legacy:
                counts["legacy_present"] += 1
            if legacy and not deal["transaction_mode"]:
                _queue_gold_candidate(engine,row,"HARD_TRANSACTION",100,
                    "Legacy transaction exists but canonical transaction abstained; human review should decide whether abstention or lineage recovery is correct.",
                    {"legacy":legacy,"deal":deal,"owned_context":owned_ctx,
                     "raw_text":raw,"parent_message_text":parent_raw})

            source_class = str(row.get("source_class") or "").upper()
            if source_class in ("REQUIREMENT","NOISE","FRAGMENT"):
                _queue_gold_candidate(engine,row,f"UNDERREPRESENTED_{source_class}",85,
                    "Underrepresented Gold V1 class",
                    {"source_class":source_class,"raw_text":raw,"deal":deal})

            with engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO alliance_deal_dimensions_v291
                    (deal_id,entity_id,message_id,intent_direction,transaction_mode,occupancy_status,
                     investment_status,source_provenance,source_evidence,legacy_transaction_type,
                     conflict_with_legacy,abstained,review_flags,ontology_version)
                    VALUES(:id,:eid,:mid,:intent,:tx,:occ,:inv,:prov,CAST(:ev AS jsonb),:legacy,:conflict,
                           :abstain,CAST(:flags AS jsonb),:v)
                    ON CONFLICT(entity_id) DO UPDATE SET message_id=EXCLUDED.message_id,
                      intent_direction=EXCLUDED.intent_direction,transaction_mode=EXCLUDED.transaction_mode,
                      occupancy_status=EXCLUDED.occupancy_status,investment_status=EXCLUDED.investment_status,
                      source_provenance=EXCLUDED.source_provenance,source_evidence=EXCLUDED.source_evidence,
                      legacy_transaction_type=EXCLUDED.legacy_transaction_type,
                      conflict_with_legacy=EXCLUDED.conflict_with_legacy,abstained=EXCLUDED.abstained,
                      review_flags=EXCLUDED.review_flags,ontology_version=EXCLUDED.ontology_version,
                      updated_at=now()
                """), {
                    "id":str(uuid.uuid4()),"eid":row["entity_id"],"mid":row.get("message_id"),
                    "intent":deal["intent_direction"],"tx":deal["transaction_mode"],
                    "occ":deal["occupancy_status"],"inv":deal["investment_status"],
                    "prov":deal["source_provenance"],"ev":json.dumps(deal["source_evidence"],ensure_ascii=False),
                    "legacy":deal["legacy_transaction_type"],"conflict":deal["conflict_with_legacy"],
                    "abstain":deal["abstained"],"flags":json.dumps(deal["review_flags"]),
                    "v":ONTOLOGY_VERSION
                })

            if len(samples) < 25 and (
                diag_class != "ATOMIC_LITERAL_OR_RECOVERABLE"
                or deal["abstained"]
                or (best and best[3] in ("NOT_LOCATION","AMBIGUOUS_LOCATION","COMPOUND_LOCATION"))
            ):
                samples.append({
                    "entity_id":row["entity_id"],
                    "location_diagnostic":diag_class,
                    "best_candidate":None if not best else {
                        "value":best[2]["value"],"class":best[3],"confidence":best[1],
                        "resolved_geography":best[5]
                    },
                    "deal":deal
                })

        except Exception as exc:
            failed.append(f"{row.get('entity_id')}:{type(exc).__name__}:{exc}"[:600])

    total = len(rows)
    _write_metric(engine,"source_location_coverage",counts["source_location_supported"],total,
                  "LIVE_AUTOMATIC",False,"Atomic literal or deterministic recoverable place mention in atomic text.")
    _write_metric(engine,"deterministic_geography_enrichment_coverage",
                  counts["enriched_or_contextual_geography"],total,
                  "LIVE_AUTOMATIC",False,"Deterministic gazetteer or proven contextual geography resolution.")
    _write_metric(engine,"transaction_resolution_coverage",counts["transaction_resolved"],total,
                  "LIVE_AUTOMATIC",False,"Canonical transaction_mode resolved from atomic/proven context.")
    _write_metric(engine,"transaction_abstention_rate",counts["transaction_abstained"],total,
                  "LIVE_AUTOMATIC",False,"Abstention rate only; correctness of abstention requires Gold.")
    _write_metric(engine,"occupancy_resolution_coverage",counts["occupancy_resolved"],total,
                  "LIVE_AUTOMATIC",False,"Occupancy explicit in atomic evidence.")
    _write_metric(engine,"legacy_conflict_rate",counts["legacy_conflict"],counts["legacy_present"],
                  "LIVE_AUTOMATIC",False,"Canonical vs comparable legacy transaction disagreement.")
    _write_metric(engine,"candidate_validation_precision",0,0,"GOLD_RELEASE_GATE",True,
                  "Requires Gold V2 negative-location labels.")
    _write_metric(engine,"transaction_accuracy",0,0,"GOLD_RELEASE_GATE",True,
                  "Requires Gold V2 hard-transaction labels.")
    _write_metric(engine,"geography_hierarchy_accuracy",0,0,"GOLD_RELEASE_GATE",True,
                  "Requires Gold V2 hard-geography labels.")

    return {
        "status":"PASS" if not failed else "PARTIAL",
        "version":VERSION,
        "engine_version":ENGINE_VERSION,
        "seen":total,
        "processed":total-len(failed),
        "failed":len(failed),
        "diagnostic_distribution":dict(diag_counts),
        "candidate_distribution":dict(candidate_counts),
        "automatic_counts":dict(counts),
        "teaching_samples":samples,
        "errors":failed[:10],
        "production_writes":0,
        "whatsapp_live_writes":0,
        "gold_v1_mutations":0,
        "gold_v2_status":"CANDIDATE_QUEUE_ONLY_NO_GOLD_MUTATION"
    }

def status(engine):
    _install(engine)
    with engine.connect() as conn:
        metrics = [dict(x) for x in conn.execute(text("""
            SELECT metric_key,metric_value,numerator,denominator,metric_scope,requires_gold,notes
            FROM alliance_eval2_v291 ORDER BY requires_gold,metric_key
        """)).mappings().all()]
        diag = [dict(x) for x in conn.execute(text("""
            SELECT diagnostic_class,count(*) AS cases
            FROM alliance_location_diagnostic_v291
            WHERE engine_version=:v GROUP BY diagnostic_class ORDER BY cases DESC
        """),{"v":ENGINE_VERSION}).mappings().all()]
        candidates = [dict(x) for x in conn.execute(text("""
            SELECT candidate_class,count(*) AS cases
            FROM alliance_location_candidate_validation_v291
            WHERE engine_version=:v GROUP BY candidate_class ORDER BY cases DESC
        """),{"v":ENGINE_VERSION}).mappings().all()]
        queue = [dict(x) for x in conn.execute(text("""
            SELECT category,count(*) AS cases,round(avg(priority_score),2) AS avg_priority
            FROM alliance_gold_v2_candidate_queue
            WHERE source_version=:v AND status='OPEN'
            GROUP BY category ORDER BY avg_priority DESC,cases DESC
        """),{"v":ENGINE_VERSION}).mappings().all()]
        unknown = [dict(x) for x in conn.execute(text("""
            SELECT candidate_value,candidate_class,confidence,validation_evidence
            FROM alliance_location_candidate_validation_v291
            WHERE engine_version=:v AND candidate_class IN ('VALID_UNKNOWN_PLACE','AMBIGUOUS_LOCATION','COMPOUND_LOCATION','NOT_LOCATION')
            ORDER BY updated_at DESC LIMIT 30
        """),{"v":ENGINE_VERSION}).mappings().all()]
        deal = conn.execute(text("""
            SELECT count(*) n,
                   count(*) FILTER(WHERE transaction_mode IS NOT NULL) tx,
                   count(*) FILTER(WHERE abstained=TRUE) abstain,
                   count(*) FILTER(WHERE occupancy_status<>'UNKNOWN') occ,
                   count(*) FILTER(WHERE conflict_with_legacy=TRUE) conflicts
            FROM alliance_deal_dimensions_v291 WHERE ontology_version=:v
        """),{"v":ONTOLOGY_VERSION}).mappings().first()
    return foundation._json_safe({
        "status":"PASS",
        "version":VERSION,
        "mode":MODE,
        "engine_version":ENGINE_VERSION,
        "gazetteer_version":GAZETTEER_VERSION,
        "ontology_version":ONTOLOGY_VERSION,
        "evaluation_version":EVAL_VERSION,
        "headline_rule":"No composite score. Coverage, abstention, conflict, and Gold-gated accuracy are reported separately.",
        "deal_schema":{
            "intent_direction":["SUPPLY","DEMAND"],
            "transaction_mode":["SALE","RENT","LEASE","BUSINESS_TRANSFER","REVENUE_SHARE","PARTNERSHIP"],
            "occupancy_status":["VACANT","TENANTED","OWNER_OCCUPIED","UNKNOWN"],
            "investment_status":["INCOME_PRODUCING","NON_INCOME","UNKNOWN"],
            "canonical_BOTH":"RETIRED",
            "requirement_purchase_mapping":"DEMAND + SALE",
            "requirement_lease_mapping":"DEMAND + LEASE or RENT"
        },
        "automatic_metrics":metrics,
        "location_diagnostic":diag,
        "candidate_validation":candidates,
        "gold_v2_candidate_queue":queue,
        "candidate_examples":unknown,
        "deal_counts":dict(deal or {}),
        "sender_history_geography_policy":"FORBIDDEN_AS_CANONICAL_EVIDENCE",
        "legacy_transaction_policy":"AUDIT_AND_CONFLICT_ONLY_NEVER_CANONICAL_SOURCE",
        "production_writes":0,
        "whatsapp_live_writes":0,
        "gold_v1_mutations":0
    })

DASH = """<!doctype html><html><body style='font-family:Arial;background:#07111a;color:#eef6ff;max-width:1320px;margin:28px auto'>
<h1>🎓 Foundation 2.9.1 Revised</h1>
<p>Location boundary diagnostic → candidate validation → contextual geography → transaction lineage → Evaluation 2.0.</p>
<p><b>No composite score.</b> Source coverage, enrichment coverage, abstention, conflict and Gold-gated accuracy remain separate.</p>
<button onclick='run()' style='padding:14px 22px;border:0;border-radius:9px;background:#f5d76e;font-weight:bold'>Run Revised 1000</button>
<button onclick='status()' style='padding:14px 22px'>Refresh</button>
<h2>Evaluation 2.0</h2><pre id='s'></pre>
<h2>Run Result</h2><pre id='r'>No run yet.</pre>
<script>
async function call(p,m='GET'){
 const x=await fetch(p,{method:m}); const t=await x.text(); let d;
 try{d=JSON.parse(t)}catch(e){d={raw:t}}
 if(!x.ok)throw Error(d.detail||d.raw||('HTTP '+x.status)); return d
}
async function status(){try{document.getElementById('s').textContent=JSON.stringify(await call('/api/property-brain/infrastructure-v291/status'),null,2)}catch(e){document.getElementById('s').textContent='ERROR '+e.message}}
async function run(){document.getElementById('r').textContent='Running diagnostic + curriculum...';try{document.getElementById('r').textContent=JSON.stringify(await call('/api/property-brain/infrastructure-v291/run?limit=1000','POST'),null,2);await status()}catch(e){document.getElementById('r').textContent='ERROR '+e.message}}
status()
</script></body></html>"""

def register(core):
    engine = _engine(core)
    app = _app(core)
    _install(engine)

    if not foundation._route_exists(app, "/api/property-brain/infrastructure-v291/status"):
        @app.get("/api/property-brain/infrastructure-v291/status")
        def _status():
            return status(engine)

    if not foundation._route_exists(app, "/api/property-brain/infrastructure-v291/run"):
        @app.post("/api/property-brain/infrastructure-v291/run")
        def _run(limit:int=Query(default=1000,ge=1,le=5000)):
            return run(engine,limit)

    if not foundation._route_exists(app, "/property-brain/infrastructure-v291"):
        @app.get("/property-brain/infrastructure-v291",response_class=HTMLResponse)
        def _dashboard():
            return HTMLResponse(DASH)

    return {
        "status":"REGISTERED",
        "version":VERSION,
        "dashboard":"/property-brain/infrastructure-v291",
        "production_writes":0,
        "whatsapp_live_writes":0,
        "gold_v1_mutations":0
    }
