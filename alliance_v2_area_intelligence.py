
import os
import re
from sqlalchemy import create_engine, text
from fastapi import Request
from fastapi.responses import HTMLResponse

MODULE_VERSION = "2.4.4A-AREA-RANGE-SAFE-RECOVERY"

AREA_UNIT_PATTERNS = {
    "SQFT": r"(?:sq\.?\s*ft|sqft|sft|sf|square\s*feet|square\s*foot)",
    "SQYD": r"(?:sq\.?\s*yds?|sqyds?|square\s*yards?|yards?|yds?|gaj)",
    "SQM": r"(?:sq\.?\s*m|sqm|square\s*met(?:er|re)s?)",
    "ACRE": r"(?:acres?|acre)",
}

LABEL_PATTERNS = [
    ("CARPET", r"(?:carpet\s*area|carpet)"),
    ("SUPER", r"(?:super\s*(?:built\s*up\s*)?area|super\s*area|super)"),
    ("BUILT_UP", r"(?:built\s*up\s*area|builtup\s*area|built\s*up)"),
    ("PLOT", r"(?:plot\s*area|plot|land\s*area)"),
    ("CHARGEABLE", r"(?:chargeable\s*area|chargeable)"),
]

MONEY_CONTEXT = re.compile(
    r"(?:₹|\brs\.?\b|\binr\b|\bcrore\b|\bcr\b|\blakh\b|\blac\b|\bprice\b|\brent\b|\basking\b|\bbudget\b)",
    re.I,
)
PHONEISH = re.compile(r"\b(?:\+?91[\s-]?)?[6-9]\d{9}\b")
DATEISH = re.compile(r"\b(?:19|20)\d{2}[-/]\d{1,2}[-/]\d{1,2}\b|\b\d{1,2}[-/]\d{1,2}[-/](?:19|20)?\d{2}\b")
PERCENTISH = re.compile(r"\b\d+(?:\.\d+)?\s*%")
FLOORISH = re.compile(r"\b\d{1,3}(?:st|nd|rd|th)\s*floor\b", re.I)

def _db_url(url):
    url = (url or "").strip()
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url

def _source_engine(primary_engine):
    wa = (os.getenv("WHATSAPP_DATABASE_URL") or "").strip()
    primary = (os.getenv("DATABASE_URL") or "").strip()
    if not wa or wa == primary:
        return primary_engine, False
    return create_engine(
        _db_url(wa),
        pool_pre_ping=True,
        pool_recycle=300,
        connect_args={"connect_timeout": 10},
    ), True

def ensure_schema(engine):
    with engine.begin() as c:
        c.execute(text("""
        CREATE TABLE IF NOT EXISTS ai_whatsapp_area_intelligence (
          listing_id UUID PRIMARY KEY,
          area_min_sqft NUMERIC(14,2),
          area_max_sqft NUMERIC(14,2),
          carpet_area_sqft NUMERIC(14,2),
          super_area_sqft NUMERIC(14,2),
          built_up_area_sqft NUMERIC(14,2),
          plot_area_sqft NUMERIC(14,2),
          chargeable_area_sqft NUMERIC(14,2),
          area_confidence NUMERIC(5,2),
          area_source TEXT,
          recovery_method TEXT,
          evidence_text TEXT,
          rejected_numeric_tokens JSONB NOT NULL DEFAULT '[]'::jsonb,
          model_version TEXT NOT NULL,
          evaluated_at TIMESTAMPTZ DEFAULT NOW()
        )
        """))
        c.execute(text("""
          CREATE INDEX IF NOT EXISTS ix_ai_whatsapp_area_intelligence_conf
          ON ai_whatsapp_area_intelligence(area_confidence DESC)
        """))

def _number(token):
    s = str(token or "").lower().strip().replace(",", "")
    mult = 1.0
    if s.endswith("k"):
        mult = 1000.0
        s = s[:-1].strip()
    try:
        n = float(s) * mult
    except Exception:
        return None
    if n != n or n < 1 or n > 100_000_000:
        return None
    return n

def _to_sqft(value, unit):
    if value is None:
        return None
    if unit == "SQFT":
        x = value
    elif unit == "SQYD":
        x = value * 9.0
    elif unit == "SQM":
        x = value * 10.7639
    elif unit == "ACRE":
        x = value * 43560.0
    else:
        return None
    if x < 1 or x > 100_000_000:
        return None
    return round(x, 2)

def _unit_from_text(s):
    for unit, pat in AREA_UNIT_PATTERNS.items():
        if re.search(rf"{pat}", s, re.I):
            return unit
    return None

def _context_reject(raw, start, end):
    left = raw[max(0, start - 22):start]
    right = raw[end:min(len(raw), end + 22)]
    context = left + raw[start:end] + right
    token = raw[start:end]

    if PHONEISH.search(context):
        return "phone_number"
    if DATEISH.search(context):
        return "date"
    if PERCENTISH.search(context):
        return "percentage"
    if FLOORISH.search(context):
        return "floor_number"
    if MONEY_CONTEXT.search(context) and not _unit_from_text(context):
        return "money_context"

    digits_only = re.sub(r"\D", "", token)
    # Long bare numbers are suspicious, but explicit area-unit phrases are safe.
    if re.fullmatch(r"\d{7,}", digits_only) and not _unit_from_text(context):
        return "long_numeric_token"

    return None

def extract_area_intelligence(raw_text):
    raw = str(raw_text or "")
    low = raw.lower()
    found = []
    rejected = []

    unit_alt = "|".join(f"(?:{p})" for p in AREA_UNIT_PATTERNS.values())

    # Range parser. Important: consumes BOTH numbers before the unit.
    range_re = re.compile(
        rf"(?<!\d)(\d+(?:\.\d+)?\s*k?)\s*(?:-|–|—|\bto\b)\s*(\d+(?:\.\d+)?\s*k?)\s*({unit_alt})",
        re.I
    )
    occupied = []
    for m in range_re.finditer(low):
        reason = _context_reject(low, m.start(), m.end())
        if reason:
            rejected.append({"token": m.group(0), "reason": reason})
            continue
        unit = _unit_from_text(m.group(3))
        a = _to_sqft(_number(m.group(1)), unit)
        b = _to_sqft(_number(m.group(2)), unit)
        if a and b:
            found.append({
                "kind": "RANGE",
                "min": min(a, b),
                "max": max(a, b),
                "value": None,
                "confidence": 98,
                "method": "EXPLICIT_RANGE_WITH_UNIT",
                "evidence": raw[m.start():m.end()],
            })
            occupied.append((m.start(), m.end()))

    # Labeled areas.
    for label, label_pat in LABEL_PATTERNS:
        labeled_re = re.compile(
            rf"\b{label_pat}\b\s*[:=\-]?\s*(\d+(?:\.\d+)?\s*k?)\s*({unit_alt})",
            re.I
        )
        for m in labeled_re.finditer(low):
            if any(m.start() >= a and m.end() <= b for a, b in occupied):
                continue
            reason = _context_reject(low, m.start(), m.end())
            if reason:
                rejected.append({"token": m.group(0), "reason": reason})
                continue
            unit = _unit_from_text(m.group(2))
            v = _to_sqft(_number(m.group(1)), unit)
            if v:
                found.append({
                    "kind": label,
                    "min": v, "max": v, "value": v,
                    "confidence": 99,
                    "method": "LABELED_AREA_EXTRACTION",
                    "evidence": raw[m.start():m.end()],
                })
                occupied.append((m.start(), m.end()))

    # Generic explicit area. Skip text already consumed by range/labeled matches.
    generic_re = re.compile(
        rf"(?<!\d)(\d+(?:\.\d+)?\s*k?)\s*({unit_alt})",
        re.I
    )
    for m in generic_re.finditer(low):
        if any(m.start() >= a and m.end() <= b for a, b in occupied):
            continue
        reason = _context_reject(low, m.start(), m.end())
        if reason:
            rejected.append({"token": m.group(0), "reason": reason})
            continue
        unit = _unit_from_text(m.group(2))
        v = _to_sqft(_number(m.group(1)), unit)
        if not v:
            continue
        found.append({
            "kind": "GENERIC",
            "min": v, "max": v, "value": v,
            "confidence": 94,
            "method": "EXPLICIT_UNIT_EXTRACTION",
            "evidence": raw[m.start():m.end()],
        })

    if not found:
        return {
            "area_min_sqft": None,
            "area_max_sqft": None,
            "carpet_area_sqft": None,
            "super_area_sqft": None,
            "built_up_area_sqft": None,
            "plot_area_sqft": None,
            "chargeable_area_sqft": None,
            "area_confidence": 0,
            "area_source": None,
            "recovery_method": None,
            "evidence_text": None,
            "rejected_numeric_tokens": rejected,
        }

    semantic = {"CARPET": None, "SUPER": None, "BUILT_UP": None, "PLOT": None, "CHARGEABLE": None}
    for x in found:
        if x["kind"] in semantic:
            semantic[x["kind"]] = x["value"]

    ranges = [x for x in found if x["kind"] == "RANGE"]
    if ranges:
        best = max(ranges, key=lambda x: x["confidence"])
        amin, amax = best["min"], best["max"]
    elif semantic["CARPET"] and semantic["SUPER"]:
        amin, amax = min(semantic["CARPET"], semantic["SUPER"]), max(semantic["CARPET"], semantic["SUPER"])
    elif semantic["CARPET"]:
        amin = amax = semantic["CARPET"]
    elif semantic["BUILT_UP"]:
        amin = amax = semantic["BUILT_UP"]
    elif semantic["SUPER"]:
        amin = amax = semantic["SUPER"]
    elif semantic["PLOT"]:
        amin = amax = semantic["PLOT"]
    else:
        values = [x["value"] for x in found if x.get("value")]
        amin, amax = min(values), max(values)

    best_conf = max(x["confidence"] for x in found)
    methods = []
    evidences = []
    for x in found:
        if x["method"] not in methods:
            methods.append(x["method"])
        if x["evidence"] not in evidences:
            evidences.append(x["evidence"])

    return {
        "area_min_sqft": round(amin, 2) if amin else None,
        "area_max_sqft": round(amax, 2) if amax else None,
        "carpet_area_sqft": semantic["CARPET"],
        "super_area_sqft": semantic["SUPER"],
        "built_up_area_sqft": semantic["BUILT_UP"],
        "plot_area_sqft": semantic["PLOT"],
        "chargeable_area_sqft": semantic["CHARGEABLE"],
        "area_confidence": best_conf,
        "area_source": "RAW_TEXT",
        "recovery_method": "+".join(methods),
        "evidence_text": " | ".join(evidences[:8]),
        "rejected_numeric_tokens": rejected[:20],
    }

def run_area_intelligence(primary_engine):
    ensure_schema(primary_engine)
    source_engine, owned = _source_engine(primary_engine)
    try:
        with source_engine.connect() as src:
            rows = src.execute(text("""
              SELECT id,raw_listing_text,summary
              FROM wai_listings
            """)).mappings().all()

        with primary_engine.connect() as c:
            missing = {
                str(r["listing_id"])
                for r in c.execute(text("""
                  SELECT listing_id
                  FROM ai_whatsapp_purity
                  WHERE recovered_area_min_sqft IS NULL
                     OR recovered_area_max_sqft IS NULL
                """)).mappings().all()
            }

        upserts = []
        purity_updates = []
        evaluated = recovered = labeled = ranged = rejected_tokens = 0

        for row in rows:
            rid = str(row["id"])
            if rid not in missing:
                continue
            evaluated += 1
            raw = row.get("raw_listing_text") or row.get("summary") or ""
            x = extract_area_intelligence(raw)
            rejected_tokens += len(x["rejected_numeric_tokens"])
            if not x["area_min_sqft"]:
                continue

            recovered += 1
            if "LABELED_AREA_EXTRACTION" in str(x["recovery_method"]):
                labeled += 1
            if "EXPLICIT_RANGE_WITH_UNIT" in str(x["recovery_method"]):
                ranged += 1

            import json
            upserts.append({
                "id": rid,
                "amin": x["area_min_sqft"],
                "amax": x["area_max_sqft"],
                "carpet": x["carpet_area_sqft"],
                "super": x["super_area_sqft"],
                "built": x["built_up_area_sqft"],
                "plot": x["plot_area_sqft"],
                "charge": x["chargeable_area_sqft"],
                "conf": x["area_confidence"],
                "source": x["area_source"],
                "method": x["recovery_method"],
                "evidence": x["evidence_text"],
                "rejected": json.dumps(x["rejected_numeric_tokens"]),
                "version": MODULE_VERSION,
            })
            purity_updates.append({"id": rid, "amin": x["area_min_sqft"], "amax": x["area_max_sqft"]})

        if upserts:
            with primary_engine.begin() as c:
                c.execute(text("""
                  INSERT INTO ai_whatsapp_area_intelligence(
                    listing_id,area_min_sqft,area_max_sqft,carpet_area_sqft,super_area_sqft,
                    built_up_area_sqft,plot_area_sqft,chargeable_area_sqft,area_confidence,
                    area_source,recovery_method,evidence_text,rejected_numeric_tokens,
                    model_version,evaluated_at
                  )
                  VALUES(
                    CAST(:id AS uuid),:amin,:amax,:carpet,:super,:built,:plot,:charge,:conf,
                    :source,:method,:evidence,CAST(:rejected AS jsonb),:version,NOW()
                  )
                  ON CONFLICT(listing_id) DO UPDATE SET
                    area_min_sqft=EXCLUDED.area_min_sqft,
                    area_max_sqft=EXCLUDED.area_max_sqft,
                    carpet_area_sqft=EXCLUDED.carpet_area_sqft,
                    super_area_sqft=EXCLUDED.super_area_sqft,
                    built_up_area_sqft=EXCLUDED.built_up_area_sqft,
                    plot_area_sqft=EXCLUDED.plot_area_sqft,
                    chargeable_area_sqft=EXCLUDED.chargeable_area_sqft,
                    area_confidence=EXCLUDED.area_confidence,
                    area_source=EXCLUDED.area_source,
                    recovery_method=EXCLUDED.recovery_method,
                    evidence_text=EXCLUDED.evidence_text,
                    rejected_numeric_tokens=EXCLUDED.rejected_numeric_tokens,
                    model_version=EXCLUDED.model_version,
                    evaluated_at=NOW()
                """), upserts)

                c.execute(text("""
                  UPDATE ai_whatsapp_purity
                  SET recovered_area_min_sqft=:amin,
                      recovered_area_max_sqft=:amax,
                      purity_score=LEAST(100,COALESCE(purity_score,0)+10),
                      review_status=CASE
                        WHEN review_status IN ('LOW_CONFIDENCE','UNKNOWN') THEN 'NEEDS_REVIEW'
                        ELSE review_status
                      END,
                      last_recovered_at=NOW()
                  WHERE listing_id=CAST(:id AS uuid)
                    AND (recovered_area_min_sqft IS NULL OR recovered_area_max_sqft IS NULL)
                """), purity_updates)

        return {
            "version": MODULE_VERSION,
            "evaluated_missing_area_rows": evaluated,
            "recovered_area_rows": recovered,
            "labeled_area_rows": labeled,
            "range_area_rows": ranged,
            "rejected_numeric_tokens": rejected_tokens,
            "source_data_modified": False,
            "derived_purity_updated": recovered,
            "next_step": "Run V2.4.3 prioritization",
        }
    finally:
        if owned:
            source_engine.dispose()

def area_summary(engine):
    ensure_schema(engine)
    with engine.connect() as c:
        r = c.execute(text("""
          SELECT
            COUNT(*)::int total,
            COUNT(*) FILTER (WHERE carpet_area_sqft IS NOT NULL)::int carpet,
            COUNT(*) FILTER (WHERE super_area_sqft IS NOT NULL)::int super,
            COUNT(*) FILTER (WHERE built_up_area_sqft IS NOT NULL)::int built_up,
            COUNT(*) FILTER (WHERE plot_area_sqft IS NOT NULL)::int plot,
            ROUND(AVG(area_confidence),2) avg_confidence
          FROM ai_whatsapp_area_intelligence
        """)).mappings().one()
    return dict(r)

def register_area_intelligence_routes(core):
    app, engine = core.app, core.engine
    ensure_schema(engine)

    @app.post("/api/v2/intelligence/whatsapp-area/run")
    def area_run(req: Request):
        if hasattr(core, "need_login"):
            core.need_login(req)
        return run_area_intelligence(engine)

    @app.get("/api/v2/intelligence/whatsapp-area/summary")
    def summary(req: Request):
        if hasattr(core, "need_login"):
            core.need_login(req)
        return {"version": MODULE_VERSION, **area_summary(engine)}

    @app.get("/v2/whatsapp-area-intelligence", response_class=HTMLResponse)
    def page(req: Request):
        if hasattr(core, "need_login"):
            core.need_login(req)
        s = area_summary(engine)
        return HTMLResponse(f"""<!doctype html>
<html><head><meta charset="utf-8"><title>WhatsApp Area Intelligence</title></head>
<body style="font-family:Arial;background:#f5f7fa">
<div style="max-width:1000px;margin:30px auto;background:white;padding:25px;border-radius:12px">
<h1>V2.4.4A WhatsApp Area Intelligence</h1>
<p>Recovered rows: <b>{s.get('total',0)}</b></p>
<p>Carpet: <b>{s.get('carpet',0)}</b> · Super: <b>{s.get('super',0)}</b> · Plot: <b>{s.get('plot',0)}</b></p>
<p>Average confidence: <b>{s.get('avg_confidence') or 0}</b></p>
<p>Raw WhatsApp source data is never modified.</p>
</div></body></html>""")

    return app
