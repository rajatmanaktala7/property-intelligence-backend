from pathlib import Path
from datetime import datetime
import shutil, py_compile, re

ROOT=Path(__file__).resolve().parent
GW=ROOT/'alliance_magazine_safe_gateway_v660.py'
FR=ROOT/'alliance_magazine_fresh_v822.py'
stamp=datetime.now().strftime('%Y%m%d-%H%M%S')
bg=GW.with_name(GW.name+'.before-8.3.2-final-'+stamp+'.bak')
bf=FR.with_name(FR.name+'.before-8.3.2-final-'+stamp+'.bak')
shutil.copy2(GW,bg); shutil.copy2(FR,bf)

try:
    g=GW.read_text(encoding='utf-8')
    f=FR.read_text(encoding='utf-8')

    # Groq output cap.
    if '\"max_completion_tokens\":900' not in g:
        needle='\"response_format\":{\"type\":\"json_object\"},\"messages\"'
        if needle not in g:
            raise RuntimeError('Groq payload marker not found')
        g=g.replace(needle,'\"max_completion_tokens\":900,'+needle,1)

    # Prompt hardening using stable rule-6 marker only.
    if '5A. DO NOT return broker' not in f:
        marker='6. transaction_type should be RENT/LEASE/SALE only when visible or clearly inherited from a visible heading.'
        if marker not in f:
            raise RuntimeError('Prompt rule 6 marker not found')
        extra=('5A. DO NOT return broker, realtor, agency or company profile advertisements as property records.\n'
               '5B. A broker office address, agency office address, email, website, multiple agent names/phones, or generic SALE-PURCHASE-RENTING-COLLABORATION language describes the broker business, not a property.\n'
               '5C. Return a record only when the page visibly describes a specific property offering. A contact card with no property-specific area, floor, price, unit or property description is NOT a property listing.\n')
        f=f.replace(marker,extra+marker,1)

    purity="def _property_purity(x):\n    raw=re.sub(r'\\s+',' ',str(getattr(x,'original_description','') or '')).strip()\n    up=raw.upper()\n    address=str(getattr(x,'exact_address',None) or '').strip()\n    ptype=str(getattr(x,'property_type',None) or '').strip()\n    floor=str(getattr(x,'floor',None) or '').strip()\n    amount=str(getattr(x,'amount_raw',None) or '').strip()\n    area=getattr(x,'area_value',None)\n    unit=str(getattr(x,'area_unit',None) or '').strip()\n    evidence=0\n    if area is not None and unit: evidence+=2\n    if floor: evidence+=1\n    if amount: evidence+=1\n    if ptype: evidence+=1\n    if address and evidence: evidence+=1\n    terms=['REALTORS','PROPERTY DEALER','REAL ESTATE CONSULTANT','SALE-PURCHASE-RENTING','SALE | PURCHASE | RENTING','SALE PURCHASE RENTING','COLLABORATION DEALS','COLLABORATION IN','SOUTH DELHI EXPERTS','WE SPL. IN','EMAIL :','EMAIL:']\n    hits=sum(1 for t in terms if t in up)\n    compact=re.sub(r'[\\s-]','',raw)\n    phones=len(set(re.findall(r'(?<!\\d)[6-9]\\d{9}(?!\\d)',compact)))\n    has_email=bool(re.search(r'\\b[\\w.+-]+@[\\w.-]+\\.[A-Za-z]{2,}\\b',raw))\n    contact_card=(hits>=2) or (has_email and phones>=2 and hits>=1)\n    if contact_card and evidence<2: return False,'BROKER_OR_AGENCY_AD'\n    if address and evidence==0 and hits>=1: return False,'AGENCY_OFFICE_ADDRESS_NOT_PROPERTY'\n    if evidence==0 and not address: return False,'NO_PROPERTY_SPECIFIC_EVIDENCE'\n    return True,'PROPERTY_LISTING'\n\n"
    if 'def _property_purity(' not in f:
        marker='def _save(e,uid,page,rows):'
        if marker not in f:
            raise RuntimeError('_save marker not found')
        f=f.replace(marker,purity+marker,1)

    # Save guard.
    if 'accepted,purity_reason=_property_purity(x)' not in f:
        old="""        for x in rows:
            original=re.sub(r'\\s+',' ',x.original_description or '').strip()
            if not original: continue"""
        new="""        for x in rows:
            accepted,purity_reason=_property_purity(x)
            if not accepted: continue
            original=re.sub(r'\\s+',' ',x.original_description or '').strip()
            if not original: continue"""
        if old not in f:
            raise RuntimeError('_save loop exact block not found')
        f=f.replace(old,new,1)

    # Parsed-row purity guard.
    if 'accepted,purity_reason=_property_purity(row)' not in f:
        old='        try:rows.append(FreshProperty.model_validate(item))\n        except Exception:continue'
        new=('        try:\n'
             '            row=FreshProperty.model_validate(item)\n'
             '            accepted,purity_reason=_property_purity(row)\n'
             '            if accepted: rows.append(row)\n'
             '        except Exception:continue')
        if old not in f:
            raise RuntimeError('gateway append exact block not found')
        f=f.replace(old,new,1)

    # Diagnostic purity preview and counts.
    if 'accepted_property_count' not in f:
        marker="                item['preview']=preview\n"
        if marker not in f:
            raise RuntimeError('diagnostic preview marker not found')
        block="""                purity_preview=[]
                accepted_count=0
                if isinstance(raw,list):
                    for rec in raw[:10]:
                        if not isinstance(rec,dict): continue
                        try:
                            candidate=dict(rec)
                            if 'original_description' not in candidate and candidate.get('raw_line'):
                                candidate['original_description']=candidate.get('raw_line')
                            obj=FreshProperty.model_validate(candidate)
                            ok,reason=_property_purity(obj)
                            if ok: accepted_count+=1
                            purity_preview.append({'accepted':ok,'purity_reason':reason,'original_description':obj.original_description,'exact_address':obj.exact_address,'locality':obj.locality,'property_type':obj.property_type,'transaction_type':obj.transaction_type,'area_value':obj.area_value,'area_unit':obj.area_unit,'floor':obj.floor,'amount_raw':obj.amount_raw,'contact_name':obj.contact_name,'contact_number':obj.contact_number})
                        except Exception as exc:
                            purity_preview.append({'accepted':False,'purity_reason':'SCHEMA_REJECTED','detail':str(exc)[:300]})
                item['purity_preview']=purity_preview
                item['accepted_property_count']=accepted_count
                item['rejected_by_purity_count']=(len(raw)-accepted_count) if isinstance(raw,list) else 0
"""
        f=f.replace(marker,marker+block,1)

    # Cosmetic versions only if old literals exist.
    f=f.replace("VERSION='8.3.1-GROQ-REAL-PAGE-PREVIEW'","VERSION='8.3.2-PROPERTY-PURITY-GROQ-900'",1)
    f=f.replace('Fresh Magazine PDF Database · CRE OS 8.3.1','Fresh Magazine PDF Database · CRE OS 8.3.2',1)
    f=f.replace("'version':'8.2.9',\n            'page':page,","'version':'8.3.2',\n            'page':page,",1)

    GW.write_text(g,encoding='utf-8')
    FR.write_text(f,encoding='utf-8')
    py_compile.compile(str(GW),doraise=True)
    py_compile.compile(str(FR),doraise=True)
    py_compile.compile(str(ROOT/'production_entrypoint.py'),doraise=True)

    print('CRE OS 8.3.2 FINAL installed successfully.')
    print('Property Purity Guard + Groq 900-token cap + purity diagnostics enabled.')
    print('Stored PDF, database records, checkpoints and startup untouched.')
except Exception:
    shutil.copy2(bg,GW)
    shutil.copy2(bf,FR)
    print('FAILED - originals restored safely.')
    raise
