
import re
from uuid import uuid4
from ..schemas import Segment
from ..utils import norm
def min_field_validator(t):
    n=norm(t);place=bool(re.search(r"\b(DLF|SAKET|KALKAJI|PANJIM|PANAJI|PORVORIM|SIOLIM|PHASE|SECTOR|ROAD|NAGAR|COLONY|PARK|PLACE)\b",n))
    fact=bool(re.search(r"\b(RENT|SALE|LEASE|PRICE|BHK|SQFT|SQ FT|SQ YD|ACRE|CR|LAC|LAKH)\b",n));return place and fact
def segment(burst):
    lines=[x.strip() for x in burst.text.splitlines() if x.strip()]
    starts=[i for i,x in enumerate(lines) if re.match(r"^(?:\d+[.)]|[1-9]️⃣|[①②③④⑤⑥⑦⑧⑨])",x)]
    chunks=[]
    if len(starts)>=2:
        for j,st in enumerate(starts):
            en=starts[j+1] if j+1<len(starts) else len(lines);chunks.append(" ".join(lines[st:en]))
    else:
        cand=[x for x in lines if len(norm(x))>15 and re.search(r"(?i)\b(RENT|SALE|PRICE|BHK|SQFT|SQ\s*YD|CR|LAC|LAKH)\b",x)]
        if len(cand)>=3:chunks=cand
    if not chunks:chunks=[burst.text]
    return [Segment(segment_id=uuid4(),raw_ids=burst.raw_ids,text=ch,
      split_method="deterministic" if len(chunks)>1 else "single",burst_group_id=burst.burst_group_id,
      insufficient=not min_field_validator(ch)) for ch in chunks]
