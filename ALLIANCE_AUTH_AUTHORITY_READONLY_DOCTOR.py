from __future__ import annotations

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
