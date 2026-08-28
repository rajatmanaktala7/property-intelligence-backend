
from __future__ import annotations
import hashlib,json,re
from pathlib import Path
from typing import Any,Optional
PHONE_RE=re.compile(r"(?<!\d)(?:\+?91[\s().-]*)?[6-9](?:[\s().-]*\d){9}(?!\d)")
def norm(v:Any)->str:
    return re.sub(r"\s+"," ",re.sub(r"[^A-Z0-9]+"," ",str(v or "").upper())).strip()
def phones(*values)->list[str]:
    seen=[]
    for value in values:
        for m in PHONE_RE.finditer(str(value or "")):
            d=re.sub(r"\D","",m.group(0))
            if len(d)==12 and d.startswith("91"):d=d[2:]
            elif len(d)==11 and d.startswith("0"):d=d[1:]
            if len(d)==10 and d[0] in "6789" and d not in seen:seen.append(d)
    return seen
def money_to_inr(num:float,unit:Optional[str])->float:
    u=(unit or "").lower()
    if u in ("cr","crore","crores"):return num*10_000_000
    if u in ("l","lac","lacs","lakh","lakhs"):return num*100_000
    if u in ("k","thousand"):return num*1_000
    return num
def fingerprint(*parts:Any)->str:
    return hashlib.sha1("|".join(norm(x) for x in parts).encode()).hexdigest()
def load_json(name:str):
    return json.loads((Path(__file__).parent/"config"/name).read_text(encoding="utf-8"))
def money_label(v):
    if v is None:return ""
    x=float(v)
    if x>=10_000_000:return f"₹{x/10_000_000:.2f} Cr"
    if x>=100_000:return f"₹{x/100_000:.2f} L"
    return f"₹{x:,.0f}"
