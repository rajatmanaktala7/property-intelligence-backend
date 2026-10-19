"""Alliance Team Panel V1.

A separate, very simple daily workspace for the sales team
(Yogesh Mehra, Zoya Saifi, Priya).

Every working morning the panel prepares, per team member:
  Task 1  WhatsApp outreach: 10 batches x 25 hospitality/retail outlet contacts
  Task 2  Hot matches: 10 requirement -> Master property matches with a ready,
          contact-free message for the broker, plus an internal source card
Plus a follow-up list so no lead is missed, a daily report per member and a
monthly AI performance review.

Safety contracts (Alliance feature-freeze contracts are respected):
  * Reads Master authorities only (pi_requirement_gate_v1191 through the
    master matcher, pi_master_properties_v711, ai_marketing_contacts).
  * Never writes to any existing table. All state lives in tp_* tables.
  * Never guesses phone numbers. Only valid Indian 10 digit mobiles are used.
  * Contact numbers are never placed inside a broker-facing match message.
  * Every route requires the normal Alliance login. Contacts stay staff-only.
"""
from __future__ import annotations

import html
import json
import os
import re
import threading
import time
import traceback
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

from sqlalchemy import inspect, text

VERSION = "1.0.0-TEAM-PANEL-DAILY-TASKS"
PREFIX = "/team-panel"
IST = timezone(timedelta(hours=5, minutes=30))

# ---------------------------------------------------------------- config

DEFAULT_MEMBERS: Dict[str, Dict[str, Any]] = {
    "yogesh": {"name": "Yogesh Mehra", "short": "Yogesh", "regions": ["NCR"]},
    "zoya": {"name": "Zoya Saifi", "short": "Zoya", "regions": ["NCR", "GOA"]},
    "priya": {"name": "Priya", "short": "Priya", "regions": ["NCR", "GOA"]},
}

# 10 daily batches: (segment, family). Fallbacks replace a batch whose
# segment has too few fresh contacts for that member today.
DEFAULT_SEGMENTS: List[Tuple[str, str]] = [
    ("RESTAURANT", "HOSPITALITY"),
    ("CAFE", "HOSPITALITY"),
    ("CLUB_BAR", "HOSPITALITY"),
    ("CLOUD_KITCHEN", "HOSPITALITY"),
    ("BANQUET", "HOSPITALITY"),
    ("FASHION", "RETAIL"),
    ("JEWELLERY", "RETAIL"),
    ("BEAUTY", "RETAIL"),
    ("FOOTWEAR", "RETAIL"),
    ("GROCERY", "RETAIL"),
]
FALLBACK_SEGMENTS: List[Tuple[str, str]] = [
    ("HOTEL", "HOSPITALITY"),
    ("ELECTRONICS", "RETAIL"),
    ("FURNITURE", "RETAIL"),
    ("PHARMACY", "RETAIL"),
    ("OTHER_RETAIL", "RETAIL"),
]
SEGMENT_LABEL = {
    "RESTAURANT": "Restaurants", "CAFE": "Cafes", "CLUB_BAR": "Bars & Lounges",
    "CLOUD_KITCHEN": "Cloud Kitchens", "BANQUET": "Banquet Halls", "HOTEL": "Hotels",
    "FASHION": "Fashion & Apparel", "JEWELLERY": "Jewellery", "BEAUTY": "Salons & Beauty",
    "FOOTWEAR": "Footwear", "GROCERY": "Grocery & Supermarkets", "ELECTRONICS": "Electronics",
    "FURNITURE": "Furniture", "PHARMACY": "Pharmacy", "OTHER_RETAIL": "Other Retail",
}

LEAD_STATUSES = [
    ("NEW", "New - reply to them"),
    ("INTERESTED", "Interested"),
    ("MEETING_FIXED", "Meeting fixed"),
    ("SITE_VISIT", "Site visit"),
    ("DEAL_WON", "Deal won"),
    ("NOT_INTERESTED", "Not interested"),
    ("LOST", "Lost"),
]
CLOSED_LEAD = {"DEAL_WON", "NOT_INTERESTED", "LOST"}

OUTREACH_FINAL = {"SENT", "REPLIED", "NO_WHATSAPP", "STOP"}
NEVER_AGAIN = {"NO_WHATSAPP", "STOP"}
BLOCKED_CONSENT = {"OPT_OUT", "OPTED_OUT", "DND", "DO_NOT_CONTACT", "UNSUBSCRIBED", "STOP", "BLOCKED"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name, "")).strip() or default)
    except Exception:
        return default


def _env_time(name: str, default: str) -> Tuple[int, int]:
    raw = (os.getenv(name) or default).strip()
    try:
        h, m = raw.split(":", 1)
        return max(0, min(23, int(h))), max(0, min(59, int(m)))
    except Exception:
        h, m = default.split(":")
        return int(h), int(m)


def members() -> Dict[str, Dict[str, Any]]:
    raw = os.getenv("TEAM_PANEL_MEMBERS")
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and data:
                return data
        except Exception:
            pass
    return DEFAULT_MEMBERS


def batch_size() -> int:
    return max(1, _env_int("TEAM_PANEL_BATCH_SIZE", 25))


def batches_per_day() -> int:
    return max(1, min(len(DEFAULT_SEGMENTS) + len(FALLBACK_SEGMENTS), _env_int("TEAM_PANEL_BATCHES", 10)))


def matches_per_day() -> int:
    return max(1, _env_int("TEAM_PANEL_MATCHES", 10))


def workdays() -> set:
    # Python weekday(): Monday=0 ... Sunday=6. Default Monday-Saturday.
    raw = os.getenv("TEAM_PANEL_WORKDAYS", "0,1,2,3,4,5")
    out = set()
    for x in raw.split(","):
        x = x.strip()
        if x.isdigit():
            out.add(int(x))
    return out or {0, 1, 2, 3, 4, 5}


# ---------------------------------------------------------------- small helpers

def now_ist() -> datetime:
    return datetime.now(IST)


def today_ist() -> date:
    return now_ist().date()


def _e(v: Any) -> str:
    return html.escape("" if v is None else str(v))


def _norm(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "").replace(" ", " ")).strip()


def phone10(v: Any) -> str:
    """Valid Indian mobile only. Never converts opaque WhatsApp IDs."""
    digits = re.sub(r"\D+", "", str(v or ""))
    if digits.startswith("00"):
        digits = digits[2:]
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[-10:]
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[-10:]
    if len(digits) == 10 and digits[0] in "6789":
        return digits
    return ""


_PHONE_LIKE = re.compile(r"(?:\+?91[\s-]*)?(?<!\d)(?:\d[\s-]*){10}(?!\d)")


def strip_contacts(msg: str) -> str:
    """Remove anything phone-shaped, emails and links from a broker-facing text."""
    out = _PHONE_LIKE.sub("", msg or "")
    out = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "", out)
    out = re.sub(r"https?://\S+|wa\.me/\S+", "", out)
    out = re.sub(r"[ \t]+\n", "\n", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out.strip()


def wa_link(phone: str, message: str) -> str:
    p = phone10(phone)
    if not p:
        return ""
    return f"https://wa.me/91{p}?text={quote(message or '')}"


def _json_list(v: Any) -> List[Any]:
    if isinstance(v, list):
        return v
    if v in (None, ""):
        return []
    if isinstance(v, str):
        try:
            x = json.loads(v)
            return x if isinstance(x, list) else [x]
        except Exception:
            return [v]
    return [v]


# ---------------------------------------------------------------- region & segment

GOA_WORDS = [
    "goa", "siolim", "assagao", "anjuna", "vagator", "morjim", "parra", "porvorim", "panjim",
    "panaji", "miramar", "caranzalem", "dona paula", "candolim", "calangute", "baga", "arpora",
    "mapusa", "thivim", "colvale", "saligao", "aldona", "moira", "reis magos", "nerul",
    "sangolda", "pilerne", "ashwem", "mandrem", "arambol", "margao", "vasco", "colva",
    "benaulim", "ponda", "old goa", "dabolim",
]
NCR_WORDS = [
    "delhi", "new delhi", "ncr", "gurgaon", "gurugram", "noida", "greater noida", "faridabad",
    "ghaziabad", "vaishali", "indirapuram", "sohna", "manesar", "dwarka", "rohini", "pitampura",
    "saket", "hauz khas", "connaught place", "rajouri garden", "janakpuri", "lajpat nagar",
    "greater kailash", "gk 1", "gk 2", "gk-1", "gk-2", "vasant kunj", "vasant vihar",
    "defence colony", "karol bagh", "chandni chowk", "punjabi bagh", "mayur vihar",
    "preet vihar", "laxmi nagar", "okhla", "nehru place", "malviya nagar", "green park",
    "south ex", "south extension", "khan market", "golf course road", "cyber city",
    "sushant lok", "dlf phase", "kamla nagar", "tilak nagar", "patel nagar", "model town",
    "shalimar bagh", "paschim vihar", "ashok vihar", "kirti nagar", "mehrauli", "chhatarpur",
    "kalkaji", "nizamuddin", "jangpura", "safdarjung", "sarojini nagar", "aerocity",
    "mahipalpur", "sector 18", "sector 29", "udyog vihar", "sohna road", "kaushambi",
]
_GOA_RX = re.compile(r"\b(" + "|".join(re.escape(w) for w in GOA_WORDS) + r")\b", re.I)
_NCR_RX = re.compile(r"\b(" + "|".join(re.escape(w) for w in NCR_WORDS) + r")\b", re.I)


def _phase5_delhi_localities() -> List[str]:
    try:
        import alliance_phase5_canonical_matcher as p5  # type: ignore
        out: List[str] = []
        for name in dir(p5):
            if "DELHI" in name and name.endswith("LOCALITIES"):
                out.extend(str(x).lower() for x in getattr(p5, name) or [])
        return out
    except Exception:
        return []


_EXTRA_NCR: Optional[re.Pattern] = None


def classify_region(*parts: Any) -> str:
    """Returns NCR, GOA, BOTH or OTHER."""
    global _EXTRA_NCR
    blob = " ".join(_norm(p) for p in parts if p not in (None, ""))
    if not blob:
        return "OTHER"
    goa = bool(_GOA_RX.search(blob))
    ncr = bool(_NCR_RX.search(blob))
    if not ncr:
        if _EXTRA_NCR is None:
            extra = [w for w in _phase5_delhi_localities() if len(w) >= 4]
            _EXTRA_NCR = re.compile(r"\b(" + "|".join(re.escape(w) for w in extra) + r")\b", re.I) if extra else re.compile(r"(?!x)x")
        ncr = bool(_EXTRA_NCR.search(blob))
    if goa and ncr:
        return "BOTH"
    if goa:
        return "GOA"
    if ncr:
        return "NCR"
    return "OTHER"


def region_allowed(region: str, member_regions: Iterable[str]) -> bool:
    regs = set(member_regions)
    if region == "BOTH":
        return bool(regs & {"NCR", "GOA"})
    return region in regs


_SEG_KEYS = [
    ("banquet", "BANQUET"), ("wedding", "BANQUET"), ("hotel", "HOTEL"), ("lodging", "HOTEL"),
    ("cloud kitchen", "CLOUD_KITCHEN"), ("cloud_kitchen", "CLOUD_KITCHEN"), ("kitchen", "CLOUD_KITCHEN"),
    ("cafe", "CAFE"), ("café", "CAFE"), ("coffee", "CAFE"), ("bakery", "CAFE"),
    ("club", "CLUB_BAR"), ("lounge", "CLUB_BAR"), ("pub", "CLUB_BAR"), ("bar", "CLUB_BAR"), ("brew", "CLUB_BAR"),
    ("restaurant", "RESTAURANT"), ("dhaba", "RESTAURANT"), ("food", "RESTAURANT"), ("qsr", "RESTAURANT"),
    ("jewel", "JEWELLERY"), ("cloth", "FASHION"), ("apparel", "FASHION"), ("fashion", "FASHION"),
    ("boutique", "FASHION"), ("garment", "FASHION"), ("footwear", "FOOTWEAR"), ("shoe", "FOOTWEAR"),
    ("supermarket", "GROCERY"), ("grocery", "GROCERY"), ("mart", "GROCERY"), ("electronic", "ELECTRONICS"),
    ("mobile", "ELECTRONICS"), ("furniture", "FURNITURE"), ("decor", "FURNITURE"), ("beauty", "BEAUTY"),
    ("salon", "BEAUTY"), ("spa", "BEAUTY"), ("pharma", "PHARMACY"), ("chemist", "PHARMACY"),
]
KNOWN_SEGMENTS = set(SEGMENT_LABEL)


def classify_segment(business_type: Any, name: Any = "") -> str:
    bt = _norm(business_type)
    if ":" in bt:
        tail = bt.split(":", 1)[1].strip().upper()
        if tail in KNOWN_SEGMENTS:
            return tail
    blob = f"{bt} {_norm(name)}".lower()
    for key, seg in _SEG_KEYS:
        if re.search(r"\b" + re.escape(key), blob):
            return seg
    if "HOSPITALITY" in bt.upper():
        return "RESTAURANT"
    if "RETAIL" in bt.upper():
        return "OTHER_RETAIL"
    return ""


# ---------------------------------------------------------------- AI

def _ai_key() -> str:
    for k in ("TEAM_PANEL_GEMINI_API_KEY", "GEMINI_API_KEY", "GOOGLE_GENAI_API_KEY", "GOOGLE_API_KEY"):
        v = (os.getenv(k) or "").strip()
        if v:
            return v
    return ""


AI_STATE: Dict[str, Any] = {"calls": 0, "ok": 0, "failed": 0, "last_error": None}


def ai_text(prompt: str, temperature: float = 0.6) -> Optional[str]:
    key = _ai_key()
    if not key:
        AI_STATE["last_error"] = "NO_AI_KEY"
        return None
    AI_STATE["calls"] += 1
    try:
        from google import genai  # type: ignore
        from google.genai import types  # type: ignore
        client = genai.Client(api_key=key, http_options=types.HttpOptions(timeout=30000))
        model = os.getenv("TEAM_PANEL_GEMINI_MODEL") or os.getenv("GEMINI_MODEL") or "gemini-2.5-flash"
        resp = client.models.generate_content(model=model, contents=prompt,
                                              config={"temperature": temperature})
        out = _norm_multiline(getattr(resp, "text", "") or "")
        if not out:
            raise RuntimeError("empty AI response")
        AI_STATE["ok"] += 1
        return out
    except Exception as exc:
        AI_STATE["failed"] += 1
        AI_STATE["last_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        return None


def _norm_multiline(s: str) -> str:
    s = s.replace("\r", "").strip()
    s = re.sub(r"^```\w*\n?|```$", "", s).strip()
    s = s.strip('"').strip()
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s


OPT_OUT_LINE = "Reply STOP if you don't want these messages."


def _ensure_greeting(msg: str) -> str:
    msg = (msg or "").strip()
    if not msg or "{name}" in msg:
        return msg
    m = re.match(r"^(hi|hello|hey|dear|namaste)\b[^,!\n]{0,30}[,!]?\s*", msg, re.I)
    rest = msg[m.end():] if m else msg
    if re.match(r"^(We|Our|Are|Do|Looking|Planning|Hope|Just)\b", rest):
        rest = rest[0].lower() + rest[1:]
    return "Hi {name}, " + rest


def polish_outreach(draft: str, segment: str, region: str, member_name: str) -> Tuple[str, str]:
    """Returns (message, ai_status). The message keeps a {name} placeholder."""
    draft = _norm_multiline(draft)
    if not draft:
        return "", "EMPTY"
    seg = SEGMENT_LABEL.get(segment, segment.title())
    where = {"GOA": "Goa", "NCR": "Delhi NCR"}.get(region, "Delhi NCR and Goa")
    prompt = f"""You improve WhatsApp messages for a commercial real estate leasing team (Alliance Infrastructure).
The team member {member_name} wrote a draft to send to owners/managers of {seg} in {where}.

Rewrite it so it is simple, friendly, clear and attractive, so more people reply.
Rules:
- Keep every fact from the draft. Do NOT invent prices, offers, locations or brand names.
- Start exactly with "Hi {{name}}," (keep the {{name}} placeholder as it is).
- Max 70 words. Short sentences. Easy English a busy shop owner understands in 5 seconds.
- 1 or 2 relevant emojis at most.
- End with one clear question that is easy to answer (yes/no or a time).
- Then on the last line write exactly: {OPT_OUT_LINE}
- Output only the final message text. No quotes, no explanations.

DRAFT:
{draft}
"""
    out = ai_text(prompt)
    status = "AI"
    if not out:
        out, status = draft, "NO_AI_BASIC_CLEANUP"
    out = _ensure_greeting(out.replace("{{name}}", "{name}"))
    if "STOP" not in out.upper():
        out = out.rstrip() + "\n\n" + OPT_OUT_LINE
    return out, status


def personalise(template: str, name: str) -> str:
    nm = _norm(name) or "there"
    # Keep business names short and readable in a greeting.
    nm = re.split(r"\s[-|–(]\s?|,", nm)[0].strip()[:40] or "there"
    return (template or "").replace("{name}", nm)


def property_message(req: Dict[str, Any], prop: Dict[str, Any], member_name: str) -> Tuple[str, str]:
    facts = {k: prop.get(k) for k in ("location", "property_type", "transaction", "area", "price", "highlights") if prop.get(k)}
    ask = _norm(req.get("original_message"))[:600]
    prompt = f"""You write WhatsApp messages for a commercial real estate team (Alliance Infrastructure).
A broker posted this requirement:
{strip_contacts(ask)}

We have a matching property. Facts (use ONLY these):
{json.dumps(facts, ensure_ascii=False)}

Write a message from {member_name} to that broker offering this property.
Rules:
- Simple, warm, attractive. Max 80 words. Easy English.
- Mention that it matches their requirement. Show the facts as 3-5 short lines with a small emoji each.
- NEVER include any phone number, owner name, broker name, email, link or exact unit number.
- Do not invent anything that is not in the facts.
- End with: "Shall I share full details or fix a visit?"
- Output only the message.
"""
    out = ai_text(prompt, temperature=0.5)
    status = "AI"
    if not out:
        status = "TEMPLATE"
        lines = ["Hi, we have a property that matches your requirement 👇", ""]
        icons = {"location": "📍", "property_type": "🏢", "transaction": "🔑", "area": "📐", "price": "💰", "highlights": "⭐"}
        labels = {"location": "Location", "property_type": "Type", "transaction": "For", "area": "Area", "price": "Price", "highlights": "Highlights"}
        for k, v in facts.items():
            lines.append(f"{icons[k]} {labels[k]}: {v}")
        lines += ["", "Shall I share full details or fix a visit?"]
        out = "\n".join(lines)
    return strip_contacts(out), status


# ---------------------------------------------------------------- schema

SCHEMA = """
CREATE TABLE IF NOT EXISTS tp_day_plans_v1(
 plan_date DATE NOT NULL,
 member TEXT NOT NULL,
 outreach_status TEXT DEFAULT 'PENDING',
 match_status TEXT DEFAULT 'PENDING',
 outreach_count INTEGER DEFAULT 0,
 match_count INTEGER DEFAULT 0,
 notes JSONB DEFAULT '{}'::jsonb,
 generated_at TIMESTAMPTZ DEFAULT NOW(),
 PRIMARY KEY(plan_date, member)
);
CREATE TABLE IF NOT EXISTS tp_outreach_batches_v1(
 id BIGSERIAL PRIMARY KEY,
 plan_date DATE NOT NULL,
 member TEXT NOT NULL,
 batch_no INTEGER NOT NULL,
 segment TEXT NOT NULL,
 family TEXT,
 region TEXT,
 draft_text TEXT,
 final_text TEXT,
 ai_status TEXT,
 created_at TIMESTAMPTZ DEFAULT NOW(),
 updated_at TIMESTAMPTZ DEFAULT NOW(),
 UNIQUE(plan_date, member, batch_no)
);
CREATE TABLE IF NOT EXISTS tp_outreach_items_v1(
 id BIGSERIAL PRIMARY KEY,
 batch_id BIGINT NOT NULL,
 plan_date DATE NOT NULL,
 member TEXT NOT NULL,
 contact_id TEXT,
 name TEXT,
 phone10 TEXT NOT NULL,
 city TEXT,
 location TEXT,
 segment TEXT,
 region TEXT,
 source_name TEXT,
 status TEXT DEFAULT 'PENDING',
 sent_at TIMESTAMPTZ,
 replied_at TIMESTAMPTZ,
 lead_id BIGINT,
 created_at TIMESTAMPTZ DEFAULT NOW(),
 UNIQUE(plan_date, phone10)
);
CREATE INDEX IF NOT EXISTS tp_outreach_items_v1_phone ON tp_outreach_items_v1(phone10);
CREATE INDEX IF NOT EXISTS tp_outreach_items_v1_member_day ON tp_outreach_items_v1(member, plan_date);
CREATE TABLE IF NOT EXISTS tp_match_items_v1(
 id BIGSERIAL PRIMARY KEY,
 plan_date DATE NOT NULL,
 member TEXT NOT NULL,
 slot INTEGER NOT NULL,
 requirement_id TEXT NOT NULL,
 requirement_text TEXT,
 requirement_region TEXT,
 broker_phone TEXT,
 broker_name TEXT,
 property_id TEXT NOT NULL,
 property JSONB DEFAULT '{}'::jsonb,
 score NUMERIC(6,2),
 bucket TEXT,
 message_text TEXT,
 ai_status TEXT,
 internal_contacts JSONB DEFAULT '[]'::jsonb,
 internal_source TEXT,
 status TEXT DEFAULT 'PENDING',
 sent_at TIMESTAMPTZ,
 replied_at TIMESTAMPTZ,
 lead_id BIGINT,
 created_at TIMESTAMPTZ DEFAULT NOW(),
 UNIQUE(plan_date, member, slot)
);
CREATE INDEX IF NOT EXISTS tp_match_items_v1_pair ON tp_match_items_v1(requirement_id, property_id);
CREATE TABLE IF NOT EXISTS tp_leads_v1(
 id BIGSERIAL PRIMARY KEY,
 member TEXT NOT NULL,
 origin TEXT NOT NULL,
 origin_item_id BIGINT,
 name TEXT,
 phone10 TEXT,
 segment TEXT,
 city TEXT,
 context TEXT,
 status TEXT DEFAULT 'NEW',
 next_followup_at TIMESTAMPTZ,
 last_followup_at TIMESTAMPTZ,
 followup_count INTEGER DEFAULT 0,
 notes TEXT,
 created_at TIMESTAMPTZ DEFAULT NOW(),
 updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS tp_leads_v1_member_due ON tp_leads_v1(member, status, next_followup_at);
CREATE INDEX IF NOT EXISTS tp_leads_v1_phone ON tp_leads_v1(phone10);
CREATE TABLE IF NOT EXISTS tp_lead_events_v1(
 id BIGSERIAL PRIMARY KEY,
 lead_id BIGINT NOT NULL,
 member TEXT,
 kind TEXT,
 note TEXT,
 at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS tp_daily_reports_v1(
 report_date DATE NOT NULL,
 member TEXT NOT NULL,
 metrics JSONB NOT NULL,
 generated_at TIMESTAMPTZ DEFAULT NOW(),
 PRIMARY KEY(report_date, member)
);
CREATE TABLE IF NOT EXISTS tp_monthly_reviews_v1(
 month TEXT PRIMARY KEY,
 metrics JSONB,
 flags JSONB,
 analysis TEXT,
 ai_status TEXT,
 generated_at TIMESTAMPTZ DEFAULT NOW()
);
"""


def install(engine) -> None:
    with engine.begin() as c:
        for stmt in [s.strip() for s in SCHEMA.split(";") if s.strip()]:
            c.execute(text(stmt))


def _table_exists(engine, name: str) -> bool:
    try:
        return name in set(inspect(engine).get_table_names())
    except Exception:
        return False


# ---------------------------------------------------------------- task 1: outreach

def _contact_pool(engine) -> List[Dict[str, Any]]:
    if not _table_exists(engine, "ai_marketing_contacts"):
        return []
    q = text("""
        SELECT contact_id, brand_name, contact_name, phone, city, location, business_type,
               source_name, consent_status
        FROM ai_marketing_contacts
        WHERE COALESCE(phone,'') <> ''
        ORDER BY created_at DESC NULLS LAST, id DESC
        LIMIT :n
    """)
    with engine.connect() as c:
        rows = [dict(r) for r in c.execute(q, {"n": _env_int("TEAM_PANEL_CONTACT_SCAN", 60000)}).mappings()]
    out = []
    seen = set()
    for r in rows:
        p = phone10(r.get("phone"))
        if not p or p in seen:
            continue
        if _norm(r.get("consent_status")).upper() in BLOCKED_CONSENT:
            continue
        seg = classify_segment(r.get("business_type"), r.get("brand_name"))
        if not seg:
            continue
        region = classify_region(r.get("city"), r.get("location"))
        if region not in ("NCR", "GOA", "BOTH"):
            continue
        seen.add(p)
        out.append({
            "contact_id": _norm(r.get("contact_id")),
            "name": _norm(r.get("brand_name") or r.get("contact_name"))[:200],
            "phone10": p,
            "city": _norm(r.get("city"))[:120],
            "location": _norm(r.get("location"))[:300],
            "segment": seg,
            "region": "NCR" if region == "BOTH" else region,
            "source_name": _norm(r.get("source_name"))[:200],
        })
    return out


def _excluded_phones(engine, plan_date: date) -> set:
    cooldown = _env_int("TEAM_PANEL_COOLDOWN_DAYS", 30)
    with engine.connect() as c:
        rows = c.execute(text("""
            SELECT phone10 FROM tp_outreach_items_v1
            WHERE plan_date = :d
               OR status IN ('NO_WHATSAPP','STOP')
               OR (status IN ('SENT','REPLIED') AND plan_date >= :since)
            UNION
            SELECT phone10 FROM tp_leads_v1 WHERE COALESCE(phone10,'') <> ''
        """), {"d": plan_date, "since": plan_date - timedelta(days=cooldown)}).all()
    return {r[0] for r in rows if r[0]}


def allocate_outreach(pool: List[Dict[str, Any]], member_cfg: Dict[str, Dict[str, Any]],
                      excluded: set, n_batches: int, size: int) -> Dict[str, List[Dict[str, Any]]]:
    """Pure allocation. Returns {member: [ {batch_no, segment, family, region, contacts:[...]}, ... ]}.

    Round-robin, one contact at a time, so a short segment is shared fairly.
    Members that cover Goa get Goa contacts first, keeping Delhi NCR stock for
    the Delhi-only member.
    """
    free = [c for c in pool if c["phone10"] not in excluded]
    by_seg: Dict[str, List[Dict[str, Any]]] = {}
    for c in free:
        by_seg.setdefault(c["segment"], []).append(c)
    used: set = set()
    plan: Dict[str, List[Dict[str, Any]]] = {m: [] for m in member_cfg}
    min_ok = max(1, min(size, _env_int("TEAM_PANEL_MIN_BATCH", 10)))

    def take(member: str, segment: str) -> Optional[Dict[str, Any]]:
        regs = member_cfg[member].get("regions") or ["NCR"]
        prefer = ["GOA", "NCR"] if "GOA" in regs else ["NCR"]
        for reg in prefer:
            if reg not in regs:
                continue
            for c in by_seg.get(segment, []):
                if c["phone10"] not in used and c["region"] == reg:
                    used.add(c["phone10"])
                    return c
        return None

    queue = list(DEFAULT_SEGMENTS) + list(FALLBACK_SEGMENTS)
    for seg, fam in queue:
        need = [m for m in member_cfg if len(plan[m]) < n_batches]
        if not need:
            break
        picked: Dict[str, List[Dict[str, Any]]] = {m: [] for m in need}
        progress = True
        while progress:
            progress = False
            for m in need:
                if len(picked[m]) >= size:
                    continue
                c = take(m, seg)
                if c:
                    picked[m].append(c)
                    progress = True
        for m in need:
            got = picked[m]
            if len(got) >= min_ok:
                regions = sorted({c["region"] for c in got})
                plan[m].append({"batch_no": len(plan[m]) + 1, "segment": seg, "family": fam,
                                "region": regions[0] if len(regions) == 1 else "MIXED", "contacts": got})
            else:
                for c in got:  # give back so another member/segment can use it
                    used.discard(c["phone10"])
    return plan


SAMPLE_DRAFTS = {
    "HOSPITALITY": "We help {seg} find the right space on lease in prime high streets and malls. "
                   "If you are planning a new outlet or want to shift, we have verified options ready. Can we share a few?",
    "RETAIL": "We help {seg} brands find shops and showrooms on lease in the best high streets and malls. "
              "Planning a new store this year? We can share matching options.",
}


def generate_outreach(engine, plan_date: date, only_member: Optional[str] = None) -> Dict[str, Any]:
    cfg = members()
    targets = {k: v for k, v in cfg.items() if not only_member or k == only_member}
    with engine.connect() as c:
        done = {r[0] for r in c.execute(text(
            "SELECT member FROM tp_day_plans_v1 WHERE plan_date=:d AND outreach_status='READY'"), {"d": plan_date}).all()}
    targets = {k: v for k, v in targets.items() if k not in done}
    if not targets:
        return {"status": "ALREADY_READY"}
    pool = _contact_pool(engine)
    plan = allocate_outreach(pool, targets, _excluded_phones(engine, plan_date), batches_per_day(), batch_size())
    summary = {}
    with engine.begin() as c:
        for m, batches in plan.items():
            total = 0
            shortages = []
            for b in batches:
                seg_label = SEGMENT_LABEL.get(b["segment"], b["segment"]).lower()
                draft = SAMPLE_DRAFTS.get(b["family"], SAMPLE_DRAFTS["RETAIL"]).format(seg=seg_label)
                bid = c.execute(text("""
                    INSERT INTO tp_outreach_batches_v1(plan_date,member,batch_no,segment,family,region,draft_text)
                    VALUES(:d,:m,:n,:s,:f,:r,:dr)
                    ON CONFLICT (plan_date,member,batch_no) DO UPDATE SET updated_at=NOW()
                    RETURNING id"""), {"d": plan_date, "m": m, "n": b["batch_no"], "s": b["segment"],
                                       "f": b["family"], "r": b["region"], "dr": draft}).scalar()
                for ct in b["contacts"]:
                    res = c.execute(text("""
                        INSERT INTO tp_outreach_items_v1(batch_id,plan_date,member,contact_id,name,phone10,city,
                               location,segment,region,source_name)
                        VALUES(:b,:d,:m,:cid,:n,:p,:city,:loc,:seg,:reg,:src)
                        ON CONFLICT (plan_date,phone10) DO NOTHING"""),
                        {"b": bid, "d": plan_date, "m": m, "cid": ct["contact_id"], "n": ct["name"],
                         "p": ct["phone10"], "city": ct["city"], "loc": ct["location"], "seg": ct["segment"],
                         "reg": ct["region"], "src": ct["source_name"]})
                    total += res.rowcount or 0
                if len(b["contacts"]) < batch_size():
                    shortages.append({"segment": b["segment"], "got": len(b["contacts"])})
            if len(batches) < batches_per_day():
                shortages.append({"batches_missing": batches_per_day() - len(batches)})
            status = "READY" if batches else "NO_CONTACTS"
            c.execute(text("""
                INSERT INTO tp_day_plans_v1(plan_date,member,outreach_status,outreach_count,notes)
                VALUES(:d,:m,:s,:n,CAST(:notes AS JSONB))
                ON CONFLICT (plan_date,member) DO UPDATE SET outreach_status=:s, outreach_count=:n,
                  notes=tp_day_plans_v1.notes || CAST(:notes AS JSONB), generated_at=NOW()"""),
                {"d": plan_date, "m": m, "s": status, "n": total,
                 "notes": json.dumps({"outreach_shortages": shortages, "contact_pool": len(pool)})})
            summary[m] = {"batches": len(batches), "contacts": total, "shortages": shortages}
    return {"status": "OK", "members": summary, "pool": len(pool)}


# ---------------------------------------------------------------- task 2: hot matches

def _req_recency(r: Dict[str, Any]) -> datetime:
    for k in ("source_date", "updated_at", "created_at"):
        v = r.get(k)
        if isinstance(v, datetime):
            return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
        if isinstance(v, date):
            return datetime(v.year, v.month, v.day, tzinfo=timezone.utc)
    return datetime(1970, 1, 1, tzinfo=timezone.utc)


def _truthy(v: Any) -> bool:
    return str(v).strip().lower() in ("1", "true", "t", "yes", "y")


def _sent_pairs(engine) -> set:
    with engine.connect() as c:
        return {(str(a), str(b)) for a, b in c.execute(text(
            "SELECT requirement_id, property_id FROM tp_match_items_v1 WHERE status <> 'PENDING' OR plan_date >= :d"),
            {"d": today_ist() - timedelta(days=_env_int("TEAM_PANEL_MATCH_REPEAT_DAYS", 3))}).all()}


def _source_label(d: Dict[str, Any], enrich: Dict[str, Any]) -> str:
    src = _norm(d.get("source_name") or d.get("source_bucket") or d.get("source"))
    extra = ", ".join((enrich or {}).get("sources") or [])
    return ", ".join(x for x in (src, extra) if x) or "Master database"


def _property_view(d: Dict[str, Any], md: Dict[str, Any]) -> Dict[str, Any]:
    def nc(v):
        v = _norm(v)
        return "" if v.lower() in ("", "not captured", "none", "null", "nan") else v
    desc = strip_contacts(nc(md.get("description") or d.get("property")))
    return {
        "location": nc(d.get("location")),
        "property_type": nc(d.get("subtype") or md.get("property_type") or d.get("family")),
        "transaction": nc(d.get("transaction")),
        "area": nc(d.get("area_display") or d.get("area_sqft") or d.get("area") or md.get("area")),
        "price": nc(d.get("price_display") or d.get("price") or md.get("price")),
        "highlights": desc[:220],
        "verification": nc(d.get("availability_verification") or d.get("verification")) or "UNVERIFIED",
        "why": ", ".join(str(x) for x in d.get("why")) if isinstance(d.get("why"), list) else nc(d.get("why")),
    }


def generate_matches(core, plan_date: date, only_member: Optional[str] = None,
                     time_budget_s: Optional[int] = None) -> Dict[str, Any]:
    engine = core.engine
    cfg = members()
    with engine.connect() as c:
        done = {r[0] for r in c.execute(text(
            "SELECT member FROM tp_day_plans_v1 WHERE plan_date=:d AND match_status='READY'"), {"d": plan_date}).all()}
        have = {}
        for m, n in c.execute(text("SELECT member, COUNT(*) FROM tp_match_items_v1 WHERE plan_date=:d GROUP BY member"),
                              {"d": plan_date}).all():
            have[m] = int(n)
    targets = [m for m in cfg if (not only_member or m == only_member) and m not in done]
    if not targets:
        return {"status": "ALREADY_READY"}
    per = matches_per_day()
    count = {m: have.get(m, 0) for m in targets}

    import alliance_master_requirement_authority_v1 as mra  # Master-only authority

    rows = mra._apply_requirement_sender(mra._requirements(core))
    window = _env_int("TEAM_PANEL_MATCH_DAYS", 21)
    since = datetime.now(timezone.utc) - timedelta(days=window)
    cands = []
    for r in rows:
        phones = [phone10(x) for x in r.get("contacts_list") or []]
        phones = [p for p in phones if p]
        if not phones or _req_recency(r) < since:
            continue
        region = classify_region(" ".join(str(x) for x in r.get("locations_list") or []), r.get("original_message"))
        if region not in ("NCR", "GOA", "BOTH"):
            continue
        r["_broker_phone"] = phones[0]
        r["_region"] = region
        cands.append(r)
    cands.sort(key=lambda r: (_truthy(r.get("matcher_eligible")), float(r.get("genuine_confidence") or 0), _req_recency(r)),
               reverse=True)

    sent = _sent_pairs(engine)
    used_brokers: set = set()
    with engine.connect() as c:
        used_brokers |= {r[0] for r in c.execute(text(
            "SELECT broker_phone FROM tp_match_items_v1 WHERE plan_date=:d"), {"d": plan_date}).all() if r[0]}
    min_score = float(_env_int("TEAM_PANEL_MIN_SCORE", 75))
    budget = time_budget_s if time_budget_s is not None else _env_int("TEAM_PANEL_MATCH_BUDGET_S", 900)
    started = time.monotonic()
    scanned = errors = 0
    made: Dict[str, int] = {m: 0 for m in targets}

    def pick_member(region: str, first_pass: bool) -> Optional[str]:
        opts = [m for m in targets if count[m] < per and region_allowed(region, cfg[m].get("regions") or [])]
        if first_pass:
            # Pass 1 keeps stock where it is needed most: Delhi requirements go to
            # Delhi-only members, Goa requirements go to Goa-covering members.
            if region == "NCR":
                opts = [m for m in opts if set(cfg[m].get("regions") or []) == {"NCR"}]
            else:
                opts = [m for m in opts if "GOA" in (cfg[m].get("regions") or [])]
        if not opts:
            return None
        opts.sort(key=lambda m: count[m])
        return opts[0]

    tried: set = set()
    for first_pass, r in [(True, x) for x in cands] + [(False, x) for x in cands]:
        if all(count[m] >= per for m in targets):
            break
        if time.monotonic() - started > budget:
            break
        if r["_broker_phone"] in used_brokers or id(r) in tried:
            continue
        member = pick_member(r["_region"], first_pass)
        if not member:
            continue
        tried.add(id(r))
        scanned += 1
        try:
            _result, items, enrich, wa_sender = mra._match(core, r)
        except Exception:
            errors += 1
            continue
        best = None
        order = {"EXACT VERIFIED": 0, "EXACT NEEDS VERIFICATION": 1, "ALTERNATIVE": 2}
        items = sorted(items, key=lambda d: (order.get(d.get("_bucket"), 9), -float(d.get("match_score") or 0)))
        for d in items:
            pid = str(d.get("record_id") or d.get("property_id") or d.get("canonical_id") or d.get("id") or "")
            if not pid or (str(r.get("id")), pid) in sent:
                continue
            if float(d.get("match_score") or 0) < min_score:
                continue
            best = (pid, d)
            break
        if not best:
            continue
        pid, d = best
        md = (mra._match_detail_map(core, [pid]) or {}).get(pid) or {}
        view = _property_view(d, md)
        contacts = list((enrich.get(pid) or {}).get("contacts") or [])
        for x in wa_sender.get(pid) or []:
            if x.get("value") and x["value"] not in [z.get("value") for z in contacts]:
                contacts.append(x)
        msg, ai_status = property_message(r, view, cfg[member].get("short") or cfg[member]["name"])
        slot = count[member] + 1
        with engine.begin() as c:
            res = c.execute(text("""
                INSERT INTO tp_match_items_v1(plan_date,member,slot,requirement_id,requirement_text,requirement_region,
                    broker_phone,broker_name,property_id,property,score,bucket,message_text,ai_status,
                    internal_contacts,internal_source)
                VALUES(:d,:m,:s,:rid,:rt,:rr,:bp,:bn,:pid,CAST(:prop AS JSONB),:sc,:bk,:msg,:ai,
                    CAST(:ic AS JSONB),:src)
                ON CONFLICT (plan_date,member,slot) DO NOTHING"""),
                {"d": plan_date, "m": member, "s": slot, "rid": str(r.get("id")),
                 "rt": _norm(r.get("original_message"))[:2000], "rr": r["_region"], "bp": r["_broker_phone"],
                 "bn": _norm(r.get("whatsapp_sender_name") or r.get("company_brand_person"))[:200],
                 "pid": pid, "prop": json.dumps(view, ensure_ascii=False, default=str),
                 "sc": float(d.get("match_score") or 0), "bk": _norm(d.get("_bucket")), "msg": msg, "ai": ai_status,
                 "ic": json.dumps(contacts, ensure_ascii=False, default=str), "src": _source_label(d, enrich.get(pid))})
        if res.rowcount:
            count[member] += 1
            made[member] += 1
            used_brokers.add(r["_broker_phone"])
            sent.add((str(r.get("id")), pid))

    with engine.begin() as c:
        for m in targets:
            # READY = full set. PARTIAL / NO_MATCHES are retried (topped up) on the next run.
            status = "READY" if count[m] >= per else ("PARTIAL" if count[m] else "NO_MATCHES")
            c.execute(text("""
                INSERT INTO tp_day_plans_v1(plan_date,member,match_status,match_count,notes)
                VALUES(:d,:m,:s,:n,CAST(:notes AS JSONB))
                ON CONFLICT (plan_date,member) DO UPDATE SET match_status=:s, match_count=:n,
                  notes=tp_day_plans_v1.notes || CAST(:notes AS JSONB), generated_at=NOW()"""),
                {"d": plan_date, "m": m, "s": status, "n": count[m],
                 "notes": json.dumps({"match_scan": {"requirements_considered": len(cands), "matched_runs": scanned,
                                                     "errors": errors, "short_by": max(0, per - count[m])}})})
    return {"status": "OK", "made": made, "counts": count, "candidates": len(cands), "scanned": scanned, "errors": errors}


# ---------------------------------------------------------------- leads & follow-ups

def _next_workday_at(base: datetime, days: int, hour: int = 11) -> datetime:
    d = base.astimezone(IST).date() + timedelta(days=days)
    wd = workdays()
    for _ in range(8):
        if d.weekday() in wd:
            break
        d += timedelta(days=1)
    return datetime(d.year, d.month, d.day, hour, 0, tzinfo=IST)


def create_lead(engine, member: str, origin: str, item_id: int, name: str, phone: str,
                segment: str, city: str, context: str) -> int:
    p = phone10(phone)
    with engine.begin() as c:
        if p:
            row = c.execute(text("""SELECT id FROM tp_leads_v1 WHERE phone10=:p AND member=:m
                                    AND status NOT IN ('DEAL_WON','NOT_INTERESTED','LOST')
                                    ORDER BY id DESC LIMIT 1"""), {"p": p, "m": member}).first()
            if row:
                c.execute(text("""UPDATE tp_leads_v1 SET next_followup_at=NOW(), updated_at=NOW(),
                                  context=COALESCE(context,'') || :ctx WHERE id=:id"""),
                          {"id": row[0], "ctx": "\n" + context[:500]})
                c.execute(text("INSERT INTO tp_lead_events_v1(lead_id,member,kind,note) VALUES(:l,:m,'REPLIED_AGAIN',:n)"),
                          {"l": row[0], "m": member, "n": context[:500]})
                return int(row[0])
        lid = c.execute(text("""
            INSERT INTO tp_leads_v1(member,origin,origin_item_id,name,phone10,segment,city,context,status,next_followup_at)
            VALUES(:m,:o,:i,:n,:p,:s,:c,:ctx,'NEW',NOW()) RETURNING id"""),
            {"m": member, "o": origin, "i": item_id, "n": name[:200], "p": p, "s": segment, "c": city,
             "ctx": context[:1500]}).scalar()
        c.execute(text("INSERT INTO tp_lead_events_v1(lead_id,member,kind,note) VALUES(:l,:m,'CREATED',:n)"),
                  {"l": lid, "m": member, "n": origin})
        return int(lid)


def set_outreach_status(engine, item_id: int, status: str) -> Dict[str, Any]:
    status = status.upper()
    if status not in {"PENDING", "SENT", "REPLIED", "NO_WHATSAPP", "STOP"}:
        raise ValueError("bad status")
    with engine.begin() as c:
        it = c.execute(text("SELECT * FROM tp_outreach_items_v1 WHERE id=:i"), {"i": item_id}).mappings().first()
        if not it:
            raise KeyError("item not found")
        it = dict(it)
        c.execute(text("""UPDATE tp_outreach_items_v1 SET status=:s,
                   sent_at=CASE WHEN :s IN ('SENT','REPLIED','STOP') THEN COALESCE(sent_at,NOW()) ELSE sent_at END,
                   replied_at=CASE WHEN :s='REPLIED' THEN COALESCE(replied_at,NOW()) ELSE replied_at END
                   WHERE id=:i"""), {"s": status, "i": item_id})
    lead_id = it.get("lead_id")
    if status == "REPLIED" and not lead_id:
        lead_id = create_lead(engine, it["member"], "OUTREACH", item_id, it.get("name") or "", it["phone10"],
                              it.get("segment") or "", it.get("city") or "",
                              f"Replied to WhatsApp outreach ({SEGMENT_LABEL.get(it.get('segment'), it.get('segment'))}, {it.get('location') or it.get('city') or ''})")
        with engine.begin() as c:
            c.execute(text("UPDATE tp_outreach_items_v1 SET lead_id=:l WHERE id=:i"), {"l": lead_id, "i": item_id})
    return {"status": status, "lead_id": lead_id}


def set_match_status(engine, item_id: int, status: str) -> Dict[str, Any]:
    status = status.upper()
    if status not in {"PENDING", "SENT", "REPLIED", "NOT_USEFUL"}:
        raise ValueError("bad status")
    with engine.begin() as c:
        it = c.execute(text("SELECT * FROM tp_match_items_v1 WHERE id=:i"), {"i": item_id}).mappings().first()
        if not it:
            raise KeyError("item not found")
        it = dict(it)
        c.execute(text("""UPDATE tp_match_items_v1 SET status=:s,
                   sent_at=CASE WHEN :s IN ('SENT','REPLIED','NOT_USEFUL') THEN COALESCE(sent_at,NOW()) ELSE sent_at END,
                   replied_at=CASE WHEN :s IN ('REPLIED','NOT_USEFUL') THEN COALESCE(replied_at,NOW()) ELSE replied_at END
                   WHERE id=:i"""), {"s": status, "i": item_id})
    lead_id = it.get("lead_id")
    if status == "REPLIED" and not lead_id:
        prop = it.get("property") or {}
        if isinstance(prop, str):
            try:
                prop = json.loads(prop)
            except Exception:
                prop = {}
        ctx = (f"Broker interested in property {it['property_id']} "
               f"({prop.get('property_type','')} {prop.get('location','')}, {prop.get('area','')}, {prop.get('price','')}) "
               f"for requirement {it['requirement_id']}. Property source: {it.get('internal_source') or ''}")
        lead_id = create_lead(engine, it["member"], "MATCH", item_id, it.get("broker_name") or "Broker",
                              it.get("broker_phone") or "", "BROKER", it.get("requirement_region") or "", ctx)
        with engine.begin() as c:
            c.execute(text("UPDATE tp_match_items_v1 SET lead_id=:l WHERE id=:i"), {"l": lead_id, "i": item_id})
    return {"status": status, "lead_id": lead_id}


def followup(engine, lead_id: int, action: str, note: str = "", when: str = "", status: str = "") -> Dict[str, Any]:
    action = (action or "").lower()
    now = now_ist()
    nxt: Optional[datetime]
    if action == "today":
        nxt = now + timedelta(hours=3)
    elif action == "tomorrow":
        nxt = _next_workday_at(now, 1)
    elif action == "3days":
        nxt = _next_workday_at(now, 3)
    elif action == "week":
        nxt = _next_workday_at(now, 7)
    elif action == "date":
        try:
            dt = datetime.fromisoformat(when)
            nxt = dt if dt.tzinfo else dt.replace(tzinfo=IST)
        except Exception:
            raise ValueError("pick a valid date")
    elif action == "close":
        nxt = None
    else:
        raise ValueError("unknown action")
    st = (status or "").upper()
    valid = {k for k, _ in LEAD_STATUSES}
    if st and st not in valid:
        raise ValueError("bad status")
    if action == "close" and st not in CLOSED_LEAD:
        st = "NOT_INTERESTED"
    with engine.begin() as c:
        row = c.execute(text("SELECT member,status FROM tp_leads_v1 WHERE id=:i"), {"i": lead_id}).first()
        if not row:
            raise KeyError("lead not found")
        new_status = st or (row[1] if row[1] != "NEW" else "INTERESTED")
        if new_status in CLOSED_LEAD:
            nxt = None
        c.execute(text("""UPDATE tp_leads_v1 SET status=:s, next_followup_at=:n, last_followup_at=NOW(),
                          followup_count=followup_count+1, updated_at=NOW(),
                          notes=CASE WHEN :note='' THEN notes ELSE COALESCE(notes||E'\\n','') || :stamp || :note END
                          WHERE id=:i"""),
                  {"s": new_status, "n": nxt, "i": lead_id, "note": note.strip()[:1000],
                   "stamp": now.strftime("%d %b %H:%M") + " - "})
        c.execute(text("INSERT INTO tp_lead_events_v1(lead_id,member,kind,note) VALUES(:l,:m,:k,:n)"),
                  {"l": lead_id, "m": row[0], "k": f"FOLLOWUP:{action}:{new_status}", "n": note[:1000]})
    return {"status": new_status, "next_followup_at": nxt.isoformat() if nxt else None}


def due_counts(engine, member: str) -> Dict[str, int]:
    end_today = datetime.combine(today_ist() + timedelta(days=1), datetime.min.time(), IST)
    start_today = datetime.combine(today_ist(), datetime.min.time(), IST)
    with engine.connect() as c:
        r = c.execute(text("""
            SELECT
              COUNT(*) FILTER (WHERE next_followup_at <= NOW()) AS due_now,
              COUNT(*) FILTER (WHERE next_followup_at < :st) AS overdue,
              COUNT(*) FILTER (WHERE next_followup_at < :et) AS due_today
            FROM tp_leads_v1 WHERE member=:m AND status NOT IN ('DEAL_WON','NOT_INTERESTED','LOST')
              AND next_followup_at IS NOT NULL"""), {"m": member, "st": start_today, "et": end_today}).mappings().first()
    return {k: int(r[k] or 0) for k in ("due_now", "overdue", "due_today")}


# ---------------------------------------------------------------- reports

def compute_daily_metrics(engine, d: date, member: str) -> Dict[str, Any]:
    day_start = datetime.combine(d, datetime.min.time(), IST)
    day_end = day_start + timedelta(days=1)
    with engine.connect() as c:
        o = c.execute(text("""SELECT COUNT(*) AS assigned,
               COUNT(*) FILTER (WHERE status IN ('SENT','REPLIED','STOP')) AS sent,
               COUNT(*) FILTER (WHERE status='REPLIED') AS replied,
               COUNT(*) FILTER (WHERE status='NO_WHATSAPP') AS no_whatsapp,
               COUNT(*) FILTER (WHERE status='STOP') AS stop
               FROM tp_outreach_items_v1 WHERE plan_date=:d AND member=:m"""), {"d": d, "m": member}).mappings().first()
        seg_rows = c.execute(text("""SELECT segment, COUNT(*) AS assigned,
               COUNT(*) FILTER (WHERE status IN ('SENT','REPLIED','STOP')) AS sent,
               COUNT(*) FILTER (WHERE status='REPLIED') AS replied
               FROM tp_outreach_items_v1 WHERE plan_date=:d AND member=:m GROUP BY segment"""),
            {"d": d, "m": member}).mappings().all()
        b = c.execute(text("""SELECT COUNT(*) AS batches,
               COUNT(*) FILTER (WHERE COALESCE(final_text,'')<>'') AS messages_ready,
               COUNT(*) FILTER (WHERE ai_status='AI') AS ai_polished
               FROM tp_outreach_batches_v1 WHERE plan_date=:d AND member=:m"""), {"d": d, "m": member}).mappings().first()
        mt = c.execute(text("""SELECT COUNT(*) AS assigned,
               COUNT(*) FILTER (WHERE status IN ('SENT','REPLIED','NOT_USEFUL')) AS sent,
               COUNT(*) FILTER (WHERE status='REPLIED') AS replied,
               COUNT(*) FILTER (WHERE status='NOT_USEFUL') AS not_useful
               FROM tp_match_items_v1 WHERE plan_date=:d AND member=:m"""), {"d": d, "m": member}).mappings().first()
        ld = c.execute(text("""SELECT
               COUNT(*) FILTER (WHERE created_at >= :s AND created_at < :e) AS new_leads,
               COUNT(*) FILTER (WHERE status NOT IN ('DEAL_WON','NOT_INTERESTED','LOST')) AS open_leads,
               COUNT(*) FILTER (WHERE status NOT IN ('DEAL_WON','NOT_INTERESTED','LOST')
                                AND next_followup_at < :e) AS due_by_day_end,
               COUNT(*) FILTER (WHERE status NOT IN ('DEAL_WON','NOT_INTERESTED','LOST')
                                AND next_followup_at < :s) AS overdue_from_before,
               COUNT(*) FILTER (WHERE status='DEAL_WON' AND updated_at >= :s AND updated_at < :e) AS won_today,
               COUNT(*) FILTER (WHERE status IN ('MEETING_FIXED','SITE_VISIT')) AS meetings_or_visits
               FROM tp_leads_v1 WHERE member=:m"""), {"m": member, "s": day_start, "e": day_end}).mappings().first()
        fu = c.execute(text("""SELECT COUNT(*) FROM tp_lead_events_v1
               WHERE member=:m AND kind LIKE 'FOLLOWUP:%' AND at >= :s AND at < :e"""),
                       {"m": member, "s": day_start, "e": day_end}).scalar()
        plan = c.execute(text("SELECT notes, outreach_status, match_status FROM tp_day_plans_v1 WHERE plan_date=:d AND member=:m"),
                         {"d": d, "m": member}).mappings().first()
    o = dict(o)
    mt = dict(mt)
    sent = int(o["sent"] or 0)
    m_sent = int(mt["sent"] or 0)
    notes = (plan or {}).get("notes") or {}
    if isinstance(notes, str):
        try:
            notes = json.loads(notes)
        except Exception:
            notes = {}
    return {
        "date": d.isoformat(), "member": member,
        "outreach": {**{k: int(v or 0) for k, v in o.items()}, **{k: int(v or 0) for k, v in dict(b).items()},
                     "send_rate": round(100 * sent / o["assigned"], 1) if o["assigned"] else 0.0,
                     "reply_rate": round(100 * int(o["replied"] or 0) / sent, 1) if sent else 0.0,
                     "by_segment": [{k: (int(v) if k != "segment" else v) for k, v in dict(r).items()} for r in seg_rows]},
        "matches": {**{k: int(v or 0) for k, v in mt.items()},
                    "send_rate": round(100 * m_sent / mt["assigned"], 1) if mt["assigned"] else 0.0,
                    "reply_rate": round(100 * int(mt["replied"] or 0) / m_sent, 1) if m_sent else 0.0},
        "leads": {**{k: int(v or 0) for k, v in dict(ld).items()}, "followups_done": int(fu or 0)},
        "plan": {"outreach_status": (plan or {}).get("outreach_status"), "match_status": (plan or {}).get("match_status"),
                 "notes": notes},
    }


def store_daily_report(engine, d: date) -> List[Dict[str, Any]]:
    out = []
    with engine.begin() as c:
        for m in members():
            met = compute_daily_metrics(engine, d, m)
            c.execute(text("""INSERT INTO tp_daily_reports_v1(report_date,member,metrics) VALUES(:d,:m,CAST(:x AS JSONB))
                              ON CONFLICT (report_date,member) DO UPDATE SET metrics=CAST(:x AS JSONB), generated_at=NOW()"""),
                      {"d": d, "m": m, "x": json.dumps(met)})
            out.append(met)
    return out


def _month_bounds(month: str) -> Tuple[date, date]:
    y, mo = [int(x) for x in month.split("-")]
    start = date(y, mo, 1)
    end = date(y + (mo == 12), (mo % 12) + 1, 1)
    return start, end


def month_metrics(engine, month: str) -> Dict[str, Any]:
    start, end = _month_bounds(month)
    with engine.connect() as c:
        rows = c.execute(text("""SELECT report_date, member, metrics FROM tp_daily_reports_v1
                                 WHERE report_date >= :s AND report_date < :e ORDER BY report_date"""),
                         {"s": start, "e": end}).mappings().all()
    agg: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        mtr = r["metrics"] if isinstance(r["metrics"], dict) else json.loads(r["metrics"])
        a = agg.setdefault(r["member"], {"days": 0, "o_assigned": 0, "o_sent": 0, "o_replied": 0, "o_no_wa": 0,
                                         "o_stop": 0, "batches_ready": 0, "batches": 0, "m_assigned": 0, "m_sent": 0,
                                         "m_replied": 0, "m_not_useful": 0, "new_leads": 0, "followups_done": 0,
                                         "won": 0, "overdue_days": 0, "max_overdue": 0, "short_days": 0,
                                         "segments": {}, "zero_send_days": 0})
        o, m, l = mtr["outreach"], mtr["matches"], mtr["leads"]
        a["days"] += 1
        a["o_assigned"] += o["assigned"]; a["o_sent"] += o["sent"]; a["o_replied"] += o["replied"]
        a["o_no_wa"] += o["no_whatsapp"]; a["o_stop"] += o["stop"]
        a["batches"] += o.get("batches", 0); a["batches_ready"] += o.get("messages_ready", 0)
        a["m_assigned"] += m["assigned"]; a["m_sent"] += m["sent"]; a["m_replied"] += m["replied"]
        a["m_not_useful"] += m["not_useful"]
        a["new_leads"] += l["new_leads"]; a["followups_done"] += l["followups_done"]; a["won"] += l["won_today"]
        if l["overdue_from_before"] > 0:
            a["overdue_days"] += 1
        a["max_overdue"] = max(a["max_overdue"], l["overdue_from_before"])
        if o["sent"] == 0 and m["sent"] == 0:
            a["zero_send_days"] += 1
        notes = (mtr.get("plan") or {}).get("notes") or {}
        if notes.get("outreach_shortages") or ((notes.get("match_scan") or {}).get("short_by") or 0) > 0:
            a["short_days"] += 1
        for s in o.get("by_segment") or []:
            sg = a["segments"].setdefault(s["segment"], {"sent": 0, "replied": 0})
            sg["sent"] += s["sent"]; sg["replied"] += s["replied"]
    return {"month": month, "members": agg, "report_days": len({r["report_date"] for r in rows})}


def rule_flags(met: Dict[str, Any]) -> List[str]:
    flags = []
    for m, a in met["members"].items():
        name = members().get(m, {}).get("short", m)
        if a["o_assigned"]:
            sr = 100 * a["o_sent"] / a["o_assigned"]
            if sr < 80:
                flags.append(f"{name}: sent only {sr:.0f}% of assigned WhatsApp contacts. Target is 90%+.")
        if a["o_sent"]:
            rr = 100 * a["o_replied"] / a["o_sent"]
            if rr < 3:
                flags.append(f"{name}: outreach reply rate is {rr:.1f}%. Messages need a stronger hook or better targeting.")
            if 100 * a["o_stop"] / a["o_sent"] > 5:
                flags.append(f"{name}: more than 5% of people replied STOP. Message tone or targeting is wrong; risk to WhatsApp number quality.")
        if a["o_assigned"] and 100 * a["o_no_wa"] / max(1, a["o_assigned"]) > 15:
            flags.append(f"{name}: over 15% contacts had no WhatsApp. Contact data quality needs fixing at the source.")
        if a["m_assigned"] and 100 * a["m_sent"] / a["m_assigned"] < 80:
            flags.append(f"{name}: sent only {100*a['m_sent']/a['m_assigned']:.0f}% of hot matches.")
        if a["m_sent"] and 100 * a["m_not_useful"] / a["m_sent"] > 50:
            flags.append(f"{name}: brokers marked over half the matches not useful. Matcher quality needs review.")
        if a["overdue_days"] > max(2, a["days"] // 4):
            flags.append(f"{name}: had overdue follow-ups on {a['overdue_days']} days (max {a['max_overdue']} at once). Leads are being missed.")
        if a["zero_send_days"]:
            flags.append(f"{name}: {a['zero_send_days']} working day(s) with zero messages sent.")
        if a["short_days"]:
            flags.append(f"{name}: contacts or matches ran short on {a['short_days']} day(s). Feed more data (Maps loop / fresh requirements).")
    return flags


def monthly_review(engine, month: str) -> Dict[str, Any]:
    met = month_metrics(engine, month)
    flags = rule_flags(met)
    analysis = None
    status = "RULES_ONLY"
    if met["members"]:
        prompt = f"""You are a sales operations analyst for a commercial real estate leasing team in India.
Below are the month's daily-activity totals for each team member and automatic warning flags.
Daily target per member: 250 WhatsApp outreach messages (10 batches x 25) and 10 hot property matches sent,
and every lead followed up on time.

Write a short, plain-English review for the business owner:
1. Overall verdict in 2 lines.
2. Per member: what is working, the main problem, and one fix. Use numbers.
3. Which outlet segments reply best and which to drop or reword.
4. The top 3 fixes for next month, in priority order.
Be direct. No fluff. Max 350 words. Use simple bullet points.

DATA:
{json.dumps(met, default=str)}

FLAGS:
{json.dumps(flags)}
"""
        analysis = ai_text(prompt, temperature=0.3)
        if analysis:
            status = "AI"
    if not analysis:
        analysis = "\n".join(f"- {f}" for f in flags) or "- No reports stored for this month yet."
    with engine.begin() as c:
        c.execute(text("""INSERT INTO tp_monthly_reviews_v1(month,metrics,flags,analysis,ai_status)
                          VALUES(:m,CAST(:x AS JSONB),CAST(:f AS JSONB),:a,:s)
                          ON CONFLICT (month) DO UPDATE SET metrics=CAST(:x AS JSONB), flags=CAST(:f AS JSONB),
                            analysis=:a, ai_status=:s, generated_at=NOW()"""),
                  {"m": month, "x": json.dumps(met, default=str), "f": json.dumps(flags), "a": analysis, "s": status})
    return {"month": month, "flags": flags, "analysis": analysis, "ai_status": status, "metrics": met}


# ---------------------------------------------------------------- scheduler

SCHED: Dict[str, Any] = {"status": "NOT_STARTED", "last_plan": None, "last_report": None, "last_error": None,
                         "running": None}
_SCHED_LOCK = threading.Lock()
_GEN_LOCK = threading.Lock()


def generate_day(core, d: Optional[date] = None, only_member: Optional[str] = None) -> Dict[str, Any]:
    d = d or today_ist()
    if not _GEN_LOCK.acquire(blocking=False):
        return {"status": "BUSY", "message": "Already preparing tasks. Wait a few minutes."}
    try:
        engine = core.engine
        with engine.connect() as c:
            got = c.execute(text("SELECT pg_try_advisory_lock(hashtext('tp_generate_day_v1'))")).scalar()
            try:
                if not got:
                    return {"status": "BUSY", "message": "Another server is preparing tasks."}
                SCHED["running"] = d.isoformat()
                out: Dict[str, Any] = {"date": d.isoformat()}
                try:
                    out["outreach"] = generate_outreach(engine, d, only_member)
                except Exception as exc:
                    out["outreach"] = {"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}
                try:
                    out["matches"] = generate_matches(core, d, only_member)
                except Exception as exc:
                    out["matches"] = {"status": "ERROR", "error": f"{type(exc).__name__}: {exc}",
                                      "trace": traceback.format_exc(limit=6)}
                SCHED["last_plan"] = {"at": now_ist().isoformat(), "result": out}
                return out
            finally:
                if got:
                    c.execute(text("SELECT pg_advisory_unlock(hashtext('tp_generate_day_v1'))"))
                    c.commit()
    finally:
        SCHED["running"] = None
        _GEN_LOCK.release()


def _scheduler_loop(core) -> None:
    ph, pm = _env_time("TEAM_PANEL_PLAN_TIME", "07:00")
    rh, rm = _env_time("TEAM_PANEL_REPORT_TIME", "20:30")
    SCHED["status"] = "RUNNING"
    SCHED["plan_time"] = f"{ph:02d}:{pm:02d} IST"
    SCHED["report_time"] = f"{rh:02d}:{rm:02d} IST"
    last_plan_day = last_report_day = None
    while True:
        n = now_ist()
        d = n.date()
        try:
            if d.weekday() in workdays():
                if (n.hour, n.minute) >= (ph, pm) and last_plan_day != d:
                    res = generate_day(core, d)
                    if res.get("status") != "BUSY":
                        last_plan_day = d
                if (n.hour, n.minute) >= (rh, rm) and last_report_day != d:
                    store_daily_report(core.engine, d)
                    SCHED["last_report"] = {"at": n.isoformat(), "date": d.isoformat()}
                    last_report_day = d
                    # On the last working day of the month, write the monthly review.
                    nxt = d + timedelta(days=1)
                    while nxt.weekday() not in workdays():
                        nxt += timedelta(days=1)
                    if nxt.month != d.month:
                        monthly_review(core.engine, d.strftime("%Y-%m"))
        except Exception as exc:
            SCHED["last_error"] = f"{n.isoformat()} {type(exc).__name__}: {exc}"
        time.sleep(60)


def start_scheduler(core) -> Dict[str, Any]:
    if os.getenv("TEAM_PANEL_SCHEDULER", "on").lower() in ("off", "0", "false", "no"):
        SCHED["status"] = "DISABLED"
        return SCHED
    with _SCHED_LOCK:
        if SCHED["status"] == "RUNNING":
            return SCHED
        SCHED["status"] = "STARTING"
        threading.Thread(target=_scheduler_loop, args=(core,), name="alliance-team-panel-scheduler", daemon=True).start()
    return SCHED


# ---------------------------------------------------------------- registration

def register(core, served_app) -> Dict[str, Any]:
    install(core.engine)
    import alliance_team_panel_ui_v1 as ui
    ui.mount(core, served_app)
    start_scheduler(core)
    return {"status": "READY", "version": VERSION, "prefix": PREFIX, "members": list(members()),
            "writes": "tp_* tables only", "matcher_source": "MASTER_ONLY"}
