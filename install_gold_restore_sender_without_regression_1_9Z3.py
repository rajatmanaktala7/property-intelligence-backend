from pathlib import Path
from datetime import datetime
import py_compile
import shutil
import sys

TARGET = Path("alliance_property_brain_foundation_v1.py")
if not TARGET.exists():
    raise SystemExit("ERROR: Run from property-intelligence-backend repository root.")

src = TARGET.read_text(encoding="utf-8")
EXPECTED_VERSION = 'VERSION = "1.9.30-ALLIANCE-PROPERTY-BRAIN-FOUNDATION"'
EXPECTED_MODE = 'MODE = "GOLD_REFRESH_STAYS_ON_CURRENT_SPAN_1_9Z2"'
if EXPECTED_VERSION not in src or EXPECTED_MODE not in src:
    raise SystemExit("ERROR: Expected Foundation 1.9.30 / 1.9Z2 baseline. Nothing changed.")

required = {
    "1.9U WhatsApp sender/JID recovery": "FOUNDATION_1_9U_WHATSAPP_SENDER_JID_RECOVERY",
    "1.9V project+BHK split": "FOUNDATION_1_9V_PROJECT_BHK_INVENTORY_SPLIT",
    "1.9W shared tail contacts": "FOUNDATION_1_9W_SHARED_TAIL_CONTACT_RECOVERY",
    "1.9X mixed pin+asset split": "FOUNDATION_1_9X_MIXED_PIN_ASSET_HEADING_SPLIT",
    "1.9Y same-source flow": "loadNextInSourceOrGlobal",
    "1.9Z builder-floor split": "FOUNDATION_1_9Z_BUILDER_FLOOR_OPTION_SPLIT",
    "1.9Z2 refresh same span": "FOUNDATION_1_9Z2_REFRESH_STAYS_ON_CURRENT_SPAN",
}
missing = [name for name, token in required.items() if token not in src]
if missing:
    raise SystemExit("ERROR: Refusing to patch; earlier fix missing: " + ", ".join(missing))

MARKER = "# FOUNDATION_1_9Z3_RESTORE_LIVE_FEED_WHATSAPP_SENDER"
if MARKER in src:
    print("FOUNDATION_1_9Z3_ALREADY_INSTALLED")
    sys.exit(0)

anchor = "\ndef span_contact_lineage_diagnostic(engine, span_id: str) -> Dict[str, Any]:\n"
if anchor not in src:
    raise SystemExit("ERROR: Contact diagnostic anchor not found. Nothing changed.")

patch = r'''
# FOUNDATION_1_9Z3_RESTORE_LIVE_FEED_WHATSAPP_SENDER
# Additive regression repair only. Existing 1.9U remains untouched.
# Exact evidence text -> exact upstream WhatsApp row -> sender/JID.
# Read-only source lookup; unique phone required; never guess.

_v19z3_live_sender_before = _v19g_live_upstream_sender_contact


def _v19z3_table_exists(conn, table_name: str) -> bool:
    try:
        return bool(conn.execute(
            text("SELECT to_regclass(:table_name)"),
            {"table_name": str(table_name)},
        ).scalar())
    except Exception:
        return False


def _v19z3_columns(conn, table_name: str) -> List[str]:
    try:
        rows = conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema=current_schema() AND table_name=:table_name "
                "ORDER BY ordinal_position"
            ),
            {"table_name": str(table_name)},
        ).mappings().all()
        return [str(r.get("column_name") or "") for r in rows if r.get("column_name")]
    except Exception:
        return []


def _v19z3_exact_rows_by_text(conn, table_name: str, raw_text: str, limit: int = 3) -> List[Dict[str, Any]]:
    if not raw_text or not _v19z3_table_exists(conn, table_name):
        return []
    columns = _v19z3_columns(conn, table_name)
    lower_map = {c.lower(): c for c in columns}
    preferred = (
        "raw_text", "message_text", "message", "text", "body",
        "content", "description", "property_text", "source_text",
    )
    text_columns = [lower_map[x] for x in preferred if x in lower_map]

    for column_name in text_columns:
        qt = _safe_identifier(table_name)
        qc = _safe_identifier(column_name)
        try:
            rows = conn.execute(
                text(
                    f"SELECT to_jsonb(t) AS row_data FROM {qt} t "
                    f"WHERE {qc}::text=:raw_text LIMIT {int(limit)}"
                ),
                {"raw_text": raw_text},
            ).mappings().all()
        except Exception:
            continue
        if rows:
            out = []
            for row in rows:
                data = row.get("row_data")
                if not isinstance(data, dict):
                    data = _loads(data, {})
                if isinstance(data, dict):
                    data = dict(data)
                    data["_matched_text_column"] = column_name
                    out.append(data)
            return out
    return []


def _v19z3_sender_from_exact_rows(rows: List[Dict[str, Any]], source_table: str) -> Dict[str, Any]:
    candidates = []
    for row in rows or []:
        phone, match_column = _v19u_sender_phone_from_row(row)
        if not phone:
            continue
        name = None
        for key in (
            "sender_display_name", "sender_name", "participant_name",
            "author_name", "push_name", "contact_name", "display_name",
        ):
            if row.get(key):
                name = str(row.get(key)).strip() or None
                if name:
                    break
        candidates.append({
            "phone": phone,
            "name": name,
            "company": None,
            "source_table": source_table,
            "match_method": "EXACT_RAW_TEXT_TO_SENDER_IDENTITY_1_9Z3",
            "match_column": match_column,
            "provenance": "WHATSAPP_SENDER",
        })

    by_phone = {str(c["phone"]): c for c in candidates if c.get("phone")}
    if len(by_phone) == 1:
        return {
            "status": "FOUND_UNIQUE_UPSTREAM_SENDER",
            "sender": next(iter(by_phone.values())),
            "candidate_count": len(candidates),
        }
    if len(by_phone) > 1:
        return {"status": "AMBIGUOUS_UPSTREAM_SENDERS", "sender": None, "candidate_count": len(candidates)}
    return {"status": "NO_SENDER_IN_EXACT_ROWS", "sender": None, "candidate_count": 0}


def _v19z3_restore_live_feed_sender(engine, source: Dict[str, Any]) -> Dict[str, Any]:
    source_table = str(source.get("source_table") or "").strip()
    raw_text = str(source.get("source_raw_text") or source.get("raw_text") or "")
    result = {
        "status": "NOT_APPLICABLE",
        "resolution_stage": "LIVE_FEED_WHATSAPP_RESTORE_1_9Z3",
        "candidate_count": 0,
        "sender": None,
        "read_only": True,
    }
    if source_table != "alliance_live_feed_entities" or not raw_text:
        return result

    # Path A: exact live-feed evidence also exists in ai_whatsapp_purity.
    try:
        with engine.connect() as conn:
            purity_rows = _v19z3_exact_rows_by_text(conn, "ai_whatsapp_purity", raw_text, 3)
        if len(purity_rows) == 1:
            listing_id = purity_rows[0].get("listing_id") or purity_rows[0].get("id")
            if listing_id:
                restored = _v19i_exact_ai_whatsapp_purity_sender(engine, {
                    "source_table": "ai_whatsapp_purity",
                    "source_row_ref": str(listing_id),
                    "source_raw_text": raw_text,
                    "raw_text": raw_text,
                    "source_metadata": {},
                })
                if restored.get("status") == "FOUND_UNIQUE_UPSTREAM_SENDER":
                    restored = dict(restored)
                    restored["resolution_stage"] = "LIVE_FEED_TO_AI_WHATSAPP_PURITY_TO_1_9U"
                    return restored
    except Exception:
        pass

    # Path B: exact evidence exists directly in the WhatsApp raw-message table.
    wa_engine = None
    dispose_wa = False
    try:
        wa_engine, dispose_wa = _v19i_whatsapp_engine(engine)
        with wa_engine.connect() as conn:
            rows = _v19z3_exact_rows_by_text(conn, "wai_raw_messages", raw_text, 3)
            if rows:
                chosen = _v19z3_sender_from_exact_rows(rows, "wai_raw_messages")
                if chosen.get("status") == "FOUND_UNIQUE_UPSTREAM_SENDER":
                    chosen["resolution_stage"] = "LIVE_FEED_EXACT_TEXT_TO_WAI_RAW_MESSAGE_1_9Z3"
                    chosen["read_only"] = True
                    return chosen
                if chosen.get("status") == "AMBIGUOUS_UPSTREAM_SENDERS":
                    chosen["resolution_stage"] = "LIVE_FEED_EXACT_TEXT_AMBIGUOUS_1_9Z3"
                    chosen["read_only"] = True
                    return chosen
        result["status"] = "EXACT_UPSTREAM_WHATSAPP_ROW_NOT_FOUND"
        return result
    except Exception as exc:
        result["status"] = "LIVE_FEED_SENDER_RESTORE_ERROR"
        result["error_type"] = type(exc).__name__
        result["error"] = str(exc)[:500]
        return result
    finally:
        if dispose_wa and wa_engine is not None:
            wa_engine.dispose()


def _v19g_live_upstream_sender_contact(engine, proposal: Dict[str, Any], source: Dict[str, Any]) -> Dict[str, Any]:
    # Preserve every existing contact behavior first, including 1.9U.
    p = _v19z3_live_sender_before(engine, proposal, source)
    if p.get("contacts"):
        return p

    # Add only the missing live-feed -> original WhatsApp fallback.
    resolution = _v19z3_restore_live_feed_sender(engine, source)
    p["sender_lineage_status"] = resolution.get("status")
    p["sender_lineage_resolution_stage"] = resolution.get("resolution_stage")
    p["sender_lineage_candidate_count"] = resolution.get("candidate_count", 0)
    if resolution.get("status") != "FOUND_UNIQUE_UPSTREAM_SENDER":
        return p

    sender = resolution.get("sender") or {}
    phone = _v19u_phone_from_sender_value(sender.get("phone"))
    if not phone:
        return p

    p["contacts"] = [{
        "phone": phone,
        "name": _v19c_clean_sender_name(sender.get("name")),
        "company": sender.get("company"),
        "role": "SOURCE_CONTACT",
        "provenance": "WHATSAPP_SENDER",
        "scope": "SOURCE_MESSAGE_SENDER",
        "owner_status": "NOT_PROVEN",
        "broker_status": "NOT_PROVEN",
        "source_table": sender.get("source_table"),
        "resolved_via": sender.get("match_method"),
        "match_column": sender.get("match_column"),
        "lineage_resolution_stage": resolution.get("resolution_stage"),
    }]
    p["sender_contact_fallback_used"] = True
    p["sender_contact_is_owner"] = False
    p["sender_contact_is_broker"] = False
    p["sender_contact_live_recovery"] = True
    p["sender_contact_restored_from_live_feed"] = True
    return p
'''

src = src.replace(anchor, "\n" + patch + anchor, 1)
src = src.replace(EXPECTED_VERSION, 'VERSION = "1.9.31-ALLIANCE-PROPERTY-BRAIN-FOUNDATION"', 1)
src = src.replace(EXPECTED_MODE, 'MODE = "RESTORE_ALL_FIXES_LIVE_FEED_SENDER_1_9Z3"', 1)

backup = TARGET.with_name(TARGET.name + ".before-1_9Z3-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".bak")
shutil.copy2(TARGET, backup)

try:
    TARGET.write_text(src, encoding="utf-8")
    py_compile.compile(str(TARGET), doraise=True)
    final = TARGET.read_text(encoding="utf-8")

    for name, token in required.items():
        if token not in final:
            raise RuntimeError("REGRESSION: earlier fix lost: " + name)

    for token in (
        MARKER,
        "_v19z3_live_sender_before = _v19g_live_upstream_sender_contact",
        "LIVE_FEED_TO_AI_WHATSAPP_PURITY_TO_1_9U",
        "LIVE_FEED_EXACT_TEXT_TO_WAI_RAW_MESSAGE_1_9Z3",
        'VERSION = "1.9.31-ALLIANCE-PROPERTY-BRAIN-FOUNDATION"',
        'MODE = "RESTORE_ALL_FIXES_LIVE_FEED_SENDER_1_9Z3"',
    ):
        if token not in final:
            raise RuntimeError("1.9Z3 validation failed: " + token)

    inside_script = False
    for line_no, line in enumerate(final.splitlines(), 1):
        low = line.lower()
        if "<script" in low:
            inside_script = True
        if inside_script and line.lstrip().startswith("#"):
            raise RuntimeError(f"REGRESSION: Python-style # comment inside JavaScript at line {line_no}")
        if "</script>" in low:
            inside_script = False

except Exception:
    shutil.copy2(backup, TARGET)
    raise

print("FOUNDATION_1_9Z3_INSTALL_PASS")
print("Earlier Gold fixes: PRESERVED")
print("1.9U WhatsApp sender/JID recovery: PRESERVED UNCHANGED")
print("1.9Z builder-floor split: PRESERVED")
print("1.9Z2 refresh-same-span: PRESERVED")
print("New behavior: alliance_live_feed_entities can recover exact upstream WhatsApp sender")
print("Contact provenance: WHATSAPP_SENDER")
print("Matching: exact evidence only; unique phone required; never guess")
print("Production inventory writes: 0")
print("Version: 1.9.31-ALLIANCE-PROPERTY-BRAIN-FOUNDATION")
print("Mode: RESTORE_ALL_FIXES_LIVE_FEED_SENDER_1_9Z3")
print("Backup:", backup)
