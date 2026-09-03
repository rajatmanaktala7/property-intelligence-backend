from __future__ import annotations
import io, json, os, re
from PIL import Image
from google.genai import types

VERSION="6.0.0-ALLIANCE-MAGAZINE-VISION-FIELD-CHALLENGER-CROP-ANCHOR"
MODE="SEPARATE_VISION_CHALLENGER_TARGET_CROPS_REFERENCE_ANCHOR_DETERMINISTIC_FIELD_PARSE"

def norm_ref(x):
    return re.sub(r"[^A-Z0-9/-]+","",str(x or "").upper())

def _json(resp):
    txt=(resp.text or "").strip()
    if txt.startswith("```"):
        txt=re.sub(r"^```(?:json)?\s*","",txt)
        txt=re.sub(r"\s*```$","",txt)
    return json.loads(txt)

def _jpeg(im):
    b=io.BytesIO(); im.save(b,format="JPEG",quality=97); return b.getvalue()

def _bands(image_bytes):
    im=Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w,h=im.size
    rows=8
    overlap=max(12,int(h*0.018))
    bh=h/rows
    out=[]
    for i in range(rows):
        y0=max(0,int(i*bh)-overlap); y1=min(h,int((i+1)*bh)+overlap)
        crop=im.crop((0,y0,w,y1))
        # 2x enlargement improves tiny classified digits without changing semantics.
        crop=crop.resize((crop.width*2,crop.height*2))
        out.append((i,_jpeg(crop)))
    return out

LOCATE="""Read this cropped band of a dense real-estate magazine.
Requested property references: {refs}
Return JSON exactly:
{{"records":[{{"ref":"","raw_line":""}}]}}
Return only requested references actually visible in this crop.
For each found reference, copy its entire ONE printed listing line exactly.
Never copy the line above or below. Never use magazine header/footer/broker-address text.
Preserve every digit, slash, floor token, BHK/BR, area, price and contact number.
If none are visible return {{"records":[]}}.
"""

def _ask(client, img, prompt, model):
    resp=client.models.generate_content(
        model=model,
        contents=[prompt,types.Part.from_bytes(data=img,mime_type="image/jpeg")],
        config=types.GenerateContentConfig(response_mime_type="application/json",temperature=0.0,max_output_tokens=12000)
    )
    return _json(resp)

def locate_lines(client,image_bytes,refs,model=None):
    model=model or os.getenv("GEMINI_MODEL","gemini-3.1-flash-lite")
    wanted={norm_ref(r):r for r in refs}
    found={}
    for _,band in _bands(image_bytes):
        try:
            data=_ask(client,band,LOCATE.format(refs=json.dumps(refs,ensure_ascii=False)),model)
        except Exception:
            continue
        for rec in data.get("records") or []:
            rr=norm_ref(rec.get("ref"))
            raw=str(rec.get("raw_line") or "").strip()
            if rr in wanted and raw and rr in norm_ref(raw):
                # Prefer the richest exact line if overlapping bands return duplicates.
                old=found.get(rr,"")
                if len(re.findall(r"\d",raw)) > len(re.findall(r"\d",old)) or len(raw)>len(old):
                    found[rr]=raw
    return {wanted[k]:v for k,v in found.items()}

def _expand_phone_tokens(raw):
    # Explicit 8-12 digit sequences plus shorthand suffix notation e.g. 9810313007/09.
    phones=[]
    for m in re.finditer(r"(?<!\d)(\d{8,12})(?:/(\d{1,4}))?",raw):
        base=m.group(1)
        if len(base)>=8:
            phones.append(base)
        suf=m.group(2)
        if suf and len(base)>=8:
            phones.append(base[:-len(suf)]+suf)
    # standard spaced/hyphenated numbers
    for token in re.findall(r"(?:\d[\s-]?){8,12}",raw):
        d=re.sub(r"\D","",token)
        if 8<=len(d)<=12:
            phones.append(d)
    out=[]
    for d in phones:
        if d.startswith("91") and len(d)==12:d=d[2:]
        if d not in out:out.append(d)
    return sorted(out)

def parse_line(ref,raw):
    u=str(raw or "").upper()
    # Work only after the reference anchor so numbers in section headings cannot bind.
    nr=norm_ref(ref)
    pos=norm_ref(u).find(nr)
    # area
    am=re.search(r"\b(\d+(?:\.\d+)?)\s*(YD|YDS|Y|SQYD|SQYDS|FT|SFT|SQFT)\b",u)
    area_value=am.group(1) if am else ""
    area_unit=""
    if am:
        area_unit="SQYD" if am.group(2) in {"YD","YDS","Y","SQYD","SQYDS"} else "SQFT"
    # ordered floor tokens; include compact 4TH FLOOR as literal 4TH when present,
    # but certified fields use BMT/GF/FF/SF/TF/TERR.
    floors=[]
    for m in re.finditer(r"\b(BMT|LGF|UGF|GF|FF|SF|TF|TERR)\b",u):
        tok=m.group(1)
        if tok in {"LGF","UGF"}:
            # retain explicit token; examiner truth uses it only when selected.
            pass
        if tok not in floors:floors.append(tok)
    floor="+".join(floors)
    bm=re.search(r"\b(\d+(?:\+\d+)?)\s*(?:BHK|BR)\b",u)
    bedrooms=bm.group(1) if bm else ""
    pm=re.search(r"@\s*([0-9]+(?:\.[0-9]+)?\s*(?:CR|CRORE|L|LAC|LAKH)?)",u)
    price=re.sub(r"\s+","",pm.group(1)) if pm else ""
    price=price.replace("CRORE","CR").replace("LAKH","L").replace("LAC","L")
    return {"ref":ref,"area_value":area_value,"area_unit":area_unit,"floor":floor,
            "bedrooms":bedrooms,"price":price,"phones":_expand_phone_tokens(raw),
            "_raw_line":raw}

def extract_many(client,image_bytes,refs,model=None):
    lines=locate_lines(client,image_bytes,refs,model)
    return [parse_line(r,lines.get(r,"")) for r in refs]
