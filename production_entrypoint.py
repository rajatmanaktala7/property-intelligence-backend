
from __future__ import annotations
import traceback
from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

BOOT_ERROR=None
BOOT_TRACE=None
CORE_LOADED=False

try:
    import newspaper_wrapper as wrapped
    app=wrapped.app
    CORE_LOADED=True
except Exception as exc:
    BOOT_ERROR=f"{type(exc).__name__}: {exc}"
    BOOT_TRACE=traceback.format_exc(limit=20)
    app=FastAPI(title="Alliance Emergency Safe Mode",version="1.0")

@app.get("/healthz")
def healthz():
    return {
        "status":"ok",
        "service":"alliance-property-intelligence",
        "mode":"NORMAL" if CORE_LOADED else "SAFE_MODE",
        "core_loaded":CORE_LOADED,
        "boot_error":BOOT_ERROR,
        "timestamp_utc":datetime.now(timezone.utc).isoformat()
    }

@app.get("/readyz")
def readyz():
    return JSONResponse(
        status_code=200 if CORE_LOADED else 503,
        content={
            "status":"ready" if CORE_LOADED else "degraded",
            "core_loaded":CORE_LOADED,
            "boot_error":BOOT_ERROR
        }
    )

@app.get("/system-status")
def system_status():
    modules={}
    for name in [
        "newspaper_intelligence",
        "alliance_v2_routes",
        "whatsapp_live_bridge",
        "whatsapp_intelligence",
        "whatsapp_hot_lead_engine"
    ]:
        try:
            __import__(name)
            modules[name]="IMPORT_OK"
        except Exception as exc:
            modules[name]=f"ERROR: {type(exc).__name__}: {exc}"
    return {
        "service":"Alliance AI Deal Intelligence OS",
        "core_loaded":CORE_LOADED,
        "mode":"NORMAL" if CORE_LOADED else "SAFE_MODE",
        "boot_error":BOOT_ERROR,
        "modules":modules,
        "timestamp_utc":datetime.now(timezone.utc).isoformat()
    }

if not CORE_LOADED:
    @app.get("/",response_class=HTMLResponse)
    def safe_home():
        err=(BOOT_ERROR or "Unknown startup error").replace("<","&lt;").replace(">","&gt;")
        return HTMLResponse(f"""<!doctype html><html><head><meta charset='utf-8'>
        <meta name='viewport' content='width=device-width,initial-scale=1'>
        <title>Alliance Safe Mode</title><style>
        body{{font-family:Arial;background:#f1e3d2;color:#2d2a26;margin:0}}
        main{{max-width:900px;margin:60px auto;background:white;padding:28px;border-radius:14px;border:1px solid #dbcdbb}}
        code{{background:#f4f1ec;padding:3px 6px;border-radius:5px}}</style></head>
        <body><main><h1>Alliance is running in Safe Mode</h1>
        <p>The web process is online, but the main application could not complete startup.</p>
        <p><b>Startup error:</b> <code>{err}</code></p>
        <p>Open <a href='/system-status'>System Status</a> for module diagnostics.</p>
        </main></body></html>""")
