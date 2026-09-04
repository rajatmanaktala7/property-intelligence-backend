from pathlib import Path
import re, shutil, time, py_compile
ROOT=Path(__file__).resolve().parent
f=ROOT/'alliance_magazine_fresh_v822.py'
if not f.exists(): raise SystemExit('alliance_magazine_fresh_v822.py not found')
original=f.read_text(encoding='utf-8')
if '/api/magazine-fresh/resume/{upload_id}' not in original:
    raise SystemExit('SAFETY STOP: 8.2.3 resume endpoint missing. No change made.')
stamp=time.strftime('%Y%m%d-%H%M%S')
bak=ROOT/f'alliance_magazine_fresh_v822.py.before-v824-fixed-{stamp}.bak'
shutil.copy2(f,bak)
page_html='<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">\n<title>Alliance Magazine Resume</title><style>body{font-family:Arial;background:#f4f7fb;margin:0;color:#172437}.top{background:#102235;color:white;padding:20px}.wrap{max-width:1180px;margin:auto;padding:20px}.card{background:white;padding:18px;border-radius:14px;margin-bottom:14px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}.num{font-size:28px;font-weight:800}.muted{color:#66758a}.btn{background:#1266f1;color:white;border:0;border-radius:9px;padding:11px 18px;font-weight:700;cursor:pointer}.good{color:#16833c}.bad{color:#bd2f2f}a{color:#1266f1;text-decoration:none}table{width:100%;border-collapse:collapse}td,th{padding:8px;border-bottom:1px solid #e5eaf0;text-align:left}</style></head>\n<body><div class="top"><b>Fresh Magazine PDF Database · CRE OS 8.2.4</b><br><small>Stored PDF recovery · resumable extraction · provider failover</small></div>\n<div class="wrap"><div class="card"><a href="/workspace">← Dashboard</a> · <a href="/magazine-fresh/records">New Magazine Records</a></div>\n<div class="card"><h2>Current / Previous Magazine</h2><p id="name" class="muted">Checking stored jobs...</p><div id="stats" class="grid"></div><p id="state" class="muted"></p><button id="resume" class="btn" style="display:none">Resume Extraction</button></div>\n<div class="card"><h3>Recent Magazine Jobs</h3><div id="jobs">Loading...</div></div>\n<div class="card"><h3>New Magazine</h3><p class="muted">For a genuinely new magazine, use the existing upload page after the current database is validated.</p></div></div>\n<script>\nlet active=null,timer=null;\nfunction esc(x){return String(x??\'\').replace(/[&<>"\']/g,m=>({\'&\':\'&amp;\',\'<\':\'&lt;\',\'>\':\'&gt;\',\'"\':\'&quot;\',"\'":\'&#39;\'}[m]))}\nfunction render(d){active=d.upload_id;name.innerHTML=\'<b>\'+esc(d.filename)+\'</b> · \'+esc(d.status);stats.innerHTML=\'<div class="card"><div class="muted">Pages</div><div class="num">\'+d.processed_pages+\'/\'+d.page_count+\'</div></div><div class="card"><div class="muted">Records</div><div class="num">\'+d.created_records+\'</div></div><div class="card"><div class="muted">Needs review</div><div class="num">\'+d.review_records+\'</div></div><div class="card"><div class="muted">Status</div><b>\'+esc(d.status)+\'</b></div>\';state.textContent=d.error_message||\'Ready.\';resume.style.display=[\'ERROR\',\'PAUSED_ERROR\',\'WAITING_FOR_PROVIDER\',\'STORED\'].includes(d.status)?\'inline-block\':\'none\'}\nasync function load(){let r=await fetch(\'/api/magazine-fresh/latest\');if(!r.ok){state.textContent=\'Unable to read stored jobs.\';return}let d=await r.json();if(d.latest)render(d.latest);else{name.textContent=\'No stored Magazine PDF found.\'}jobs.innerHTML=(d.uploads||[]).length?\'<table><tr><th>PDF</th><th>Status</th><th>Pages</th><th>Records</th></tr>\'+d.uploads.map(x=>\'<tr><td>\'+esc(x.filename)+\'</td><td>\'+esc(x.status)+\'</td><td>\'+x.processed_pages+\'/\'+x.page_count+\'</td><td>\'+x.created_records+\'</td></tr>\').join(\'\')+\'</table>\':\'No previous jobs.\'}\nresume.onclick=async()=>{if(!active)return;resume.disabled=true;let r=await fetch(\'/api/magazine-fresh/resume/\'+active,{method:\'POST\'});let d=await r.json();state.textContent=d.status||d.detail||\'Resume requested\';resume.disabled=false;setTimeout(load,1000)}\nload();timer=setInterval(load,4000);\n</script></body></html>'
page_code='def _page():\n    return '+repr(page_html)+'\n\n'
pat=r'(?ms)^def _page\(\):.*?(?=^def register\(core\):)'
if not re.search(pat,original):
    raise SystemExit('SAFETY STOP: _page function not found. No change made.')
s=re.sub(pat,lambda m:page_code,original,count=1)
latest_code="""    @app.get('/api/magazine-fresh/latest')
    def latest(req:Request):
        _login(core,req)
        with e.connect() as c:
            rows=c.execute(text(\"\"\"SELECT upload_id::text,filename,status,page_count,processed_pages,created_records,
              review_records,error_message,created_at,completed_at
              FROM pi_magazine_fresh_uploads ORDER BY created_at DESC LIMIT 10\"\"\")).mappings().all()
        return {'status':'OK','version':'8.2.4','latest':dict(rows[0]) if rows else None,'uploads':[dict(x) for x in rows]}

"""
needle="    @app.get('/api/magazine-fresh/status/{upload_id}')"
if '/api/magazine-fresh/latest' not in s:
    if needle not in s:
        raise SystemExit('SAFETY STOP: status route marker missing. No change made.')
    s=s.replace(needle,latest_code+needle,1)
s=s.replace("VERSION='8.2.3-RESILIENT-MAGAZINE-PDF'","VERSION='8.2.4-RESUME-DASHBOARD-MAGAZINE-PDF'",1)
f.write_text(s,encoding='utf-8')
try:
    py_compile.compile(str(f),doraise=True)
    py_compile.compile(str(ROOT/'alliance_magazine_safe_gateway_v660.py'),doraise=True)
    py_compile.compile(str(ROOT/'production_entrypoint.py'),doraise=True)
except Exception:
    shutil.copy2(bak,f)
    print('COMPILE FAILED - original file restored:',bak)
    raise
print('PASS: Alliance CRE OS 8.2.4 fixed Resume Dashboard installed')
print('Backup:',bak)
