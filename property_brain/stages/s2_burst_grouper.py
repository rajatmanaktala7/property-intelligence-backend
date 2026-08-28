
from datetime import timedelta
from uuid import uuid4
from ..schemas import BurstGroup
from ..utils import norm
NEW_SIGNAL=("LOCATION ","FOR SALE","FOR RENT","AVAILABLE ","PRICE ","RENT ","SALE ")
def _strong_new(t):return any(norm(t).startswith(x) for x in NEW_SIGNAL)
def group_bursts(records,gap_minutes=4):
    records=sorted(records,key=lambda x:x.captured_at);groups=[];current=[]
    def flush():
        nonlocal current
        if not current:return
        f=current[0];groups.append(BurstGroup(burst_group_id=uuid4(),raw_ids=[x.raw_id for x in current],
          source_type=f.source_type,sender=f.sender,source_group=f.source_group,captured_at=f.captured_at,
          text="\n".join(x.raw_text for x in current)));current=[]
    for rec in records:
        if not current:current=[rec];continue
        prev=current[-1];same=(rec.sender_phone or rec.sender)==(prev.sender_phone or prev.sender) and rec.source_group==prev.source_group
        if same and rec.captured_at-prev.captured_at<=timedelta(minutes=gap_minutes) and not _strong_new(rec.raw_text):current.append(rec)
        else:flush();current=[rec]
    flush();return groups
