from pathlib import Path
from datetime import datetime
import shutil

ROOT=Path(__file__).resolve().parent
MOD=ROOT/'alliance_magazine_line_strip_v635.py'
APP=ROOT/'app.py'

def chk(s,n):
    compile(s,n,'exec')

def main():
    if not MOD.exists():
        raise SystemExit('alliance_magazine_line_strip_v635.py not found')

    src=MOD.read_text(encoding='utf-8')
    if '6.3.5-ALLIANCE-MAGAZINE-LINE-STRIP-SEGMENTATION-REPAIR' not in src and '6.3.5.1-ALLIANCE-MAGAZINE-LINE-STRIP-SEGMENTATION-REPAIR-NO-NUMPY' not in src:
        raise SystemExit('Unexpected v635 module version. Refusing to overwrite.')

    backup=ROOT/f"alliance_magazine_line_strip_v635-before-6351-{datetime.now().strftime('%Y%m%d-%H%M%S')}.py"
    shutil.copy2(MOD,backup)

    src=src.replace('import numpy as np\n','')
    src=src.replace('VERSION="6.3.5-ALLIANCE-MAGAZINE-LINE-STRIP-SEGMENTATION-REPAIR"',
                    'VERSION="6.3.5.1-ALLIANCE-MAGAZINE-LINE-STRIP-SEGMENTATION-REPAIR-NO-NUMPY"')
    src=src.replace('MODE="LOCK_199_FROM_631_DETERMINISTIC_HORIZONTAL_TEXT_LINE_SEGMENTATION_CONTACT_SHEET_EXACT_STRIP_VERIFY_NO_FRESH_EXAM"',
                    'MODE="SAME_635_LOGIC_PURE_PIL_PIXEL_PROJECTION_NO_NUMPY_RUNTIME_IMPORT_FIX"')

    start=src.index('def _right_text_boundary(gray):')
    end=src.index('\ndef segment_lines(img_bytes):', start)
    new_right = '''def _right_text_boundary(gray):
    w,h=gray.size
    pix=gray.load()
    y0=max(0,int(h*.14));y1=min(h,int(h*.91))
    span=max(1,y1-y0)
    start=int(w*.72)
    active=[]
    for x in range(start,w):
        dark=0
        for y in range(y0,y1):
            if pix[x,y] < 165:
                dark += 1
        if dark/span > .18:
            active.append(x)
    if len(active)>=20:
        first=min(active)
        return max(int(w*.60),first-22)
    return w-38
'''
    src=src[:start]+new_right+src[end+1:]

    start=src.index('def segment_lines(img_bytes):')
    anchor='\n    merged=[]'
    mid=src.index(anchor,start)
    new_seg_head = '''def segment_lines(img_bytes):
    im=Image.open(io.BytesIO(img_bytes)).convert("RGB")
    gray=im.convert("L")
    w,h=gray.size
    x0=max(35,int(w*.045));x1=_right_text_boundary(gray)
    y0=max(145,int(h*.125));y1=min(h-90,int(h*.925))
    pix=gray.load()
    mask=[]
    for y in range(y0,y1):
        dark=0
        for x in range(x0,x1):
            if pix[x,y] < 170:
                dark += 1
        mask.append(dark > 12)

    raw=[];s=None
    for i,v in enumerate(mask):
        if v and s is None:s=i
        if s is not None and (not v or i==len(mask)-1):
            e=i if not v else i+1
            height=e-s
            if 3<=height<=20:raw.append((y0+s,y0+e))
            s=None
'''
    src=src[:start]+new_seg_head+src[mid:]

    chk(src,str(MOD))
    MOD.write_text(src,encoding='utf-8')

    app=APP.read_text(encoding='utf-8')
    marker='FOUNDATION_6_3_5_MAGAZINE_LINE_STRIP_SEGMENTATION_REPAIR'
    if marker not in app:
        raise SystemExit('6.3.5 app registration marker missing; refusing blind patch')
    chk(app,str(APP))

    print('FOUNDATION 6.3.5.1 INSTALLED')
    print('Cause fixed: v635 imported numpy but requirements.txt does not include numpy.')
    print('Hotfix removes numpy and uses Pillow/Python pixel scans.')
    print('No route, exam, truth, Gold, Champion, canonical or training semantics changed.')
    print('Dashboard remains: /property-brain/magazine-line-strip-v635')
    print('Backup:',backup)

if __name__=='__main__':
    main()
