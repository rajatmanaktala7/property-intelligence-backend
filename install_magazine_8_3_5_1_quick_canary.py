from pathlib import Path
from datetime import datetime
import shutil, py_compile

ROOT=Path(__file__).resolve().parent
FR=ROOT/'alliance_magazine_fresh_v822.py'
stamp=datetime.now().strftime('%Y%m%d-%H%M%S')
backup=FR.with_name(FR.name+'.before-8.3.5.1-'+stamp+'.bak')
shutil.copy2(FR,backup)

try:
    f=FR.read_text(encoding='utf-8')
    if "VERSION='8.3.5-LOSSLESS-REGION-CAPTURE'" not in f:
        raise RuntimeError('Expected CRE OS 8.3.5 baseline not found')

    f=f.replace("VERSION='8.3.5-LOSSLESS-REGION-CAPTURE'",
                "VERSION='8.3.5.1-QUICK-LOSSLESS-CANARY'",1)

    anchor='\ndef _extract_lossless_page(gw,page):'
    pos=f.index(anchor)
    quick='''\ndef _extract_lossless_page_quick(gw,page):
    groups=[]; region_results=[]; failed=[]
    for region in _lossless_regions(page):
        rows,rmeta=_gateway_extract(gw,region['jpg'])
        item={
            'region':region['region'],'column':region['column'],'band':region['band'],
            'bbox':region['bbox'],'status':rmeta.get('status'),
            'provider':rmeta.get('provider'),'records':None if rows is None else len(rows),
            'attempts':1
        }
        region_results.append(item)
        if rows is None:
            failed.append(region['region'])
        else:
            groups.append((region,rows))
    merged,evidence=_merge_lossless_rows(groups)
    return merged,{
        'status':'OK' if not failed else 'PARTIAL_DIAGNOSTIC',
        'failed_regions':failed,'regions':region_results,'records':len(merged),
        'provider':'QUICK_6_REGION_CANARY','evidence':evidence
    }

'''
    f=f[:pos]+quick+f[pos:]

    old='rows,meta=_extract_lossless_page(gw,p)'
    if old not in f:
        raise RuntimeError('8.3.5 diagnostic extraction anchor not found')
    f=f.replace(old,'rows,meta=_extract_lossless_page_quick(gw,p)',1)

    oldret="""            return {'status':'OK' if rows is not None else 'REGION_INCOMPLETE','version':'8.3.5','mode':'LOSSLESS_6_REGION','page':page,'region_results':meta.get('regions',[]),'failed_regions':meta.get('failed_regions',[]),'merged_record_count':len(rows or []),'complete_line_35plus_count':len(complete_rows),'phone_preserved_count':len(with_phone),'preview':preview,'note':'8.3.5 lossless canary only. All required regions must succeed. No records written and no checkpoint advanced.'}"""
    newret="""            return {'status':meta.get('status','UNKNOWN'),'version':'8.3.5.1','mode':'QUICK_LOSSLESS_6_REGION','page':page,'region_results':meta.get('regions',[]),'failed_regions':meta.get('failed_regions',[]),'merged_record_count':len(rows or []),'complete_line_35plus_count':len(complete_rows),'phone_preserved_count':len(with_phone),'preview':preview,'note':'8.3.5.1 quick canary: one attempt per region, no retry sleeps, partial results allowed for diagnosis only. No records written and no checkpoint advanced. Production Resume still requires all 6 regions to succeed.'}"""
    if oldret not in f:
        raise RuntimeError('8.3.5 diagnostic response anchor not found')
    f=f.replace(oldret,newret,1)

    f=f.replace("'version':'8.3.5',\n            'page':page,",
                "'version':'8.3.5.1',\n            'page':page,",1)
    f=f.replace('Fresh Magazine PDF Database · CRE OS 8.3.5',
                'Fresh Magazine PDF Database · CRE OS 8.3.5.1',1)

    FR.write_text(f,encoding='utf-8')
    py_compile.compile(str(FR),doraise=True)
    py_compile.compile(str(ROOT/'alliance_magazine_safe_gateway_v660.py'),doraise=True)
    py_compile.compile(str(ROOT/'production_entrypoint.py'),doraise=True)

    print('CRE OS 8.3.5.1 installed successfully.')
    print('Browser canary now uses one attempt per each of 6 regions with no 12/30 second retry sleeps.')
    print('Partial region results are returned for diagnosis instead of causing a long blocking request.')
    print('Production Resume safety is unchanged: all 6 regions must succeed before save/checkpoint.')
    print('Stored September PDF, records and checkpoint were not modified.')
except Exception:
    shutil.copy2(backup,FR)
    print('FAILED - original 8.3.5 file restored safely.')
    raise
