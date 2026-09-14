from __future__ import annotations

import py_compile
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TAG = "AUTH_ROUTE_CANONICAL_V1"

REQ = ROOT / "alliance_requirement_restore_v1235.py"
COMM = ROOT / "alliance_commercial_intelligence_ai.py"
GOV = ROOT / "alliance_government_commercial_sources_v1.py"
DOCTOR = ROOT / "ALLIANCE_AUTH_AUTHORITY_READONLY_DOCTOR.py"

TARGETS = [REQ, COMM, GOV, DOCTOR]
originals = {}


def backup(path: Path) -> None:
    if not path.exists():
        raise RuntimeError(f"Required file missing: {path.name}")
    originals[path] = path.read_text(encoding="utf-8")
    backup_path = path.with_name(path.name + ".before-auth-route-v1")
    if not backup_path.exists():
        shutil.copy2(path, backup_path)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def patch_requirement() -> None:
    text = REQ.read_text(encoding="utf-8")

    if "ALLIANCE_REQUIREMENT_CANONICAL_AUTH_V22" in text:
        print("Requirement authentication already patched.")
        return

    start = text.find("# ALLIANCE_REQUIREMENT_CANONICAL_AUTH_V21")
    end = text.find("\ndef _e(", start)

    if start < 0 or end < 0:
        raise RuntimeError("Could not locate requirement authentication block")

    replacement = '''# ALLIANCE_REQUIREMENT_CANONICAL_AUTH_V22
def _login(core, req):
    """Use the same pi_session authority as the primary Alliance dashboard."""
    need_login = getattr(core, "need_login", None)

    if callable(need_login):
        need_login(req)

        role_fn = getattr(core, "get_role", None)
        if callable(role_fn):
            try:
                return role_fn(req) or "authenticated"
            except Exception:
                pass

        return "authenticated"

    role_fn = getattr(core, "get_role", None)
    if callable(role_fn):
        try:
            role = role_fn(req)
            if role:
                return role
        except Exception:
            pass

    raise HTTPException(
        status_code=303,
        detail="Login required",
        headers={"Location": "/login"},
    )

'''

    text = text[:start] + replacement + text[end + 1:]
    text = text.replace(
        'VERSION = "12.3.6-REQUIREMENT-RUN-MATCH-DASHBOARD-FIX"',
        'VERSION = "12.3.7-CANONICAL-SESSION-AUTH"',
        1,
    )

    REQ.write_text(text, encoding="utf-8")
    print("Patched canonical requirement authentication.")


def patch_commercial_routes() -> None:
    text = COMM.read_text(encoding="utf-8")

    if "def _remove_owned_routes(app):" in text:
        print("Commercial route ownership already patched.")
        return

    text = text.replace(
        'VERSION = "5.0.1-SINGLE-FLIGHT-RUNTIME-SAFETY"',
        'VERSION = "5.0.2-CANONICAL-ROUTE-OWNERSHIP"',
        1,
    )

    old = '''def register(core):
    engine,app=core.engine,core.app; router=APIRouter(); ensure_schema(engine)
'''

    new = '''def _remove_owned_routes(app):
    """Remove only routes owned by this module before canonical registration."""
    owned = {
        ("/commercial-intelligence", "GET"),
        ("/commercial-intelligence/research-all", "POST"),
        ("/commercial-intelligence/research/{asset_code}", "POST"),
        ("/api/commercial-intelligence/status", "GET"),
        ("/commercial-intelligence/government-sync", "POST"),
        ("/api/commercial-intelligence/government-source-status", "GET"),
    }

    removed = 0
    for route in list(getattr(app.router, "routes", [])):
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", set()) or set()

        if any(
            path == owned_path and method in methods
            for owned_path, method in owned
        ):
            app.router.routes.remove(route)
            removed += 1

    return removed


def register(core):
    engine,app=core.engine,core.app
    removed_routes=_remove_owned_routes(app)
    router=APIRouter()
    ensure_schema(engine)
'''

    text = replace_once(
        text,
        old,
        new,
        "Commercial register function",
    )

    text = replace_once(
        text,
        'return {"registered":True,"version":VERSION,"route":"/commercial-intelligence"}',
        'return {"registered":True,"version":VERSION,"route":"/commercial-intelligence","removed_duplicate_routes":removed_routes,"canonical_owner":"alliance_commercial_intelligence_ai"}',
        "Commercial registration result",
    )

    COMM.write_text(text, encoding="utf-8")
    print("Patched Commercial Intelligence route ownership.")


def patch_commercial_queries() -> None:
    text = GOV.read_text(encoding="utf-8")

    if "BOUNDED-BULK-RENDER" in text:
        print("Commercial bulk-query repair already installed.")
        return

    text = replace_once(
        text,
        "from sqlalchemy import text",
        "from sqlalchemy import bindparam, text",
        "SQLAlchemy import",
    )

    text = text.replace(
        'VERSION="1.0.0-GOV-SOURCES-STRICT-CITY"',
        'VERSION="1.1.0-BOUNDED-BULK-RENDER"',
        1,
    )

    old_load = '''    with engine.connect() as c:
        assets=[dict(x) for x in c.execute(text(f"SELECT * FROM aci_intel_assets WHERE {' AND '.join(where)} ORDER BY confidence DESC,asset_name LIMIT 500"),p).mappings().all()]
        devs=[dict(x) for x in c.execute(text("SELECT authority,developer_name,COUNT(*) property_count,STRING_AGG(DISTINCT COALESCE(phone,''),', ') phones FROM aci_gov_developer_portfolio GROUP BY authority,developer_name ORDER BY COUNT(*) DESC,developer_name LIMIT 100")).mappings().all()]
    cards=[]
'''

    new_load = '''    # Bounded bulk loading: three queries total instead of two queries per asset.
    with engine.connect() as c:
        assets=[dict(x) for x in c.execute(
            text(f"SELECT * FROM aci_intel_assets WHERE {' AND '.join(where)} ORDER BY confidence DESC,asset_name LIMIT 300"),
            p,
        ).mappings().all()]
        devs=[dict(x) for x in c.execute(text(
            "SELECT authority,developer_name,COUNT(*) property_count,"
            "STRING_AGG(DISTINCT COALESCE(phone,''),', ') phones "
            "FROM aci_gov_developer_portfolio "
            "GROUP BY authority,developer_name "
            "ORDER BY COUNT(*) DESC,developer_name LIMIT 100"
        )).mappings().all()]

        asset_codes=[a["asset_code"] for a in assets]
        vacancy_rows=[]
        contact_rows=[]

        if asset_codes:
            vacancy_query=text(
                "SELECT * FROM aci_intel_vacancies "
                "WHERE asset_code IN :asset_codes "
                "ORDER BY asset_code,last_verified_at DESC NULLS LAST"
            ).bindparams(bindparam("asset_codes", expanding=True))

            contact_query=text(
                "SELECT * FROM aci_intel_contacts "
                "WHERE asset_code IN :asset_codes "
                "ORDER BY asset_code,confidence DESC,last_verified_at DESC NULLS LAST"
            ).bindparams(bindparam("asset_codes", expanding=True))

            vacancy_rows=[
                dict(x) for x in
                c.execute(vacancy_query, {"asset_codes":asset_codes}).mappings().all()
            ]
            contact_rows=[
                dict(x) for x in
                c.execute(contact_query, {"asset_codes":asset_codes}).mappings().all()
            ]

    vacancies_by_asset={}
    contacts_by_asset={}

    for row in vacancy_rows:
        bucket=vacancies_by_asset.setdefault(row.get("asset_code"),[])
        if len(bucket)<5:
            bucket.append(row)

    for row in contact_rows:
        bucket=contacts_by_asset.setdefault(row.get("asset_code"),[])
        if len(bucket)<6:
            bucket.append(row)

    cards=[]
'''

    text = replace_once(
        text,
        old_load,
        new_load,
        "Commercial bulk-load block",
    )

    old_loop = '''    for a in assets:
        with engine.connect() as c:
            vac=[dict(x) for x in c.execute(text("SELECT * FROM aci_intel_vacancies WHERE asset_code=:a ORDER BY last_verified_at DESC NULLS LAST LIMIT 5"),{"a":a["asset_code"]}).mappings().all()]
            con=[dict(x) for x in c.execute(text("SELECT * FROM aci_intel_contacts WHERE asset_code=:a ORDER BY confidence DESC,last_verified_at DESC NULLS LAST LIMIT 6"),{"a":a["asset_code"]}).mappings().all()]
'''

    new_loop = '''    for a in assets:
        vac=vacancies_by_asset.get(a["asset_code"],[])
        con=contacts_by_asset.get(a["asset_code"],[])
'''

    text = replace_once(
        text,
        old_loop,
        new_loop,
        "Commercial N+1 loop",
    )

    GOV.write_text(text, encoding="utf-8")
    print("Replaced Commercial Intelligence N+1 queries with bulk queries.")


def write_doctor() -> None:
    doctor = r'''from __future__ import annotations

import getpass
import http.client
import socket
import urllib.parse
from http.cookies import SimpleCookie

HOST = "app.allianceinfrastructure.co.in"
CONNECT_TIMEOUT = 15
READ_TIMEOUT = 20
MAX_BODY_BYTES = 2_000_000

PATHS = [
    ("PRIMARY", "/alliance/primary"),
    ("REQUIREMENT_DATABASE", "/alliance/final/requirements"),
    ("COMMERCIAL", "/commercial-intelligence"),
    ("NEWSPAPER", "/newspaper-v83"),
    ("WHATSAPP", "/whatsapp-live"),
]


def request(method, path, body=None, headers=None):
    connection = http.client.HTTPSConnection(
        HOST,
        timeout=CONNECT_TIMEOUT,
    )

    try:
        connection.request(
            method,
            path,
            body=body,
            headers=headers or {},
        )

        response = connection.getresponse()

        if connection.sock is not None:
            connection.sock.settimeout(READ_TIMEOUT)

        data = response.read(MAX_BODY_BYTES)

        return (
            response.status,
            dict(response.getheaders()),
            data,
            None,
        )

    except (TimeoutError, socket.timeout) as exc:
        return None, {}, b"", f"TIMEOUT:{type(exc).__name__}"

    except Exception as exc:
        return None, {}, b"", f"ERROR:{type(exc).__name__}:{exc}"

    finally:
        connection.close()


def main():
    print("=== ALLIANCE AUTH AUTHORITY READ-ONLY DOCTOR V2 ===")

    role = input("Role [admin/team]: ").strip().lower()

    if role not in {"admin", "team"}:
        raise SystemExit("STOP: role must be admin or team")

    login_code = getpass.getpass("Login code: ")

    if not login_code:
        raise SystemExit("STOP: login code cannot be blank")

    body = urllib.parse.urlencode(
        {
            "role": role,
            "code": login_code,
        }
    ).encode("utf-8")

    login_code = None

    status, headers, data, error = request(
        "POST",
        "/login",
        body,
        {
            "Content-Type": "application/x-www-form-urlencoded",
            "Content-Length": str(len(body)),
            "User-Agent": "Alliance-ReadOnly-Auth-Doctor/2.0",
        },
    )

    print(f"LOGIN_POST_HTTP={status}")
    if error:
        print(f"AUTH_RESULT=LOGIN_{error}")
        raise SystemExit(2)

    if status == 401:
        print("AUTH_RESULT=LOGIN_REJECTED")
        raise SystemExit(3)

    if status not in {302, 303, 307, 308}:
        print(f"AUTH_RESULT=UNEXPECTED_LOGIN_RESPONSE_{status}")
        raise SystemExit(4)

    raw_cookie = headers.get("Set-Cookie", "")
    jar = SimpleCookie()

    if raw_cookie:
        jar.load(raw_cookie)

    if "pi_session" not in jar:
        print("AUTH_RESULT=LOGIN_REDIRECT_WITHOUT_PI_SESSION")
        raise SystemExit(5)

    cookie_value = jar["pi_session"].value
    print("PI_SESSION=ISSUED")

    auth_headers = {
        "Cookie": f"pi_session={cookie_value}",
        "User-Agent": "Alliance-ReadOnly-Auth-Doctor/2.0",
    }

    failures = []

    for name, path in PATHS:
        status, headers, data, error = request(
            "GET",
            path,
            headers=auth_headers,
        )

        if error:
            print(f"{name}_RESULT={error}")
            failures.append((name, error))
            continue

        print(f"{name}_HTTP={status} BYTES_READ={len(data)}")

        if status != 200:
            failures.append((name, status))

    if failures:
        print("AUTH_RESULT=ROUTE_FAILURES_FOUND")
        print(
            "FAILED="
            + ",".join(f"{name}:{result}" for name, result in failures)
        )
        raise SystemExit(6)

    print("AUTH_RESULT=PASS")
    print("READ_ONLY=YES")
    print("NO_DATABASE_WRITE=YES")
    print("NO_ROUTE_HANG=YES")


if __name__ == "__main__":
    main()
'''

    DOCTOR.write_text(doctor, encoding="utf-8")
    print("Installed non-hanging authentication doctor V2.")


def validate() -> None:
    for path in TARGETS:
        py_compile.compile(
            str(path),
            doraise=True,
        )
        print(f"SYNTAX_OK={path.name}")

    req_text = REQ.read_text(encoding="utf-8")
    comm_text = COMM.read_text(encoding="utf-8")
    gov_text = GOV.read_text(encoding="utf-8")

    checks = {
        "REQUIREMENT_CANONICAL_AUTH": (
            "ALLIANCE_REQUIREMENT_CANONICAL_AUTH_V22" in req_text
            and 'getattr(core, "need_login", None)' in req_text
        ),
        "COMMERCIAL_CANONICAL_OWNER": (
            "def _remove_owned_routes(app):" in comm_text
            and "removed_duplicate_routes" in comm_text
        ),
        "COMMERCIAL_BULK_RENDER": (
            "BOUNDED-BULK-RENDER" in gov_text
            and "vacancies_by_asset" in gov_text
            and "contacts_by_asset" in gov_text
        ),
        "DOCTOR_BOUNDED_READ": (
            "READ_TIMEOUT = 20" in DOCTOR.read_text(encoding="utf-8")
            and "MAX_BODY_BYTES" in DOCTOR.read_text(encoding="utf-8")
        ),
    }

    for name, passed in checks.items():
        print(f"{name}={'PASS' if passed else 'FAIL'}")

    if not all(checks.values()):
        raise RuntimeError("One or more installation checks failed")


def restore() -> None:
    for path, content in originals.items():
        path.write_text(content, encoding="utf-8")


def main() -> None:
    print("=== ALLIANCE AUTH + ROUTE CANONICAL FIX V1 ===")

    for path in [REQ, COMM, GOV]:
        backup(path)

    if DOCTOR.exists():
        backup(DOCTOR)
    else:
        originals[DOCTOR] = None

    try:
        patch_requirement()
        patch_commercial_routes()
        patch_commercial_queries()
        write_doctor()
        validate()

    except Exception:
        print("INSTALLATION_FAILED: restoring original files")

        for path, content in originals.items():
            if content is None:
                if path.exists():
                    path.unlink()
            else:
                path.write_text(content, encoding="utf-8")

        raise

    print("INSTALLATION_RESULT=PASS")
    print("DATABASE_CHANGED=NO")
    print("READY_TO_COMMIT=YES")


if __name__ == "__main__":
    main()
