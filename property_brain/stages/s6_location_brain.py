
from sqlalchemy import text
from ..schemas import LocationResolution
from ..utils import norm
def resolve_location(engine,x):
    raw=x.fields.get("location_raw")
    if not raw:return LocationResolution(extraction_id=x.extraction_id,resolution_method="unresolved")
    try:
        with engine.connect() as c:r=c.execute(text("SELECT canonical_name,city,project_name,confidence,status FROM pb_location_aliases WHERE alias_norm=:a"),{"a":norm(raw)}).mappings().first()
        if r and r["status"]=="CONFIRMED":return LocationResolution(extraction_id=x.extraction_id,city=r["city"],locality_id=norm(r["canonical_name"]),locality_name=r["canonical_name"],project_name=r["project_name"],resolution_confidence=float(r["confidence"]),resolution_method="alias_table")
    except Exception:pass
    return LocationResolution(extraction_id=x.extraction_id,locality_id=norm(raw),locality_name=raw,resolution_confidence=.70,resolution_method="direct")
