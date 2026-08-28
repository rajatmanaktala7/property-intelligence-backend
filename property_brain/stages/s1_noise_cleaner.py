
import re
from ..schemas import RawEvidence,LineTag
from ..utils import load_json,norm
CFG=load_json("noise_patterns.json");REQ=("REQUIRE","REQUIRED","LOOKING FOR","WANTED","CLIENT NEED");AVL=("AVAILABLE","FOR RENT","FOR SALE","TO LET","RENT","SALE","PRICE","AREA","BHK","SQFT")
def clean_and_tag(raw):
    out=[]
    for i,line in enumerate(raw.raw_text.splitlines() or [raw.raw_text],1):
        s=line.strip();n=norm(s)
        if not s:tag="noise"
        elif any(x in s.lower() for x in CFG["contains"]) or any(re.search(p,s,re.I) for p in CFG["regex"]):tag="noise"
        elif any(x in n for x in REQ):tag="requirement_signal"
        elif any(x in n for x in AVL):tag="availability_signal"
        else:tag="ambiguous"
        out.append(LineTag(raw_id=raw.raw_id,line_no=i,tag=tag,line_text=line))
    return out
