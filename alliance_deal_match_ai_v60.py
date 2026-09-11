from __future__ import annotations

from html import escape
import re
from typing import Any, Dict, List

from fastapi import Request, Query, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import text

import alliance_phase5_canonical_matcher as phase5
import alliance_whatsapp_first_match_v1 as whatsapp_first

VERSION = "6.4.1-EMOJI-SECTOR-DECOMPOSITION"
ROUTE = "/deal-match-ai-v60"
ENGINE_VERSION = getattr(whatsapp_first, "VERSION", "UNKNOWN-MATCH-ENGINE")

# This file intentionally keeps the existing public route and API names.
# Matching is delegated to the separately validated Phase 5 canonical engine.
# Contacts are never rendered or returned by this adapter.


def esc(v: Any) -> str:
    return escape(str(v if v is not None else ""), quote=True)


_REQ_MARKER = re.compile(
    r"(?i)(?:"
    r"(?<![A-Z0-9])(\d+)(?:ST|ND|RD|TH)?\s*(?:HOT\s*)?REQUIREMENT\s*(?:👇|:)?"
    r"|([1-9])\ufe0f?\u20e3"
    r"|([①②③④⑤⑥⑦⑧⑨⑩])"
    r")"
)

_CIRCLED_TO_INT = {
    "①": 1, "②": 2, "③": 3, "④": 4, "⑤": 5,
    "⑥": 6, "⑦": 7, "⑧": 8, "⑨": 9, "⑩": 10,
}


def _marker_number(marker) -> int:
    if marker.group(1):
        return int(marker.group(1))
    if marker.group(2):
        return int(marker.group(2))
    return _CIRCLED_TO_INT[marker.group(3)]

def _expand_sector_lists(value: str) -> str:
    s = str(value or "")

    def repl(match):
        nums = re.findall(r"\d{1,3}", match.group(0))
        return " ".join("SECTOR " + n for n in nums)

    s = re.sub(
        r"(?i)\b(?:SEC|SECTOR)\s*-?\s*\d{1,3}"
        r"(?:\s*(?:,|&|AND|/)\s*\d{1,3})+",
        repl,
        s,
    )
    s = re.sub(
        r"(?i)\b(?:SEC|SECTOR)\s*-?\s*(\d{1,3})\b",
        lambda m: "SECTOR " + m.group(1),
        s,
    )
    return s


def _normalize_requirement_segment(raw_segment: str, inherited_transaction: str = "") -> str:
    s = str(raw_segment or "").replace("*", " ")
    s = re.sub(r"[ \t]+", " ", s).strip()

    s = re.sub(
        r"(?i)\b(?:DLF\s*)?PH(?:ASE)?\s*-?\s*([1-5])\b",
        lambda m: "DLF PHASE " + m.group(1),
        s,
    )
    s = _expand_sector_lists(s)

    s = re.sub(
        r"(?i)\bSUSHANT\s+LOK(?:\s+1)?\s+A\s*B\s*C(?:\s+BLOCK)?\b",
        "SUSHANT LOK 1",
        s,
    )

    s = re.sub(
        r"(?i)(?<![A-Z0-9])(\d+(?:\.\d+)?)\s*L\b",
        lambda m: m.group(1) + " lakh",
        s,
    )

    if inherited_transaction and not phase5.canonical_transaction(s):
        s = inherited_transaction + " · " + s

    return re.sub(r"\s+", " ", s).strip()


def split_requirement_text(raw_text: str) -> List[Dict[str, Any]]:
    raw = str(raw_text or "").strip()
    if not raw:
        return []

    markers = list(_REQ_MARKER.finditer(raw))
    prefix = raw[:markers[0].start()] if markers else raw
    inherited = phase5.canonical_transaction(prefix)
    inherited_phrase = ("FOR " + inherited) if inherited else ""

    if len(markers) < 2:
        return [{
            "number": 1,
            "source": raw,
            "normalized": _normalize_requirement_segment(raw, inherited_phrase),
        }]

    out = []
    for i, marker in enumerate(markers):
        end = markers[i + 1].start() if i + 1 < len(markers) else len(raw)
        segment = raw[marker.end():end].strip(" :-\n\r\t")
        if not segment:
            continue
        out.append({
            "number": _marker_number(marker),
            "source": segment,
            "normalized": _normalize_requirement_segment(segment, inherited_phrase),
        })
    return out


def _constraint_summary(raw_segment: str):
    n = phase5.norm(raw_segment)
    hard, prefs = [], []

    for token, label in (
        ("ONLY", "Only stated markets"),
        ("NO SOUTH", "No south-facing"),
        ("NO T POINT", "No T-point"),
        ("NO T-POINT", "No T-point"),
    ):
        if token in n and label not in hard:
            hard.append(label)

    for token, label in (
        ("FULLY FURNISHED", "Fully furnished"),
        ("SEMI FURNISHED", "Semi furnished"),
        ("LIFT", "Lift"),
        ("PARKING", "Parking"),
        ("NEW FLOOR", "New floor"),
        ("UNUSED", "Unused property"),
        ("PICTURE", "Pictures requested"),
        ("BUSINESS CLASS", "Business-class family"),
    ):
        if token in n and label not in prefs:
            prefs.append(label)

    return {"hard_constraints": hard, "preferences": prefs}


def _collect_matches(core, q: str, mode: str, min_score: float, limit: int = 100):
    jobs = []
    for part in split_requirement_text(q):
        res = run_match(core, part["normalized"], mode, min_score, limit)
        jobs.append({
            "number": part["number"],
            "source": phase5.sanitize_text(part["source"]),
            "constraints": _constraint_summary(part["source"]),
            "result": res,
        })
    return jobs


def run_match_multi(core, q: str, mode: str = "SMART",
                    min_score: float = 70.0, limit: int = 100):
    jobs = _collect_matches(core, q, mode, min_score, limit)
    if len(jobs) == 1:
        return jobs[0]["result"]

    return {
        "version": VERSION,
        "engine_version": ENGINE_VERSION,
        "multi_requirement": True,
        "requirement_count": len(jobs),
        "results": [
            {
                "requirement_number": j["number"],
                "source_requirement": j["source"],
                "constraints": j["constraints"],
                **j["result"],
            }
            for j in jobs
        ],
    }


def _ensure_feedback_tables(engine):
    # Existing user-triggered feedback behavior is preserved.
    # This is not part of candidate matching and does not mutate property inventory.
    with engine.begin() as c:
        c.execute(text("""
            CREATE TABLE IF NOT EXISTS ai_deal_match_feedback(
                id BIGSERIAL PRIMARY KEY,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                requirement_text TEXT,
                source_bucket TEXT,
                source_table TEXT,
                record_id TEXT,
                match_score NUMERIC(5,2),
                feedback TEXT,
                notes TEXT
            )
        """))


def run_match(core, requirement_text: str, mode: str = "SMART",
              min_score: float = 70.0, limit: int = 100) -> Dict[str, Any]:
    mode = str(mode or "SMART").upper()
    if mode not in {"SMART", "STRICT", "EXPANSION"}:
        mode = "SMART"

    result = whatsapp_first.run_match(
        core.engine,
        requirement_text=requirement_text,
        min_score=float(min_score),
        limit=int(limit),
    )

    # STRICT preserves the old route contract: exact location only.
    if mode == "STRICT":
        result["alternatives"] = []
        result["summary"]["approved_alternatives"] = 0

    result["route_version"] = VERSION
    result["engine_version"] = ENGINE_VERSION
    result["mode"] = mode

    # Defence-in-depth: canonical engine already blocks contact leaks.
    payload = repr(result)
    if phase5.PHONE_RE.search(payload) or phase5.EMAIL_RE.search(payload):
        raise RuntimeError("CONTACT_LEAK_GUARD_TRIGGERED_AT_ROUTE")

    return result


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<style>
*{{box-sizing:border-box}}
body{{margin:0;font-family:Arial,sans-serif;background:#f1e7d8;color:#2f251d}}
header{{background:#3f3329;color:#fff;padding:18px 24px}}
nav{{background:#fff8ef;padding:10px 18px;display:flex;gap:8px;flex-wrap:wrap}}
nav a,.btn,button{{background:#6b513d;color:#fff;text-decoration:none;border:0;border-radius:8px;padding:9px 12px;font-weight:800;cursor:pointer}}
main{{max-width:1900px;margin:auto;padding:18px}}
.card{{background:#fffdf9;border:1px solid #d7c4b1;border-radius:12px;padding:15px;margin-bottom:14px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px}}
input,select,textarea{{width:100%;padding:10px;border:1px solid #ccb8a5;border-radius:8px}}
table{{width:100%;border-collapse:collapse;min-width:1350px;background:#fff}}
th,td{{padding:9px;border-bottom:1px solid #eee1d2;text-align:left;vertical-align:top;font-size:12px}}
th{{background:#f7ecdf;position:sticky;top:0}}
.scroll{{overflow:auto;max-height:68vh}}
.score{{font-size:17px;font-weight:900}}
.green{{color:#19723d;font-weight:800}}
.amber{{color:#9b6500;font-weight:800}}
.red{{color:#a32626;font-weight:800}}
.pill{{display:inline-block;padding:4px 7px;border-radius:999px;background:#efe5d9;margin:2px}}
.muted{{color:#77685c}}
</style>
</head>
<body>
<header>
<h2 style="margin:0">Alliance Deal Match AI</h2>
<small>Canonical inventory · eligibility first · exact before approved alternatives · verification protected</small>
</header>
<nav>
<a href="javascript:history.back()">← Previous Page</a>
<a href="/alliance/primary">Dashboard</a>
</nav>
<main>{body}</main>
</body>
</html>"""


def render_form():
    body = """<div class="card">
<h2>Canonical Property Matcher</h2>
<p class="muted">Write the requirement naturally. Example: <b>Restaurant for rent in Saket, 2000 sqft, budget 4 lakh.</b></p>
<form method="get" action="/deal-match-ai-v60">
<div class="grid">
<div style="grid-column:1/-1">
<label>Requirement</label>
<textarea name="q" rows="4" required></textarea>
</div>
<div>
<label>Location Mode</label>
<select name="mode">
<option value="SMART" selected>SMART - exact + approved alternatives if no verified exact match</option>
<option value="STRICT">STRICT - exact location only</option>
<option value="EXPANSION">EXPANSION - exact + approved alternatives</option>
</select>
</div>
<div>
<label>Minimum Match %</label>
<input type="number" name="min_score" value="70" min="40" max="100">
</div>
<div style="align-self:end"><button>Run Canonical Matcher</button></div>
</div>
</form>
</div>
<div class="card">
<h3>Safety rules</h3>
<p>
<span class="pill">Canonical inventory only</span>
<span class="pill">Transaction hard gate</span>
<span class="pill">Area hard gate</span>
<span class="pill">Use/type hard gate</span>
<span class="pill">Price only if comparable</span>
<span class="pill">Price excluded from identity</span>
<span class="pill">Contacts hidden</span>
<span class="pill">READY + VERIFIED before send</span>
</p>
</div>"""
    return HTMLResponse(_page("Alliance Deal Match AI", body))


def _table(rows, empty_message: str):
    if not rows:
        return f'<div class="card"><p class="muted">{esc(empty_message)}</p></div>'

    trs = []
    for r in rows:
        why = "; ".join(r.get("why") or [])
        send = bool(r.get("send_eligible"))
        send_text = "READY TO SEND" if send else "VERIFY FIRST"
        send_class = "green" if send else "amber"
        trs.append(f"""<tr>
<td class="score">{esc(r.get("match_score"))}%</td>
<td><b>{esc(r.get("location") or "Unknown")}</b></td>
<td>{esc(r.get("transaction") or "Unknown")}</td>
<td>{esc(r.get("family") or "Unknown")} / {esc(r.get("subtype") or "Generic")}</td>
<td>{esc(r.get("property"))}</td>
<td>{esc(r.get("area_sqft"))}</td>
<td>{esc(r.get("price_display"))}</td>
<td>{esc(r.get("data_quality"))}</td>
<td>{esc(r.get("availability_verification"))}</td>
<td class="{send_class}">{send_text}</td>
<td>{esc(r.get("source_bucket"))} · {esc(r.get("source_table"))} · {esc(r.get("record_id"))}</td>
<td>{esc(why)}</td>
</tr>""")

    return f"""<div class="scroll"><table>
<tr>
<th>Match</th><th>Location</th><th>Transaction</th><th>Type / Use</th>
<th>Property</th><th>Area Sq Ft</th><th>Price/Rent</th><th>Data Quality</th>
<th>Availability Verification</th><th>Action Status</th><th>Source</th><th>Why Matched</th>
</tr>
{''.join(trs)}
</table></div>"""



def _result_table(rows, empty_message="No matching properties found.", *args, **kwargs):
    """
    Backward-compatible renderer.

    Older Alliance matcher/dashboard modules call `_result_table`.
    V6.1 renamed the implementation to `_table`.

    Keep both public helper names so older registered routes and
    middleware cannot break /deal-match-ai-v60.
    """
    return _table(rows or [], empty_message)

def render_results(core, q: str, mode: str, min_score: float):
    jobs = _collect_matches(core, q, mode, min_score, 100)

    if len(jobs) == 1:
        res = jobs[0]["result"]
        req = res["requirement"]
        s = res["summary"]
        body = (
            '<div class="card"><h2>Requirement Intelligence Card</h2>'
            '<p>' + esc(jobs[0]["source"]) + '</p><div class="grid">'
            '<div><b>Primary Locations</b><br>' + esc(", ".join(req.get("primary_locations") or ([req.get("location")] if req.get("location") else [])) or "Not identified") + '</div>'
            '<div><b>Transaction</b><br>' + esc(req.get("transaction") or "Not identified") + '</div>'
            '<div><b>Property Family</b><br>' + esc(req.get("family") or "Not identified") + '</div>'
            '<div><b>Subtype / Use</b><br>' + esc(req.get("subtype") or "Generic") + '</div>'
            '<div><b>Area Min</b><br>' + esc(req.get("area_min_sqft")) + '</div>'
            '<div><b>Area Max</b><br>' + esc(req.get("area_max_sqft")) + '</div>'
            '<div><b>Budget Max</b><br>' + esc(req.get("budget_max")) + '</div>'
            '<div><b>Mode</b><br>' + esc(res.get("mode")) + '</div></div></div>'
            '<div class="card"><h3>Canonical Search Coverage</h3><p><b>' + esc(s.get("pi_properties")) + '</b> canonical property rows + <b>' + esc(s.get("pi_whatsapp_property_master")) + '</b> clean WhatsApp master rows -> <b>' + esc(s.get("deduped_candidates")) + '</b> deduped candidates.</p>'
            '<p><b>' + esc(s.get("exact_verified")) + '</b> exact verified · <b>' + esc(s.get("exact_needs_verification")) + '</b> exact needs verification · <b>' + esc(s.get("approved_alternatives")) + '</b> approved alternatives</p></div>'
            '<div class="card"><h2>A. Exact Matches: VERIFIED + READY</h2>' + _table(res["exact_verified"], "No verified READY exact match found.") + '</div>'
            '<div class="card"><h2>B. Exact Matches: Verification Required</h2>' + _table(res["exact_needs_verification"], "No exact unverified candidate passed the hard gates.") + '</div>'
            '<div class="card"><h2>C. Smart Approved Alternatives</h2>' + _table(res["alternatives"], "No approved alternative passed the hard gates.") + '</div>'
            '<div class="card"><p><b>Matcher:</b> ' + esc(VERSION) + ' · <b>Engine:</b> ' + esc(ENGINE_VERSION) + '</p><p class="green">Contact details are intentionally hidden from matcher output.</p><a class="btn" href="/deal-match-ai-v60">Run Another Requirement</a></div>'
        )
        return HTMLResponse(_page("Alliance Deal Match AI Results", body))

    sections = [
        '<div class="card"><h2>Multi-Requirement WhatsApp Intelligence</h2>'
        '<p class="green">Detected ' + esc(len(jobs)) + ' independent requirements. Each is matched independently against canonical inventory.</p></div>'
    ]

    for job in jobs:
        res = job["result"]
        req = res["requirement"]
        s = res["summary"]
        c = job["constraints"]
        n = job["number"]
        locations = req.get("primary_locations") or ([req.get("location")] if req.get("location") else [])
        hard = ", ".join(c.get("hard_constraints") or []) or "None captured"
        prefs = ", ".join(c.get("preferences") or []) or "None captured"

        card = (
            '<div class="card"><h2>Requirement ' + esc(n) + ' Intelligence Card</h2><p>' + esc(job["source"]) + '</p><div class="grid">'
            '<div><b>Primary Locations</b><br>' + esc(", ".join(locations) if locations else "Not identified") + '</div>'
            '<div><b>Transaction</b><br>' + esc(req.get("transaction") or "Not identified") + '</div>'
            '<div><b>Property Family</b><br>' + esc(req.get("family") or "Not identified") + '</div>'
            '<div><b>Subtype / Use</b><br>' + esc(req.get("subtype") or "Generic") + '</div>'
            '<div><b>Accepted Forms</b><br>' + esc(", ".join(req.get("acceptable_subtypes") or [])) + '</div>'
            '<div><b>Area Min</b><br>' + esc(req.get("area_min_sqft")) + '</div>'
            '<div><b>Area Max</b><br>' + esc(req.get("area_max_sqft")) + '</div>'
            '<div><b>Budget Min</b><br>' + esc(req.get("budget_min")) + '</div>'
            '<div><b>Budget Max</b><br>' + esc(req.get("budget_max")) + '</div>'
            '<div><b>Hard Constraints</b><br>' + esc(hard) + '</div>'
            '<div><b>Preferences</b><br>' + esc(prefs) + '</div>'
            '<div><b>Mode</b><br>' + esc(res.get("mode")) + '</div></div></div>'
            '<div class="card"><h3>Requirement ' + esc(n) + ' · Canonical Search Coverage</h3><p><b>' + esc(s.get("pi_properties")) + '</b> canonical rows · <b>' + esc(s.get("pi_whatsapp_property_master")) + '</b> WhatsApp evidence rows · <b>' + esc(s.get("deduped_candidates")) + '</b> deduped candidates.</p><p><b>' + esc(s.get("exact_verified")) + '</b> exact verified · <b>' + esc(s.get("exact_needs_verification")) + '</b> exact needs verification · <b>' + esc(s.get("approved_alternatives")) + '</b> approved alternatives</p></div>'
            '<div class="card"><h3>A. Requirement ' + esc(n) + ' · Exact VERIFIED + READY</h3>' + _table(res["exact_verified"], "No verified READY exact match found.") + '</div>'
            '<div class="card"><h3>B. Requirement ' + esc(n) + ' · Exact Verification Required</h3>' + _table(res["exact_needs_verification"], "No exact unverified candidate passed the hard gates.") + '</div>'
            '<div class="card"><h3>C. Requirement ' + esc(n) + ' · Smart Approved Alternatives</h3>' + _table(res["alternatives"], "No approved alternative passed the hard gates.") + '</div>'
        )
        sections.append(card)

    sections.append(
        '<div class="card"><p><b>Matcher:</b> ' + esc(VERSION) + ' · <b>Engine:</b> ' + esc(ENGINE_VERSION) + '</p>'
        '<p class="green">Numbered requirements are independently parsed and matched. Contacts remain hidden.</p>'
        '<a class="btn" href="/deal-match-ai-v60">Run Another Requirement</a></div>'
    )
    return HTMLResponse(_page("Alliance Deal Match AI Results", "".join(sections)))


def register(core):
    app = core.app

    if any(getattr(r, "path", None) == ROUTE for r in app.router.routes):
        return {"status": "ALREADY_REGISTERED", "version": VERSION}

    @app.get(ROUTE, response_class=HTMLResponse)
    def deal_match_page(
        q: str = Query("", max_length=5000),
        mode: str = Query("SMART"),
        min_score: float = Query(70, ge=40, le=100),
    ):
        if not q.strip():
            return render_form()
        return render_results(core, q.strip(), mode, min_score)

    @app.get("/api/v60/deal-match")
    def deal_match_api(
        q: str = Query(..., min_length=2, max_length=5000),
        mode: str = Query("SMART"),
        min_score: float = Query(70, ge=40, le=100),
        limit: int = Query(50, ge=1, le=200),
    ):
        return JSONResponse(run_match_multi(core, q, mode, min_score, limit))

    @app.get("/api/v60/status")
    def deal_match_status():
        raw, counts = phase5.load_candidates(core.engine)
        deduped = phase5.dedupe_candidates(raw)
        return {
            "status": "OK",
            "version": VERSION,
            "engine_version": ENGINE_VERSION,
            "matching_model": "CANONICAL_MASTER_ONLY",
            "sources": counts,
            "candidate_count": len(deduped),
            "contacts_exposed": False,
            "price_used_only_when_comparable": True,
            "price_excluded_from_identity": True,
            "strict_send_rule": "READY_AND_VERIFIED",
            "source_data_mutation": False,
            "exact_and_alternatives_separated": True,
            "multi_requirement_decomposition": True,
        }

    @app.post("/api/v60/feedback")
    async def deal_match_feedback(request: Request):
        payload = await request.json()
        fb = str(payload.get("feedback") or "").strip()
        allowed = {
            "GOOD_MATCH", "BAD_MATCH", "WRONG_LOCATION", "WRONG_TRANSACTION",
            "WRONG_PROPERTY_TYPE", "TOO_EXPENSIVE", "WRONG_AREA", "WRONG_FLOOR",
            "WRONG_USE", "UNAVAILABLE", "CLIENT_INTERESTED", "SITE_VISIT",
            "NEGOTIATION", "DEAL_CLOSED"
        }
        if fb not in allowed:
            raise HTTPException(400, "Unsupported feedback")

        _ensure_feedback_tables(core.engine)
        with core.engine.begin() as c:
            c.execute(text("""
                INSERT INTO ai_deal_match_feedback(
                    requirement_text, source_bucket, source_table,
                    record_id, match_score, feedback, notes
                ) VALUES(:q,:sb,:st,:rid,:ms,:fb,:notes)
            """), {
                "q": payload.get("requirement_text"),
                "sb": payload.get("source_bucket"),
                "st": payload.get("source_table"),
                "rid": payload.get("record_id"),
                "ms": payload.get("match_score"),
                "fb": fb,
                "notes": payload.get("notes"),
            })
        return {"status": "OK", "feedback": fb}

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "engine_version": ENGINE_VERSION,
        "route": ROUTE,
    }
