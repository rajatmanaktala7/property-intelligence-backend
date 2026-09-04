from pathlib import Path
from datetime import datetime
import shutil, py_compile

ROOT=Path(__file__).resolve().parent
GW=ROOT/"alliance_magazine_safe_gateway_v660.py"
FR=ROOT/"alliance_magazine_fresh_v822.py"
stamp=datetime.now().strftime("%Y%m%d-%H%M%S")
bg=GW.with_name(GW.name+".before-8.3.3-"+stamp+".bak")
bf=FR.with_name(FR.name+".before-8.3.3-"+stamp+".bak")
shutil.copy2(GW,bg); shutil.copy2(FR,bf)

try:
    g=GW.read_text(encoding="utf-8")
    f=FR.read_text(encoding="utf-8")

    # Prefer OpenRouter before Groq after Gemini.
    a=g.index('        # Groq vision fallback using Groq OpenAI-compatible HTTPS API.')
    b=g.index('    def _available(self,p):',a)
    provider_block="""        # OpenRouter first for dense magazine pages; page-23 canary showed materially better row coverage.
        ork=(os.getenv("OPENROUTER_API_KEY") or "").strip()
        orm=(os.getenv("OPENROUTER_VISION_MODEL") or "").strip()
        if ork and orm:
            self.providers.append({
                "kind":"openrouter","label":f"OPENROUTER:{orm}",
                "api_key":ork,"model":orm
            })

        # Groq remains a vision fallback / verifier.
        gk=(os.getenv("GROQ_API_KEY") or "").strip()
        gm=(os.getenv("GROQ_VISION_MODEL") or "qwen/qwen3.6-27b").strip()
        if gk and gm:
            self.providers.append({"kind":"groq","label":f"GROQ:{gm}","api_key":gk,"model":gm})

"""
    g=g[:a]+provider_block+g[b:]
    g=g.replace('VERSION="8.3.0-ALLIANCE-MAGAZINE-GROQ-VISION-WATERFALL"','VERSION="8.3.3-ALLIANCE-MAGAZINE-LOSSLESS-CAPTURE"',1)

    f=f.replace("    area_value: Optional[float]=None","    area_value: Optional[object]=None",1)

    marker="9. If unclear, return null rather than guessing.\n10. extraction_confidence is 0-100."
    replacement="9. If unclear, return null rather than guessing.\n9A. CAPTURE FIRST, PARSE SECOND: if a genuine property row has a complex area such as 2200FT+200FT GARAGE, preserve the complete expression in original_description and return area_value as the visible expression if a single numeric value is impossible.\n9B. Never omit a genuine property row merely because area, amount, floor, contact, address or another structured field is missing or complex. Keep the row and use null for fields that cannot be safely normalized.\n9C. A genuine classified property row may be short. Missing price or missing area does NOT make it an advertisement.\n10. extraction_confidence is 0-100."
    if marker not in f: raise RuntimeError("Prompt rules marker not found")
    f=f.replace(marker,replacement,1)

    old="""def _sqft(value,unit):
    if value is None: return None
    try: v=float(value)
    except: return None
    u=str(unit or '').upper().replace(' ','')
    return {'SQFT':v,'SQYD':v*9,'SQM':v*10.7639104167,'ACRE':v*43560}.get(u)
"""
    new="""def _area_number(value):
    if value is None: return None
    if isinstance(value,(int,float)): return float(value)
    s=str(value).strip().replace(',','')
    if re.fullmatch(r'\\d+(?:\\.\\d+)?',s):
        try:return float(s)
        except:return None
    return None

def _sqft(value,unit):
    v=_area_number(value)
    if v is None: return None
    u=str(unit or '').upper().replace(' ','')
    return {'SQFT':v,'SQYD':v*9,'SQM':v*10.7639104167,'ACRE':v*43560}.get(u)
"""
    if old not in f: raise RuntimeError("_sqft block not found")
    f=f.replace(old,new,1)

    start=f.index("def _property_purity(x):")
    end=f.index("\ndef _save(e,uid,page,rows):",start)
    purity="""def _property_purity(x):
    raw=re.sub(r'\\s+',' ',str(getattr(x,'original_description','') or '')).strip()
    if not raw:return False,'EMPTY_ROW'
    up=raw.upper()
    area=getattr(x,'area_value',None)
    floor=str(getattr(x,'floor',None) or '').strip()
    amount=str(getattr(x,'amount_raw',None) or '').strip()
    address=str(getattr(x,'exact_address',None) or '').strip()
    locality=str(getattr(x,'locality',None) or '').strip()
    ptype=str(getattr(x,'property_type',None) or '').strip()
    tx=str(getattr(x,'transaction_type',None) or '').strip()
    property_signal=bool(area is not None or floor or amount or ptype or tx)
    if re.search(r'\\b(?:BHK|BR|GF|FF|SF|TF|BMT|BASEMENT|FLOOR|FLR|APT|APARTMENT|FLAT|KOTHI|PLOT|SHOP|OFFICE|SHOWROOM|SQFT|SQYD|SQM|ACRE|\\d+\\s*Y\\b|\\d+\\s*FT\\b)\\b',up):
        property_signal=True
    if address and locality and address.upper()!=locality.upper(): property_signal=True
    agency_terms=['REALTORS','PROPERTY DEALER','REAL ESTATE CONSULTANT','REALTY','SALE-PURCHASE-RENTING','SALE | PURCHASE | RENTING','SALE PURCHASE RENTING','COLLABORATION DEALS','COLLABORATION IN','WE SPL. IN','WE SPECIALISE IN','WE SPECIALIZE IN']
    agency_hits=sum(1 for t in agency_terms if t in up)
    compact=re.sub(r'[\\s-]','',raw)
    phones=len(set(re.findall(r'(?<!\\d)[6-9]\\d{9}(?!\\d)',compact)))
    has_email=bool(re.search(r'\\b[\\w.+-]+@[\\w.-]+\\.[A-Za-z]{2,}\\b',raw))
    has_web=bool(re.search(r'\\b(?:WWW\\.|HTTPS?://)',up))
    if not property_signal and (agency_hits>=2 or (agency_hits>=1 and (has_email or has_web or phones>=2))):
        return False,'BROKER_OR_AGENCY_AD'
    if not property_signal:return True,'PROPERTY_CANDIDATE_NEEDS_REVIEW'
    return True,'PROPERTY_LISTING'
"""
    f=f[:start]+purity+f[end:]

    old="""            nr=not x.exact_address or not x.contact_number or x.extraction_confidence is None or float(x.extraction_confidence)<80
            p=dict(record_id='MAGNEW-'+uuid.uuid4().hex[:16].upper(),uid=uid,page=page,section=x.section_heading,
              original=original,address=x.exact_address,locality=x.locality,city=x.city,ptype=x.property_type,tx=x.transaction_type,
              area=x.area_value,unit=x.area_unit,sqft=_sqft(x.area_value,x.area_unit),floor=x.floor,amount=x.amount_raw,"""
    new="""            area_num=_area_number(x.area_value)
            complex_area=(x.area_value is not None and area_num is None)
            nr=complex_area or not x.exact_address or not x.contact_number or x.extraction_confidence is None or float(x.extraction_confidence)<80
            p=dict(record_id='MAGNEW-'+uuid.uuid4().hex[:16].upper(),uid=uid,page=page,section=x.section_heading,
              original=original,address=x.exact_address,locality=x.locality,city=x.city,ptype=x.property_type,tx=x.transaction_type,
              area=area_num,unit=x.area_unit,sqft=_sqft(x.area_value,x.area_unit),floor=x.floor,amount=x.amount_raw,"""
    if old not in f: raise RuntimeError("_save area block not found")
    f=f.replace(old,new,1)

    old="pdf=bytes(row[0]);start=int(row[1] or 0);doc=fitz.open(stream=pdf,filetype='pdf');pages=len(doc)"
    new="""pdf=bytes(row[0]);start=int(row[1] or 0);doc=fitz.open(stream=pdf,filetype='pdf');pages=len(doc)
        requested_start=max(1,int(os.getenv("MAGAZINE_START_PAGE","23")))
        if start < requested_start-1:
            start=requested_start-1
            with e.begin() as c:
                c.execute(text("UPDATE pi_magazine_fresh_uploads SET processed_pages=:d,error_message=:x WHERE upload_id=CAST(:u AS UUID)"),{'d':start,'x':"Skipped non-property front matter through page "+str(start),'u':uid})"""
    if old not in f: raise RuntimeError("processor start block not found")
    f=f.replace(old,new,1)

    f=f.replace("VERSION='8.3.2-PROPERTY-PURITY-GROQ-900'","VERSION='8.3.3-LOSSLESS-PROPERTY-CAPTURE'",1)
    f=f.replace("Fresh Magazine PDF Database · CRE OS 8.3.2","Fresh Magazine PDF Database · CRE OS 8.3.3",1)
    f=f.replace("Gemini -> Groq Vision -> OpenRouter","Gemini -> OpenRouter -> Groq Vision",1)
    f=f.replace("'version':'8.3.2',\n            'page':page,","'version':'8.3.3',\n            'page':page,",1)

    GW.write_text(g,encoding="utf-8"); FR.write_text(f,encoding="utf-8")
    py_compile.compile(str(GW),doraise=True); py_compile.compile(str(FR),doraise=True); py_compile.compile(str(ROOT/"production_entrypoint.py"),doraise=True)
    print("CRE OS 8.3.3 installed successfully.")
    print("Lossless capture enabled: uncertain/complex genuine rows go to review, not deletion.")
    print("September extraction starts at PDF page 23 by default.")
    print("Provider order after Gemini: OpenRouter -> Groq.")
    print("Stored PDF and existing records untouched.")
except Exception:
    shutil.copy2(bg,GW); shutil.copy2(bf,FR)
    print("FAILED - originals restored safely.")
    raise
