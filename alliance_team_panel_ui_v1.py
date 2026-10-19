"""Alliance Team Panel V1 - pages and API routes.

Very simple, phone-first screens for a non-technical team.
All routes live under /team-panel and need the normal Alliance login.
"""
from __future__ import annotations

import json
import threading
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional

from fastapi import Body, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import text

import alliance_team_panel_v1 as tp

P = tp.PREFIX
_e = tp._e

CSS = """
:root{--bg:#f4f6f8;--card:#fff;--ink:#14212b;--muted:#5b6b78;--line:#e2e8ee;--green:#128c4a;--green2:#e8f6ee;
--blue:#1f5fbf;--blue2:#e9f0fb;--red:#c62828;--red2:#fdecec;--amber:#a15c00;--amber2:#fff6e5;--grey:#6b7785}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif}
a{color:var(--blue);text-decoration:none}
.wrap{max-width:760px;margin:0 auto;padding:12px 14px 80px}
.top{display:flex;align-items:center;justify-content:space-between;gap:8px;margin:4px 0 12px}
.top h1{font-size:21px;margin:0}
.crumb{font-size:14px;color:var(--muted)}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;margin:10px 0}
.big{display:block;color:inherit}
.big h2{margin:0 0 4px;font-size:19px}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.sp{justify-content:space-between}
.muted{color:var(--muted);font-size:14px}
.chip{display:inline-block;border-radius:999px;padding:2px 10px;font-size:13px;font-weight:600;background:#eef1f4;color:#33414d}
.chip.g{background:var(--green2);color:var(--green)}.chip.r{background:var(--red2);color:var(--red)}
.chip.b{background:var(--blue2);color:var(--blue)}.chip.a{background:var(--amber2);color:var(--amber)}
.bar{height:10px;background:#e6ebef;border-radius:99px;overflow:hidden;margin-top:8px}
.bar i{display:block;height:100%;background:var(--green)}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;min-height:44px;padding:10px 14px;border-radius:11px;
border:0;font-size:15px;font-weight:700;cursor:pointer;background:var(--blue);color:#fff}
.btn.g{background:var(--green)}.btn.r{background:var(--red)}.btn.l{background:#e9edf1;color:#22313d}
.btn.o{background:#fff;color:var(--ink);border:1px solid var(--line)}
.btn.s{min-height:36px;padding:6px 10px;font-size:13px}
.btn[disabled]{opacity:.45;cursor:not-allowed}
.full{width:100%}
textarea,input,select{width:100%;font:inherit;padding:11px;border:1px solid #cfd8e0;border-radius:10px;background:#fff}
textarea{min-height:120px}
.alert{border-radius:12px;padding:12px 14px;margin:10px 0;font-weight:600}
.alert.r{background:var(--red2);color:var(--red)}.alert.g{background:var(--green2);color:var(--green)}
.alert.a{background:var(--amber2);color:var(--amber)}
.secret{background:#fffbea;border:1px dashed #e0b84a;border-radius:12px;padding:12px;margin-top:10px}
.contact{border-top:1px solid var(--line);padding:10px 0}
.contact:first-child{border-top:0}
.done{opacity:.55}
.num{font-size:28px;font-weight:800}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.grid .card{margin:0;text-align:center;padding:10px}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{border-bottom:1px solid var(--line);padding:8px 6px;text-align:left;vertical-align:top}
th{background:#f0f3f6}
.tbl{overflow-x:auto}
pre{white-space:pre-wrap;font:inherit;margin:0}
.toast{position:fixed;left:50%;bottom:18px;transform:translateX(-50%);background:#14212b;color:#fff;padding:10px 16px;
border-radius:10px;display:none;z-index:9;font-weight:600}
@media (max-width:480px){.grid{grid-template-columns:1fr 1fr 1fr}.num{font-size:22px}}
"""

JS = r"""
function toast(t){var x=document.getElementById('toast');if(!x)return;x.textContent=t;x.style.display='block';clearTimeout(window._tt);window._tt=setTimeout(function(){x.style.display='none'},2200)}
async function api(url,body){var r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});
var d={};try{d=await r.json()}catch(e){}; if(!r.ok){toast((d&&d.detail)||'Something went wrong');throw new Error(d.detail||'error')} return d}
function shortName(n){n=(n||'').split(/\s[-|–(]\s?|,/)[0].trim().slice(0,40);return n||'there'}
function copyText(t){navigator.clipboard.writeText(t).then(function(){toast('Copied')})}
var MEMBER=document.body.getAttribute('data-member');
async function checkDue(){ if(!MEMBER)return; try{var r=await fetch('/team-panel/api/m/'+MEMBER+'/due');if(!r.ok)return;var d=await r.json();
 var b=document.getElementById('duebadge'); if(b){b.textContent=d.due_now; b.style.display=d.due_now>0?'inline-block':'none'}
 document.title=(d.due_now>0?'('+d.due_now+') ':'')+document.title.replace(/^\(\d+\)\s/,'');
 var last=parseInt(sessionStorage.getItem('tp_last_due')||'0');
 if(d.due_now>last && window.Notification && Notification.permission==='granted'){new Notification('Follow-up due',{body:d.due_now+' lead(s) are waiting for your follow-up.'})}
 try{sessionStorage.setItem('tp_last_due',String(d.due_now))}catch(e){}
}catch(e){}}
function enableReminders(){ if(!window.Notification){toast('This phone does not support reminders in browser');return}
 Notification.requestPermission().then(function(p){toast(p==='granted'?'Reminders ON':'Reminders blocked')})}
if(MEMBER){checkDue();setInterval(checkDue,60000)}
"""


def page(title: str, body: str, member: str = "", back: str = "") -> HTMLResponse:
    back_html = f"<a class='crumb' href='{_e(back)}'>← Back</a>" if back else "<span></span>"
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(title)}</title><style>{CSS}</style></head>
<body data-member="{_e(member)}"><div class="wrap">
<div class="row sp" style="margin-bottom:4px">{back_html}<a class="crumb" href="/alliance/primary">Alliance home</a></div>
{body}</div><div id="toast" class="toast"></div><script>{JS}</script></body></html>"""
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


def _pct(a: int, b: int) -> int:
    return int(round(100 * a / b)) if b else 0


def _bar(a: int, b: int) -> str:
    return f"<div class='bar'><i style='width:{_pct(a, b)}%'></i></div>"


# ---------------------------------------------------------------- data access

def _member_or_404(key: str) -> Dict[str, Any]:
    cfg = tp.members().get(key)
    if not cfg:
        raise HTTPException(404, "Unknown team member")
    return cfg


def _today_summary(engine, member: str, d: date) -> Dict[str, Any]:
    with engine.connect() as c:
        o = c.execute(text("""SELECT COUNT(*) a, COUNT(*) FILTER (WHERE status<>'PENDING') s,
                              COUNT(*) FILTER (WHERE status='REPLIED') r
                              FROM tp_outreach_items_v1 WHERE plan_date=:d AND member=:m"""),
                      {"d": d, "m": member}).mappings().first()
        mt = c.execute(text("""SELECT COUNT(*) a, COUNT(*) FILTER (WHERE status<>'PENDING') s,
                               COUNT(*) FILTER (WHERE status='REPLIED') r
                               FROM tp_match_items_v1 WHERE plan_date=:d AND member=:m"""),
                       {"d": d, "m": member}).mappings().first()
        plan = c.execute(text("SELECT outreach_status, match_status, notes FROM tp_day_plans_v1 WHERE plan_date=:d AND member=:m"),
                         {"d": d, "m": member}).mappings().first()
    return {"o": dict(o), "m": dict(mt), "plan": dict(plan) if plan else None, "due": tp.due_counts(engine, member)}


def _role(core, request: Request) -> Optional[str]:
    try:
        return core.need_login(request)
    except Exception:
        return None


# ---------------------------------------------------------------- mount

def mount(core, app) -> None:
    engine = core.engine

    def need(request: Request) -> str:
        role = _role(core, request)
        if not role:
            raise HTTPException(401, "Login required")
        return role

    def need_admin(request: Request) -> str:
        role = need(request)
        if role != "admin":
            raise HTTPException(403, "Admin login required")
        return role

    def start_bg(fn, *args) -> bool:
        if tp.SCHED.get("running"):
            return False
        threading.Thread(target=fn, args=args, name="team-panel-prepare", daemon=True).start()
        return True

    # ------------------------------------------------ picker
    @app.get(P, response_class=HTMLResponse)
    @app.get(P + "/", response_class=HTMLResponse)
    def picker(request: Request):
        role = _role(core, request)
        if not role:
            return RedirectResponse("/login", 303)
        d = tp.today_ist()
        cards = []
        for key, cfg in tp.members().items():
            s = _today_summary(engine, key, d)
            due = s["due"]["due_now"]
            due_chip = f"<span class='chip r'>{due} follow-up due</span>" if due else "<span class='chip g'>No follow-ups due</span>"
            regions = " + ".join("Delhi NCR" if r == "NCR" else "Goa" for r in cfg.get("regions") or [])
            cards.append(f"""<a class="card big" href="{P}/m/{_e(key)}">
<div class="row sp"><h2>{_e(cfg['name'])}</h2>{due_chip}</div>
<div class="muted">{_e(regions)}</div>
<div class="muted" style="margin-top:6px">WhatsApp {s['o']['s']}/{s['o']['a']} · Matches {s['m']['s']}/{s['m']['a']}</div>
{_bar(int(s['o']['s']) + int(s['m']['s']), int(s['o']['a']) + int(s['m']['a']))}</a>""")
        admin = f"<a class='btn o full' href='{P}/admin'>📊 Owner view: progress & reports</a>" if role == "admin" else ""
        body = f"""<div class="top"><h1>Team Panel</h1><span class="muted">{d.strftime('%a, %d %b')}</span></div>
<p class="muted">Tap your name to see today's work.</p>{''.join(cards)}{admin}"""
        return page("Team Panel", body)

    # ------------------------------------------------ member home
    @app.get(P + "/m/{key}", response_class=HTMLResponse)
    def member_home(key: str, request: Request):
        if not _role(core, request):
            return RedirectResponse("/login", 303)
        cfg = _member_or_404(key)
        d = tp.today_ist()
        s = _today_summary(engine, key, d)
        due = s["due"]
        hour = tp.now_ist().hour
        hello = "Good morning" if hour < 12 else ("Good afternoon" if hour < 17 else "Good evening")
        alert = ""
        if due["overdue"]:
            alert = f"<div class='alert r'>⚠️ {due['overdue']} follow-up(s) are OVERDUE. Do these first.</div>"
        elif due["due_now"]:
            alert = f"<div class='alert a'>🔔 {due['due_now']} follow-up(s) due now.</div>"
        not_ready = ""
        plan = s["plan"]
        if not plan or (plan.get("outreach_status") != "READY" and not s["o"]["a"]) or (not s["m"]["a"] and plan.get("match_status") != "READY"):
            running = tp.SCHED.get("running")
            not_ready = f"""<div class="card"><b>{'⏳ AI is preparing tasks right now. Refresh in 5 minutes.' if running else 'Some of today’s tasks are not ready yet.'}</b>
<div class="muted">Tasks are prepared automatically every working morning.</div>
<button class="btn l full" style="margin-top:8px" onclick="api('{P}/api/m/{_e(key)}/prepare').then(function(d){{toast(d.message)}})">Prepare my tasks now</button></div>"""
        notes = (plan or {}).get("notes") or {}
        if isinstance(notes, str):
            try:
                notes = json.loads(notes)
            except Exception:
                notes = {}
        short_by = (notes.get("match_scan") or {}).get("short_by") or 0
        body = f"""<div class="top"><h1>{hello}, {_e(cfg.get('short') or cfg['name'])} 👋</h1>
<span class="muted">{d.strftime('%a, %d %b')}</span></div>
{alert}{not_ready}
<p class="muted">Do the tasks in this order:</p>
<a class="card big" href="{P}/m/{_e(key)}/leads"><div class="row sp"><h2>1. Follow-ups</h2>
<span id="duebadge" class="chip r" style="display:{'inline-block' if due['due_now'] else 'none'}">{due['due_now']}</span></div>
<div class="muted">Reply to people who answered. Never miss a lead.</div></a>
<a class="card big" href="{P}/m/{_e(key)}/outreach"><div class="row sp"><h2>2. WhatsApp outreach</h2>
<span class="chip b">{s['o']['s']} / {s['o']['a']} sent</span></div>
<div class="muted">10 batches of outlets. Write message → AI improves → tap Send.</div>{_bar(int(s['o']['s']), int(s['o']['a']))}</a>
<a class="card big" href="{P}/m/{_e(key)}/matches"><div class="row sp"><h2>3. Hot property matches</h2>
<span class="chip b">{s['m']['s']} / {s['m']['a']} sent</span></div>
<div class="muted">Ready messages for brokers. Contact details shown only to you.</div>{_bar(int(s['m']['s']), int(s['m']['a']))}
{f"<div class='muted' style='margin-top:6px'>Only {s['m']['a']} strong matches found today.</div>" if short_by and s['m']['a'] else ''}</a>
<div class="row" style="margin-top:14px"><button class="btn o" onclick="enableReminders()">🔔 Turn on reminders</button>
<span class="muted">Keep this page open to get follow-up alerts.</span></div>"""
        return page(f"{cfg['short'] if 'short' in cfg else cfg['name']} - Today", body, member=key, back=P)

    # ------------------------------------------------ outreach
    @app.get(P + "/m/{key}/outreach", response_class=HTMLResponse)
    def outreach(key: str, request: Request, b: Optional[int] = None):
        if not _role(core, request):
            return RedirectResponse("/login", 303)
        cfg = _member_or_404(key)
        d = tp.today_ist()
        if b:
            return _batch_page(key, cfg, b)
        with engine.connect() as c:
            rows = c.execute(text("""SELECT b.id,b.batch_no,b.segment,b.region,COALESCE(b.final_text,'')<>'' AS ready,
                       COUNT(i.id) AS total, COUNT(i.id) FILTER (WHERE i.status<>'PENDING') AS done,
                       COUNT(i.id) FILTER (WHERE i.status='REPLIED') AS replied
                       FROM tp_outreach_batches_v1 b LEFT JOIN tp_outreach_items_v1 i ON i.batch_id=b.id
                       WHERE b.plan_date=:d AND b.member=:m GROUP BY b.id ORDER BY b.batch_no"""),
                             {"d": d, "m": key}).mappings().all()
        if not rows:
            body = f"""<div class="top"><h1>WhatsApp outreach</h1></div><div class="card">No batches yet for today.
<button class="btn l full" style="margin-top:8px" onclick="api('{P}/api/m/{_e(key)}/prepare').then(function(d){{toast(d.message)}})">Prepare my tasks now</button></div>"""
            return page("Outreach", body, member=key, back=f"{P}/m/{key}")
        tot = sum(int(r["total"]) for r in rows)
        done = sum(int(r["done"]) for r in rows)
        cards = []
        for r in rows:
            chip = "<span class='chip g'>Done ✓</span>" if r["done"] >= r["total"] and r["total"] else (
                "<span class='chip b'>Message ready</span>" if r["ready"] else "<span class='chip a'>Write message</span>")
            cards.append(f"""<a class="card big {'done' if r['done'] >= r['total'] and r['total'] else ''}" href="{P}/m/{_e(key)}/outreach?b={r['id']}">
<div class="row sp"><h2>Batch {r['batch_no']} · {_e(tp.SEGMENT_LABEL.get(r['segment'], r['segment']))}</h2>{chip}</div>
<div class="muted">{_e({'NCR':'Delhi NCR','GOA':'Goa'}.get(r['region'], 'Delhi NCR + Goa'))} · {r['done']}/{r['total']} sent · {r['replied']} replied</div>
{_bar(int(r['done']), int(r['total']))}</a>""")
        body = f"""<div class="top"><h1>WhatsApp outreach</h1><span class="chip b">{done}/{tot}</span></div>
<p class="muted">Open a batch. Write one message. AI makes it better. Then tap <b>Send</b> on each contact.</p>{''.join(cards)}"""
        return page("Outreach", body, member=key, back=f"{P}/m/{key}")

    def _batch_page(key: str, cfg: Dict[str, Any], bid: int) -> HTMLResponse:
        with engine.connect() as c:
            b = c.execute(text("SELECT * FROM tp_outreach_batches_v1 WHERE id=:i AND member=:m"),
                          {"i": bid, "m": key}).mappings().first()
            if not b:
                raise HTTPException(404, "Batch not found")
            items = c.execute(text("SELECT * FROM tp_outreach_items_v1 WHERE batch_id=:i ORDER BY id"), {"i": bid}).mappings().all()
        seg = tp.SEGMENT_LABEL.get(b["segment"], b["segment"])
        final = b["final_text"] or ""
        rows = []
        for it in items:
            st = it["status"]
            chip = {"PENDING": "", "SENT": "<span class='chip b'>Sent</span>", "REPLIED": "<span class='chip g'>Replied → Lead</span>",
                    "NO_WHATSAPP": "<span class='chip'>No WhatsApp</span>", "STOP": "<span class='chip r'>STOP</span>"}.get(st, st)
            rows.append(f"""<div class="contact {'done' if st != 'PENDING' else ''}" id="it{it['id']}" data-id="{it['id']}"
 data-phone="{_e(it['phone10'])}" data-name="{_e(it['name'])}">
<div class="row sp"><b>{_e(it['name'] or 'Outlet')}</b><span class="st">{chip}</span></div>
<div class="muted">{_e(it['location'] or it['city'])}</div>
<div class="row" style="margin-top:6px">
<button class="btn g send" onclick="sendOne(this)" {'disabled' if not final else ''}>Send ▶</button>
<button class="btn l s" onclick="mark(this,'REPLIED')">Replied ✅</button>
<button class="btn l s" onclick="mark(this,'NO_WHATSAPP')">No WhatsApp</button>
<button class="btn l s" onclick="mark(this,'STOP')">Said STOP</button></div></div>""")
        js = """
var BID=%d;
function finalText(){return document.getElementById('final').value.trim()}
async function polish(){var dr=document.getElementById('draft').value.trim(); if(!dr){toast('Write your message first');return}
 var btn=document.getElementById('pbtn'); btn.disabled=true; btn.textContent='AI is improving...';
 try{var d=await api('/team-panel/api/batch/'+BID+'/polish',{draft:dr}); document.getElementById('final').value=d.text;
 document.getElementById('finalbox').style.display='block'; toast(d.ai_status==='AI'?'AI improved your message':'AI busy - basic version ready');
 document.getElementById('finalbox').scrollIntoView({behavior:'smooth'})}finally{btn.disabled=false;btn.textContent='✨ Make it better with AI'}}
async function useIt(){var t=finalText(); if(!t){toast('Message is empty');return} await api('/team-panel/api/batch/'+BID+'/final',{text:t});
 document.querySelectorAll('.send').forEach(function(b){b.disabled=false}); toast('Saved. Now tap Send on each contact.');
 document.getElementById('list').scrollIntoView({behavior:'smooth'})}
async function sendOne(btn){var row=btn.closest('.contact'); var t=finalText(); if(!t){toast('Save your message first');return}
 var msg=t.split('{name}').join(shortName(row.dataset.name)); var url='https://wa.me/91'+row.dataset.phone+'?text='+encodeURIComponent(msg);
 window.open(url,'_blank'); try{await api('/team-panel/api/outreach/'+row.dataset.id+'/status',{status:'SENT'})}catch(e){}
 row.classList.add('done'); row.querySelector('.st').innerHTML="<span class='chip b'>Sent</span>"}
async function mark(btn,st){var row=btn.closest('.contact'); var d=await api('/team-panel/api/outreach/'+row.dataset.id+'/status',{status:st});
 row.classList.add('done'); var lab={REPLIED:"<span class='chip g'>Replied → Lead</span>",NO_WHATSAPP:"<span class='chip'>No WhatsApp</span>",STOP:"<span class='chip r'>STOP</span>"}[st];
 row.querySelector('.st').innerHTML=lab; toast(st==='REPLIED'?'Lead created. Follow it up in Follow-ups.':'Saved')}
""" % bid
        draft = b["draft_text"] or ""
        body = f"""<div class="top"><h1>Batch {b['batch_no']} · {_e(seg)}</h1><span class="chip b">{len(items)} contacts</span></div>
<div class="card"><b>Step 1. Write your message</b>
<div class="muted" style="margin:4px 0 8px">Write simply what you want to say. A sample is filled in. Change it as you like.</div>
<textarea id="draft">{_e(draft)}</textarea>
<button id="pbtn" class="btn full" style="margin-top:8px" onclick="polish()">✨ Make it better with AI</button></div>
<div class="card" id="finalbox" style="display:{'block' if final else 'none'}"><b>Step 2. Check the AI message</b>
<div class="muted" style="margin:4px 0 8px"><code>{{name}}</code> is replaced by each outlet's name automatically.</div>
<textarea id="final">{_e(final)}</textarea>
<button class="btn g full" style="margin-top:8px" onclick="useIt()">✅ Use this message</button></div>
<div class="card" id="list"><b>Step 3. Tap Send on each contact</b>
<div class="muted" style="margin:4px 0 8px">WhatsApp opens with the message ready. Press send there, then come back. If they reply, tap <b>Replied ✅</b>.</div>
{''.join(rows) or '<div class="muted">No contacts.</div>'}</div>
<script>{js}</script>"""
        return page(f"Batch {b['batch_no']}", body, member=key, back=f"{P}/m/{key}/outreach")

    # ------------------------------------------------ matches
    @app.get(P + "/m/{key}/matches", response_class=HTMLResponse)
    def matches(key: str, request: Request):
        if not _role(core, request):
            return RedirectResponse("/login", 303)
        cfg = _member_or_404(key)
        d = tp.today_ist()
        with engine.connect() as c:
            items = c.execute(text("SELECT * FROM tp_match_items_v1 WHERE plan_date=:d AND member=:m ORDER BY slot"),
                              {"d": d, "m": key}).mappings().all()
        cards = []
        for it in items:
            prop = it["property"] if isinstance(it["property"], dict) else json.loads(it["property"] or "{}")
            contacts = it["internal_contacts"] if isinstance(it["internal_contacts"], list) else json.loads(it["internal_contacts"] or "[]")
            clist = "".join(
                f"<div>{_e(str(x.get('label') or 'Contact').replace('_', ' ').title())}: <a href='tel:{_e(tp.phone10(x.get('value')) or x.get('value'))}'><b>{_e(x.get('value'))}</b></a></div>"
                for x in contacts if x.get("value")) or "<div>Contact not captured. Check the source below.</div>"
            st = it["status"]
            chip = {"PENDING": "<span class='chip a'>To send</span>", "SENT": "<span class='chip b'>Sent</span>",
                    "REPLIED": "<span class='chip g'>Broker replied → Lead</span>", "NOT_USEFUL": "<span class='chip'>Not useful</span>"}.get(st, st)
            req_txt = it["requirement_text"] or ""
            cards.append(f"""<div class="card {'done' if st in ('REPLIED','NOT_USEFUL') else ''}" data-id="{it['id']}" data-phone="{_e(it['broker_phone'])}">
<div class="row sp"><h2 style="margin:0;font-size:18px">Match {it['slot']} · {_e(prop.get('location') or '')}</h2><span class="st">{chip}</span></div>
<div class="muted">Score {_e(int(float(it['score'] or 0)))} · {_e(it['bucket'] or '')} · {_e({'NCR':'Delhi NCR','GOA':'Goa'}.get(it['requirement_region'], 'Delhi NCR / Goa'))}</div>
<details style="margin-top:8px"><summary><b>What the broker wants</b> (tap to read)</summary><pre class="muted">{_e(tp.strip_contacts(req_txt)[:900])}</pre></details>
<div style="margin-top:10px"><b>Message to send</b> <span class="muted">(no contact numbers inside)</span></div>
<textarea class="msg">{_e(it['message_text'] or '')}</textarea>
<div class="row" style="margin-top:8px">
<button class="btn g" onclick="sendMatch(this)">Send to broker ▶</button>
<button class="btn l s" onclick="copyText(this.closest('.card').querySelector('.msg').value)">Copy</button>
<button class="btn l s" onclick="rewrite(this)">✨ Rewrite</button></div>
<div class="muted" style="margin-top:6px">Send to: {_e(it['broker_name'] or 'Broker')} · <a href="tel:{_e(it['broker_phone'])}">{_e(it['broker_phone'])}</a></div>
<div class="secret"><b>🔒 Team only - do NOT forward</b>
<div style="margin-top:6px"><b>Property:</b> {_e(prop.get('property_type'))} · {_e(prop.get('transaction'))} · {_e(prop.get('area'))} · {_e(prop.get('price'))}</div>
<div><b>Property ID:</b> {_e(it['property_id'])} · <b>Checked:</b> {_e(prop.get('verification'))}</div>
<div><b>Where it came from:</b> {_e(it['internal_source'])}</div>
<div style="margin-top:6px"><b>Whom to call if the broker likes it:</b></div>{clist}
{f"<div class='muted' style='margin-top:6px'>Why matched: {_e(prop.get('why'))}</div>" if prop.get('why') else ''}
<div style="margin-top:6px"><a href="/alliance/primary/property/{_e(it['property_id'])}/edit">Open full property record →</a></div></div>
<div class="row" style="margin-top:10px">
<button class="btn l s" onclick="markMatch(this,'REPLIED')">Broker replied ✅</button>
<button class="btn l s" onclick="markMatch(this,'NOT_USEFUL')">Not useful</button></div></div>""")
        js = """
async function sendMatch(btn){var card=btn.closest('.card'); var t=card.querySelector('.msg').value.trim(); if(!t){toast('Message is empty');return}
 await api('/team-panel/api/match/'+card.dataset.id+'/message',{text:t});
 window.open('https://wa.me/91'+card.dataset.phone+'?text='+encodeURIComponent(t),'_blank');
 await api('/team-panel/api/match/'+card.dataset.id+'/status',{status:'SENT'}); card.querySelector('.st').innerHTML="<span class='chip b'>Sent</span>"}
async function rewrite(btn){var card=btn.closest('.card'); btn.disabled=true; btn.textContent='Writing...';
 try{var d=await api('/team-panel/api/match/'+card.dataset.id+'/rewrite',{}); card.querySelector('.msg').value=d.text; toast('New message ready')}
 finally{btn.disabled=false;btn.textContent='✨ Rewrite'}}
async function markMatch(btn,st){var card=btn.closest('.card'); await api('/team-panel/api/match/'+card.dataset.id+'/status',{status:st});
 card.classList.add('done'); card.querySelector('.st').innerHTML=st==='REPLIED'?"<span class='chip g'>Broker replied → Lead</span>":"<span class='chip'>Not useful</span>";
 toast(st==='REPLIED'?'Lead created. See Follow-ups.':'Saved')}
"""
        sent = sum(1 for it in items if it["status"] != "PENDING")
        empty = "" if items else f"""<div class="card">No matches yet for today.
<button class="btn l full" style="margin-top:8px" onclick="api('{P}/api/m/{_e(key)}/prepare').then(function(d){{toast(d.message)}})">Prepare my tasks now</button></div>"""
        body = f"""<div class="top"><h1>Hot property matches</h1><span class="chip b">{sent}/{len(items)}</span></div>
<p class="muted">Each card: a broker's requirement and a property that fits. Tap <b>Send to broker</b>. If they like it, call the person in the yellow box.</p>
{empty}{''.join(cards)}<script>{js}</script>"""
        return page("Hot matches", body, member=key, back=f"{P}/m/{key}")

    # ------------------------------------------------ leads / follow-ups
    @app.get(P + "/m/{key}/leads", response_class=HTMLResponse)
    def leads(key: str, request: Request):
        if not _role(core, request):
            return RedirectResponse("/login", 303)
        cfg = _member_or_404(key)
        with engine.connect() as c:
            open_rows = c.execute(text("""SELECT * FROM tp_leads_v1 WHERE member=:m AND status NOT IN ('DEAL_WON','NOT_INTERESTED','LOST')
                       ORDER BY next_followup_at NULLS LAST, id"""), {"m": key}).mappings().all()
            closed = c.execute(text("""SELECT * FROM tp_leads_v1 WHERE member=:m AND status IN ('DEAL_WON','NOT_INTERESTED','LOST')
                       ORDER BY updated_at DESC LIMIT 15"""), {"m": key}).mappings().all()
        now = tp.now_ist()
        due, later = [], []
        for r in open_rows:
            nf = r["next_followup_at"]
            (due if nf is None or nf <= now else later).append(r)
        status_opts = "".join(f"<option value='{k}'>{_e(v)}</option>" for k, v in tp.LEAD_STATUSES)

        def card(r, is_due: bool) -> str:
            nf = r["next_followup_at"]
            when = nf.astimezone(tp.IST).strftime("%a %d %b, %I:%M %p") if nf else "-"
            overdue = nf is not None and nf.astimezone(tp.IST).date() < now.date()
            chip = "<span class='chip r'>OVERDUE</span>" if overdue else ("<span class='chip a'>Due now</span>" if is_due else f"<span class='chip'>{_e(when)}</span>")
            label = dict(tp.LEAD_STATUSES).get(r["status"], r["status"])
            p = r["phone10"] or ""
            wa = f"<a class='btn g s' target='_blank' href='https://wa.me/91{_e(p)}'>WhatsApp</a>" if p else ""
            call = f"<a class='btn s' href='tel:{_e(p)}'>Call</a>" if p else ""
            sel = status_opts.replace(f"value='{r['status']}'", f"value='{r['status']}' selected")
            return f"""<div class="card" data-id="{r['id']}"><div class="row sp"><b>{_e(r['name'] or 'Lead')}</b>{chip}</div>
<div class="muted">{_e(label)} · {_e('Broker' if r['origin']=='MATCH' else tp.SEGMENT_LABEL.get(r['segment'], r['segment'] or ''))} · {_e({'NCR':'Delhi NCR','GOA':'Goa','BOTH':'Delhi NCR / Goa'}.get(r['city'] or '', r['city'] or ''))} · {_e(p)}</div>
<div class="muted" style="margin-top:4px">{_e((r['context'] or '')[:300])}</div>
{f"<pre class='muted' style='margin-top:6px'>{_e(r['notes'][-500:])}</pre>" if r['notes'] else ''}
<div class="row" style="margin-top:8px">{call}{wa}</div>
<details style="margin-top:8px"{' open' if is_due else ''}><summary><b>Update after talking</b></summary>
<div style="margin-top:8px"><select class="lst">{sel}</select></div>
<div style="margin-top:6px"><input class="note" placeholder="What happened? (short note)"></div>
<div class="muted" style="margin-top:8px">Next follow-up:</div>
<div class="row" style="margin-top:4px">
<button class="btn s" onclick="fu(this,'today')">Later today</button>
<button class="btn s" onclick="fu(this,'tomorrow')">Tomorrow</button>
<button class="btn s" onclick="fu(this,'3days')">In 3 days</button>
<button class="btn s" onclick="fu(this,'week')">Next week</button></div>
<div class="row" style="margin-top:6px"><input type="datetime-local" class="when" style="flex:1">
<button class="btn s o" onclick="fu(this,'date')">Set date</button></div>
<button class="btn r s" style="margin-top:8px" onclick="fu(this,'close')">Close lead</button></details></div>"""

        js = """
async function fu(btn,action){var c=btn.closest('.card'); var body={action:action,note:c.querySelector('.note').value,
 status:c.querySelector('.lst').value,when:c.querySelector('.when').value};
 if(action==='date'&&!body.when){toast('Pick a date and time');return}
 if(action==='close'&&['DEAL_WON','NOT_INTERESTED','LOST'].indexOf(body.status)<0){body.status='NOT_INTERESTED'}
 await api('/team-panel/api/lead/'+c.dataset.id+'/followup',body); toast('Saved'); setTimeout(function(){location.reload()},600)}
async function addLead(){var n=document.getElementById('ln').value.trim(),p=document.getElementById('lp').value.trim(),t=document.getElementById('lt').value.trim();
 if(!n||!p){toast('Name and phone needed');return} await api('/team-panel/api/m/'+MEMBER+'/lead',{name:n,phone:p,note:t}); location.reload()}
"""
        body = f"""<div class="top"><h1>Follow-ups</h1><button class="btn o s" onclick="enableReminders()">🔔 Reminders</button></div>
<h3>Do now ({len(due)})</h3>{''.join(card(r, True) for r in due) or "<div class='alert g'>All caught up. 👍</div>"}
<h3>Coming up ({len(later)})</h3>{''.join(card(r, False) for r in later) or "<div class='muted'>Nothing scheduled.</div>"}
<details class="card"><summary><b>+ Add a lead by hand</b> (call or walk-in)</summary>
<div style="margin-top:8px"><input id="ln" placeholder="Name / business"></div>
<div style="margin-top:6px"><input id="lp" placeholder="Mobile number" inputmode="tel"></div>
<div style="margin-top:6px"><input id="lt" placeholder="What do they need?"></div>
<button class="btn full" style="margin-top:8px" onclick="addLead()">Add lead</button></details>
<details class="card"><summary><b>Closed recently ({len(closed)})</b></summary>
{''.join(f"<div class='contact'><b>{_e(r['name'])}</b> · {_e(dict(tp.LEAD_STATUSES).get(r['status']))}</div>" for r in closed)}</details>
<script>{js}</script>"""
        return page("Follow-ups", body, member=key, back=f"{P}/m/{key}")

    # ------------------------------------------------ admin
    @app.get(P + "/admin", response_class=HTMLResponse)
    def admin(request: Request, day: Optional[str] = None, month: Optional[str] = None):
        role = _role(core, request)
        if not role:
            return RedirectResponse("/login", 303)
        if role != "admin":
            return page("Admin only", "<div class='alert r'>Owner view needs the Admin login.</div>", back=P)
        d = date.fromisoformat(day) if day else tp.today_ist()
        mo = month or d.strftime("%Y-%m")
        live = [tp.compute_daily_metrics(engine, d, m) for m in tp.members()]
        trs = []
        for met in live:
            cfg = tp.members()[met["member"]]
            o, mt, l = met["outreach"], met["matches"], met["leads"]
            trs.append(f"""<tr><td><b>{_e(cfg['name'])}</b></td>
<td>{o['sent']}/{o['assigned']}<br><span class='muted'>{o['send_rate']}%</span></td>
<td>{o['replied']}<br><span class='muted'>{o['reply_rate']}%</span></td>
<td>{mt['sent']}/{mt['assigned']}</td><td>{mt['replied']}</td>
<td>{l['new_leads']}</td><td>{l['followups_done']}</td>
<td>{f"<span class='chip r'>{l['overdue_from_before']}</span>" if l['overdue_from_before'] else '0'}</td>
<td>{l['open_leads']}</td></tr>""")
        with engine.connect() as c:
            reps = c.execute(text("""SELECT report_date, COUNT(*) n FROM tp_daily_reports_v1
                                     GROUP BY report_date ORDER BY report_date DESC LIMIT 31""")).mappings().all()
            rev = c.execute(text("SELECT * FROM tp_monthly_reviews_v1 WHERE month=:m"), {"m": mo}).mappings().first()
            months = [r[0] for r in c.execute(text("SELECT month FROM tp_monthly_reviews_v1 ORDER BY month DESC LIMIT 12")).all()]
        rep_links = " ".join(f"<a class='chip b' href='{P}/admin?day={r['report_date']}'>{r['report_date'].strftime('%d %b')}</a>" for r in reps) or "<span class='muted'>No saved reports yet.</span>"
        review_html = (f"<div class='muted'>Generated {rev['generated_at'].astimezone(tp.IST).strftime('%d %b %I:%M %p')} · {_e(rev['ai_status'])}</div>"
                       f"<pre style='margin-top:8px'>{_e(rev['analysis'])}</pre>") if rev else "<div class='muted'>Not generated yet for this month.</div>"
        sched = tp.SCHED
        js = """
async function prep(){var d=await api('/team-panel/api/admin/prepare',{}); toast(d.message)}
async function saveRep(){var d=await api('/team-panel/api/admin/report',{day:document.getElementById('day').value}); toast('Report saved for '+d.saved+' members'); setTimeout(function(){location.reload()},700)}
async function review(){var b=document.getElementById('rvb'); b.disabled=true; b.textContent='AI is analysing...';
 try{await api('/team-panel/api/admin/monthly',{month:document.getElementById('mo').value}); location.reload()}finally{b.disabled=false}}
"""
        body = f"""<div class="top"><h1>Owner view</h1><span class="muted">{d.strftime('%a %d %b %Y')}</span></div>
<div class="card"><form class="row" method="get"><input type="date" id="day" name="day" value="{d.isoformat()}" style="flex:1">
<button class="btn s" type="submit">Show day</button></form>
<div class="tbl" style="margin-top:10px"><table><tr><th>Member</th><th>WA sent</th><th>WA replies</th><th>Matches sent</th>
<th>Match replies</th><th>New leads</th><th>Follow-ups done</th><th>Overdue</th><th>Open leads</th></tr>{''.join(trs)}</table></div>
<div class="row" style="margin-top:10px"><button class="btn l s" onclick="prep()">Prepare today's tasks now</button>
<button class="btn l s" onclick="saveRep()">Save this day's report</button></div></div>
<div class="card"><b>Saved daily reports</b><div class="row" style="margin-top:8px">{rep_links}</div></div>
<div class="card"><div class="row sp"><b>Monthly AI review</b>
<form class="row" method="get"><input name="month" id="mo" value="{_e(mo)}" style="width:120px"><button class="btn s o">Open</button></form></div>
<button id="rvb" class="btn full" style="margin:8px 0" onclick="review()">🤖 Analyse this month</button>{review_html}
<div class="row" style="margin-top:8px">{' '.join(f"<a class='chip' href='{P}/admin?month={_e(x)}'>{_e(x)}</a>" for x in months)}</div></div>
<div class="card muted">Auto plan: {_e(sched.get('plan_time','07:00 IST'))} · Auto report: {_e(sched.get('report_time','20:30 IST'))} ·
Scheduler: {_e(sched.get('status'))} · Working now: {_e(sched.get('running') or 'no')} · AI calls ok/failed: {tp.AI_STATE['ok']}/{tp.AI_STATE['failed']}
{f"<br>Last error: {_e(sched.get('last_error'))}" if sched.get('last_error') else ''}</div>
<script>{js}</script>"""
        return page("Owner view", body, back=P)

    # ------------------------------------------------ APIs
    def _ok(**kw):
        return JSONResponse({"ok": True, **kw})

    @app.get(P + "/api/status")
    def status(request: Request):
        need(request)
        return {"version": tp.VERSION, "scheduler": tp.SCHED, "ai": tp.AI_STATE, "members": list(tp.members())}

    @app.get(P + "/api/m/{key}/due")
    def api_due(key: str, request: Request):
        need(request)
        _member_or_404(key)
        return tp.due_counts(engine, key)

    @app.post(P + "/api/m/{key}/prepare")
    def api_prepare(key: str, request: Request):
        need(request)
        _member_or_404(key)
        started = start_bg(tp.generate_day, core, tp.today_ist(), key)
        return _ok(message="AI started preparing your tasks. Refresh in 5 minutes." if started else "Tasks are already being prepared. Refresh in a few minutes.")

    @app.post(P + "/api/batch/{bid}/polish")
    def api_polish(bid: int, request: Request, payload: Dict[str, Any] = Body(...)):
        need(request)
        draft = str(payload.get("draft") or "")[:2000]
        with engine.connect() as c:
            b = c.execute(text("SELECT member, segment, region FROM tp_outreach_batches_v1 WHERE id=:i"), {"i": bid}).mappings().first()
        if not b:
            raise HTTPException(404, "Batch not found")
        cfg = tp.members().get(b["member"], {})
        out, st = tp.polish_outreach(draft, b["segment"], b["region"], cfg.get("short") or cfg.get("name") or "")
        if not out:
            raise HTTPException(400, "Write your message first")
        with engine.begin() as c:
            c.execute(text("UPDATE tp_outreach_batches_v1 SET draft_text=:d, ai_status=:s, updated_at=NOW() WHERE id=:i"),
                      {"d": draft, "s": st, "i": bid})
        return _ok(text=out, ai_status=st)

    @app.post(P + "/api/batch/{bid}/final")
    def api_final(bid: int, request: Request, payload: Dict[str, Any] = Body(...)):
        need(request)
        t = str(payload.get("text") or "").strip()[:3000]
        if not t:
            raise HTTPException(400, "Message is empty")
        with engine.begin() as c:
            n = c.execute(text("UPDATE tp_outreach_batches_v1 SET final_text=:t, updated_at=NOW() WHERE id=:i"),
                          {"t": t, "i": bid}).rowcount
        if not n:
            raise HTTPException(404, "Batch not found")
        return _ok()

    @app.post(P + "/api/outreach/{iid}/status")
    def api_outreach_status(iid: int, request: Request, payload: Dict[str, Any] = Body(...)):
        need(request)
        try:
            return _ok(**tp.set_outreach_status(engine, iid, str(payload.get("status") or "")))
        except KeyError:
            raise HTTPException(404, "Not found")
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @app.post(P + "/api/match/{iid}/status")
    def api_match_status(iid: int, request: Request, payload: Dict[str, Any] = Body(...)):
        need(request)
        try:
            return _ok(**tp.set_match_status(engine, iid, str(payload.get("status") or "")))
        except KeyError:
            raise HTTPException(404, "Not found")
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @app.post(P + "/api/match/{iid}/message")
    def api_match_message(iid: int, request: Request, payload: Dict[str, Any] = Body(...)):
        need(request)
        t = tp.strip_contacts(str(payload.get("text") or ""))[:3000]
        with engine.begin() as c:
            c.execute(text("UPDATE tp_match_items_v1 SET message_text=:t WHERE id=:i"), {"t": t, "i": iid})
        return _ok(text=t)

    @app.post(P + "/api/match/{iid}/rewrite")
    def api_match_rewrite(iid: int, request: Request):
        need(request)
        with engine.connect() as c:
            it = c.execute(text("SELECT * FROM tp_match_items_v1 WHERE id=:i"), {"i": iid}).mappings().first()
        if not it:
            raise HTTPException(404, "Not found")
        prop = it["property"] if isinstance(it["property"], dict) else json.loads(it["property"] or "{}")
        cfg = tp.members().get(it["member"], {})
        msg, st = tp.property_message({"original_message": it["requirement_text"]}, prop, cfg.get("short") or cfg.get("name") or "")
        with engine.begin() as c:
            c.execute(text("UPDATE tp_match_items_v1 SET message_text=:t, ai_status=:s WHERE id=:i"), {"t": msg, "s": st, "i": iid})
        return _ok(text=msg, ai_status=st)

    @app.post(P + "/api/lead/{lid}/followup")
    def api_followup(lid: int, request: Request, payload: Dict[str, Any] = Body(...)):
        need(request)
        try:
            return _ok(**tp.followup(engine, lid, str(payload.get("action") or ""), str(payload.get("note") or ""),
                                     str(payload.get("when") or ""), str(payload.get("status") or "")))
        except KeyError:
            raise HTTPException(404, "Lead not found")
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @app.post(P + "/api/m/{key}/lead")
    def api_add_lead(key: str, request: Request, payload: Dict[str, Any] = Body(...)):
        need(request)
        _member_or_404(key)
        p = tp.phone10(payload.get("phone"))
        if not p:
            raise HTTPException(400, "Enter a valid 10 digit mobile number")
        lid = tp.create_lead(engine, key, "MANUAL", 0, str(payload.get("name") or "")[:200], p, "", "",
                             str(payload.get("note") or "Added by hand"))
        return _ok(lead_id=lid)

    @app.post(P + "/api/admin/prepare")
    def api_admin_prepare(request: Request):
        need_admin(request)
        started = start_bg(tp.generate_day, core, tp.today_ist(), None)
        return _ok(message="Preparing tasks for all members. Takes a few minutes." if started else "Already preparing. Wait a few minutes.")

    @app.post(P + "/api/admin/report")
    def api_admin_report(request: Request, payload: Dict[str, Any] = Body(default={})):
        need_admin(request)
        d = date.fromisoformat(payload["day"]) if payload.get("day") else tp.today_ist()
        return _ok(saved=len(tp.store_daily_report(engine, d)), day=d.isoformat())

    @app.post(P + "/api/admin/monthly")
    def api_admin_monthly(request: Request, payload: Dict[str, Any] = Body(default={})):
        need_admin(request)
        month = str(payload.get("month") or tp.today_ist().strftime("%Y-%m"))
        try:
            tp._month_bounds(month)
        except Exception:
            raise HTTPException(400, "Month must look like 2026-10")
        res = tp.monthly_review(engine, month)
        return _ok(month=month, flags=res["flags"], analysis=res["analysis"], ai_status=res["ai_status"])

    @app.get(P + "/api/admin/report/{day}")
    def api_admin_report_get(day: str, request: Request):
        need_admin(request)
        with engine.connect() as c:
            rows = c.execute(text("SELECT member, metrics FROM tp_daily_reports_v1 WHERE report_date=:d"),
                             {"d": date.fromisoformat(day)}).mappings().all()
        return {"day": day, "reports": [dict(r) for r in rows]}
