from __future__ import annotations
import html
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

VERSION = "2.0.0-CLEAN-SHELL"

CSS = """*{box-sizing:border-box}body{margin:0;background:#f4f6f8;color:#172033;font-family:Arial,sans-serif}header{background:#10223f;color:#fff;padding:16px 22px}.wrap{max-width:1500px;margin:auto;padding:18px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px}.card{background:#fff;border:1px solid #d0d5dd;border-radius:10px;padding:16px}.card h3{margin:0 0 8px}.btn{display:inline-block;background:#10223f;color:#fff;text-decoration:none;padding:8px 11px;border-radius:6px;font-weight:700;margin:3px 4px 3px 0}.muted{color:#667085}.rule{background:#ecfdf3;border:1px solid #abefc6;padding:12px;border-radius:8px;margin:0 0 16px}"""

def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{html.escape(title)}</title><style>{CSS}</style></head><body><header><b>Alliance CRE</b> · Clean Core V2</header><main class='wrap'><h1>{html.escape(title)}</h1>{body}</main></body></html>")

def _card(title, text, links):
    a="".join(f"<a class='btn' href='{u}'>{html.escape(t)}</a>" for t,u in links)
    return f"<section class='card'><h3>{html.escape(title)}</h3><p class='muted'>{html.escape(text)}</p>{a}</section>"

def register(core):
    app=core.app
    # Clean shell owns only the main navigation. Data authorities remain untouched.
    app.router.routes[:] = [r for r in app.router.routes if not (getattr(r,"path",None) in {"/alliance/primary","/alliance"} and "GET" in set(getattr(r,"methods",set()) or set()))]

    @app.get("/alliance/primary", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/alliance", response_class=HTMLResponse, include_in_schema=False)
    async def clean_home(request: Request):
        body="""<div class='rule'><b>Foundation rule:</b> preserve all existing data. Every database shows all records. AI cleans and completes fields from evidence but never invents missing data. All requirements match against Master Properties only.</div>"""
        cards=[
          _card("Property Databases","Same clean format, full records and search in every source database.",[
            ("Master Properties","/alliance/final/database/master"),("WhatsApp","/alliance/final/database/whatsapp"),("Manual","/alliance/final/database/manual"),("Newspaper","/alliance/final/database/newspaper"),("Magazine","/alliance/final/database/magazine")]),
          _card("Requirements","All requirements visible with search and one Run Matcher action.",[
            ("Master Requirements","/alliance/master-requirement-matcher"),("Manual Requirements","/alliance/final/requirements/manual"),("WhatsApp Requirements","/alliance/final/requirements/whatsapp"),("Add Requirement","/alliance/final/requirements/manual")]),
          _card("Match & Deal","One matcher. Master Properties is the only property authority.",[
            ("Smart Matcher","/alliance/primary/matcher"),("Deal Desk","/alliance/deal-desk")]),
          _card("Intelligence","Live intake and AI verification without changing database authorities.",[
            ("WhatsApp Live","/whatsapp-live"),("AI Assistant","/commercial-intelligence")]),
          _card("Marketing Contacts","Preserve existing marketing contacts and category views.",[
            ("Marketing Contacts","/marketing-contacts")]),
          _card("Data Input","One entry point per source. Successful entries flow to source database and Master.",[
            ("Add Manual Property","/property-manual"),("Newspaper Capture","/newspaper"),("Magazine Upload","/magazine-master-import")]),
        ]
        return _page("Command Centre",body+"<div class='grid'>"+"".join(cards)+"</div>")
    return {"status":"REGISTERED","version":VERSION,"route":"/alliance/primary","policy":"DATA_PRESERVED_EXISTING_AUTHORITIES_UNCHANGED"}
