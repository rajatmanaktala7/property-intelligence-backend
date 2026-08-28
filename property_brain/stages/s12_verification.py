
from sqlalchemy import text
ALLOWED={"UNVERIFIED","PENDING","VERIFIED","NOT_AVAILABLE","TERMS_CHANGED","STALE"}
def set_verification(engine,property_id,status):
    s=status.upper()
    if s not in ALLOWED:raise ValueError("Unsupported verification status")
    with engine.begin() as c:c.execute(text("""UPDATE pb_canonical_properties SET verification_status=:s,last_verified_at=CASE WHEN :s='VERIFIED' THEN NOW() ELSE last_verified_at END,current_status=CASE WHEN :s='NOT_AVAILABLE' THEN 'INACTIVE' ELSE current_status END,updated_at=NOW() WHERE property_id=:id"""),{"s":s,"id":property_id})
    return {"status":"OK","property_id":str(property_id),"verification_status":s}
