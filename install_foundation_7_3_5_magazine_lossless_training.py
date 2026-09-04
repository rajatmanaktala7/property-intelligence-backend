from pathlib import Path
from datetime import datetime
import shutil, re, subprocess, sys

ROOT = Path(__file__).resolve().parent
GATEWAY = ROOT / "alliance_magazine_safe_gateway_v660.py"
WORKSPACE = ROOT / "alliance_primary_workspace_v730.py"
TRAINER = ROOT / "alliance_magazine_lossless_extraction_v670.py"
TARGET = "7.3.5-ALLIANCE-MAGAZINE-LOSSLESS-TRAINING"
MARKER = "# 7.3.5 MAGAZINE LOSSLESS EXTRACTION TRAINING"

for p in [GATEWAY, WORKSPACE, TRAINER]:
    if not p.exists():
        raise SystemExit(f"ERROR: missing {p.name}")

cp = subprocess.run([sys.executable, str(TRAINER)], capture_output=True, text=True)
if cp.returncode != 0 or '"status": "PASS"' not in cp.stdout:
    print(cp.stdout); print(cp.stderr)
    raise SystemExit("ERROR: lossless training self-test failed")

stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

g = GATEWAY.read_text(encoding="utf-8")
if MARKER not in g:
    gb = ROOT / f"alliance_magazine_safe_gateway_v660-before-v735-{stamp}.py"
    shutil.copy2(GATEWAY, gb)

    imp = "import alliance_magazine_challenger_v514 as semantic_student"
    if "import alliance_magazine_lossless_extraction_v670 as lossless_v670" not in g:
        if imp not in g:
            raise SystemExit("ERROR: v660 import anchor not found")
        g = g.replace(imp, imp + "\nimport alliance_magazine_lossless_extraction_v670 as lossless_v670", 1)

    pat = re.compile(r'PROMPT="""You are a forensic vision extractor reading ONE complete real-estate magazine page\..*?"""', re.S)
    g, n = pat.subn('PROMPT="""You are the Alliance forensic vision extractor reading ONE complete real-estate magazine page.\n\n{training_rules}\n\nTARGET REFERENCES:\n{refs}\n\n{schema_prompt}\n\nAdditional target rules:\n1. Return only target property rows visibly present on this page.\n2. One target reference = one complete property row.\n3. Preserve all digits exactly: address/unit, area, floor, BHK/BR, @price and row-owned phones.\n4. Never replace a specific address with only the parent locality.\n5. Do not use page header/footer/broker office address/advertisements/adjacent property rows.\n6. If a target is not confidently readable, omit it rather than inventing text.\n7. If the row visibly contains an address identifier but address is blank, mark needs_review=true.\n"""', g, count=1)
    if n != 1:
        raise SystemExit(f"ERROR: v660 prompt replacement failed ({n})")

    if 'def _clean_records(data,provider_label):\n    out=[]\n    for rec in (data.get("records") or []):\n        ref=str(rec.get("ref") or "").strip()\n        raw=str(rec.get("raw_line") or "").strip()\n        if not ref or not raw: continue\n        out.append({"ref":ref,"raw_line":raw,"provider":provider_label})\n    return out' not in g:
        raise SystemExit("ERROR: v660 clean-record anchor not found")
    g = g.replace('def _clean_records(data,provider_label):\n    out=[]\n    for rec in (data.get("records") or []):\n        ref=str(rec.get("ref") or "").strip()\n        raw=str(rec.get("raw_line") or "").strip()\n        if not ref or not raw: continue\n        out.append({"ref":ref,"raw_line":raw,"provider":provider_label})\n    return out', 'def _clean_records(data,provider_label):\n    out=[]\n    for rec in (data.get("records") or []):\n        ref=str(rec.get("ref") or "").strip()\n        raw=str(rec.get("raw_line") or rec.get("original_description") or "").strip()\n        if not ref or not raw: continue\n        enriched=lossless_v670.enrich_record({\n            "ref":ref,\n            "raw_line":raw,\n            "original_description":str(rec.get("original_description") or raw).strip(),\n            "address":rec.get("address") or "",\n            "locality":rec.get("locality") or "",\n            "city":rec.get("city") or "",\n            "area_raw":rec.get("area_raw") or "",\n            "area_sqft":rec.get("area_sqft"),\n            "floor_codes":rec.get("floor_codes") or "",\n            "floors":rec.get("floors") or [],\n            "contact_name":rec.get("contact_name") or "",\n            "phones":rec.get("phones") or [],\n            "transaction_type":rec.get("transaction_type") or "",\n            "confidence":rec.get("confidence"),\n            "needs_review":rec.get("needs_review",False),\n            "review_reason":rec.get("review_reason") or "",\n        })\n        enriched["provider"]=provider_label\n        out.append(enriched)\n    return out', 1)

    old_fmt = 'prompt=PROMPT.format(refs=json.dumps(refs,ensure_ascii=False))'
    new_fmt = 'prompt=PROMPT.format(refs=json.dumps(refs,ensure_ascii=False),training_rules=lossless_v670.TRAINING_RULES,schema_prompt=lossless_v670.VISION_SCHEMA_PROMPT)'
    if old_fmt not in g:
        raise SystemExit("ERROR: v660 prompt format anchor not found")
    g = g.replace(old_fmt, new_fmt, 1)

    g += "\n\n" + MARKER + "\n"
    compile(g, str(GATEWAY), "exec")
    GATEWAY.write_text(g, encoding="utf-8")
else:
    gb = None

w = WORKSPACE.read_text(encoding="utf-8")
if TARGET not in w:
    wb = ROOT / f"alliance_primary_workspace_v730-before-v735-{stamp}.py"
    shutil.copy2(WORKSPACE, wb)

    w = w.replace('VERSION="7.3.4-ALLIANCE-UNIVERSAL-RECORD-STANDARD"', 'VERSION="'+TARGET+'"', 1)
    w = w.replace("Alliance CRE Operating System · 7.3.4", "Alliance CRE Operating System · 7.3.5", 1)

    old_row = """<td>{u['name']}</td><td>{u['contact']}</td><td style='min-width:300px'>{u['description']}</td>
              <td>{html.escape(str(p.get('locality') or ''))}</td>"""
    new_row = """<td>{u['name']}</td><td>{u['contact']}</td><td style='min-width:300px'>{u['description']}</td>
              <td>{html.escape(str(_v733_pick_any(p,["address","property_address","unit_address","building_address"]) or "Not captured"))}</td>
              <td>{html.escape(str(p.get('locality') or ''))}</td>"""
    if old_row in w:
        w = w.replace(old_row, new_row, 1)

    old_head = """<th>Actions</th><th>Date & Time</th><th>Source</th><th>Source Name</th><th>Name</th><th>Contact No.</th><th>Original Description / Message</th>
        <th>Locality</th>"""
    new_head = """<th>Actions</th><th>Date & Time</th><th>Source</th><th>Source Name</th><th>Name</th><th>Contact No.</th><th>Original Description / Message</th>
        <th>Address</th><th>Locality</th>"""
    if old_head in w:
        w = w.replace(old_head, new_head, 1)

    old_detail = """<p><b>ID:</b> {html.escape(cid)}<br><b>Transaction:</b>"""
    new_detail = """<p><b>ID:</b> {html.escape(cid)}<br><b>Address:</b> {html.escape(str(_v733_pick_any(p,["address","property_address","unit_address","building_address"]) or "Not captured"))}<br><b>Transaction:</b>"""
    if old_detail in w:
        w = w.replace(old_detail, new_detail, 1)

    w += "\n\n" + MARKER + "\n"
    compile(w, str(WORKSPACE), "exec")
    WORKSPACE.write_text(w, encoding="utf-8")
else:
    wb = None

print(TARGET)
print("TRAINING SELF-TEST: PASS")
print("A-7 INNER CIRCLE 7500FT FF+SF+TF (KAPIL 011 41550460)")
print("=> Address: A-7, Inner Circle")
print("=> Locality: Connaught Place")
print("=> Area: 7500 sq ft")
print("=> Floors: First Floor + Second Floor + Third Floor")
print("=> Contact: Kapil / 01141550460")
print("=> Original row preserved verbatim")
print("QUALITY GATE: address visible but blank => FAIL_REEXTRACT")
print("QUALITY GATE: original description missing => FAIL_REEXTRACT")
print("Old records without source evidence are not fabricated or silently rewritten.")
if gb: print("Gateway backup:", gb)
if wb: print("Workspace backup:", wb)
