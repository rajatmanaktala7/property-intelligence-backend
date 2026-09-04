from pathlib import Path
from datetime import datetime
import shutil, py_compile

ROOT=Path(__file__).resolve().parent
FR=ROOT/'alliance_magazine_fresh_v822.py'
stamp=datetime.now().strftime('%Y%m%d-%H%M%S')
backup=FR.with_name(FR.name+'.before-8.3.5-'+stamp+'.bak')
shutil.copy2(FR,backup)

try:
    f=FR.read_text(encoding='utf-8')

    if "VERSION='8.3.4-DENSE-REGION-CAPTURE'" not in f:
        raise RuntimeError('Expected CRE OS 8.3.4 baseline was not found')

    f=f.replace('import hashlib, html, json, math, os, re, uuid',
                'import hashlib, html, json, math, os, re, time, uuid',1)
    f=f.replace("VERSION='8.3.4-DENSE-REGION-CAPTURE'",
                "VERSION='8.3.5-LOSSLESS-REGION-CAPTURE'",1)

    prompt_anchor="9C. A genuine classified property row may be short. Missing price or missing area does NOT make it an advertisement.\n10. extraction_confidence is 0-100."
    prompt_repl='''9C. A genuine classified property row may be short. Missing price or missing area does NOT make it an advertisement.
9D. LOSSLESS REGION RULE: original_description must contain the COMPLETE visible printed property row from its first character through its final visible character. Never shorten, summarize, ellipsize or stop after area/floor.
9E. Preserve every visible phone number, bracketed contact name, price/amount, floor code and trailing qualifier that belongs to that row.
9F. If the crop cuts a property row at its left or right edge and the full row is not visible, OMIT that edge-cut row from that crop. An overlapping neighboring crop will capture it completely.
9G. Do not duplicate the same row merely because it appears in an overlap.
10. extraction_confidence is 0-100.'''
    if prompt_anchor not in f:
        raise RuntimeError('8.3.5 prompt anchor not found')
    f=f.replace(prompt_anchor,prompt_repl,1)

    old_original="            original=re.sub(r'\\s+',' ',x.original_description or '').strip()"
    new_original="""            original=x.original_description if x.original_description is not None else ''
            if not isinstance(original,str): original=str(original)
            original=original.rstrip('\\r\\n')"""
    if old_original not in f:
        raise RuntimeError('Original-description save anchor not found')
    f=f.replace(old_original,new_original,1)

    a=f.index('def _dense_regions(page):')
    b=f.index('\ndef _gateway_extract(gw,jpg):',a)
    block='''def _lossless_regions(page):
    rect=page.rect
    w=float(rect.width); h=float(rect.height)
    xbands=[(0.00,0.42),(0.29,0.71),(0.58,1.00)]
    ybands=[(0.00,0.56),(0.44,1.00)]
    out=[]; scale=PDF_RENDER_DPI/72.0; n=0
    for yi,(y0f,y1f) in enumerate(ybands,1):
        for xi,(x0f,x1f) in enumerate(xbands,1):
            n+=1
            clip=fitz.Rect(w*x0f,h*y0f,w*x1f,h*y1f)
            jpg=page.get_pixmap(matrix=fitz.Matrix(scale,scale),clip=clip,alpha=False).tobytes('jpeg')
            out.append({
                'region':n,'column':xi,'band':yi,
                'bbox':[round(x0f,4),round(y0f,4),round(x1f,4),round(y1f,4)],
                'jpg':jpg
            })
    return out

def _row_key(x):
    raw=str(getattr(x,'original_description','') or '').upper()
    raw=re.sub(r'\\s+',' ',raw).strip()
    return re.sub(r'[^A-Z0-9]+','',raw)

def _row_quality(x):
    raw=str(getattr(x,'original_description','') or '')
    score=len(raw)
    if re.search(r'(?<!\\d)[6-9]\\d{9}(?!\\d)',re.sub(r'[\\s-]','',raw)): score+=80
    if getattr(x,'contact_number',None): score+=60
    if getattr(x,'amount_raw',None): score+=30
    if getattr(x,'exact_address',None): score+=20
    return score

def _near_duplicate_key(x):
    raw=re.sub(r'\\s+',' ',str(getattr(x,'original_description','') or '')).strip().upper()
    return re.sub(r'[^A-Z0-9]+','',raw)[:34]

def _merge_lossless_rows(region_groups):
    exact={}
    for region,rows in region_groups:
        for x in rows:
            k=_row_key(x)
            if not k: continue
            prev=exact.get(k)
            if prev is None or _row_quality(x)>_row_quality(prev[1]):
                exact[k]=(region,x)

    chosen={}
    for region,x in exact.values():
        k=_near_duplicate_key(x)
        if len(k)<18:
            k=_row_key(x)
        prev=chosen.get(k)
        if prev is None or _row_quality(x)>_row_quality(prev[1]):
            chosen[k]=(region,x)

    rows=[]; evidence={}
    for region,x in chosen.values():
        rows.append(x)
        evidence[_row_key(x)]={
            'region':region['region'],'column':region['column'],'band':region['band'],
            'bbox':region['bbox']
        }
    return rows,evidence

def _region_extract_with_retry(gw,region,max_attempts=3):
    last_meta={'status':'NOT_ATTEMPTED'}
    delays=[0,12,30]
    active_gw=gw
    for attempt in range(max_attempts):
        if delays[attempt]:
            time.sleep(delays[attempt])
        rows,meta=_gateway_extract(active_gw,region['jpg'])
        last_meta=meta or {}
        if rows is not None:
            return rows,last_meta,attempt+1
        if attempt+1<max_attempts:
            active_gw=safe_gateway.ProviderGateway()
            active_gw.max_calls=int(os.getenv("ALLIANCE_MAGAZINE_V823_MAX_CALLS","1000"))
    return None,last_meta,max_attempts

def _extract_lossless_page(gw,page):
    groups=[]; region_results=[]; failed=[]
    for region in _lossless_regions(page):
        rows,rmeta,attempts=_region_extract_with_retry(gw,region)
        item={
            'region':region['region'],'column':region['column'],'band':region['band'],
            'bbox':region['bbox'],'status':rmeta.get('status'),
            'provider':rmeta.get('provider'),'records':None if rows is None else len(rows),
            'attempts':attempts
        }
        region_results.append(item)
        if rows is None:
            failed.append(region['region'])
        else:
            groups.append((region,rows))

    if failed:
        return None,{
            'status':'REGION_INCOMPLETE',
            'failed_regions':failed,
            'regions':region_results,
            'provider':'REGION_WATERFALL'
        }

    merged,evidence=_merge_lossless_rows(groups)
    return merged,{
        'status':'OK','regions':region_results,'records':len(merged),
        'provider':'LOSSLESS_6_REGION','evidence':evidence
    }

'''
    f=f[:a]+block+f[b:]

    if 'rows,meta=_extract_dense_page(gw,page)' not in f:
        raise RuntimeError('Production 8.3.4 dense-call anchor not found')
    f=f.replace('rows,meta=_extract_dense_page(gw,page)',
                'rows,meta=_extract_lossless_page(gw,page)',1)

    old_msg='                msg="AI provider unavailable. The PDF is safe and extraction can resume from page "+str(i)+"."'
    new_msg='                msg="Magazine page "+str(i+1)+" is incomplete because one or more required regions failed. No records from this page were saved and the page checkpoint was not advanced."'
    if old_msg not in f:
        raise RuntimeError('Provider-unavailable message anchor not found')
    f=f.replace(old_msg,new_msg,1)

    old_raw="              raw=json.dumps(x.model_dump(),ensure_ascii=False))"
    new_raw="              raw=json.dumps(dict(x.model_dump(),_source_region=(meta_evidence or {}).get(_row_key(x))),ensure_ascii=False))"
    if old_raw not in f:
        raise RuntimeError('raw_json anchor not found')
    f=f.replace(old_raw,new_raw,1)

    if 'def _save(e,uid,page,rows):' not in f:
        raise RuntimeError('_save signature anchor not found')
    f=f.replace('def _save(e,uid,page,rows):',
                'def _save(e,uid,page,rows,meta_evidence=None):',1)

    if 'm,r=_save(e,uid,i+1,rows);tm+=m;tr+=r' not in f:
        raise RuntimeError('_save production call anchor not found')
    f=f.replace('m,r=_save(e,uid,i+1,rows);tm+=m;tr+=r',
                "m,r=_save(e,uid,i+1,rows,meta.get('evidence'));tm+=m;tr+=r",1)

    if 'rows,meta=_extract_dense_page(gw,p)' not in f:
        raise RuntimeError('Dense diagnostic anchor not found')
    f=f.replace('rows,meta=_extract_dense_page(gw,p)',
                'rows,meta=_extract_lossless_page(gw,p)',1)

    old_return="""            return {'status':'OK' if rows is not None else 'PROVIDER_UNAVAILABLE','version':'8.3.4','mode':'DENSE_4_REGION','page':page,'region_results':meta.get('regions',[]),'merged_record_count':len(rows or []),'preview':preview,'note':'Dense canary only. No records written and no checkpoint advanced.'}"""
    new_return="""            complete_rows=[x for x in (rows or []) if len(str(x.original_description or '').strip())>=35]
            with_phone=[x for x in (rows or []) if x.contact_number or re.search(r'(?<!\\d)[6-9]\\d{9}(?!\\d)',re.sub(r'[\\s-]','',str(x.original_description or '')))]
            return {'status':'OK' if rows is not None else 'REGION_INCOMPLETE','version':'8.3.5','mode':'LOSSLESS_6_REGION','page':page,'region_results':meta.get('regions',[]),'failed_regions':meta.get('failed_regions',[]),'merged_record_count':len(rows or []),'complete_line_35plus_count':len(complete_rows),'phone_preserved_count':len(with_phone),'preview':preview,'note':'8.3.5 lossless canary only. All required regions must succeed. No records written and no checkpoint advanced.'}"""
    if old_return not in f:
        raise RuntimeError('8.3.4 diagnostic return anchor not found')
    f=f.replace(old_return,new_return,1)

    f=f.replace("'version':'8.3.4',\n            'page':page,",
                "'version':'8.3.5',\n            'page':page,",1)
    f=f.replace('Fresh Magazine PDF Database · CRE OS 8.3.4',
                'Fresh Magazine PDF Database · CRE OS 8.3.5',1)

    FR.write_text(f,encoding='utf-8')
    py_compile.compile(str(FR),doraise=True)
    py_compile.compile(str(ROOT/'alliance_magazine_safe_gateway_v660.py'),doraise=True)
    py_compile.compile(str(ROOT/'production_entrypoint.py'),doraise=True)

    print('CRE OS 8.3.5 installed successfully.')
    print('Lossless layout: 3 broad overlapping columns x 2 overlapping horizontal bands = 6 regions.')
    print('Full-line prompt added; edge-cut rows are omitted from a crop and recovered by overlap.')
    print('Each failed region retries independently up to 3 attempts.')
    print('A page is never saved or checkpointed unless all 6 required regions succeed.')
    print('Original model transcription is stored without whitespace normalization.')
    print('Crop provenance is stored inside raw_json for each staged record.')
    print('September PDF, existing records and current checkpoint were not modified by installation.')
except Exception:
    shutil.copy2(backup,FR)
    print('FAILED - original 8.3.4 file restored safely.')
    raise
