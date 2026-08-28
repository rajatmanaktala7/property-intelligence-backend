from __future__ import annotations

"""
Alliance WhatsApp Entity-First Newspaper V4.0

This is a thin compatibility wrapper around the already-proven 3.6 live-feed
logic.  The installer preserves the current 3.6 module as
alliance_live_feed_purity_legacy36.py, then this file delegates all existing
functions to it and overlays only the three WhatsApp presentation routes.

Production stability is intentionally NOT touched.
"""

import alliance_live_feed_purity_legacy36 as _legacy

VERSION = "WHATSAPP-NEWSPAPER-ENTITY-FIRST-4.0"


def __getattr__(name):
    return getattr(_legacy, name)


def register(wrapped):
    # Keep the proven 3.6 purity/status/market APIs alive first.
    legacy_result = _legacy.register(wrapped)

    # Overlay only:
    #   /whatsapp-live
    #   /whatsapp-live/feed
    #   /whatsapp-live/requirements
    import alliance_newspaper_views as _newspaper
    newspaper_result = _newspaper.register(wrapped)

    return {
        "status": "REGISTERED",
        "version": VERSION,
        "legacy": legacy_result,
        "newspaper": newspaper_result,
    }
