from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from fastapi import Request
from sqlalchemy import text

VERSION = "1.0.0-BOUNDED-HOSPITALITY-DISCOVERY"
MAX_QUERIES = 12
MAX_RESULTS_PER_QUERY = 8
QUERY_WORKERS = 4

_CATEGORIES = (
    "restaurant", "cafe", "banquet hall", "hotel",
    "lounge", "wedding venue",
)
_ZONES = ("South Delhi", "Gurgaon", "Noida")


def _fetch(core: Any, category: str, zone: str) -> tuple[str, str, list[dict], str | None]:
    try:
        rows = list(core._google_places_search(f"{category} in {zone}") or [])
        return category, zone, rows[:MAX_RESULTS_PER_QUERY], None
    except Exception as exc:
        return category, zone, [], f"{type(exc).__name__}: {exc}"


def _worker(core: Any, run_id: str) -> None:
    queries = [(category, zone) for category in _CATEGORIES for zone in _ZONES][:MAX_QUERIES]
    found = created = 0
    errors: list[str] = []

    try:
        fetched: list[tuple[str, str, list[dict], str | None]] = []
        with ThreadPoolExecutor(max_workers=QUERY_WORKERS, thread_name_prefix="hospitality-search") as pool:
            futures = [pool.submit(_fetch, core, category, zone) for category, zone in queries]
            for future in as_completed(futures):
                fetched.append(future.result())

        # Database writes remain sequential. Slow website email scraping is deliberately
        # excluded; public phones and provider-returned emails are saved immediately.
        for category, zone, places, error in fetched:
            if error:
                errors.append(f"{category}/{zone}: {error}")
                continue
            for place in places:
                found += 1
                name = ((place.get("displayName") or {}).get("text") or "").strip()
                if not name:
                    continue
                phone = place.get("nationalPhoneNumber") or place.get("internationalPhoneNumber")
                email = place.get("_v179_email")
                website = place.get("websiteUri")
                address = place.get("formattedAddress") or zone
                try:
                    result = core._save_marketing_contact({
                        "business_type": category.title(),
                        "brand_name": name,
                        "contact_name": None,
                        "phone": phone,
                        "email": email,
                        "website": website,
                        "location": address,
                        "city": "Delhi NCR",
                        "source_name": "Google Places / Web Discovery",
                        "source_url": place.get("googleMapsUri") or website,
                        "consent_status": "UNKNOWN",
                        "verification_status": "PUBLIC_SOURCE",
                    })
                    if result.get("status") == "created":
                        created += 1
                        core._log_activity(
                            "Hospitality Data Bot", "HOSPITALITY", "CONTACT_FOUND",
                            "marketing_contact", result.get("contact_id"),
                            f"{name} | {phone or 'no phone'}",
                        )
                except Exception as exc:
                    errors.append(f"{name}: {type(exc).__name__}: {exc}")

        if found:
            status = "COMPLETED"
        else:
            status = "FAILED"
        summary = (
            f"Hospitality scan completed: {len(queries)} bounded searches; "
            f"{found} places reviewed; {created} new contacts"
        )
        core._finish_bot(
            run_id, status, found, created, summary,
            " | ".join(errors[:8]) or None,
        )
    except Exception as exc:
        core._finish_bot(
            run_id, "FAILED", found, created,
            "Hospitality scan failed", f"{type(exc).__name__}: {exc}",
        )


def register(core: Any) -> dict:
    required = (
        "_google_places_search", "_save_marketing_contact",
        "_log_activity", "_finish_bot",
    )
    missing = [name for name in required if not callable(getattr(core, name, None))]
    if missing:
        raise RuntimeError("Hospitality runtime hooks missing: " + ", ".join(missing))

    # The existing /api/v4/hospitality-bot/start handler resolves this module
    # global when each request runs, so the public route and route ownership stay stable.
    def bounded_worker(run_id: str) -> None:
        _worker(core, run_id)

    core._hospitality_worker = bounded_worker

    app = core.app
    if not any(getattr(route, "path", None) == "/api/alliance/hospitality-bot-runtime-v1/status"
               for route in app.router.routes):
        @app.get("/api/alliance/hospitality-bot-runtime-v1/status")
        def runtime_status(req: Request):
            core.need_login(req)
            with core.engine.connect() as connection:
                row = connection.execute(text("""
                    SELECT run_id,status,records_found,records_created,summary,
                           error_message,started_at,completed_at,
                           provider_used,provider_report
                    FROM ai_bot_runs
                    WHERE division='HOSPITALITY'
                    ORDER BY started_at DESC
                    LIMIT 1
                """)).mappings().first()
            return {
                "version": VERSION,
                "status": "READY",
                "worker": "BOUNDED_PARALLEL_PUBLIC_DISCOVERY",
                "latest_run": dict(row) if row else None,
                "database_changed": False,
            }

    return {
        "status": "READY",
        "version": VERSION,
        "route_preserved": "/api/v4/hospitality-bot/start",
        "status_route": "/api/alliance/hospitality-bot-runtime-v1/status",
        "max_queries": MAX_QUERIES,
        "max_results_per_query": MAX_RESULTS_PER_QUERY,
        "workers": QUERY_WORKERS,
        "model": "DETERMINISTIC_PYTHON_NO_GPT",
    }
