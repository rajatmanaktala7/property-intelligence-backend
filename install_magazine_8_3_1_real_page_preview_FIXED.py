from pathlib import Path
from datetime import datetime
import py_compile, shutil

ROOT=Path(__file__).resolve().parent
F=ROOT/"alliance_magazine_fresh_v822.py"
stamp=datetime.now().strftime("%Y%m%d-%H%M%S")
bak=F.with_name(F.name+f".before-8.3.1-fixed-{stamp}.bak")
shutil.copy2(F,bak)

try:
    s=F.read_text(encoding="utf-8")

    old="""                item['has_properties_array']=isinstance(raw,list)
                item['record_count']=len(raw) if isinstance(raw,list) else 0
                item['top_level_keys']=sorted([str(k) for k in data.keys()])[:20] if isinstance(data,dict) else []
                item['result']='OK' if isinstance(raw,list) else 'JSON_SCHEMA_MISMATCH'
"""
    new="""                item['has_properties_array']=isinstance(raw,list)
                item['record_count']=len(raw) if isinstance(raw,list) else 0
                item['top_level_keys']=sorted([str(k) for k in data.keys()])[:20] if isinstance(data,dict) else []
                preview=[]
                if isinstance(raw,list):
                    for rec in raw[:10]:
                        if isinstance(rec,dict):
                            preview.append({
                                'section_heading':rec.get('section_heading'),
                                'original_description':rec.get('original_description') or rec.get('raw_line'),
                                'exact_address':rec.get('exact_address') or rec.get('address'),
                                'locality':rec.get('locality'),
                                'city':rec.get('city'),
                                'property_type':rec.get('property_type'),
                                'transaction_type':rec.get('transaction_type'),
                                'area_value':rec.get('area_value'),
                                'area_unit':rec.get('area_unit'),
                                'floor':rec.get('floor'),
                                'amount_raw':rec.get('amount_raw'),
                                'contact_name':rec.get('contact_name'),
                                'contact_number':rec.get('contact_number'),
                                'extraction_confidence':rec.get('extraction_confidence')
                            })
                item['preview']=preview
                item['result']='OK' if isinstance(raw,list) else 'JSON_SCHEMA_MISMATCH'
"""
    if old not in s:
        raise RuntimeError("Exact 8.3.0 diagnostic block not found; no changes made.")
    s=s.replace(old,new,1)

    s=s.replace("VERSION='8.3.0-GROQ-VISION-WATERFALL'",
                "VERSION='8.3.1-GROQ-REAL-PAGE-PREVIEW'",1)
    s=s.replace("Fresh Magazine PDF Database · CRE OS 8.3.0",
                "Fresh Magazine PDF Database · CRE OS 8.3.1",1)

    F.write_text(s,encoding="utf-8")
    py_compile.compile(str(F),doraise=True)
    py_compile.compile(str(ROOT/"production_entrypoint.py"),doraise=True)

    print("CRE OS 8.3.1 FIXED installed successfully.")
    print("Only real-page diagnostic preview changed.")
    print("Stored PDF, database records, checkpoints, Dockerfile and startup untouched.")
except Exception:
    shutil.copy2(bak,F)
    print("FAILED - original restored safely.")
    raise
