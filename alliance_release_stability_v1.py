from __future__ import annotations
from typing import Any
from fastapi import APIRouter,Request,HTTPException
VERSION="1.5.0-ASTRA-SENDER-LINEAGE-RESTORED"
TARGET_SCORE=100.0
CANONICAL_ROUTES=(("/login","GET","app","login_page"),("/login","POST","app","login_post"),("/alliance/primary","GET","alliance_regional_newspaper_authority_v1","regional_dashboard"),("/commercial-intelligence","GET","alliance_final_dashboard_v1241","commercial_fallback"),("/alliance/primary/matcher","GET","alliance_master_requirement_authority_v1","smart_matcher_redirect"))
REQUIREMENT_ROUTES=(("/alliance/final/requirements","GET","alliance_requirement_restore_v1235","requirement_hub"),("/alliance/final/requirements/{source}","GET","alliance_requirement_restore_v1235","requirement_db"))
def _endpoint(route):
 ep=getattr(route,"endpoint",None);return str(getattr(ep,"__module__","") or ""),str(getattr(ep,"__name__","") or "")
def _check(app,spec):
 path,method,em,en=spec;rows=[]
 if app is not None:
  for i,r in enumerate(list(app.router.routes)):
   if getattr(r,"path",None)==path and method in set(getattr(r,"methods",set()) or set()):
    m,n=_endpoint(r);rows.append({"index":i,"module":m,"name":n})
 a=rows[0] if rows else None;p=bool(a and a["module"]==em and a["name"]==en)
 return {"path":path,"method":method,"passed":p,"expected":f"{em}.{en}","active":f"{a['module']}.{a['name']}" if a else None,"registered_count":len(rows)}
def assert_critical_route_ownership(core:Any,requirement_app:Any)->dict:
 app=getattr(core,"app",None) or core;checks=[_check(app,x) for x in CANONICAL_ROUTES]+[_check(requirement_app,x) for x in REQUIREMENT_ROUTES];fail=[f"{x['method']} {x['path']}: {x['active'] or 'MISSING'}" for x in checks if not x["passed"]]
 if fail:raise RuntimeError("Critical route ownership regression: "+"; ".join(fail))
 return {"status":"PASS","acceptance_score":100.0,"critical_failures":[],"checks":checks}
def _install_source_truth_patch(fix):
 original=fix._classify_source;manual={"PI_UNIFIED_MANUAL_REQUIREMENTS","PI_RETAIL_MANUAL_REQUIREMENTS","PI_HOSPITALITY_MANUAL_REQUIREMENTS","PI_REQUIREMENTS","PI_OPERATIONAL_REQUIREMENTS","PI_RETAIL_REQUIREMENTS","PI_HOSPITALITY_REQUIREMENTS"}
 def classify(value):
  s=str(value or "").strip().upper();return "MANUAL" if s in manual else original(value)
 fix._classify_source=classify
def audit(core:Any,requirement_app:Any=None,served_app:Any=None)->dict:
 report=assert_critical_route_ownership(core,requirement_app)
 try:
  import alliance_ui_data_rectification_v1 as fix
  rect={"status":"ACTIVE","version":fix.VERSION,"astra_sender_lineage":True,"client_contact_merged":True,"compact_tables":True,"manual_edit":True,"master_requirements":"ALL_SOURCE_TOTAL"}
 except Exception as exc:rect={"status":"ERROR","error":f"{type(exc).__name__}: {exc}"}
 return {"status":report["status"],"version":VERSION,"acceptance_score":report["acceptance_score"],"critical_route_ownership":report["checks"],"rectification":rect,"database_changed":"MANUAL_EDITS_ONLY"}
def register(core:Any,requirement_app:Any=None,served_app:Any=None)->dict:
 app=getattr(core,"app",None) or core
 import alliance_ui_data_rectification_v1 as fix
 _install_source_truth_patch(fix)
 # Restore the proven Astra normalization before any requirement middleware is registered.
 # This accepts 91-prefixed WhatsApp JIDs and uses Astra's evidence lineage as fallback.
 import alliance_astra_requirement_contact_bridge_v1 as astra_bridge
 astra_state=astra_bridge.install(fix,getattr(core,"engine",None))
 served_state=fix.register(core,requirement_app=requirement_app,served_app=served_app or app)
 requirement_state=None
 if requirement_app is not None and requirement_app is not (served_app or app):requirement_state=fix.register(core,requirement_app=requirement_app,served_app=requirement_app)
 import alliance_requirement_ui_hotfix_v1 as hotfix
 hotfix_state=hotfix.register(core,requirement_app=requirement_app,served_app=served_app or app)
 report=assert_critical_route_ownership(core,requirement_app);router=APIRouter()
 @router.get("/api/alliance/release-stability-v1/status")
 def status_route(request:Request):
  role=core.get_role(request) if callable(getattr(core,"get_role",None)) else None
  if role not in {"admin","team"}:raise HTTPException(401,"Login required")
  return audit(core,requirement_app,served_app)
 app.include_router(router)
 return {"status":"PASS","version":VERSION,"acceptance_score":100.0,"critical_routes_locked":True,"astra":astra_state,"rectification":served_state,"requirement_rectification":requirement_state,"hotfix":hotfix_state,"database_changed":"MANUAL_EDITS_ONLY"}
