from __future__ import annotations

import hashlib
import re
from datetime import date
from html import escape
from urllib.parse import quote_plus

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.routing import APIRoute
from sqlalchemy import text

VERSION = "3.0.0-PROPERTY-DATA-QUALITY"


AREA_FACTORS = {
    "SQFT": 1.0,
    "SQMT": 10.7639104167,
    "SQYD": 9.0,
}

CITY_ONLY = {
    "gurgaon", "gurugram", "delhi", "new delhi", "delhi ncr", "ncr",
    "noida", "greater noida", "faridabad", "ghaziabad", "goa",
    "north goa", "south goa", "mumbai", "bombay", "bangalore", "bengaluru",
}


def _norm(v):
    return re.sub(r"\s+", " ", str(v or "").strip().lower())


def _money_to_number(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    n = _norm(raw).replace(",", "").replace("₹", "").replace("rs.", "").replace("rs", "").strip()
    if n in {"on request", "price on request", "por", "negotiable", "call for price"}:
        return None
    m = re.search(r"(-?\d+(?:\.\d+)?)", n)
    if not m:
        return None
    value = float(m.group(1))
    if re.search(r"\b(cr|crore|crores)\b", n):
        value *= 10_000_000
    elif re.search(r"\b(l|lac|lakh|lakhs)\b", n):
        value *= 100_000
    return round(value, 2)


def _area_to_sqft(value, unit):
    if value in (None, ""):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    factor = AREA_FACTORS.get(str(unit or "").upper())
    return round(n * factor, 2) if factor else None


def _meaningful_location(value):
    n = _norm(value)
    return bool(n and n not in CITY_ONLY and n not in {"na", "n/a", "unknown", "not specified", "-"})


def _ensure_schema(engine):
    stmts = [
        "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS area_value NUMERIC(14,2)",
        "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS area_unit TEXT",
        "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS sale_price_display TEXT",
        "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS sale_price_normalized NUMERIC(18,2)",
        "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS monthly_rent_display TEXT",
        "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS monthly_rent_normalized NUMERIC(18,2)",
        "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS available_from DATE",
        "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS available_until DATE",
        "ALTER TABLE pi_properties ADD COLUMN IF NOT EXISTS data_quality_status TEXT DEFAULT 'NEEDS_REVIEW'",
    ]
    with engine.begin() as c:
        for stmt in stmts:
            c.execute(text(stmt))


def _remove_routes(app, path):
    kept = []
    for route in app.router.routes:
        methods = getattr(route, "methods", set()) or set()
        if isinstance(route, APIRoute) and getattr(route, "path", None) == path and ({"GET", "POST"} & set(methods)):
            continue
        kept.append(route)
    app.router.routes[:] = kept


def _page(message=""):
    msg = f"<div class='msg'>{escape(message)}</div>" if message else ""
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Add Property</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#f5f7fb;font-family:Arial;color:#172033}}
header{{background:#111827;color:white;padding:18px 22px}}main{{max-width:1050px;margin:auto;padding:20px}}
.card{{background:white;border:1px solid #e5e7eb;border-radius:14px;padding:18px;margin-bottom:14px}}
.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}}
label{{font-weight:700;font-size:13px}}input,select,textarea{{width:100%;padding:11px;border:1px solid #cfd6df;border-radius:8px;margin-top:5px}}
button,.btn{{display:inline-block;background:#111827;color:white;padding:11px 15px;border:0;border-radius:8px;text-decoration:none;font-weight:700;cursor:pointer}}
.note{{background:#eff6ff;color:#1e40af;padding:10px;border-radius:8px;margin-bottom:12px}}.msg{{background:#ecfdf5;color:#065f46;padding:10px;border-radius:8px;margin-bottom:12px}}
@media(max-width:720px){{.grid{{grid-template-columns:1fr}}}}
</style></head>
<body><header><b>Alliance Property Entry V3</b><br><small>Sale/Rent separated · flexible area units · readable price · availability date</small></header>
<main>{msg}
<div class="note">Transaction is never assumed. Area is stored exactly in the selected unit and also normalized internally to sqft for matching.</div>
<form method="post" action="/property-manual">
<div class="card"><div class="grid">
<div><label>Transaction Type *</label><select name="rent_or_sale" id="tx" required onchange="txMode()"><option value="">Select Transaction</option><option value="Sale">Sale</option><option value="Rent">Rent</option></select></div>
<div><label>Property Type *</label><input name="property_type" required placeholder="Villa / Apartment / Plot / Commercial"></div>
<div><label>City</label><input name="city" placeholder="Goa / Gurugram"></div>
<div><label>Micro-location / Project *</label><input name="location" required placeholder="Siolim / DLF Phase 1 / Sector 52"></div>
<div><label>Property Name / Project</label><input name="property_name" placeholder="Project or property name"></div>
<div><label>Floor / Configuration</label><input name="floor" placeholder="4 BHK / 2nd floor / 20th–25th"></div>
</div></div>

<div class="card"><h3>Area</h3><div class="grid">
<div><label>Area Value</label><input name="area_value" type="number" step="any" min="0" placeholder="500"></div>
<div><label>Area Unit</label><select name="area_unit"><option value="">Select Unit</option><option value="SQFT">Sq Ft</option><option value="SQMT">Sq Mtr</option><option value="SQYD">Sq Yd</option></select></div>
</div></div>

<div class="card"><h3>Price</h3>
<div id="saleBox" style="display:none"><label>Sale Price</label><input name="sale_price_display" placeholder="5 Cr / 85L / On Request"></div>
<div id="rentBox" style="display:none"><label>Monthly Rent</label><input name="monthly_rent_display" placeholder="3.5 Lakh / 2L / On Request"></div>
</div>

<div class="card"><h3>Availability</h3><div class="grid">
<div><label>Available From</label><input name="available_from" type="date"></div>
<div><label>Available Till (optional)</label><input name="available_until" type="date"></div>
</div></div>

<div class="card"><h3>Contact & Follow-up</h3><div class="grid">
<div><label>Owner Name</label><input name="owner_name"></div>
<div><label>Owner Contact</label><input name="owner_contact"></div>
<div><label>Broker Name</label><input name="broker_name"></div>
<div><label>Broker Contact</label><input name="broker_contact"></div>
</div><label>Remarks</label><textarea name="remarks" rows="4"></textarea></div>

<button type="submit">Save Property</button> <a class="btn" href="/workspace">Back to Workspace</a>
</form></main>
<script>
function txMode(){{
 const tx=document.getElementById('tx').value;
 document.getElementById('saleBox').style.display=tx==='Sale'?'block':'none';
 document.getElementById('rentBox').style.display=tx==='Rent'?'block':'none';
}}
txMode();
</script></body></html>"""


def register(core):
    app = core.app
    engine = core.engine
    _ensure_schema(engine)
    _remove_routes(app, "/property-manual")
    router = APIRouter()

    @router.get("/api/v3/property-data-quality/status")
    def status():
        return {"status": "OK", "version": VERSION}

    @router.get("/property-manual", response_class=HTMLResponse)
    def property_manual(request: Request):
        if hasattr(core, "need_login"):
            core.need_login(request)
        return HTMLResponse(_page(str(request.query_params.get("message") or "")))

    @router.post("/property-manual")
    def property_manual_save(
        request: Request,
        rent_or_sale: str = Form(...),
        property_type: str = Form(...),
        city: str = Form(""),
        location: str = Form(...),
        property_name: str = Form(""),
        floor: str = Form(""),
        area_value: str = Form(""),
        area_unit: str = Form(""),
        sale_price_display: str = Form(""),
        monthly_rent_display: str = Form(""),
        available_from: str = Form(""),
        available_until: str = Form(""),
        owner_name: str = Form(""),
        owner_contact: str = Form(""),
        broker_name: str = Form(""),
        broker_contact: str = Form(""),
        remarks: str = Form(""),
    ):
        if hasattr(core, "need_login"):
            core.need_login(request)

        tx = str(rent_or_sale or "").strip()
        if tx not in {"Sale", "Rent"}:
            return HTMLResponse(_page("Please select Sale or Rent."), status_code=400)
        if not _meaningful_location(location):
            return HTMLResponse(_page("Enter a micro-location/project. City alone is not enough."), status_code=400)

        area_sqft = _area_to_sqft(area_value, area_unit)
        sale_display = sale_price_display.strip() if tx == "Sale" else ""
        rent_display = monthly_rent_display.strip() if tx == "Rent" else ""
        sale_num = _money_to_number(sale_display) if tx == "Sale" else None
        rent_num = _money_to_number(rent_display) if tx == "Rent" else None

        payload = {
            "property_name": property_name.strip() or None,
            "property_type": property_type.strip() or "NA",
            "city": city.strip() or "NA",
            "location": location.strip(),
            "available_area_sqft": area_sqft,
            "floor": floor.strip() or None,
            "rent_or_sale": tx,
            "owner_name": owner_name.strip() or None,
            "owner_contact": owner_contact.strip() or None,
            "broker_name": broker_name.strip() or None,
            "broker_contact": broker_contact.strip() or None,
            "remarks": remarks.strip() or None,
            "source": "Manual",
        }

        if hasattr(core, "save_property"):
            result = core.save_property(payload)
            property_id = result.get("property_id")
            save_status = result.get("status")
        else:
            property_id = None
            save_status = "error"

        if not property_id:
            return HTMLResponse(_page("Property could not be saved."), status_code=500)

        av_from = available_from.strip() or None
        av_until = available_until.strip() or None
        with engine.begin() as c:
            c.execute(text("""
                UPDATE pi_properties SET
                    area_value=:area_value,
                    area_unit=:area_unit,
                    sale_price_display=:sale_display,
                    sale_price_normalized=:sale_num,
                    monthly_rent_display=:rent_display,
                    monthly_rent_normalized=:rent_num,
                    available_from=CAST(:available_from AS DATE),
                    available_until=CAST(:available_until AS DATE),
                    data_quality_status='READY',
                    updated_at=NOW()
                WHERE property_id=:property_id
            """), {
                "area_value": float(area_value) if area_value not in ("", None) else None,
                "area_unit": area_unit or None,
                "sale_display": sale_display or None,
                "sale_num": sale_num,
                "rent_display": rent_display or None,
                "rent_num": rent_num,
                "available_from": av_from,
                "available_until": av_until,
                "property_id": property_id,
            })

        msg = f"Property {save_status}: {property_id}"
        return RedirectResponse("/property-manual?message=" + quote_plus(msg), status_code=303)

    app.include_router(router)
    return {"version": VERSION, "status": "REGISTERED"}
