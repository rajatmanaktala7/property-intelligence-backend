from __future__ import annotations

"""
Alliance Magazine Evidence Probe 11.9.14
READ ONLY. This script performs SELECT/introspection only and never writes to the database.

Goal:
1) Locate selected legacy Magazine Master rows.
2) Trace their descriptions into upstream magazine tables.
3) Identify upload_id + page_number when available.
4) Inspect the retained source PDF text around the property row.
5) Show the preceding heading candidates so we can prove the exact locality
   before any historical repair.

Run from:
C:\\Users\\jasleen\\Desktop\\property-intelligence-backend

Command:
python alliance_magazine_evidence_probe_v11914.py
"""

import os
import re
import json
import sys
from collections import defaultdict
from typing import Any

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from sqlalchemy import create_engine, text

try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None


VERSION = "11.9.14-READ-ONLY-MAGAZINE-EVIDENCE-PROBE"

DEFAULT_TARGET_IDS = [
    "SHV-L05633",
    "SHV-L05632",
    "SHV-L05631",
    "SHV-L05630",
    "SHV-L05629",
    "SHV-L05628",
]

LIKELY_TABLES = [
    "pi_magazine_master",
    "pi_magazine_complete_v860",
    "pi_magazine_organized_v850",
    "pi_magazine_fastlane_records",
    "pi_magazine_fresh_records",
    "pi_magazine_fresh_properties",
]

ID_CANDIDATES = [
    "property_id", "record_id", "source_record_id", "id",
    "magazine_id", "row_id"
]

DESC_CANDIDATES = [
    "original_raw_text", "original_description", "raw_line", "raw_text",
    "description", "property_description", "details", "text", "clean_description"
]

UPLOAD_CANDIDATES = ["upload_id", "source_upload_id"]
PAGE_CANDIDATES = ["page_number", "page_no", "page"]
SECTION_CANDIDATES = [
    "section_heading", "original_section", "category_heading",
    "locality_heading", "location_heading", "heading"
]

UPPER_HEADING_RE = re.compile(r"^[A-Z][A-Z0-9 .&'()/\-]{2,80}$")
PROPERTYISH_RE = re.compile(
    r"(?i)(?:\b\d{2,7}\s*(?:FT|SQFT|Y|YD|SQYD|SQM|MTR|ACRE)\b|"
    r"\b(?:GF|FF|SF|TF|BMT|BASEMENT)\b|\b\d+\s*(?:BHK|BR)\b)"
)


def safe_ident(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name or ""):
        raise ValueError(f"Unsafe SQL identifier: {name!r}")
    return '"' + name + '"'


def norm(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()


def database_url() -> str:
    for key in ("DATABASE_URL", "DATABASE_PUBLIC_URL", "POSTGRES_URL"):
        v = os.getenv(key)
        if v:
            return v
    raise RuntimeError(
        "DATABASE_URL not found. Run this inside the project/Railway environment "
        "where the database environment variable is available."
    )


def table_exists(conn, table: str) -> bool:
    return bool(conn.execute(
        text("SELECT to_regclass(:t) IS NOT NULL"), {"t": table}
    ).scalar())


def columns(conn, table: str) -> list[str]:
    return [r[0] for r in conn.execute(text("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema=current_schema() AND table_name=:t
        ORDER BY ordinal_position
    """), {"t": table}).all()]


def pick(cols: list[str], candidates: list[str]) -> str | None:
    m = {c.lower(): c for c in cols}
    for x in candidates:
        if x.lower() in m:
            return m[x.lower()]
    return None


def json_row_search(conn, table: str, needle: str, limit: int = 20) -> list[dict]:
    q = f"""
        SELECT to_jsonb(t) AS d
        FROM {safe_ident(table)} t
        WHERE to_jsonb(t)::text ILIKE :needle
        LIMIT :lim
    """
    return [
        dict(r[0]) if isinstance(r[0], dict) else r[0]
        for r in conn.execute(text(q), {"needle": f"%{needle}%", "lim": limit}).all()
    ]


def row_description(d: dict) -> str:
    for k in DESC_CANDIDATES:
        if norm(d.get(k)):
            return norm(d.get(k))
    return ""


def short_fingerprint(s: str) -> str:
    s = norm(s)
    s = re.sub(r"\b[6-9]\d{9}\b", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    # First meaningful chunk is enough for upstream lookup.
    return s[:42]


def print_row(title: str, d: dict):
    print("\n" + "=" * 100)
    print(title)
    print("=" * 100)
    preferred = (
        ID_CANDIDATES + UPLOAD_CANDIDATES + PAGE_CANDIDATES +
        SECTION_CANDIDATES + ["location", "locality", "property_category",
        "transaction_type", "property_type"] + DESC_CANDIDATES +
        ["contact_name", "contact_numbers", "phone", "raw_json", "source",
         "source_name", "source_file", "file_name", "batch_id"]
    )
    shown = set()
    for k in preferred:
        if k in d and k not in shown and d.get(k) not in (None, "", [], {}):
            val = d.get(k)
            if isinstance(val, (dict, list)):
                val = json.dumps(val, ensure_ascii=False)
            print(f"{k:28} : {val}")
            shown.add(k)


def heading_score(line: str) -> int:
    s = norm(line)
    if not s or len(s) > 90:
        return -10
    score = 0
    if UPPER_HEADING_RE.fullmatch(s):
        score += 4
    if not PROPERTYISH_RE.search(s):
        score += 3
    if not re.search(r"\b[6-9]\d{9}\b", s):
        score += 2
    if len(s.split()) <= 7:
        score += 2
    if re.search(r"(?i)\b(RESIDENTIAL|COMMERCIAL|INDUSTRIAL|SALE|RENT|LEASE)\b", s):
        score += 1
    return score


def inspect_pdf_context(pdf_bytes: bytes, page_no: int, fingerprint: str, filename: str = ""):
    print("\n" + "#" * 100)
    print(f"SOURCE PDF CONTEXT | {filename or 'stored PDF'} | page {page_no}")
    print("#" * 100)

    if fitz is None:
        print("PyMuPDF/fitz is unavailable. PDF context inspection skipped.")
        return

    try:
        doc = fitz.open(stream=bytes(pdf_bytes), filetype="pdf")
    except Exception as exc:
        print(f"Could not open stored PDF: {type(exc).__name__}: {exc}")
        return

    try:
        if page_no < 1 or page_no > len(doc):
            print(f"Page {page_no} outside PDF range 1..{len(doc)}")
            return

        page = doc.load_page(page_no - 1)
        raw_lines = [norm(x) for x in page.get_text("text", sort=True).splitlines()]
        raw_lines = [x for x in raw_lines if x]

        fp = short_fingerprint(fingerprint)
        fp_tokens = [x for x in re.findall(r"[A-Z0-9]+", fp.upper()) if len(x) >= 2][:5]

        best_i = None
        best_hits = -1
        for i, line in enumerate(raw_lines):
            u = line.upper()
            hits = sum(1 for t in fp_tokens if t in u)
            if hits > best_hits:
                best_hits = hits
                best_i = i

        if best_i is None or best_hits <= 0:
            print(f"Could not find row fingerprint in page text: {fp!r}")
            print("Top page lines:")
            for i, line in enumerate(raw_lines[:60]):
                print(f"{i:03d} | {line}")
            return

        start = max(0, best_i - 16)
        end = min(len(raw_lines), best_i + 8)

        print(f"Matched row near line {best_i}; token hits={best_hits}")
        print("\nPage text neighborhood:")
        for i in range(start, end):
            marker = ">>>" if i == best_i else "   "
            print(f"{marker} {i:03d} | {raw_lines[i]}")

        preceding = []
        for i in range(max(0, best_i - 30), best_i):
            line = raw_lines[i]
            sc = heading_score(line)
            if sc >= 7:
                preceding.append((sc, i, line))

        print("\nStrong preceding heading candidates:")
        if not preceding:
            print("  NONE FOUND")
        else:
            for sc, i, line in sorted(preceding, key=lambda x: (x[1], x[0]))[-12:]:
                print(f"  score={sc:02d} line={i:03d} | {line}")
    finally:
        doc.close()


def fetch_pdf(conn, upload_id: str):
    if not table_exists(conn, "pi_magazine_fresh_uploads"):
        return None
    cols = columns(conn, "pi_magazine_fresh_uploads")
    if "pdf_content" not in cols or "upload_id" not in cols:
        return None
    filename_col = pick(cols, ["filename", "file_name", "source_file"])
    select_filename = safe_ident(filename_col) if filename_col else "NULL"
    q = f"""
        SELECT pdf_content, {select_filename} AS filename
        FROM pi_magazine_fresh_uploads
        WHERE CAST(upload_id AS TEXT)=:u
        LIMIT 1
    """
    return conn.execute(text(q), {"u": str(upload_id)}).first()


def main():
    print(f"\nAlliance Magazine Evidence Probe")
    print(f"Version: {VERSION}")
    print("MODE: READ ONLY. No INSERT / UPDATE / DELETE / DDL is executed.\n")

    targets = sys.argv[1:] or DEFAULT_TARGET_IDS

    engine = create_engine(database_url(), future=True)

    evidence_by_upload_page: dict[tuple[str, int], list[tuple[str, str]]] = defaultdict(list)

    with engine.connect() as conn:
        # Prove connection and current DB.
        info = conn.execute(text(
            "SELECT current_database(), current_schema(), now()"
        )).first()
        print(f"Database: {info[0]} | Schema: {info[1]} | Time: {info[2]}")

        existing = [t for t in LIKELY_TABLES if table_exists(conn, t)]
        print("\nRelevant tables found:")
        for t in existing:
            print(f"  - {t}")

        print("\nSTEP 1: Locate selected Master rows")
        master_rows: list[dict] = []
        if table_exists(conn, "pi_magazine_master"):
            for target in targets:
                hits = json_row_search(conn, "pi_magazine_master", target, 5)
                if not hits:
                    print(f"\n{target}: NOT FOUND in pi_magazine_master")
                    continue
                for d in hits:
                    print_row(f"MASTER MATCH FOR {target}", d)
                    master_rows.append(d)

        if not master_rows:
            print("\nNo selected master rows found. Stopping without changes.")
            return

        print("\n\nSTEP 2: Trace each master description upstream")
        upstream_tables = [
            t for t in [
                "pi_magazine_complete_v860",
                "pi_magazine_organized_v850",
                "pi_magazine_fastlane_records",
                "pi_magazine_fresh_records",
                "pi_magazine_fresh_properties",
            ] if table_exists(conn, t)
        ]

        seen_upstream = set()
        for md in master_rows:
            desc = row_description(md)
            fp = short_fingerprint(desc)
            target_id = next((norm(md.get(k)) for k in ID_CANDIDATES if norm(md.get(k))), "UNKNOWN")

            print("\n" + "-" * 100)
            print(f"Tracing master row: {target_id}")
            print(f"Fingerprint: {fp}")

            needles = []
            if fp:
                needles.append(fp)
            # Add address-like first token for hard cases.
            m = re.match(r"^\s*([A-Z0-9/-]{2,15})\b", desc.upper())
            if m:
                needles.append(m.group(1))

            found_any = False
            for table in upstream_tables:
                table_hits = []
                for needle in needles:
                    if not needle:
                        continue
                    hits = json_row_search(conn, table, needle, 20)
                    for h in hits:
                        key = (table, json.dumps(h, sort_keys=True, default=str))
                        if key not in seen_upstream:
                            seen_upstream.add(key)
                            table_hits.append(h)
                for d in table_hits:
                    found_any = True
                    print_row(f"UPSTREAM MATCH | {table}", d)

                    upload = next((norm(d.get(k)) for k in UPLOAD_CANDIDATES if norm(d.get(k))), "")
                    page_raw = next((d.get(k) for k in PAGE_CANDIDATES if d.get(k) not in (None, "")), None)
                    try:
                        page = int(page_raw) if page_raw is not None else None
                    except Exception:
                        page = None
                    udesc = row_description(d) or desc

                    if upload and page:
                        evidence_by_upload_page[(upload, page)].append((target_id, udesc))

            if not found_any:
                print("No upstream row match found from this fingerprint.")

        print("\n\nSTEP 3: Inspect retained source PDF page text")
        if not evidence_by_upload_page:
            print("No upstream upload_id/page_number pair was recovered.")
            print("This means the next probe must inspect other historical source tables.")
        else:
            done_pdf = set()
            for (upload, page), items in evidence_by_upload_page.items():
                pdf = fetch_pdf(conn, upload)
                if not pdf or pdf[0] is None:
                    print(f"\nStored PDF not found for upload_id={upload}, page={page}")
                    continue
                filename = pdf[1] if len(pdf) > 1 else ""
                for target_id, desc in items:
                    key = (upload, page, target_id, desc)
                    if key in done_pdf:
                        continue
                    done_pdf.add(key)
                    inspect_pdf_context(pdf[0], page, desc, filename or str(upload))

        print("\n\nSTEP 4: Read-only verdict")
        print("If a locality heading appears immediately before the property rows in the PDF context,")
        print("we have deterministic evidence for historical repair.")
        print("If no locality heading is visible, we will leave those rows MISSING rather than guess.")

    engine.dispose()


if __name__ == "__main__":
    main()

