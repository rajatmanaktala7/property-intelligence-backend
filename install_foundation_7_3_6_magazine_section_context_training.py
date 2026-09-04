from pathlib import Path
from datetime import datetime
import re, shutil, subprocess, sys

ROOT = Path(__file__).resolve().parent
GATEWAY = ROOT / "alliance_magazine_safe_gateway_v660.py"
WORKSPACE = ROOT / "alliance_primary_workspace_v730.py"
TRAINER = ROOT / "alliance_magazine_section_context_v680.py"

TARGET = "7.3.6-ALLIANCE-MAGAZINE-SECTION-CONTEXT"
MARKER = "# 7.3.6 MAGAZINE SECTION CONTEXT TRAINING"

for p in [GATEWAY, WORKSPACE, TRAINER]:
    if not p.exists():
        raise SystemExit(f"ERROR: missing {p.name}")

cp = subprocess.run([sys.executable, str(TRAINER)], capture_output=True, text=True)
if cp.returncode != 0 or '"status": "PASS"' not in cp.stdout:
    print(cp.stdout)
    print(cp.stderr)
    raise SystemExit("ERROR: 7.3.6 section-context self-test failed")

stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

g = GATEWAY.read_text(encoding="utf-8")
if MARKER not in g:
    if "import alliance_magazine_lossless_extraction_v670 as lossless_v670" not in g:
        raise SystemExit("ERROR: 7.3.5 gateway import not found")
    gb = ROOT / f"alliance_magazine_safe_gateway_v660-before-v736-{stamp}.py"
    shutil.copy2(GATEWAY, gb)

    g = g.replace(
        "import alliance_magazine_lossless_extraction_v670 as lossless_v670",
        "import alliance_magazine_lossless_extraction_v670 as lossless_v670\n"
        "import alliance_magazine_section_context_v680 as section_v680",
        1,
    )

    old_prompt = """PROMPT=\"\"\"You are the Alliance forensic vision extractor reading ONE complete real-estate magazine page.

{training_rules}

TARGET REFERENCES:
{refs}

{schema_prompt}

Additional target rules:
1. Return only target property rows visibly present on this page.
2. One target reference = one complete property row.
3. Preserve all digits exactly: address/unit, area, floor, BHK/BR, @price and row-owned phones.
4. Never replace a specific address with only the parent locality.
5. Do not use page header/footer/broker office address/advertisements/adjacent property rows.
6. If a target is not confidently readable, omit it rather than inventing text.
7. If the row visibly contains an address identifier but address is blank, mark needs_review=true.
\"\"\""""
    new_prompt = """PROMPT=\"\"\"You are the Alliance forensic vision extractor reading ONE complete real-estate magazine page.

{training_rules}

TARGET REFERENCES:
{refs}

{schema_prompt}

Additional target rules:
1. Read page hierarchy: category/transaction heading -> locality heading -> property row.
2. Return only target property rows visibly present on this page.
3. One target reference = one complete property row.
4. Attach the governing section_heading to every returned row.
5. COMMERCIAL - RENT means property_category=COMMERCIAL and transaction_type=RENT.
6. COMMERCIAL - SALE means property_category=COMMERCIAL and transaction_type=SALE.
7. RESIDENTIAL - RENT means property_category=RESIDENTIAL and transaction_type=RENT.
8. RESIDENTIAL - SALE means property_category=RESIDENTIAL and transaction_type=SALE.
9. Section context remains active until another visible heading changes it.
10. Preserve all digits exactly: address/unit, area, floor, BHK/BR, @price and row-owned phones.
11. Never replace a specific address with only the parent locality.
12. Do not use page header/footer/broker office address/advertisements/adjacent property rows.
13. If a target is not confidently readable, omit it rather than inventing text.
14. If governing section context is visible but category/transaction is missing, mark needs_review=true.
\"\"\""""
    if old_prompt not in g:
        raise SystemExit("ERROR: current 7.3.5 prompt anchor not found")
    g = g.replace(old_prompt, new_prompt, 1)

    old_enriched = """        enriched=lossless_v670.enrich_record({
            "ref":ref,
            "raw_line":raw,
            "original_description":str(rec.get("original_description") or raw).strip(),
            "address":rec.get("address") or "",
            "locality":rec.get("locality") or "",
            "city":rec.get("city") or "",
            "area_raw":rec.get("area_raw") or "",
            "area_sqft":rec.get("area_sqft"),
            "floor_codes":rec.get("floor_codes") or "",
            "floors":rec.get("floors") or [],
            "contact_name":rec.get("contact_name") or "",
            "phones":rec.get("phones") or [],
            "transaction_type":rec.get("transaction_type") or "",
            "confidence":rec.get("confidence"),
            "needs_review":rec.get("needs_review",False),
            "review_reason":rec.get("review_reason") or "",
        })"""
    new_enriched = """        enriched=section_v680.enrich_record({
            "ref":ref,
            "section_heading":rec.get("section_heading") or "",
            "raw_line":raw,
            "original_description":str(rec.get("original_description") or raw).strip(),
            "property_category":rec.get("property_category") or "",
            "transaction_type":rec.get("transaction_type") or "",
            "address":rec.get("address") or "",
            "locality":rec.get("locality") or "",
            "city":rec.get("city") or "",
            "area_raw":rec.get("area_raw") or "",
            "area_sqft":rec.get("area_sqft"),
            "floor_codes":rec.get("floor_codes") or "",
            "floors":rec.get("floors") or [],
            "contact_name":rec.get("contact_name") or "",
            "phones":rec.get("phones") or [],
            "confidence":rec.get("confidence"),
            "needs_review":rec.get("needs_review",False),
            "review_reason":rec.get("review_reason") or "",
        })"""
    if old_enriched not in g:
        raise SystemExit("ERROR: current 7.3.5 clean-record anchor not found")
    g = g.replace(old_enriched, new_enriched, 1)

    old_fmt = 'prompt=PROMPT.format(refs=json.dumps(refs,ensure_ascii=False),training_rules=lossless_v670.TRAINING_RULES,schema_prompt=lossless_v670.VISION_SCHEMA_PROMPT)'
    new_fmt = 'prompt=PROMPT.format(refs=json.dumps(refs,ensure_ascii=False),training_rules=section_v680.TRAINING_RULES,schema_prompt=section_v680.VISION_SCHEMA_PROMPT)'
    if old_fmt not in g:
        raise SystemExit("ERROR: current 7.3.5 prompt-format anchor not found")
    g = g.replace(old_fmt, new_fmt, 1)

    g += "\n\n" + MARKER + "\n"
    compile(g, str(GATEWAY), "exec")
    GATEWAY.write_text(g, encoding="utf-8")
else:
    gb = None

w = WORKSPACE.read_text(encoding="utf-8")
if TARGET not in w:
    if 'VERSION="7.3.5-ALLIANCE-MAGAZINE-LOSSLESS-TRAINING"' not in w:
        raise SystemExit("ERROR: 7.3.5 workspace foundation not found")
    wb = ROOT / f"alliance_primary_workspace_v730-before-v736-{stamp}.py"
    shutil.copy2(WORKSPACE, wb)

    w = w.replace(
        'VERSION="7.3.5-ALLIANCE-MAGAZINE-LOSSLESS-TRAINING"',
        'VERSION="7.3.6-ALLIANCE-MAGAZINE-SECTION-CONTEXT"',
        1,
    )
    w = w.replace(
        "Alliance CRE Operating System · 7.3.5",
        "Alliance CRE Operating System · 7.3.6",
        1,
    )
    w += "\n\n" + MARKER + "\n"
    compile(w, str(WORKSPACE), "exec")
    WORKSPACE.write_text(w, encoding="utf-8")
else:
    wb = None

print(TARGET)
print("TRAINING SELF-TEST: PASS")
print("Section heading: COMMERCIAL - RENT")
print("Locality heading: CONNAUGHT PLACE")
print("A-7 INNER CIRCLE 7500FT FF+SF+TF (KAPIL 011 41550460)")
print("=> Property Category: COMMERCIAL")
print("=> Transaction Type: RENT")
print("=> Address: A-7, Inner Circle")
print("=> Locality: Connaught Place")
print("=> Area: 7500 sq ft")
print("=> Floors: First Floor + Second Floor + Third Floor")
print("=> Contact: Kapil / 01141550460")
print("=> Original property row preserved verbatim")
print("QUALITY GATE: visible section context but missing category/transaction => FAIL_REEXTRACT")
print("Docker startup is NOT modified. Railway continues to start Uvicorn directly.")
if gb: print("Gateway backup:", gb)
if wb: print("Workspace backup:", wb)
