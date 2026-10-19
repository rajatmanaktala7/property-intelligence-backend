"""Team Panel V1 tests.

Unit tests always run. Database tests run when TEAM_PANEL_TEST_DB points at a
throwaway PostgreSQL database, e.g.
  TEAM_PANEL_TEST_DB=postgresql+psycopg://postgres@/tp_test?host=/var/tmp/pg&port=5433
"""
from __future__ import annotations

import os
import re
import sys
import types
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("TEAM_PANEL_SCHEDULER", "off")
for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_API_KEY", "TEAM_PANEL_GEMINI_API_KEY"):
    os.environ.pop(k, None)

import alliance_team_panel_v1 as tp  # noqa: E402


# ---------------------------------------------------------------- unit

def test_phone10_rules():
    assert tp.phone10("+91 98110 12345") == "9811012345"
    assert tp.phone10("09811012345") == "9811012345"
    assert tp.phone10("011-2345-6789") == ""          # landline
    assert tp.phone10("123456789012345") == ""        # opaque WhatsApp id, never guessed
    assert tp.phone10("") == ""


def test_strip_contacts_removes_numbers_links_emails():
    s = tp.strip_contacts("Call +91 98110 12345 or 9876543210, mail a@b.com, see https://x.y/z . 2500 sqft at 4.5 lakh")
    assert not re.search(r"\d{10}", s.replace(" ", ""))
    assert "@" not in s and "http" not in s
    assert "2500 sqft" in s and "4.5 lakh" in s


def test_region_classifier():
    assert tp.classify_region("Saket, New Delhi") == "NCR"
    assert tp.classify_region("Golf Course Road Gurugram") == "NCR"
    assert tp.classify_region("Assagao, North Goa") == "GOA"
    assert tp.classify_region("Need space in Delhi or Goa") == "BOTH"
    assert tp.classify_region("Bandra West Mumbai") == "OTHER"
    assert tp.region_allowed("BOTH", ["NCR"]) is True
    assert tp.region_allowed("GOA", ["NCR"]) is False


def test_segment_classifier():
    assert tp.classify_segment("HOSPITALITY:CAFE") == "CAFE"
    assert tp.classify_segment("Restaurant") == "RESTAURANT"
    assert tp.classify_segment("", "Blue Tokai Coffee") == "CAFE"
    assert tp.classify_segment("RETAIL:JEWELLERY") == "JEWELLERY"
    assert tp.classify_segment("unknown thing") == ""


def test_greeting_and_personalise():
    assert tp._ensure_greeting("Hello there, we help cafes.").startswith("Hi {name}, we help")
    assert tp._ensure_greeting("We help cafes.").startswith("Hi {name}, we help")
    assert tp.personalise("Hi {name}, x", "Cafe Delhi Heights - Saket") == "Hi Cafe Delhi Heights, x"
    assert tp.personalise("Hi {name}, x", "") == "Hi there, x"


def test_polish_without_ai_keeps_text_and_adds_optout():
    out, st = tp.polish_outreach("We have shops on lease in Saket.", "CAFE", "NCR", "Zoya")
    assert st == "NO_AI_BASIC_CLEANUP"
    assert out.startswith("Hi {name},") and "Saket" in out and "STOP" in out


def test_property_message_template_has_no_contacts():
    msg, st = tp.property_message({"original_message": "Need 2000 sqft in Saket call 9811012345"},
                                  {"location": "Saket", "area": "2000 sqft", "price": "4 lakh",
                                   "highlights": "Ground floor, owner 9876543210"}, "Yogesh")
    assert st == "TEMPLATE"
    assert "9876543210" not in msg and "9811012345" not in msg
    assert "Saket" in msg


def _pool(n, seg, region, start):
    city = "New Delhi" if region == "NCR" else "Goa"
    return [{"contact_id": f"C{start+i}", "name": f"{seg} {i}", "phone10": f"9{start+i:09d}", "city": city,
             "location": city, "segment": seg, "region": region, "source_name": "t"} for i in range(n)]


def test_allocation_respects_regions_size_and_no_duplicates():
    cfg = tp.DEFAULT_MEMBERS
    pool = []
    s = 100000
    for seg, _ in tp.DEFAULT_SEGMENTS + tp.FALLBACK_SEGMENTS:
        pool += _pool(80, seg, "NCR", s); s += 1000
    pool += _pool(60, "CAFE", "GOA", s); s += 1000
    plan = tp.allocate_outreach(pool, cfg, set(), 10, 25)
    seen = set()
    for m, batches in plan.items():
        assert len(batches) == 10
        assert sum(len(b["contacts"]) for b in batches) == 250
        for b in batches:
            assert len(b["contacts"]) <= 25
            for c in b["contacts"]:
                assert c["phone10"] not in seen
                seen.add(c["phone10"])
                if m == "yogesh":
                    assert c["region"] == "NCR"
    # Goa stock goes to the Goa-covering members.
    goa_users = {m for m, bs in plan.items() for b in bs for c in b["contacts"] if c["region"] == "GOA"}
    assert goa_users <= {"zoya", "priya"} and goa_users


def test_allocation_short_segment_is_shared_and_fallback_used():
    pool = _pool(30, "RESTAURANT", "NCR", 200000) + _pool(500, "OTHER_RETAIL", "NCR", 300000)
    plan = tp.allocate_outreach(pool, tp.DEFAULT_MEMBERS, set(), 10, 25)
    rest = {m: sum(len(b["contacts"]) for b in bs if b["segment"] == "RESTAURANT") for m, bs in plan.items()}
    assert sum(rest.values()) == 30 and all(v == 10 for v in rest.values())
    assert any(b["segment"] == "OTHER_RETAIL" for bs in plan.values() for b in bs)


def test_allocation_excludes_contacts():
    pool = _pool(50, "CAFE", "NCR", 400000)
    excl = {pool[0]["phone10"], pool[1]["phone10"]}
    plan = tp.allocate_outreach(pool, tp.DEFAULT_MEMBERS, excl, 10, 25)
    got = {c["phone10"] for bs in plan.values() for b in bs for c in b["contacts"]}
    assert not (got & excl)


# ---------------------------------------------------------------- database

DB = os.getenv("TEAM_PANEL_TEST_DB")
needs_db = pytest.mark.skipif(not DB, reason="TEAM_PANEL_TEST_DB not set")


def _fake_mra(n_ncr=20, n_goa=20):
    now = datetime.now(timezone.utc)
    reqs = []
    for i in range(n_ncr + n_goa):
        goa = i >= n_ncr
        loc = "Assagao" if goa else "Saket"
        reqs.append({"id": 1000 + i, "original_message": f"Need {1000+i} sqft retail in {loc}{' Goa' if goa else ' Delhi'}. Call 98{i:08d}",
                     "locations_list": [loc], "contacts_list": [f"98{i:08d}"], "classification": "GENUINE",
                     "genuine_confidence": 0.9, "matcher_eligible": True, "source_date": now - timedelta(days=1),
                     "whatsapp_sender_name": f"Broker {i}", "company_brand_person": ""})
    reqs.append({"id": 9999, "original_message": "Old Delhi requirement", "locations_list": ["Saket"],
                 "contacts_list": ["9811111111"], "source_date": now - timedelta(days=90)})
    reqs.append({"id": 9998, "original_message": "Requirement in Mumbai", "locations_list": ["Bandra"],
                 "contacts_list": ["9822222222"], "source_date": now})
    reqs.append({"id": 9997, "original_message": "No phone Delhi", "locations_list": ["Saket"],
                 "contacts_list": ["123456789012345"], "source_date": now})

    def _match(core, req):
        rid = req["id"]
        loc = req["locations_list"][0]
        items = [
            {"record_id": f"P{rid}A", "match_score": 92, "_bucket": "EXACT VERIFIED", "location": loc,
             "subtype": "Retail shop", "transaction": "Rent", "area_display": "1200 sqft", "price_display": "3.5 L",
             "property": "Ground floor shop, call owner 9123456789", "source_name": "WhatsApp: Delhi Brokers", "why": ["location", "area"]},
            {"record_id": f"P{rid}B", "match_score": 60, "_bucket": "ALTERNATIVE", "location": loc},
        ]
        enrich = {f"P{rid}A": {"contacts": [{"label": "owner_phone", "value": "9123456789"}], "sources": ["Master"]}}
        sender = {f"P{rid}A": [{"label": "WhatsApp Sender", "value": "9000000001"}]}
        return {}, items, enrich, sender

    mod = types.ModuleType("alliance_master_requirement_authority_v1")
    mod._requirements = lambda core: [dict(r) for r in reqs]
    mod._apply_requirement_sender = lambda rows: rows
    mod._match = _match
    mod._match_detail_map = lambda core, ids: {i: {"description": "Corner shop, high footfall"} for i in ids}
    return mod


@pytest.fixture()
def core():
    from sqlalchemy import create_engine, text
    eng = create_engine(DB, future=True)
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
        c.execute(text("""CREATE TABLE ai_marketing_contacts(id BIGSERIAL PRIMARY KEY, contact_id VARCHAR(50) UNIQUE NOT NULL,
            fingerprint VARCHAR(64) UNIQUE, business_type TEXT, brand_name TEXT, contact_name TEXT, phone TEXT, email TEXT,
            website TEXT, location TEXT, city TEXT, source_name TEXT, source_url TEXT, consent_status VARCHAR(40) DEFAULT 'UNKNOWN',
            verification_status VARCHAR(40) DEFAULT 'UNVERIFIED', assigned_to TEXT, created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW())"""))
        n = 0
        rows = []
        for seg, fam in tp.DEFAULT_SEGMENTS + tp.FALLBACK_SEGMENTS:
            for i in range(90):
                n += 1
                rows.append({"id": f"C{n}", "bt": f"{fam}:{seg}", "b": f"{seg.title()} Outlet {i}", "p": f"9{n:09d}",
                             "loc": "Hauz Khas, New Delhi", "city": "New Delhi", "cs": "UNKNOWN"})
        for i in range(80):
            n += 1
            rows.append({"id": f"C{n}", "bt": "HOSPITALITY:CAFE", "b": f"Goa Cafe {i}", "p": f"9{n:09d}",
                         "loc": "Anjuna, North Goa", "city": "Goa", "cs": "UNKNOWN"})
        rows.append({"id": "STOP1", "bt": "HOSPITALITY:CAFE", "b": "Opted out cafe", "p": "9555555555",
                     "loc": "Saket, New Delhi", "city": "New Delhi", "cs": "OPT_OUT"})
        rows.append({"id": "BAD1", "bt": "HOSPITALITY:CAFE", "b": "Landline cafe", "p": "011-23456789",
                     "loc": "Saket, New Delhi", "city": "New Delhi", "cs": "UNKNOWN"})
        c.execute(text("""INSERT INTO ai_marketing_contacts(contact_id,business_type,brand_name,phone,location,city,source_name,consent_status)
                          VALUES(:id,:bt,:b,:p,:loc,:city,'Google Maps',:cs)"""), rows)
    sys.modules["alliance_master_requirement_authority_v1"] = _fake_mra()
    tp.install(eng)
    ns = types.SimpleNamespace(engine=eng)

    def need_login(req):
        role = req.headers.get("x-test-role")
        if not role:
            raise Exception("login")
        return role
    ns.need_login = need_login
    yield ns
    eng.dispose()


@needs_db
def test_end_to_end_day(core):
    from sqlalchemy import text
    d = tp.today_ist()
    res = tp.generate_day(core, d)
    assert res["outreach"]["status"] == "OK", res
    assert res["matches"]["status"] == "OK", res
    eng = core.engine
    with eng.connect() as c:
        per = dict(c.execute(text("SELECT member, COUNT(*) FROM tp_outreach_items_v1 WHERE plan_date=:d GROUP BY member"), {"d": d}).all())
        assert per == {"yogesh": 250, "zoya": 250, "priya": 250}
        assert c.execute(text("SELECT COUNT(*) FROM tp_outreach_items_v1 WHERE member='yogesh' AND region<>'NCR'")).scalar() == 0
        assert c.execute(text("SELECT COUNT(*) FROM tp_outreach_items_v1 WHERE phone10 IN ('9555555555')")).scalar() == 0
        assert c.execute(text("SELECT COUNT(DISTINCT phone10) FROM tp_outreach_items_v1 WHERE plan_date=:d"), {"d": d}).scalar() == 750
        mt = dict(c.execute(text("SELECT member, COUNT(*) FROM tp_match_items_v1 WHERE plan_date=:d GROUP BY member"), {"d": d}).all())
        assert mt == {"yogesh": 10, "zoya": 10, "priya": 10}
        assert c.execute(text("SELECT COUNT(*) FROM tp_match_items_v1 WHERE member='yogesh' AND requirement_region<>'NCR'")).scalar() == 0
        assert c.execute(text("SELECT COUNT(*) FROM tp_match_items_v1 WHERE requirement_id IN ('9999','9998','9997')")).scalar() == 0
        msgs = [r[0] for r in c.execute(text("SELECT message_text FROM tp_match_items_v1")).all()]
        assert all(not re.search(r"\d{10}", m.replace(" ", "")) for m in msgs)
        ic = c.execute(text("SELECT internal_contacts FROM tp_match_items_v1 LIMIT 1")).scalar()
        assert {x["value"] for x in ic} == {"9123456789", "9000000001"}
        assert c.execute(text("SELECT COUNT(DISTINCT broker_phone) FROM tp_match_items_v1")).scalar() == 30

    # Idempotent: a second run adds nothing.
    again = tp.generate_day(core, d)
    assert again["outreach"]["status"] == "ALREADY_READY" and again["matches"]["status"] == "ALREADY_READY"

    with eng.connect() as c:
        items = [r[0] for r in c.execute(text("SELECT id FROM tp_outreach_items_v1 WHERE member='zoya' ORDER BY id LIMIT 12")).all()]
        mid = c.execute(text("SELECT id FROM tp_match_items_v1 WHERE member='yogesh' ORDER BY slot LIMIT 1")).scalar()
    for i in items[:10]:
        tp.set_outreach_status(eng, i, "SENT")
    r = tp.set_outreach_status(eng, items[0], "REPLIED")
    assert r["lead_id"]
    tp.set_outreach_status(eng, items[10], "STOP")
    m = tp.set_match_status(eng, mid, "REPLIED")
    assert m["lead_id"]

    due = tp.due_counts(eng, "zoya")
    assert due["due_now"] == 1
    tp.followup(eng, r["lead_id"], "tomorrow", note="Spoke, wants Saket options", status="INTERESTED")
    assert tp.due_counts(eng, "zoya")["due_now"] == 0
    with pytest.raises(ValueError):
        tp.followup(eng, r["lead_id"], "date", when="not a date")
    tp.followup(eng, m["lead_id"], "close", status="DEAL_WON")

    reps = tp.store_daily_report(eng, d)
    z = [x for x in reps if x["member"] == "zoya"][0]
    assert z["outreach"]["sent"] == 11 and z["outreach"]["replied"] == 1 and z["outreach"]["stop"] == 1
    assert z["leads"]["new_leads"] == 1 and z["leads"]["followups_done"] == 1
    y = [x for x in reps if x["member"] == "yogesh"][0]
    assert y["matches"]["replied"] == 1 and y["leads"]["won_today"] == 1

    rev = tp.monthly_review(eng, d.strftime("%Y-%m"))
    assert rev["ai_status"] == "RULES_ONLY" and rev["flags"]

    # Next day: SENT/STOP/lead phones are on cooldown, unsent PENDING ones come back.
    with eng.connect() as c:
        sent_phones = {r[0] for r in c.execute(text("SELECT phone10 FROM tp_outreach_items_v1 WHERE status<>'PENDING'")).all()}
        pend = {r[0] for r in c.execute(text("SELECT phone10 FROM tp_outreach_items_v1 WHERE status='PENDING'")).all()}
    d2 = d + timedelta(days=1)
    tp.generate_outreach(eng, d2)
    with eng.connect() as c:
        nxt = {r[0] for r in c.execute(text("SELECT phone10 FROM tp_outreach_items_v1 WHERE plan_date=:d"), {"d": d2}).all()}
    assert not (nxt & sent_phones)
    assert nxt & pend


@needs_db
def test_http_routes(core):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import alliance_team_panel_ui_v1 as ui
    tp.generate_day(core, tp.today_ist())
    app = FastAPI()
    ui.mount(core, app)
    cl = TestClient(app)
    team = {"x-test-role": "team"}
    admin = {"x-test-role": "admin"}

    assert cl.get("/team-panel", follow_redirects=False).status_code == 303
    r = cl.get("/team-panel", headers=team)
    assert r.status_code == 200 and "Yogesh Mehra" in r.text and "Owner view" not in r.text
    assert "Owner view" in cl.get("/team-panel", headers=admin).text
    for path in ("/team-panel/m/yogesh", "/team-panel/m/zoya/outreach", "/team-panel/m/priya/matches", "/team-panel/m/zoya/leads"):
        assert cl.get(path, headers=team).status_code == 200, path
    assert cl.get("/team-panel/m/nobody", headers=team).status_code == 404

    from sqlalchemy import text
    with core.engine.connect() as c:
        bid = c.execute(text("SELECT id FROM tp_outreach_batches_v1 WHERE member='zoya' ORDER BY batch_no LIMIT 1")).scalar()
        iid = c.execute(text("SELECT id FROM tp_outreach_items_v1 WHERE batch_id=:b LIMIT 1"), {"b": bid}).scalar()
        mid = c.execute(text("SELECT id FROM tp_match_items_v1 WHERE member='priya' LIMIT 1")).scalar()
    page = cl.get(f"/team-panel/m/zoya/outreach?b={bid}", headers=team)
    assert page.status_code == 200 and "Make it better with AI" in page.text
    p = cl.post(f"/team-panel/api/batch/{bid}/polish", json={"draft": "We have cafe spaces in Hauz Khas"}, headers=team).json()
    assert "{name}" in p["text"]
    assert cl.post(f"/team-panel/api/batch/{bid}/final", json={"text": p["text"]}, headers=team).json()["ok"]
    assert cl.post(f"/team-panel/api/outreach/{iid}/status", json={"status": "REPLIED"}, headers=team).json()["lead_id"]
    assert cl.post(f"/team-panel/api/outreach/{iid}/status", json={"status": "bogus"}, headers=team).status_code == 400
    mpage = cl.get("/team-panel/m/priya/matches", headers=team).text
    assert "Team only" in mpage and "9123456789" in mpage
    saved = cl.post(f"/team-panel/api/match/{mid}/message", json={"text": "Nice shop, call 9811012345"}, headers=team).json()
    assert "9811012345" not in saved["text"]
    assert cl.post(f"/team-panel/api/match/{mid}/rewrite", headers=team).json()["ok"]
    assert cl.post("/team-panel/api/m/zoya/lead", json={"name": "Walk in", "phone": "9876500000"}, headers=team).json()["lead_id"]
    assert cl.get("/team-panel/api/m/zoya/due", headers=team).json()["due_now"] == 2
    assert cl.post("/team-panel/api/admin/report", json={}, headers=team).status_code == 403
    assert cl.post("/team-panel/api/admin/report", json={}, headers=admin).json()["saved"] == 3
    assert cl.post("/team-panel/api/admin/monthly", json={"month": tp.today_ist().strftime("%Y-%m")}, headers=admin).json()["ok"]
    a = cl.get("/team-panel/admin", headers=admin)
    assert a.status_code == 200 and "Monthly AI review" in a.text
    assert "Admin login" in cl.get("/team-panel/admin", headers=team).text
    assert cl.post("/team-panel/api/status", headers=team).status_code == 405
