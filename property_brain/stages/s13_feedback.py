
from uuid import uuid4
from sqlalchemy import text
ALLOWED={"GOOD_MATCH","BAD_MATCH","WRONG_LOCATION","WRONG_TRANSACTION","WRONG_PROPERTY_TYPE","TOO_EXPENSIVE","WRONG_AREA","NOT_AVAILABLE","CLIENT_INTERESTED","SITE_VISIT","NEGOTIATION","DEAL_CLOSED","LOST"}
def log_outcome(engine,requirement_id,property_id,outcome,notes=None,actor=None):
    o=outcome.upper()
    if o not in ALLOWED:raise ValueError("Unsupported outcome")
    fid=uuid4()
    with engine.begin() as c:c.execute(text("INSERT INTO pb_feedback_outcomes(feedback_id,requirement_id,property_id,outcome,notes,actor) VALUES(:id,:r,:p,:o,:n,:a)"),{"id":fid,"r":requirement_id,"p":property_id,"o":o,"n":notes,"a":actor})
    return {"status":"OK","feedback_id":str(fid)}
