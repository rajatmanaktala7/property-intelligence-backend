from pathlib import Path
from datetime import datetime
import shutil, py_compile

ROOT=Path(__file__).resolve().parent
GW=ROOT/'alliance_magazine_safe_gateway_v660.py'
FR=ROOT/'alliance_magazine_fresh_v822.py'
stamp=datetime.now().strftime('%Y%m%d-%H%M%S')
bg=GW.with_name(GW.name+'.before-8.3.4-fixed-'+stamp+'.bak')
bf=FR.with_name(FR.name+'.before-8.3.4-fixed-'+stamp+'.bak')
shutil.copy2(GW,bg); shutil.copy2(FR,bf)

try:
    g=GW.read_text(encoding='utf-8')
    f=FR.read_text(encoding='utf-8')

    if "8.3.3-ALLIANCE-MAGAZINE-LOSSLESS-CAPTURE" not in g:
        raise RuntimeError('Gateway is not the expected 8.3.3 baseline')
    if "8.3.3-LOSSLESS-PROPERTY-CAPTURE" not in f:
        raise RuntimeError('Fresh module is not the expected 8.3.3 baseline')

    # Robust JSON extraction for provider replies with fences or leading/trailing prose.
    a=g.index('def _json_text(s):')
    b=g.index('\ndef _norm_ref',a)
    json_parser='''def _json_text(s):
    s=(s or "").strip()
    s=re.sub(r"^```(?:json)?\\s*","",s,flags=re.I)
    s=re.sub(r"\\s*```$","",s)
    try:return json.loads(s)
    except Exception:pass
    for start,c0 in enumerate(s):
        if c0 not in "{[":continue
        opener=c0;closer="}" if opener=="{" else "]"
        depth=0;quoted=False;esc=False
        for i in range(start,len(s)):
            c=s[i]
            if quoted:
                if esc:esc=False
                elif c=="\\\\":esc=True
                elif c=='"':quoted=False
                continue
            if c=='"':quoted=True;continue
            if c==opener:depth+=1
            elif c==closer:
                depth-=1
                if depth==0:
                    try:return json.loads(s[start:i+1])
                    except Exception:break
    raise ValueError("No valid JSON object/array found in provider response")
'''
    g=g[:a]+json_parser+g[b:]
    g=g.replace('"max_completion_tokens":900','"max_completion_tokens":450',1)
    g=g.replace('VERSION="8.3.3-ALLIANCE-MAGAZINE-LOSSLESS-CAPTURE"','VERSION="8.3.4-ALLIANCE-MAGAZINE-DENSE-REGION-CAPTURE"',1)

    # Four overlapping vertical regions so dense classified pages do not depend on one huge response.
    marker='def _gateway_extract(gw,jpg):'
    if marker not in f: raise RuntimeError('_gateway_extract marker not found')
    helper='''def _dense_regions(page):
    rect=page.rect
    w=float(rect.width);h=float(rect.height)
    overlap=w*0.025
    cuts=[0.0,0.25,0.50,0.75,1.0]
    out=[];scale=PDF_RENDER_DPI/72.0
    for n in range(4):
        x0=max(0.0,w*cuts[n]-overlap);x1=min(w,w*cuts[n+1]+overlap)
        clip=fitz.Rect(x0,0,x1,h)
        jpg=page.get_pixmap(matrix=fitz.Matrix(scale,scale),clip=clip,alpha=False).tobytes('jpeg')
        out.append((n+1,jpg))
    return out

def _row_key(x):
    raw=re.sub(r'\\s+',' ',str(getattr(x,'original_description','') or '')).strip().upper()
    return re.sub(r'[^A-Z0-9]+','',raw)

def _merge_region_rows(groups):
    out=[];seen=set()
    for rows in groups:
        for x in rows:
            k=_row_key(x)
            if not k or k in seen:continue
            seen.add(k);out.append(x)
    return out

def _extract_dense_page(gw,page):
    groups=[];meta={'status':'OK','regions':[]}
    for region_no,jpg in _dense_regions(page):
        rows,rmeta=_gateway_extract(gw,jpg)
        meta['regions'].append({'region':region_no,'status':rmeta.get('status'),'provider':rmeta.get('provider'),'records':None if rows is None else len(rows)})
        if rows is None:
            if not groups:
                return None,{'status':rmeta.get('status','VISION_PROVIDER_UNAVAILABLE'),'regions':meta['regions']}
            continue
        groups.append(rows)
    merged=_merge_region_rows(groups)
    meta['records']=len(merged);meta['provider']='REGION_WATERFALL'
    return merged,meta

'''
    f=f.replace(marker,helper+marker,1)

    old='''        for i in range(start,pages):
            page=doc.load_page(i);scale=PDF_RENDER_DPI/72.0
            jpg=page.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False).tobytes('jpeg')
            rows,meta=_gateway_extract(gw,jpg)
            if rows is None:'''
    new='''        for i in range(start,pages):
            page=doc.load_page(i)
            rows,meta=_extract_dense_page(gw,page)
            if rows is None:'''
    if old not in f: raise RuntimeError('Production extraction block not found')
    f=f.replace(old,new,1)

    # Exact 8.3.3 diagnostic signature is req before page.
    sig='def real_page_test(upload_id:str,req:Request,page:int=Query(1,ge=1)):'
    if sig not in f:
        raise RuntimeError('Exact real_page_test signature not found')
    f=f.replace(sig,'def real_page_test(upload_id:str,req:Request,page:int=Query(1,ge=1),dense:int=Query(0,ge=0,le=1)):',1)

    # Replace the exact render block inside the diagnostic.
    needle='''        doc=fitz.open(stream=pdf,filetype='pdf')
        try:
            if page>len(doc): raise HTTPException(400,f'Page out of range: {page}/{len(doc)}')
            p=doc.load_page(page-1)
            scale=PDF_RENDER_DPI/72.0
            jpg=p.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False).tobytes('jpeg')
        finally:
            doc.close()

        gw=safe_gateway.ProviderGateway()
        results=[]'''
    repl='''        doc=fitz.open(stream=pdf,filetype='pdf')
        if page>len(doc):
            doc.close()
            raise HTTPException(400,f'Page out of range: {page}/{len(doc)}')
        p=doc.load_page(page-1)

        if dense:
            gw=safe_gateway.ProviderGateway()
            gw.max_calls=int(os.getenv("ALLIANCE_MAGAZINE_V823_MAX_CALLS","1000"))
            try:
                rows,meta=_extract_dense_page(gw,p)
            finally:
                doc.close()
            preview=[]
            for x in (rows or [])[:80]:
                ok,reason=_property_purity(x)
                preview.append({'accepted':ok,'purity_reason':reason,'original_description':x.original_description,'exact_address':x.exact_address,'locality':x.locality,'property_type':x.property_type,'transaction_type':x.transaction_type,'area_value':x.area_value,'area_unit':x.area_unit,'floor':x.floor,'amount_raw':x.amount_raw,'contact_name':x.contact_name,'contact_number':x.contact_number})
            return {'status':'OK' if rows is not None else 'PROVIDER_UNAVAILABLE','version':'8.3.4','mode':'DENSE_4_REGION','page':page,'region_results':meta.get('regions',[]),'merged_record_count':len(rows or []),'preview':preview,'note':'Dense canary only. No records written and no checkpoint advanced.'}

        try:
            scale=PDF_RENDER_DPI/72.0
            jpg=p.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False).tobytes('jpeg')
        finally:
            doc.close()

        gw=safe_gateway.ProviderGateway()
        results=[]'''
    if needle not in f:
        raise RuntimeError('Exact real-page render block not found')
    f=f.replace(needle,repl,1)

    f=f.replace("VERSION='8.3.3-LOSSLESS-PROPERTY-CAPTURE'","VERSION='8.3.4-DENSE-REGION-CAPTURE'",1)
    f=f.replace('Fresh Magazine PDF Database · CRE OS 8.3.3','Fresh Magazine PDF Database · CRE OS 8.3.4',1)
    f=f.replace("'version':'8.3.3',\n            'page':page,","'version':'8.3.4',\n            'page':page,",1)

    GW.write_text(g,encoding='utf-8')
    FR.write_text(f,encoding='utf-8')
    py_compile.compile(str(GW),doraise=True)
    py_compile.compile(str(FR),doraise=True)
    py_compile.compile(str(ROOT/'production_entrypoint.py'),doraise=True)

    print('CRE OS 8.3.4 FIXED installed successfully.')
    print('Dense pages extract as 4 overlapping vertical regions.')
    print('OpenRouter JSON recovery hardened; Groq per-region output cap set to 450.')
    print('Dense canary available with ?page=23&dense=1.')
    print('Stored PDF, existing records, checkpoints and startup untouched.')
except Exception:
    shutil.copy2(bg,GW);shutil.copy2(bf,FR)
    print('FAILED - originals restored safely.')
    raise
