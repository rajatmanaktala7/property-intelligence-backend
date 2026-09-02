from __future__ import annotations

import py_compile
import shutil
from datetime import datetime
from pathlib import Path

TARGET = Path("alliance_property_brain_foundation_v1.py")
MARKER = "# FOUNDATION_1_9U_WHATSAPP_SENDER_JID_RECOVERY"

if not TARGET.exists():
    raise SystemExit(f"ERROR: {TARGET} not found. Run this installer from the repository root.")

src = TARGET.read_text(encoding="utf-8")

if MARKER in src:
    print("FOUNDATION_1_9U_ALREADY_INSTALLED")
    raise SystemExit(0)

required = [
    'VERSION = "1.9.22-ALLIANCE-PROPERTY-BRAIN-FOUNDATION"',
    'MODE = "GOLD_REPAIR_STAY_ON_SOURCE_1_9T3"',
    "def _v19i_exact_ai_whatsapp_purity_sender(engine, source: Dict[str, Any]) -> Dict[str, Any]:",
    "def _v19g_live_upstream_sender_contact(",
]
missing = [x for x in required if x not in src]
if missing:
    raise SystemExit("ERROR: Current foundation file is not the expected 1.9T3 baseline. Missing: " + repr(missing))

backup = TARGET.with_name(
    TARGET.stem + ".before-1.9U-" + datetime.now().strftime("%Y%m%d-%H%M%S") + TARGET.suffix
)
shutil.copy2(TARGET, backup)

src = src.replace(
    'VERSION = "1.9.22-ALLIANCE-PROPERTY-BRAIN-FOUNDATION"',
    'VERSION = "1.9.23-ALLIANCE-PROPERTY-BRAIN-FOUNDATION"',
    1,
)
src = src.replace(
    'MODE = "GOLD_REPAIR_STAY_ON_SOURCE_1_9T3"',
    'MODE = "WHATSAPP_SENDER_JID_CONTACT_RECOVERY_1_9U"',
    1,
)

anchor = "\ndef _v19g_live_upstream_sender_contact(\n"
if anchor not in src:
    raise SystemExit("ERROR: Could not find 1.9G live sender recovery anchor.")

patch = r'''
# FOUNDATION_1_9U_WHATSAPP_SENDER_JID_RECOVERY
#
# Gold Lab contact recovery rule:
#   explicit number in evidence span/source -> keep existing extraction
#   otherwise exact WhatsApp lineage -> sender phone/JID -> WHATSAPP_SENDER
#
# This is read-only against WhatsApp source tables and only enriches the Gold
# Lab proposal returned to the browser. It does not write production inventory.

def _v19u_phone_from_sender_value(value: Any) -> Optional[str]:
    if value is None:
        return None

    try:
        phone = _v19_phone_from_sender_value(value)
        if phone:
            return phone
    except Exception:
        pass

    raw = str(value).strip()
    if not raw:
        return None

    before_at = raw.split("@", 1)[0]
    digits = re.sub(r"\D+", "", before_at)

    if len(digits) == 12 and digits.startswith("91") and digits[2] in "6789":
        return digits[2:]

    if len(digits) == 10 and digits[0] in "6789":
        return digits

    stripped = digits.lstrip("0")
    if len(stripped) == 10 and stripped[0] in "6789":
        return stripped

    return None


def _v19u_sender_phone_from_row(row_data: Any) -> Tuple[Optional[str], Optional[str]]:
    if not isinstance(row_data, dict):
        return None, None

    preferred = [
        "sender_phone",
        "sender_jid",
        "sender_id",
        "sender",
        "participant_phone",
        "participant_jid",
        "participant_id",
        "participant",
        "author_phone",
        "author_jid",
        "author_id",
        "author",
        "from_phone",
        "from_jid",
        "from_id",
        "from",
        "wa_sender",
        "whatsapp_sender",
    ]

    lower_map = {str(k).lower(): k for k in row_data.keys()}

    for wanted in preferred:
        actual = lower_map.get(wanted)
        if actual is None:
            continue
        phone = _v19u_phone_from_sender_value(row_data.get(actual))
        if phone:
            return phone, str(actual)

    for key, value in row_data.items():
        key_l = str(key).lower()
        if not re.search(r"(sender|participant|author)", key_l):
            continue
        if not re.search(r"(phone|jid|wa|whatsapp|id|number|mobile|sender|participant|author)", key_l):
            continue
        phone = _v19u_phone_from_sender_value(value)
        if phone:
            return phone, str(key)

    return None, None


def _v19u_json_row(conn, table_name: str, id_value: Any) -> Optional[Dict[str, Any]]:
    if id_value is None or str(id_value).strip() == "":
        return None
    table_sql = _safe_identifier(table_name)
    rows = conn.execute(
        text(
            "SELECT to_jsonb(t) AS row_data "
            f"FROM {table_sql} t "
            "WHERE t.id::text=:row_id LIMIT 2"
        ),
        {"row_id": str(id_value)},
    ).mappings().all()
    if len(rows) != 1:
        return None
    data = rows[0].get("row_data")
    return data if isinstance(data, dict) else _loads(data, {})


_v19u_exact_ai_whatsapp_purity_sender_before = _v19i_exact_ai_whatsapp_purity_sender


def _v19i_exact_ai_whatsapp_purity_sender(engine, source: Dict[str, Any]) -> Dict[str, Any]:
    base = _v19u_exact_ai_whatsapp_purity_sender_before(engine, source)
    if base.get("status") == "FOUND_UNIQUE_UPSTREAM_SENDER":
        return base
    if str(source.get("source_table") or "") != "ai_whatsapp_purity":
        return base

    row_ref = str(source.get("source_row_ref") or "").strip()
    raw_text = str(source.get("source_raw_text") or source.get("raw_text") or "")

    result: Dict[str, Any] = {
        "status": base.get("status") or "UNRESOLVED",
        "resolution_stage": "AI_WHATSAPP_PURITY_EXACT_JID_RECOVERY_1_9U",
        "candidate_count": 0,
        "sender": None,
        "read_only": True,
    }

    try:
        with engine.connect() as conn:
            purity_rows = []
            if row_ref:
                purity_rows = conn.execute(
                    text(
                        "SELECT listing_id::text AS listing_id, raw_text "
                        "FROM ai_whatsapp_purity "
                        "WHERE listing_id::text=:row_ref LIMIT 2"
                    ),
                    {"row_ref": row_ref},
                ).mappings().all()
            if not purity_rows and raw_text:
                purity_rows = conn.execute(
                    text(
                        "SELECT listing_id::text AS listing_id, raw_text "
                        "FROM ai_whatsapp_purity "
                        "WHERE raw_text=:raw_text LIMIT 3"
                    ),
                    {"raw_text": raw_text},
                ).mappings().all()

        result["candidate_count"] = len(purity_rows)
        if len(purity_rows) == 0:
            result["status"] = "PURITY_ROW_NOT_FOUND"
            return result
        if len(purity_rows) > 1:
            result["status"] = "AMBIGUOUS_PURITY_ROWS"
            return result

        listing_id = str(purity_rows[0].get("listing_id") or "").strip()
        if not listing_id:
            result["status"] = "PURITY_LISTING_ID_MISSING"
            return result

        wa_engine = None
        dispose_wa = False
        try:
            wa_engine, dispose_wa = _v19i_whatsapp_engine(engine)
            with wa_engine.connect() as conn:
                has_listings = bool(conn.execute(text("SELECT to_regclass('wai_listings')")).scalar())
                has_raw = bool(conn.execute(text("SELECT to_regclass('wai_raw_messages')")).scalar())
                has_contacts = bool(conn.execute(text("SELECT to_regclass('wai_contacts')")).scalar())

                if not has_listings:
                    result["status"] = "WAI_LISTINGS_NOT_FOUND"
                    return result

                listing_rows = conn.execute(
                    text(
                        "SELECT to_jsonb(l) AS row_data "
                        "FROM wai_listings l "
                        "WHERE l.id::text=:listing_id LIMIT 2"
                    ),
                    {"listing_id": listing_id},
                ).mappings().all()

                if len(listing_rows) != 1:
                    result["candidate_count"] = len(listing_rows)
                    result["status"] = (
                        "WAI_LISTING_NOT_FOUND" if len(listing_rows) == 0
                        else "AMBIGUOUS_WAI_LISTING"
                    )
                    return result

                listing = listing_rows[0].get("row_data")
                if not isinstance(listing, dict):
                    listing = _loads(listing, {})

                source_message_id = listing.get("source_message_id")
                contact_id = listing.get("contact_id")

                if has_raw and source_message_id:
                    raw_row = _v19u_json_row(conn, "wai_raw_messages", source_message_id)
                    phone, match_column = _v19u_sender_phone_from_row(raw_row)
                    if phone:
                        name = None
                        if isinstance(raw_row, dict):
                            for name_key in (
                                "sender_display_name", "sender_name",
                                "participant_name", "author_name", "push_name"
                            ):
                                if raw_row.get(name_key):
                                    name = raw_row.get(name_key)
                                    break

                        result["status"] = "FOUND_UNIQUE_UPSTREAM_SENDER"
                        result["resolution_stage"] = "EXACT_WAI_LISTING_TO_RAW_MESSAGE_JID_1_9U"
                        result["sender"] = {
                            "phone": phone,
                            "name": name,
                            "company": None,
                            "source_table": "wai_raw_messages",
                            "match_method": "EXACT_SOURCE_MESSAGE_ID_SENDER_IDENTITY",
                            "match_column": match_column,
                            "provenance": "WHATSAPP_SENDER",
                        }
                        return result

                if has_contacts and contact_id:
                    contact_row = _v19u_json_row(conn, "wai_contacts", contact_id)
                    if isinstance(contact_row, dict):
                        phone = None
                        match_column = None
                        for key in ("phone", "mobile", "phone_number", "whatsapp_phone", "wa_phone", "jid"):
                            if key in contact_row:
                                phone = _v19u_phone_from_sender_value(contact_row.get(key))
                                if phone:
                                    match_column = key
                                    break
                        if phone:
                            result["status"] = "FOUND_UNIQUE_UPSTREAM_SENDER"
                            result["resolution_stage"] = "EXACT_WAI_LISTING_TO_CONTACT_1_9U"
                            result["sender"] = {
                                "phone": phone,
                                "name": contact_row.get("display_name") or contact_row.get("name"),
                                "company": contact_row.get("firm_name") or contact_row.get("company"),
                                "source_table": "wai_contacts",
                                "match_method": "EXACT_CONTACT_ID",
                                "match_column": match_column,
                                "provenance": "SOURCE_CONTACT",
                            }
                            return result

                result["status"] = "EXACT_LINEAGE_HAS_NO_RECOVERABLE_PHONE"
                return result
        finally:
            if dispose_wa and wa_engine is not None:
                wa_engine.dispose()

    except Exception as exc:
        result["status"] = "JID_RECOVERY_ERROR"
        result["error_type"] = type(exc).__name__
        result["error"] = str(exc)[:500]
        return result

'''

src = src.replace(anchor, "\n" + patch + anchor, 1)

TARGET.write_text(src, encoding="utf-8")

try:
    py_compile.compile(str(TARGET), doraise=True)
except Exception:
    shutil.copy2(backup, TARGET)
    raise

print("FOUNDATION_1_9U_INSTALL_PASS")
print(f"Updated: {TARGET.resolve()}")
print(f"Backup: {backup.resolve()}")
print("Fix: Gold Lab now recovers a WhatsApp sender phone from exact sender/participant JID fields when sender_phone is empty.")
print("Fix: explicit message contacts remain first priority; sender-derived contact provenance is WHATSAPP_SENDER.")
print("Fix: exact listing contact fallback remains SOURCE_CONTACT and is not falsely labeled as sender.")
print("Safety: no production inventory writes added; WhatsApp source tables are read-only.")
print("Preserved: 1.9T3 same-source repair navigation and 1.9T2 commercial splitter.")
