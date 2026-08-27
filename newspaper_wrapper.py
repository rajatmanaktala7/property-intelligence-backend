import app as core

# ============================================================
# ALLIANCE PRODUCTION FAIL-SAFE WRAPPER
# Core application ALWAYS starts first.
# Newspaper / V2 failures must never take the main site offline.
# ============================================================

app = core.app

# Newspaper Intelligence
try:
    from newspaper_intelligence import register as register_newspaper
    register_newspaper(core)
    print("Newspaper Intelligence: routes registered successfully")
except Exception as e:
    print(
        "Newspaper Intelligence registration warning:",
        type(e).__name__,
        str(e)
    )

# Alliance Data Intelligence V2
try:
    from alliance_v2_routes import register as register_alliance_v2
    register_alliance_v2(core)
    print("Alliance V2: routes registered successfully")
except Exception as e:
    print(
        "Alliance V2 registration warning:",
        type(e).__name__,
        str(e)
    )

# Emergency production health endpoint
@app.get("/production-health")
def production_health():
    return {
        "status": "OK",
        "service": "Alliance Property Intelligence",
        "core_app_loaded": True,
        "wrapper": "PRODUCTION_FAIL_SAFE_V1"
    }
