from __future__ import annotations
import html, json, threading, time
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID
from typing import Any, Dict, List

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import inspect, text

VERSION = "2.2.0-READ-ONLY-SAFE-HTML-MULTISTORE-WHATSAPP-FORENSICS"
API_ROUTE = "/api/alliance/whatsapp-reconciliation-v2"
PAGE_ROUTE = "/alliance/admin/whatsapp-reconciliation-v2"
CACHE_SECONDS = 60
_lock = threading.Lock()
_cache = {"at": 0.0, "value": None}

def _safe(v):
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, (UUID, Decimal)):
        return str(v)
    if isinstance(v, dict):
        return {str(k): _safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [_safe(x) for x in v]
    return str(v)

def _tables(engine):
    try:
        return set(inspect(engine).get_table_names())
    except Exception:
        return set()

def _cols(engine, table):
    try:
        return {x["name"] for x in inspect(engine).get_columns(table)}
    except Exception:
        return set()

def _scalar(conn, sql, params=None):
    try:
        return int(conn.execute(text(sql), params or {}).scalar() or 0)
    except Exception:
        return None

def _rows(conn, sql, params=None):
    try:
        return [dict(r) for r in conn.execute(text(sql), params or {}).mappings().all()]
    except Exception:
        return []

def _wa_engine():
    candidates = [
        ("whatsapp_intelligence", "wa_engine"),
        ("whatsapp_intelligence_property_purity", "wa_engine"),
    ]
    for modname, attr in candidates:
        try:
            mod = __import__(modname)
            eng = getattr(mod, attr, None)
            if eng is not None:
                return eng
        except Exception:
            pass
    return None

def _auth_guard(core, request: Request):
    # Reuse Alliance's existing authoritative authentication contract.
    # Avoid direct Request.session access; it may raise when SessionMiddleware
    # is not the auth mechanism serving this application.
    try:
        core.need_login(request)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Login required") from exc

def _group_forensics(wa_engine, main_engine):
    wt = _tables(wa_engine)
    mt = _tables(main_engine)
    report = {
        "status": "PASS",
        "version": VERSION,
        "mode": "READ_ONLY_FORENSICS",
        "read_only": True,
        "source_mutations": 0,
        "master_mutations": 0,
        "matcher_mutations": 0,
        "terminology": {
            "observed_sources": "rows present in wa_sources; not asserted to equal connector-configured groups",
            "declared_messages": "wa_sources.total_messages when available",
        },
        "schemas": {
            "whatsapp": sorted(x for x in wt if x.startswith(("wa_", "wai_"))),
            "master": sorted(x for x in mt if x in {
                "pi_master_properties_v711", "pi_master_source_links_v711",
                "pi_master_workflow_v720", "pi_whatsapp_live_clean_ledger",
                "pi_whatsapp_property_master"
            }),
        },
        "totals": {},
        "groups": [],
        "failure_codes": {},
    }
    if "wa_sources" not in wt:
        report["status"] = "FAIL"
        report["failure_codes"]["WA_SOURCES_MISSING"] = 1
        return report

    sc = _cols(wa_engine, "wa_sources")
    select = ["source_id"]
    for c in ("group_name","source_name","ingestion_status","total_messages","created_at"):
        if c in sc: select.append(c)
    with wa_engine.connect() as c:
        sources = _rows(c, "SELECT " + ",".join(select) + " FROM wa_sources ORDER BY COALESCE(group_name,source_name,'')")
        report["totals"]["observed_sources"] = len(sources)

        wmc = _cols(wa_engine, "wa_messages") if "wa_messages" in wt else set()
        wpc = _cols(wa_engine, "wa_properties") if "wa_properties" in wt else set()
        wrc = _cols(wa_engine, "wa_requirements") if "wa_requirements" in wt else set()
        wai_gc = _cols(wa_engine, "wai_groups") if "wai_groups" in wt else set()
        wai_mc = _cols(wa_engine, "wai_raw_messages") if "wai_raw_messages" in wt else set()

        # Build WAI group-name counts once. WAI uses derived group IDs, so group name
        # is the safest read-only bridge unless the schema exposes a direct source ID.
        wai_by_name = {}
        if "wai_groups" in wt and "wai_raw_messages" in wt and "name" in wai_gc and "group_id" in wai_mc:
            gidcol = "id" if "id" in wai_gc else ("group_id" if "group_id" in wai_gc else None)
            if gidcol:
                rr = _rows(c, f"""SELECT g.name AS group_name, COUNT(m.*) AS n
                                  FROM wai_groups g LEFT JOIN wai_raw_messages m
                                  ON m.group_id=g.{gidcol} GROUP BY g.name""")
                wai_by_name = {str(r.get("group_name") or ""): int(r.get("n") or 0) for r in rr}

        for s in sources:
            sid = s.get("source_id")
            name = s.get("group_name") or s.get("source_name") or "Unknown WhatsApp Group"
            declared = s.get("total_messages")
            raw = 0
            if "wa_messages" in wt and "source_id" in wmc:
                raw = _scalar(c, "SELECT COUNT(*) FROM wa_messages WHERE source_id=:sid", {"sid": sid}) or 0
            props = 0
            if "wa_properties" in wt and "source_id" in wpc:
                props = _scalar(c, "SELECT COUNT(*) FROM wa_properties WHERE source_id=:sid", {"sid": sid}) or 0
            reqs = 0
            if "wa_requirements" in wt and "source_id" in wrc:
                reqs = _scalar(c, "SELECT COUNT(*) FROM wa_requirements WHERE source_id=:sid", {"sid": sid}) or 0
            wai_raw = int(wai_by_name.get(str(name), 0))
            gaps = []
            if declared is not None and int(declared or 0) != raw:
                gaps.append({"code":"SOURCE_MESSAGE_COUNT_MISMATCH","declared":int(declared or 0),"wa_messages":raw})
            if int(declared or 0) > 0 and raw == 0 and wai_raw > 0:
                gaps.append({"code":"MESSAGES_PRESENT_IN_WAI_NOT_WA","wai_raw_messages":wai_raw})
            if int(declared or 0) > 0 and raw == 0 and wai_raw == 0:
                gaps.append({"code":"DECLARED_WITHOUT_DISCOVERED_RAW_MESSAGES"})
            report["groups"].append({
                "source_id": _safe(sid), "group": _safe(name),
                "ingestion_status": _safe(s.get("ingestion_status")),
                "declared_messages": _safe(declared),
                "wa_messages": raw, "wai_raw_messages_by_group_name": wai_raw,
                "wa_properties": props, "wa_requirements": reqs,
                "gaps": gaps, "status": "FAIL" if gaps else "PASS",
            })

    # Main/master side: aggregate evidence only; no DML.
    with main_engine.connect() as c:
        for t in ("pi_master_properties_v711","pi_master_source_links_v711",
                  "pi_master_workflow_v720","pi_whatsapp_live_clean_ledger",
                  "pi_whatsapp_property_master"):
            if t in mt:
                report["totals"][t] = _scalar(c, f"SELECT COUNT(*) FROM {t}")

        if "pi_master_source_links_v711" in mt:
            lc = _cols(main_engine, "pi_master_source_links_v711")
            if "source_type" in lc:
                report["totals"]["whatsapp_master_source_links"] = _scalar(
                    c, "SELECT COUNT(*) FROM pi_master_source_links_v711 WHERE UPPER(COALESCE(source_type,'')) LIKE '%WHATSAPP%'"
                )
            if "source_table" in lc:
                report["totals"]["wa_properties_master_links"] = _scalar(
                    c, "SELECT COUNT(*) FROM pi_master_source_links_v711 WHERE source_table='wa_properties'"
                )

    failures = {}
    for g in report["groups"]:
        for gap in g["gaps"]:
            failures[gap["code"]] = failures.get(gap["code"], 0) + 1
    report["failure_codes"] = failures
    report["totals"]["groups_pass"] = sum(g["status"] == "PASS" for g in report["groups"])
    report["totals"]["groups_fail"] = sum(g["status"] == "FAIL" for g in report["groups"])
    if failures:
        report["status"] = "FAIL"
    return _safe(report)

def audit(core, force=False):
    now = time.time()
    with _lock:
        if not force and _cache["value"] is not None and now - _cache["at"] < CACHE_SECONDS:
            out = dict(_cache["value"])
            out["cache"] = "HIT"
            return out
    we = _wa_engine()
    if we is None:
        return {"status":"FAIL","version":VERSION,"read_only":True,
                "failure_codes":{"WHATSAPP_ENGINE_UNAVAILABLE":1}}
    out = _group_forensics(we, core.engine)
    with _lock:
        _cache["at"], _cache["value"] = time.time(), out
    out = dict(out); out["cache"] = "MISS"
    return out

def _page(data):
    rows = []
    for g in data.get("groups", []):
        gaps = ", ".join(html.escape(x.get("code","")) for x in g.get("gaps", [])) or "—"
        cells = [
            g.get("group",""),
            g.get("declared_messages",""),
            g.get("wa_messages",""),
            g.get("wai_raw_messages_by_group_name",""),
            g.get("wa_properties",""),
            g.get("status",""),
        ]
        rows.append(
            "<tr>" +
            "".join("<td>" + html.escape(str(v)) + "</td>" for v in cells) +
            "<td>" + gaps + "</td></tr>"
        )

    totals = data.get("totals", {})
    status = html.escape(str(data.get("status", "")))
    observed = html.escape(str(totals.get("observed_sources", 0)))
    passed = html.escape(str(totals.get("groups_pass", 0)))
    failed = html.escape(str(totals.get("groups_fail", 0)))

    return (
        "<!doctype html><html><head>"
        "<title>Alliance WhatsApp Source Reconciliation V2</title>"
        "<style>"
        "body{font-family:Arial;margin:24px}"
        "table{border-collapse:collapse;width:100%}"
        "td,th{border:1px solid #ddd;padding:7px;text-align:left}"
        ".muted{color:#666}"
        "</style></head><body>"
        "<h1>Alliance WhatsApp Source Reconciliation V2</h1>"
        "<p class='muted'>Read-only multi-store forensic view. "
        "Observed sources means wa_sources rows, not asserted connector configuration.</p>"
        "<p>Status: <b>" + status + "</b> · Observed sources: " + observed +
        " · Pass: " + passed + " · Fail: " + failed + "</p>"
        "<table><tr><th>Observed source/group</th><th>Declared</th>"
        "<th>wa_messages</th><th>wai_raw</th><th>wa_properties</th>"
        "<th>Status</th><th>Gaps</th></tr>" + "".join(rows) +
        "</table></body></html>"
    )

def register(core) -> Dict[str, Any]:
    app = core.app
    paths = {getattr(r, "path", None) for r in app.router.routes}
    registered = []
    if API_ROUTE not in paths:
        @app.get(API_ROUTE)
        def api(request: Request, force: bool=False):
            _auth_guard(core, request)
            return audit(core, force=force)
        registered.append(API_ROUTE)
    if PAGE_ROUTE not in paths:
        @app.get(PAGE_ROUTE, response_class=HTMLResponse)
        def page(request: Request, force: bool=False):
            _auth_guard(core, request)
            return HTMLResponse(_page(audit(core, force=force)))
        registered.append(PAGE_ROUTE)
    return {"status":"REGISTERED","version":VERSION,"mode":"READ_ONLY",
            "routes":[API_ROUTE,PAGE_ROUTE],"registered_now":registered,
            "source_mutations":0,"master_mutations":0,"matcher_mutations":0,
            "privacy":"ALLIANCE_AUTHORITY_REQUIRED","cache_seconds":CACHE_SECONDS}
