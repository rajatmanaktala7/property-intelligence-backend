"""Alliance Maps Contact Loop V1.

Permanent backend loop that pulls public business contact details for
hospitality and retail outlets from Google Maps data and saves them into
ai_marketing_contacts without duplicates.

Two triggers:
  1. Interval worker. One category x market slice per cycle. Postgres
     advisory lock so only one worker runs even with several replicas.
  2. Bot hook. Wraps /api/internal/v2/marketing-search so every time the
     V2 Retail/Hospitality bot runs, Maps contacts are fetched, saved here
     and returned to V2 in the same response.

Providers (first configured wins, second is fallback):
  GOOGLE_PLACES_API_KEY / GOOGLE_MAPS_API_KEY  -> Places API (New) Text Search
  SERPER_API_KEY                               -> google.serper.dev/maps

Duplicate rule: one row per 10-digit phone. Also one row per place id.
Existing ai_marketing_contacts rows with the same phone are never duplicated
and never overwritten. Missing website/email on an existing row is filled.

Env knobs:
  MAPS_LOOP_ENABLED          default "1"
  MAPS_LOOP_INTERVAL_MIN     default 30
  MAPS_DAILY_QUERY_CAP       default 220  (keeps Google India free tier: 7000/month)
  MAPS_PAGES_PER_QUERY       default 1    (20 places per page on Google)
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import text

VERSION = "1.0.0-ALLIANCE-MAPS-CONTACT-LOOP"
LOCK_KEY = 74110001

HOSPITALITY = ("restaurants", "cafes", "bars and lounges", "banquet halls", "hotels", "cloud kitchens")
RETAIL = ("clothing stores", "jewellery stores", "supermarkets", "electronics stores",
          "furniture stores", "footwear stores", "beauty salons", "pharmacies")
MARKETS = (
    "Connaught Place, New Delhi", "Hauz Khas, New Delhi", "Saket, New Delhi", "Lajpat Nagar, New Delhi",
    "Rajouri Garden, New Delhi", "Karol Bagh, New Delhi", "Greater Kailash, New Delhi", "Dwarka, New Delhi",
    "Pitampura, New Delhi", "Sector 29, Gurugram", "Golf Course Road, Gurugram", "Sohna Road, Gurugram",
    "Sector 18, Noida", "Sector 62, Noida", "Greater Noida", "Faridabad", "Indirapuram, Ghaziabad",
)
BAD_PHONES = {"9876543210", "9999999999", "8888888888", "1234567890", "0000000000"}

_THREAD: Optional[threading.Thread] = None
_STOP = threading.Event()
_START_LOCK = threading.Lock()


# ---------------------------------------------------------------- helpers
def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _key(*names: str) -> str:
    for n in names:
        v = os.getenv(n, "").strip()
        if v:
            return v
    return ""


def _norm(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


def phone10(raw: Any) -> Optional[str]:
    """Canonical dedupe key: last 10 digits. Works for mobiles and STD landlines."""
    d = re.sub(r"\D", "", str(raw or ""))
    if d.startswith("91") and len(d) == 12:
        d = d[2:]
    if d.startswith("0") and len(d) == 11:
        d = d[1:]
    if len(d) > 10:
        d = d[-10:]
    if len(d) != 10 or d in BAD_PHONES or len(set(d)) < 4:
        return None
    return d


def _segment(category: str, query: str, types: str) -> str:
    blob = f"{query} {types}".lower()
    if category == "HOSPITALITY":
        for k, v in (("banquet", "BANQUET"), ("hotel", "HOTEL"), ("lodging", "HOTEL"), ("cafe", "CAFE"),
                     ("coffee", "CAFE"), ("bar", "CLUB_BAR"), ("lounge", "CLUB_BAR"), ("kitchen", "CLOUD_KITCHEN")):
            if k in blob:
                return v
        return "RESTAURANT"
    for k, v in (("jewel", "JEWELLERY"), ("cloth", "FASHION"), ("apparel", "FASHION"), ("footwear", "FOOTWEAR"),
                 ("shoe", "FOOTWEAR"), ("supermarket", "GROCERY"), ("grocery", "GROCERY"), ("electronic", "ELECTRONICS"),
                 ("furniture", "FURNITURE"), ("beauty", "BEAUTY"), ("salon", "BEAUTY"), ("pharma", "PHARMACY")):
        if k in blob:
            return v
    return "OTHER_RETAIL"


# ---------------------------------------------------------------- providers
def _httpx():
    import httpx
    return httpx


def _google_places(query: str, pages: int) -> List[Dict[str, Any]]:
    key = _key("GOOGLE_PLACES_API_KEY", "GOOGLE_MAPS_API_KEY")
    if not key:
        raise RuntimeError("google_key_missing")
    fields = ("places.id,places.displayName,places.formattedAddress,places.nationalPhoneNumber,"
              "places.internationalPhoneNumber,places.websiteUri,places.googleMapsUri,places.primaryType,"
              "places.types,places.businessStatus,nextPageToken")
    out: List[Dict[str, Any]] = []
    token = None
    for _ in range(max(1, pages)):
        body: Dict[str, Any] = {"textQuery": query, "pageSize": 20, "languageCode": "en", "regionCode": "IN"}
        if token:
            body["pageToken"] = token
        r = _httpx().post("https://places.googleapis.com/v1/places:searchText",
                          headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": fields},
                          json=body, timeout=30.0)
        r.raise_for_status()
        data = r.json()
        for p in data.get("places") or []:
            if p.get("businessStatus") in ("CLOSED_PERMANENTLY",):
                continue
            out.append({
                "place_id": "g:" + str(p.get("id") or ""),
                "name": _norm((p.get("displayName") or {}).get("text")),
                "phone": p.get("nationalPhoneNumber") or p.get("internationalPhoneNumber") or "",
                "website": p.get("websiteUri") or "",
                "address": p.get("formattedAddress") or "",
                "maps_url": p.get("googleMapsUri") or "",
                "types": ",".join(p.get("types") or []),
            })
        token = data.get("nextPageToken")
        if not token:
            break
    return out


def _serper_maps(query: str, pages: int) -> List[Dict[str, Any]]:
    key = _key("SERPER_API_KEY")
    if not key:
        raise RuntimeError("serper_key_missing")
    out: List[Dict[str, Any]] = []
    for page in range(1, max(1, pages) + 1):
        r = _httpx().post("https://google.serper.dev/maps",
                          headers={"X-API-KEY": key, "Content-Type": "application/json"},
                          json={"q": query, "gl": "in", "hl": "en", "page": page}, timeout=30.0)
        r.raise_for_status()
        places = r.json().get("places") or []
        for p in places:
            pid = p.get("placeId") or p.get("cid") or ""
            out.append({
                "place_id": ("s:" + str(pid)) if pid else "",
                "name": _norm(p.get("title")),
                "phone": p.get("phoneNumber") or p.get("phone") or "",
                "website": p.get("website") or "",
                "address": p.get("address") or "",
                "maps_url": f"https://www.google.com/maps?cid={p.get('cid')}" if p.get("cid") else "",
                "types": ",".join(p.get("types") or ([p.get("type")] if p.get("type") else [])),
            })
        if len(places) < 10:
            break
    return out


def fetch_places(query: str, pages: int) -> Tuple[str, List[Dict[str, Any]], Optional[str]]:
    errors = []
    for name, fn in (("GOOGLE_PLACES", _google_places), ("SERPER_MAPS", _serper_maps)):
        try:
            return name, fn(query, pages), None
        except Exception as exc:  # try next provider
            errors.append(f"{name}:{type(exc).__name__}:{str(exc)[:120]}")
    return "NONE", [], " | ".join(errors)


# ---------------------------------------------------------------- storage
def ensure_schema(engine) -> None:
    with engine.begin() as c:
        c.execute(text("""CREATE TABLE IF NOT EXISTS ai_maps_contact_ledger_v1(
            phone10 TEXT PRIMARY KEY,
            place_id TEXT,
            business_name TEXT NOT NULL,
            category TEXT NOT NULL,
            segment TEXT,
            market TEXT,
            phone_raw TEXT,
            website TEXT,
            address TEXT,
            maps_url TEXT,
            provider TEXT,
            contact_id TEXT,
            seen_count INT NOT NULL DEFAULT 1,
            first_seen_at TIMESTAMPTZ DEFAULT NOW(),
            last_seen_at TIMESTAMPTZ DEFAULT NOW())"""))
        c.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_maps_ledger_place_v1 ON ai_maps_contact_ledger_v1(place_id) WHERE place_id IS NOT NULL AND place_id<>''"))
        c.execute(text("""CREATE TABLE IF NOT EXISTS ai_maps_contact_loop_control_v1(
            singleton_id INT PRIMARY KEY DEFAULT 1 CHECK(singleton_id=1),
            cursor_index INT NOT NULL DEFAULT 0,
            day DATE, day_queries INT NOT NULL DEFAULT 0,
            last_run_at TIMESTAMPTZ, next_run_at TIMESTAMPTZ, last_status TEXT, last_error TEXT,
            total_runs BIGINT NOT NULL DEFAULT 0, total_places BIGINT NOT NULL DEFAULT 0,
            total_new BIGINT NOT NULL DEFAULT 0, total_duplicates BIGINT NOT NULL DEFAULT 0,
            total_no_phone BIGINT NOT NULL DEFAULT 0, updated_at TIMESTAMPTZ DEFAULT NOW())"""))
        c.execute(text("INSERT INTO ai_maps_contact_loop_control_v1(singleton_id) VALUES(1) ON CONFLICT DO NOTHING"))
        c.execute(text("""CREATE TABLE IF NOT EXISTS ai_maps_contact_runs_v1(
            run_id BIGSERIAL PRIMARY KEY, trigger TEXT, category TEXT, query TEXT, market TEXT,
            provider TEXT, places INT, new_contacts INT, duplicates INT, no_phone INT, error TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW())"""))
    try:  # fast phone lookup on the existing contact table; optional
        with engine.begin() as c:
            c.execute(text("""CREATE INDEX IF NOT EXISTS ix_ai_marketing_contacts_phone10
                ON ai_marketing_contacts ((right(regexp_replace(coalesce(phone,''),'\\D','','g'),10)))"""))
    except Exception:
        pass


def _contact_id(core) -> str:
    fn = getattr(core, "_new_code", None)
    if fn:
        return fn("MKT")
    return "MKT-" + hashlib.sha1(os.urandom(16)).hexdigest()[:12].upper()


def save_place(core, place: Dict[str, Any], category: str, query: str, market: str, provider: str) -> str:
    """Returns NEW, DUPLICATE or NO_PHONE. Never creates a second row for a phone."""
    p10 = phone10(place.get("phone"))
    if not p10 or not place.get("name"):
        return "NO_PHONE"
    engine = core.engine
    seg = _segment(category, query, place.get("types", ""))
    with engine.begin() as c:
        # Serialise concurrent writers on the same phone.
        c.execute(text("SELECT pg_advisory_xact_lock(hashtext(:p))"), {"p": "maps:" + p10})
        hit = c.execute(text("""UPDATE ai_maps_contact_ledger_v1 SET seen_count=seen_count+1, last_seen_at=NOW(),
            website=COALESCE(NULLIF(website,''),:w) WHERE phone10=:p
            OR (:pid<>'' AND place_id=:pid) RETURNING phone10"""),
            {"p": p10, "pid": place.get("place_id") or "", "w": place.get("website") or ""}).first()
        if hit:
            return "DUPLICATE"
        existing = c.execute(text("""SELECT contact_id FROM ai_marketing_contacts
            WHERE right(regexp_replace(coalesce(phone,''),'\\D','','g'),10)=:p LIMIT 1"""), {"p": p10}).first()
        if existing:
            cid = existing[0]
            c.execute(text("""UPDATE ai_marketing_contacts SET
                website=CASE WHEN coalesce(website,'')='' THEN :w ELSE website END, updated_at=NOW()
                WHERE contact_id=:id"""), {"w": place.get("website") or None, "id": cid})
            state = "DUPLICATE"
        else:
            cid = _contact_id(core)
            fp = hashlib.sha256(("maps|" + p10).encode()).hexdigest()
            c.execute(text("""INSERT INTO ai_marketing_contacts(contact_id,fingerprint,business_type,brand_name,
                phone,website,location,city,source_name,source_url,consent_status,verification_status)
                VALUES(:id,:fp,:bt,:brand,:phone,:web,:loc,:city,:src,:url,'UNKNOWN','UNVERIFIED')
                ON CONFLICT (fingerprint) DO NOTHING"""),
                {"id": cid, "fp": fp, "bt": f"{category}:{seg}", "brand": place["name"][:200],
                 "phone": "0" + p10 if not p10.startswith(("6", "7", "8", "9")) else p10,
                 "web": place.get("website") or None, "loc": (place.get("address") or market)[:300],
                 "city": market.split(",")[-1].strip(), "src": "Google Maps (" + provider + ")",
                 "url": place.get("maps_url") or None})
            state = "NEW"
        c.execute(text("""INSERT INTO ai_maps_contact_ledger_v1(phone10,place_id,business_name,category,segment,market,
            phone_raw,website,address,maps_url,provider,contact_id)
            VALUES(:p,NULLIF(:pid,''),:n,:cat,:seg,:m,:raw,:w,:a,:u,:prov,:cid) ON CONFLICT DO NOTHING"""),
            {"p": p10, "pid": place.get("place_id") or "", "n": place["name"][:200], "cat": category, "seg": seg,
             "m": market, "raw": place.get("phone"), "w": place.get("website") or None,
             "a": place.get("address") or None, "u": place.get("maps_url") or None, "prov": provider, "cid": cid})
    return state


def run_query(core, category: str, query: str, market: str, trigger: str) -> Dict[str, Any]:
    category = (category or "HOSPITALITY").upper()
    pages = _env_int("MAPS_PAGES_PER_QUERY", 1)
    provider, places, err = fetch_places(f"{query} in {market}", pages)
    counts = {"NEW": 0, "DUPLICATE": 0, "NO_PHONE": 0}
    items: List[Dict[str, Any]] = []
    for p in places:
        try:
            state = save_place(core, p, category, query, market, provider)
        except Exception as exc:
            err = (err or "") + f" save:{type(exc).__name__}"
            continue
        counts[state] += 1
        if state != "NO_PHONE":
            items.append({**p, "state": state, "phone10": phone10(p.get("phone"))})
    with core.engine.begin() as c:
        c.execute(text("""INSERT INTO ai_maps_contact_runs_v1(trigger,category,query,market,provider,places,
            new_contacts,duplicates,no_phone,error) VALUES(:t,:c,:q,:m,:p,:n,:new,:dup,:np,:e)"""),
            {"t": trigger, "c": category, "q": query, "m": market, "p": provider, "n": len(places),
             "new": counts["NEW"], "dup": counts["DUPLICATE"], "np": counts["NO_PHONE"], "e": err})
    return {"provider": provider, "places": len(places), "new": counts["NEW"], "duplicates": counts["DUPLICATE"],
            "no_phone": counts["NO_PHONE"], "error": err, "items": items}


# ---------------------------------------------------------------- queue + worker
def _demand_markets(engine) -> List[str]:
    """Localities from fresh requirements become extra markets: data follows demand."""
    try:
        with engine.connect() as c:
            rows = c.execute(text("""SELECT DISTINCT trim(concat_ws(', ', nullif(trim(locality),''), nullif(trim(city),''))) m
                FROM pi_master_requirements_v711 WHERE created_at > NOW() - INTERVAL '30 days'
                AND coalesce(trim(locality),'')<>'' LIMIT 40""")).all()
        return [r[0] for r in rows if r[0] and r[0] not in MARKETS]
    except Exception:
        return []


def build_queue(engine) -> List[Tuple[str, str, str]]:
    markets = list(MARKETS) + _demand_markets(engine)
    q: List[Tuple[str, str, str]] = []
    for m in markets:
        q += [("HOSPITALITY", x, m) for x in HOSPITALITY]
        q += [("RETAIL", x, m) for x in RETAIL]
    return q


def run_cycle(core, trigger: str = "INTERVAL") -> Dict[str, Any]:
    engine = core.engine
    ensure_schema(engine)
    lock = engine.connect()
    try:
        if not lock.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}).scalar():
            return {"status": "SKIP_LOCKED", "version": VERSION}
        cap = _env_int("MAPS_DAILY_QUERY_CAP", 220)
        today = datetime.now(timezone.utc).date()
        with engine.begin() as c:
            ctl = dict(c.execute(text("SELECT * FROM ai_maps_contact_loop_control_v1 WHERE singleton_id=1")).mappings().first())
            if ctl.get("day") != today:
                c.execute(text("UPDATE ai_maps_contact_loop_control_v1 SET day=:d, day_queries=0 WHERE singleton_id=1"), {"d": today})
                ctl["day_queries"] = 0
        if int(ctl.get("day_queries") or 0) >= cap:
            return {"status": "DAILY_CAP_REACHED", "cap": cap}
        queue = build_queue(engine)
        idx = int(ctl.get("cursor_index") or 0) % len(queue)
        category, query, market = queue[idx]
        out = run_query(core, category, query, market, trigger)
        status = "PASS" if not out["error"] else ("PARTIAL" if out["places"] else "ERROR")
        interval = _env_int("MAPS_LOOP_INTERVAL_MIN", 30)
        with engine.begin() as c:
            c.execute(text("""UPDATE ai_maps_contact_loop_control_v1 SET cursor_index=:n, day_queries=day_queries+1,
                last_run_at=NOW(), next_run_at=NOW() + (:iv || ' minutes')::interval, last_status=:s, last_error=:e,
                total_runs=total_runs+1, total_places=total_places+:pl, total_new=total_new+:new,
                total_duplicates=total_duplicates+:dup, total_no_phone=total_no_phone+:np, updated_at=NOW()
                WHERE singleton_id=1"""),
                {"n": (idx + 1) % len(queue), "iv": str(interval), "s": status, "e": out["error"], "pl": out["places"],
                 "new": out["new"], "dup": out["duplicates"], "np": out["no_phone"]})
        return {"status": status, "slice": [category, query, market], **{k: v for k, v in out.items() if k != "items"}}
    finally:
        try:
            lock.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
        except Exception:
            pass
        lock.close()


def _loop(core) -> None:
    while not _STOP.is_set():
        try:
            with core.engine.connect() as c:
                due = c.execute(text("SELECT next_run_at FROM ai_maps_contact_loop_control_v1 WHERE singleton_id=1")).scalar()
            if due is None or datetime.now(timezone.utc) >= due:
                run_cycle(core)
            _STOP.wait(60)
        except Exception:
            _STOP.wait(120)


def start_worker(core) -> Dict[str, Any]:
    global _THREAD
    if os.getenv("MAPS_LOOP_ENABLED", "1").strip() in ("0", "false", "False"):
        return {"status": "DISABLED_BY_ENV"}
    with _START_LOCK:
        if _THREAD and _THREAD.is_alive():
            return {"status": "ALREADY_RUNNING"}
        _STOP.clear()
        _THREAD = threading.Thread(target=_loop, args=(core,), daemon=True, name="maps-contact-loop-v1")
        _THREAD.start()
        return {"status": "STARTED"}


def status(core) -> Dict[str, Any]:
    ensure_schema(core.engine)
    with core.engine.connect() as c:
        ctl = dict(c.execute(text("SELECT * FROM ai_maps_contact_loop_control_v1 WHERE singleton_id=1")).mappings().first() or {})
        ledger = int(c.execute(text("SELECT COUNT(*) FROM ai_maps_contact_ledger_v1")).scalar() or 0)
        recent = [dict(r) for r in c.execute(text("""SELECT trigger,category,query,market,provider,places,new_contacts,
            duplicates,no_phone,error,created_at FROM ai_maps_contact_runs_v1 ORDER BY run_id DESC LIMIT 15""")).mappings().all()]
    provider = "GOOGLE_PLACES" if _key("GOOGLE_PLACES_API_KEY", "GOOGLE_MAPS_API_KEY") else (
        "SERPER_MAPS" if _key("SERPER_API_KEY") else "NOT_CONFIGURED")
    return {"version": VERSION, "worker_alive": bool(_THREAD and _THREAD.is_alive()), "provider": provider,
            "ledger_contacts": ledger, "queue_size": len(build_queue(core.engine)),
            "daily_cap": _env_int("MAPS_DAILY_QUERY_CAP", 220), "control": ctl, "recent_runs": recent}


# ---------------------------------------------------------------- routes
def _v2_token_ok(request: Request) -> bool:
    import hmac
    expected = _key("V2_PROVIDER_PROXY_TOKEN")
    auth = (request.headers.get("Authorization") or "").strip()
    supplied = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    return bool(expected and supplied and hmac.compare_digest(supplied, expected))


def register(core) -> Dict[str, Any]:
    app = getattr(core, "app", None) or core
    ensure_schema(core.engine)
    own = {"/alliance/maps-contact-loop", "/api/alliance/maps-contact-loop/status",
           "/api/alliance/maps-contact-loop/run-now", "/api/internal/v2/maps-contacts"}
    # Keep the original V2 search handler so the hook can call it, then take over its path.
    original = None
    kept = []
    for r in list(app.router.routes):
        path = getattr(r, "path", None)
        if path == "/api/internal/v2/marketing-search" and "POST" in (getattr(r, "methods", set()) or set()):
            original = original or getattr(r, "endpoint", None)
            continue
        if path in own:
            continue
        kept.append(r)
    app.router.routes[:] = kept

    def _login(req: Request):
        fn = getattr(core, "need_login", None)
        return fn(req) if fn else "team"

    @app.get("/alliance/maps-contact-loop", response_class=HTMLResponse)
    def page(req: Request):
        _login(req)
        st = status(core)
        ctl = st["control"]
        rows = "".join(
            f"<tr><td>{html.escape(str(r['created_at'])[:16])}</td><td>{html.escape(r['trigger'] or '')}</td>"
            f"<td>{html.escape(r['category'] or '')}</td><td>{html.escape(r['query'] or '')}</td>"
            f"<td>{html.escape(r['market'] or '')}</td><td>{r['places']}</td><td>{r['new_contacts']}</td>"
            f"<td>{r['duplicates']}</td><td>{html.escape((r['error'] or '')[:80])}</td></tr>" for r in st["recent_runs"])
        return HTMLResponse(f"""<html><head><meta name=viewport content="width=device-width,initial-scale=1">
        <style>body{{font:14px system-ui;margin:16px}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ddd;padding:4px 6px;font-size:12px}}</style></head>
        <body><h2>Maps Contact Loop</h2>
        <p>Worker <b>{'RUNNING' if st['worker_alive'] else 'STOPPED'}</b> · Provider <b>{st['provider']}</b> · Queue {st['queue_size']} slices · Daily cap {st['daily_cap']}</p>
        <p>Unique contacts in ledger <b>{st['ledger_contacts']}</b> · Runs {ctl.get('total_runs',0)} · New {ctl.get('total_new',0)} · Duplicates blocked {ctl.get('total_duplicates',0)}</p>
        <p>Last {html.escape(str(ctl.get('last_status') or 'not run'))} at {html.escape(str(ctl.get('last_run_at') or ''))} · Next {html.escape(str(ctl.get('next_run_at') or ''))}</p>
        <form method=post action="/api/alliance/maps-contact-loop/run-now"><button>Run one slice now</button></form>
        <table><tr><th>When</th><th>Trigger</th><th>Category</th><th>Query</th><th>Market</th><th>Places</th><th>New</th><th>Dup</th><th>Error</th></tr>{rows}</table>
        </body></html>""", headers={"Cache-Control": "no-store"})

    @app.get("/api/alliance/maps-contact-loop/status")
    def api_status(req: Request):
        _login(req)
        return JSONResponse(json.loads(json.dumps(status(core), default=str)))

    @app.post("/api/alliance/maps-contact-loop/run-now")
    def api_run(req: Request):
        _login(req)
        return JSONResponse(json.loads(json.dumps(run_cycle(core, "MANUAL"), default=str)))

    @app.post("/api/internal/v2/maps-contacts")
    def v2_maps(payload: dict, request: Request):
        if not _v2_token_ok(request):
            return JSONResponse(status_code=401, content={"error": "provider_proxy_authentication_required"})
        out = run_query(core, str(payload.get("category") or "HOSPITALITY"), str(payload.get("query") or "")[:120],
                        str(payload.get("location") or "Delhi NCR")[:120], "V2_BOT")
        return JSONResponse(json.loads(json.dumps(out, default=str)))

    @app.post("/api/internal/v2/marketing-search")
    def v2_marketing_search_with_maps(payload: dict, request: Request):
        """Same contract as before. Maps contacts are fetched and saved first, then merged in."""
        if not _v2_token_ok(request):
            return JSONResponse(status_code=401, content={"error": "provider_proxy_authentication_required"})
        category = str(payload.get("category") or "GENERAL").strip().upper()
        maps_items: List[Dict[str, Any]] = []
        maps_meta: Dict[str, Any] = {}
        if category in ("RETAIL", "HOSPITALITY"):
            try:
                m = run_query(core, category, str(payload.get("query") or "")[:120],
                              str(payload.get("location") or "Delhi NCR")[:120], "V2_BOT_HOOK")
                maps_meta = {k: v for k, v in m.items() if k != "items"}
                hook_seen = set()
                for p in m["items"]:
                    if p["phone10"] in hook_seen:
                        continue
                    hook_seen.add(p["phone10"])
                    maps_items.append({"place_id": p.get("place_id") or ("maps:" + p["phone10"]), "name": p["name"],
                                       "address": p.get("address") or "", "phone": p.get("phone") or "",
                                       "website": p.get("website") or "", "source_url": p.get("maps_url") or "",
                                       "source_title": p["name"], "source_excerpt": p.get("address") or "",
                                       "email": "", "types": (p.get("types") or "").split(",") if p.get("types") else [],
                                       "primary_type": category.lower(), "primary_type_label": category.title(),
                                       "provider": "V1_" + m["provider"]})
            except Exception as exc:
                maps_meta = {"error": f"{type(exc).__name__}: {exc}"}
        base: Dict[str, Any] = {"items": []}
        if original is not None:
            try:
                resp = original(payload, request)
                body = getattr(resp, "body", None)
                base = json.loads(body) if body else (resp if isinstance(resp, dict) else {"items": []})
            except Exception as exc:
                base = {"items": [], "fallback_error": type(exc).__name__}
        seen = {phone10(i.get("phone")) for i in maps_items}
        merged = maps_items + [i for i in base.get("items") or [] if phone10(i.get("phone")) not in seen]
        base.update({"items": merged, "results": len(merged), "maps": maps_meta,
                     "provider": "V1_MAPS_PLUS_" + str(base.get("provider") or "LANGSEARCH")})
        return JSONResponse(base)

    return {"status": "REGISTERED", "version": VERSION, "worker": start_worker(core),
            "hooked_v2_marketing_search": original is not None, "status_page": "/alliance/maps-contact-loop"}
