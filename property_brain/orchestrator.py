
import json
from datetime import datetime,timezone
from uuid import uuid4
from sqlalchemy import text
from .schemas import RawEvidence
from .stages.s1_noise_cleaner import clean_and_tag
from .stages.s2_burst_grouper import group_bursts
from .stages.s3_segmentation import segment
from .stages.s4_extractor import extract
from .stages.s5_validator import validate
from .stages.s6_location_brain import resolve_location
from .stages.s7_quality_gate import gate
from .stages.s8_entity_resolution import resolve_and_upsert
def ingest_raw(engine,source_type,source_ref,raw_text,sender=None,sender_phone=None,source_group=None,captured_at=None):
    rid=uuid4();captured_at=captured_at or datetime.now(timezone.utc)
    with engine.begin() as c:
        old=c.execute(text("SELECT raw_id FROM pb_raw_evidence WHERE source_type=:s AND source_ref=:r"),{"s":source_type,"r":source_ref}).scalar()
        if old:return old,False
        c.execute(text("""INSERT INTO pb_raw_evidence(raw_id,source_type,source_ref,raw_text,sender,sender_phone,source_group,captured_at,status) VALUES(:id,:s,:r,:t,:sn,:sp,:g,:ca,'new')"""),{"id":rid,"s":source_type,"r":source_ref,"t":raw_text,"sn":sender,"sp":sender_phone,"g":source_group,"ca":captured_at})
    return rid,True
def process_raw_ids(engine,raw_ids):
    with engine.connect() as c:rows=c.execute(text("SELECT raw_id,source_type,source_ref,raw_text,sender,sender_phone,source_group,captured_at,status FROM pb_raw_evidence WHERE raw_id=ANY(:ids) ORDER BY captured_at"),{"ids":list(raw_ids)}).mappings().all()
    raws=[RawEvidence(**dict(r)) for r in rows];results=[]
    with engine.begin() as c:
        for raw in raws:
            for tag in clean_and_tag(raw):c.execute(text("INSERT INTO pb_line_tags(raw_id,line_no,tag,line_text) VALUES(:r,:n,:t,:x)"),{"r":tag.raw_id,"n":tag.line_no,"t":tag.tag,"x":tag.line_text})
            c.execute(text("UPDATE pb_raw_evidence SET status='cleaned' WHERE raw_id=:id"),{"id":raw.raw_id})
    for burst in group_bursts(raws):
        with engine.begin() as c:c.execute(text("""INSERT INTO pb_bursts(burst_group_id,source_type,sender,source_group,captured_at,raw_ids,burst_text) VALUES(:id,:s,:sn,:g,:ca,CAST(:ids AS jsonb),:t)"""),{"id":burst.burst_group_id,"s":burst.source_type,"sn":burst.sender,"g":burst.source_group,"ca":burst.captured_at,"ids":json.dumps([str(x) for x in burst.raw_ids]),"t":burst.text})
        for seg in segment(burst):
            with engine.begin() as c:c.execute(text("""INSERT INTO pb_segments(segment_id,burst_group_id,raw_ids,segment_text,split_method,insufficient) VALUES(:id,:b,CAST(:ids AS jsonb),:t,:m,:i)"""),{"id":seg.segment_id,"b":seg.burst_group_id,"ids":json.dumps([str(x) for x in seg.raw_ids]),"t":seg.text,"m":seg.split_method,"i":seg.insufficient})
            ex=extract(seg);v=validate(ex);loc=resolve_location(engine,ex);g=gate(ex,v,loc)
            with engine.begin() as c:
                c.execute(text("""INSERT INTO pb_extractions(extraction_id,segment_id,raw_ids,classification,fields,field_confidence,extraction_method,validation_flags,gate_outcome) VALUES(:id,:sid,CAST(:rids AS jsonb),:cl,CAST(:f AS jsonb),CAST(:fc AS jsonb),:m,CAST(:vf AS jsonb),:go)"""),{"id":ex.extraction_id,"sid":ex.segment_id,"rids":json.dumps([str(x) for x in ex.raw_ids]),"cl":ex.classification,"f":json.dumps(ex.fields),"fc":json.dumps(ex.field_confidence),"m":ex.extraction_method,"vf":json.dumps(v.flags),"go":g.outcome})
            if g.outcome=="clean":
                pid=resolve_and_upsert(engine,ex,loc,{"contact_name":burst.sender});results.append({"outcome":"clean","property_id":str(pid)})
            else:
                with engine.begin() as c:c.execute(text("""INSERT INTO pb_review_queue(review_id,queue_type,target_type,target_id,payload,reason) VALUES(:id,:q,'extraction',:tid,CAST(:p AS jsonb),:r)"""),{"id":uuid4(),"q":"holding" if g.outcome=="holding" else "rejected","tid":ex.extraction_id,"p":json.dumps({"fields":ex.fields,"location":loc.model_dump(mode="json")}),"r":", ".join(g.reasons) or g.outcome})
                results.append({"outcome":g.outcome,"reasons":g.reasons})
    return results
