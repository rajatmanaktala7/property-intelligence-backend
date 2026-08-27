import app as core

app = core.app

try:
    from alliance_v2_routes import register as register_alliance_v2
    register_alliance_v2(core)
    print("Alliance V2: routes registered successfully")
except Exception as e:
    print("Alliance V2 registration warning:", type(e).__name__, str(e))

# Newspaper V8.3 only.
# Do NOT register newspaper_intelligence.register(core) here.
try:
    import newspaper_upload_v83 as _newspaper_v83
    _newspaper_v83.register(core)
    print("Newspaper V8.3 self-healing upload registered successfully")
except Exception as e:
    print("Newspaper V8.3 registration warning:", type(e).__name__, str(e))

@app.get("/production-health")
def production_health():
    return {
        "status": "OK",
        "service": "Alliance Property Intelligence",
        "core_app_loaded": True,
        "wrapper": "NEWSPAPER_V8_3_1_NO_STARTUP_HANG",
        "legacy_newspaper_startup_registration": False,
        "newspaper_mode": "ON_DEMAND_SELF_HEALING"
    }


# ALLIANCE V3.8 SOURCE-AWARE MATCHER
try:
    import alliance_v38_source_aware_matcher as _v38
    _v38.register(core)
    print("Alliance V3.8 source-aware matcher registered successfully")
except Exception as e:
    print("Alliance V3.8 registration warning:", type(e).__name__, str(e))
