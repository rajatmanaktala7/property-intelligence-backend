from __future__ import annotations
VERSION='11.7.1-MANUAL-ENTRY-FULL-COUNTS-NAV'
FINAL_PATHS=('/team-dashboard-v376','/team-dashboard-live','/alliance/primary','/alliance/final/databases','/alliance/final/requirements','/alliance/final/database/{source}','/alliance/final/requirements/{source}','/alliance/primary/availability','/alliance/primary/matcher','/alliance/primary/followups','/alliance/primary/reports','/alliance/primary/contacts','/alliance/primary/ai-control','/alliance/primary/data-health','/commercial-intelligence')
def _move_front(app,path):
 found=[r for r in list(app.router.routes) if getattr(r,'path',None)==path]
 for r in found:
  try:app.router.routes.remove(r)
  except ValueError:pass
 for r in reversed(found):app.router.routes.insert(0,r)
 return len(found)
def register(wrapped):
 app=wrapped.app;core=wrapped.core;result={'status':'REGISTERED','version':VERSION}
 for name,mod,arg in [('renderer','alliance_cre_os_v1000',core),('cre11','alliance_cre_os_v1100',core),('auth','alliance_browser_auth_v1140',wrapped),('manual_restore','alliance_manual_database_restore_v1150',wrapped),('property_sources','alliance_unified_property_sources_v1160',wrapped),('full_property_database','alliance_full_property_database_v1170',wrapped),('cre1171','alliance_cre_os_v1171',wrapped)]:
  try:result[name]=__import__(mod).register(arg)
  except Exception as ex:result[name+'_error']=f'{type(ex).__name__}: {ex}'
 result['moved']={p:_move_front(app,p) for p in reversed(FINAL_PATHS)};result['route_count']=len(app.router.routes);return result
