from __future__ import annotations

import hashlib
import re
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import text

VERSION = "3.8.3-CANONICAL-DATABASE-FOUNDATION"

SCHEMA = r"""
CREATE TABLE IF NOT EXISTS alliance_canonical_properties(
    id BIGSERIAL PRIMARY KEY,
    property_code TEXT UNIQUE NOT NULL,
    canonical_fingerprint TEXT UNIQUE NOT NULL,
    property_name TEXT,
    canonical_location TEXT,
    city TEXT,
    building_project TEXT,
    property_type TEXT,
    transaction_type TEXT,
    area_sqft NUMERIC(14,2),
    floor TEXT,
    intended_use_tags TEXT,
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS alliance_property_listings(
    id BIGSERIAL PRIMARY KEY,
    listing_code TEXT UNIQUE NOT NULL,
    property_code TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_table TEXT NOT NULL DEFAULT '',
    source_record_id TEXT NOT NULL DEFAULT '',
    source_name TEXT,
    raw_text TEXT,
    asking_rent_inr NUMERIC(16,2),
    asking_sale_price_inr NUMERIC(16,2),
    availability_status TEXT DEFAULT 'UNKNOWN',
    verification_status TEXT DEFAULT 'UNVERIFIED',
    verification_confidence NUMERIC(5,2) DEFAULT 0,
    captured_at TIMESTAMPTZ,
    last_verified_at TIMESTAMPTZ,
    next_verification_due TIMESTAMPTZ,
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(source_type, source_table, source_record_id)
);

CREATE TABLE IF NOT EXISTS alliance_contacts(
    id BIGSERIAL PRIMARY KEY,
    contact_code TEXT UNIQUE NOT NULL,
    normalized_phone TEXT UNIQUE,
    display_name TEXT,
    firm_brokerage TEXT,
    contact_type TEXT DEFAULT 'BROKER',
    notes TEXT,
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS alliance_listing_contacts(
    id BIGSERIAL PRIMARY KEY,
    listing_code TEXT NOT NULL,
    contact_code TEXT NOT NULL,
    relationship_type TEXT DEFAULT 'BROKER',
    is_primary BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(listing_code, contact_code, relationship_type)
);

CREATE TABLE IF NOT EXISTS alliance_location_aliases(
    id BIGSERIAL PRIMARY KEY,
    alias_text TEXT UNIQUE NOT NULL,
    canonical_location TEXT NOT NULL,
    city TEXT,
    confidence NUMERIC(5,2) DEFAULT 100,
    approved BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS alliance_database_migrations(
    id BIGSERIAL PRIMARY KEY,
    migration_key TEXT UNIQUE NOT NULL,
    status TEXT NOT NULL,
    rows_processed INTEGER DEFAULT 0,
    details TEXT,
    completed_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_alliance_property_location ON alliance_canonical_properties(canonical_location);
CREATE INDEX IF NOT EXISTS idx_alliance_property_type ON alliance_canonical_properties(property_type);
CREATE INDEX IF NOT EXISTS idx_alliance_listing_property ON alliance_property_listings(property_code);
CREATE INDEX IF NOT EXISTS idx_alliance_listing_status ON alliance_property_listings(verification_status, availability_status);
CREATE INDEX IF NOT EXISTS idx_alliance_contact_phone ON alliance_contacts(normalized_phone);
"""

LOCATION_SEED = {
    "saket": "Saket",
    "saket district centre": "Saket",
    "saket district center": "Saket",
    "select citywalk": "Saket",
    "select city walk": "Saket",
    "dlf avenue saket": "Saket",
    "greater kailash 1": "Greater Kailash 1",
    "gk 1": "Greater Kailash 1",
    "gk-1": "Greater Kailash 1",
    "greater kailash 2": "Greater Kailash 2",
    "gk 2": "Greater Kailash 2",
    "gk-2": "Greater Kailash 2",
    "hauz khas": "Hauz Khas",
    "vasant kunj": "Vasant Kunj",
    "vasant vihar": "Vasant Vihar",
    "defence colony": "Defence Colony",
    "defense colony": "Defence Colony",
    "south extension": "South Extension",
    "south ex": "South Extension",
    "nehru place": "Nehru Place",
    "connaught place": "Connaught Place",
    "aerocity": "Aerocity",
    "cyber city": "Cyber City",
    "dlf cyber city": "Cyber City",
    "cyber hub": "Cyber City",
}

def _ensure_schema(engine):
    with engine.begin() as c:
        for stmt in [s.strip() for s in SCHEMA.split(";") if s.strip()]:
            c.execute(text(stmt))
        for alias, canonical in LOCATION_SEED.items():
            c.execute(text("""
                INSERT INTO alliance_location_aliases(alias_text,canonical_location,city,confidence,approved)
                VALUES(:a,:c,'Delhi NCR',100,TRUE)
                ON CONFLICT(alias_text) DO UPDATE SET
                    canonical_location=EXCLUDED.canonical_location,
                    updated_at=NOW()
            """), {"a": alias, "c": canonical})

def _table_exists(c, name):
    return bool(c.execute(text("SELECT to_regclass(:n) IS NOT NULL"), {"n": "public."+name}).scalar())

def _clean(v):
    return re.sub(r"\s+", " ", str(v or "").replace("\u00a0", " ")).strip()

def _phone(v) -> Optional[str]:
    digits = re.sub(r"\D", "", str(v or ""))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10 and digits[0] in "6789":
        return "+91" + digits
    return None

def _num(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except Exception:
        m = re.search(r"\d+(?:\.\d+)?", str(v).replace(",", ""))
        return float(m.group(0)) if m else None

def _money(v):
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).lower().replace(",", "").replace("₹", "")
    m = re.search(r"\d+(?:\.\d+)?", s)
    if not m:
        return None
    n = float(m.group(0))
    if "crore" in s or re.search(r"\bcr\b", s):
        n *= 10_000_000
    elif "lakh" in s or "lac" in s:
        n *= 100_000
    return n

def _normalize_location(c, raw):
    value = _clean(raw)
    if not value:
        return "UNKNOWN"
    low = value.lower()
    exact = c.execute(text("""
        SELECT canonical_location FROM alliance_location_aliases
        WHERE alias_text=:a AND approved=TRUE LIMIT 1
    """), {"a": low}).scalar()
    if exact:
        return exact
    row = c.execute(text("""
        SELECT canonical_location FROM alliance_location_aliases
        WHERE approved=TRUE AND length(alias_text)>=5
          AND (:raw LIKE '%' || alias_text || '%')
        ORDER BY length(alias_text) DESC LIMIT 1
    """), {"raw": low}).scalar()
    return row or value

def _fingerprint(location, building, property_name, floor, area, property_type, transaction):
    n = _num(area)
    area_band = "NA" if not n else str(int(max(100, round(n / 100) * 100)))
    base = "|".join([
        _clean(location).lower(), _clean(building or property_name).lower(),
        _clean(floor).lower(), area_band, _clean(property_type).lower(),
        _clean(transaction).upper()
    ])
    return hashlib.sha256(base.encode()).hexdigest()

def _code(prefix, seed):
    return prefix + "-" + hashlib.sha1(seed.encode()).hexdigest()[:12].upper()

def _upsert_property(c, row):
    loc = _normalize_location(c, row.get("location"))
    fp = _fingerprint(loc, row.get("building_project"), row.get("property_name"),
                      row.get("floor"), row.get("area_sqft"), row.get("property_type"),
                      row.get("transaction_type"))
    code = _code("PROP", fp)
    c.execute(text("""
        INSERT INTO alliance_canonical_properties(
          property_code,canonical_fingerprint,property_name,canonical_location,city,
          building_project,property_type,transaction_type,area_sqft,floor,intended_use_tags)
        VALUES(:pc,:fp,:pn,:loc,:city,:bp,:pt,:tx,:area,:floor,:tags)
        ON CONFLICT(canonical_fingerprint) DO UPDATE SET
          property_name=COALESCE(NULLIF(EXCLUDED.property_name,''),alliance_canonical_properties.property_name),
          canonical_location=COALESCE(NULLIF(EXCLUDED.canonical_location,''),alliance_canonical_properties.canonical_location),
          building_project=COALESCE(NULLIF(EXCLUDED.building_project,''),alliance_canonical_properties.building_project),
          property_type=COALESCE(NULLIF(EXCLUDED.property_type,''),alliance_canonical_properties.property_type),
          transaction_type=COALESCE(NULLIF(EXCLUDED.transaction_type,''),alliance_canonical_properties.transaction_type),
          area_sqft=COALESCE(EXCLUDED.area_sqft,alliance_canonical_properties.area_sqft),
          floor=COALESCE(NULLIF(EXCLUDED.floor,''),alliance_canonical_properties.floor),
          updated_at=NOW(), active=TRUE
    """), {"pc":code,"fp":fp,"pn":_clean(row.get("property_name")),"loc":loc,
           "city":_clean(row.get("city")) or "Delhi NCR","bp":_clean(row.get("building_project")),
           "pt":_clean(row.get("property_type")),"tx":_clean(row.get("transaction_type")),
           "area":_num(row.get("area_sqft")),"floor":_clean(row.get("floor")),
           "tags":_clean(row.get("intended_use_tags"))})
    return c.execute(text("SELECT property_code FROM alliance_canonical_properties WHERE canonical_fingerprint=:fp"),
                     {"fp":fp}).scalar() or code

def _upsert_listing(c, property_code, row):
    st = _clean(row.get("source_type")) or "UNKNOWN"
    table = _clean(row.get("source_table"))
    rid = _clean(row.get("source_record_id"))
    seed = f"{st}|{table}|{rid}"
    lc = _code("LIST", seed)
    c.execute(text("""
        INSERT INTO alliance_property_listings(
          listing_code,property_code,source_type,source_table,source_record_id,source_name,raw_text,
          asking_rent_inr,asking_sale_price_inr,availability_status,verification_status,
          verification_confidence,captured_at,last_verified_at,active)
        VALUES(:lc,:pc,:st,:tb,:rid,:sn,:raw,:rent,:sale,:avail,:verify,:vc,:captured,:lastv,TRUE)
        ON CONFLICT(source_type,source_table,source_record_id) DO UPDATE SET
          property_code=EXCLUDED.property_code,
          source_name=COALESCE(NULLIF(EXCLUDED.source_name,''),alliance_property_listings.source_name),
          raw_text=COALESCE(NULLIF(EXCLUDED.raw_text,''),alliance_property_listings.raw_text),
          asking_rent_inr=COALESCE(EXCLUDED.asking_rent_inr,alliance_property_listings.asking_rent_inr),
          asking_sale_price_inr=COALESCE(EXCLUDED.asking_sale_price_inr,alliance_property_listings.asking_sale_price_inr),
          availability_status=COALESCE(NULLIF(EXCLUDED.availability_status,''),alliance_property_listings.availability_status),
          verification_status=COALESCE(NULLIF(EXCLUDED.verification_status,''),alliance_property_listings.verification_status),
          verification_confidence=GREATEST(alliance_property_listings.verification_confidence,EXCLUDED.verification_confidence),
          updated_at=NOW(),active=TRUE
    """), {"lc":lc,"pc":property_code,"st":st,"tb":table,"rid":rid,
           "sn":_clean(row.get("source_name")),"raw":row.get("raw_text") or "",
           "rent":_money(row.get("rent_inr")),"sale":_money(row.get("sale_price_inr")),
           "avail":_clean(row.get("availability_status")) or "UNKNOWN",
           "verify":_clean(row.get("verification_status")) or "UNVERIFIED",
           "vc":float(row.get("verification_confidence") or 0),
           "captured":row.get("captured_at"),"lastv":row.get("last_verified_at")})
    return c.execute(text("""
        SELECT listing_code FROM alliance_property_listings
        WHERE source_type=:st AND source_table=:tb AND source_record_id=:rid
    """), {"st":st,"tb":table,"rid":rid}).scalar() or lc

def _upsert_contact(c, listing_code, name, phone, relationship, primary=False):
    p = _phone(phone)
    if not p:
        return
    cc = _code("CON", p)
    c.execute(text("""
        INSERT INTO alliance_contacts(contact_code,normalized_phone,display_name,contact_type)
        VALUES(:cc,:p,:n,:t)
        ON CONFLICT(normalized_phone) DO UPDATE SET
          display_name=COALESCE(NULLIF(EXCLUDED.display_name,''),alliance_contacts.display_name),
          active=TRUE,updated_at=NOW()
    """), {"cc":cc,"p":p,"n":_clean(name),"t":relationship})
    actual = c.execute(text("SELECT contact_code FROM alliance_contacts WHERE normalized_phone=:p"), {"p":p}).scalar() or cc
    c.execute(text("""
        INSERT INTO alliance_listing_contacts(listing_code,contact_code,relationship_type,is_primary)
        VALUES(:lc,:cc,:rel,:pri)
        ON CONFLICT(listing_code,contact_code,relationship_type) DO UPDATE SET
          is_primary=alliance_listing_contacts.is_primary OR EXCLUDED.is_primary
    """), {"lc":listing_code,"cc":actual,"rel":relationship,"pri":bool(primary)})

def _migrate(engine, limit=10000):
    _ensure_schema(engine)
    manual = clean = 0
    with engine.begin() as c:
        if _table_exists(c, "pi_properties"):
            for d in c.execute(text("SELECT * FROM pi_properties ORDER BY id LIMIT :lim"), {"lim":limit}).mappings():
                pc = _upsert_property(c, {
                    "property_name":d.get("property_name") or d.get("property_id"),
                    "location":d.get("location"),"city":d.get("city"),
                    "building_project":d.get("property_name"),"property_type":d.get("property_type"),
                    "transaction_type":d.get("rent_or_sale"),
                    "area_sqft":d.get("available_area_sqft") or d.get("minimum_area_sqft") or d.get("maximum_area_sqft"),
                    "floor":d.get("floor"),"intended_use_tags":d.get("suitable_category")
                })
                lc = _upsert_listing(c, pc, {
                    "source_type":"MANUAL","source_table":"pi_properties",
                    "source_record_id":str(d.get("property_id") or d.get("id")),
                    "source_name":d.get("source") or "Manual Property Database",
                    "raw_text":d.get("remarks") or "",
                    "availability_status":d.get("availability_status") or "UNKNOWN",
                    "verification_status":d.get("verification_status") or "UNVERIFIED",
                    "verification_confidence":100 if str(d.get("verification_status") or "").upper()=="VERIFIED" else 0,
                    "captured_at":d.get("created_at"),"last_verified_at":d.get("verified_date")
                })
                _upsert_contact(c, lc, d.get("owner_name"), d.get("owner_contact"), "OWNER", True)
                _upsert_contact(c, lc, d.get("broker_name"), d.get("broker_contact"), "BROKER", not bool(d.get("owner_contact")))
                manual += 1

        if _table_exists(c, "ai_clean_property_entity"):
            for d in c.execute(text("SELECT * FROM ai_clean_property_entity WHERE active=TRUE ORDER BY id LIMIT :lim"),
                               {"lim":limit}).mappings():
                pc = _upsert_property(c, {
                    "property_name":d.get("property_name"),"location":d.get("location"),
                    "city":"Delhi NCR","building_project":d.get("property_name"),
                    "property_type":d.get("property_type"),"transaction_type":d.get("transaction_type"),
                    "area_sqft":d.get("area_sqft")
                })
                lc = _upsert_listing(c, pc, {
                    "source_type":d.get("source_type") or "UNKNOWN",
                    "source_table":d.get("source_table") or "ai_clean_property_entity",
                    "source_record_id":d.get("source_record_id") or d.get("entity_code"),
                    "source_name":d.get("source_name"),
                    "raw_text":d.get("raw_text") or d.get("description") or "",
                    "rent_inr":d.get("rent_inr"),"sale_price_inr":d.get("sale_price_inr"),
                    "verification_status":d.get("verification_status") or "UNVERIFIED",
                    "verification_confidence":100 if str(d.get("verification_status") or "").upper()=="VERIFIED" else 0,
                    "captured_at":d.get("capture_date") or d.get("created_at")
                })
                _upsert_contact(c, lc, d.get("contact_name"), d.get("contact_phone"), "BROKER", True)
                clean += 1
        total = manual + clean
        c.execute(text("""
            INSERT INTO alliance_database_migrations(migration_key,status,rows_processed,details)
            VALUES('v383_foundation_sync','COMPLETED',:n,:d)
            ON CONFLICT(migration_key) DO UPDATE SET
              status='COMPLETED',rows_processed=:n,details=:d,completed_at=NOW()
        """), {"n":total,"d":f"pi_properties={manual}; ai_clean_property_entity={clean}"})
    return {"manual_rows":manual,"clean_entity_rows":clean,"rows_processed":manual+clean}

def register(core):
    app, engine, need_login = core.app, core.engine, core.need_login
    router = APIRouter(tags=["Alliance Canonical Database V383"])
    # STARTUP HOTFIX:
    # Never run the full legacy migration while newspaper_wrapper is importing.
    # Route registration must stay fast so Railway core can become READY.
    # Existing canonical data is preserved. Manual resync remains available at /api/v383/sync.
    try:
        _ensure_schema(engine)
        startup_status, startup_error = "READY", None
    except Exception as exc:
        startup_status = "DEGRADED"
        startup_error = f"{type(exc).__name__}: {exc}"
        print("[v383] startup warning:", startup_error)

    @router.get("/api/v383/status")
    def status(req: Request):
        need_login(req)
        _ensure_schema(engine)
        with engine.connect() as c:
            counts = {
                "properties":c.execute(text("SELECT COUNT(*) FROM alliance_canonical_properties WHERE active=TRUE")).scalar(),
                "listings":c.execute(text("SELECT COUNT(*) FROM alliance_property_listings WHERE active=TRUE")).scalar(),
                "contacts":c.execute(text("SELECT COUNT(*) FROM alliance_contacts WHERE active=TRUE")).scalar(),
                "listing_contacts":c.execute(text("SELECT COUNT(*) FROM alliance_listing_contacts")).scalar(),
                "location_aliases":c.execute(text("SELECT COUNT(*) FROM alliance_location_aliases WHERE approved=TRUE")).scalar(),
            }
        return {"version":VERSION,"status":startup_status,"startup_error":startup_error,
                "authoritative_foundation":True,"legacy_tables_preserved":True,"startup_mode":"SCHEMA_ONLY_NONBLOCKING","counts":counts}

    @router.post("/api/v383/sync")
    def sync(req: Request, limit: int = Query(10000, ge=1, le=50000)):
        need_login(req)
        try:
            return {"status":"OK","version":VERSION,**_migrate(engine,limit)}
        except Exception as exc:
            raise HTTPException(500, f"V383_SYNC_FAILED: {type(exc).__name__}: {exc}")

    app.include_router(router)
    return router
