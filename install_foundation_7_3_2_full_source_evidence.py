from pathlib import Path
from datetime import datetime
import shutil

ROOT=Path(__file__).resolve().parent
MOD=ROOT/"alliance_primary_workspace_v730.py"
APP=ROOT/"app.py"
VERSION="7.3.2-ALLIANCE-FULL-SOURCE-EVIDENCE-VIEW"
MARKER="FOUNDATION_7_3_2_FULL_SOURCE_EVIDENCE"
HELPERS='# 7.3.2 FULL SOURCE EVIDENCE\ndef _v732_columns(engine,table):\n    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$",table or ""): return []\n    with engine.connect() as c:\n        return [x[0] for x in c.execute(text("""SELECT column_name FROM information_schema.columns\n          WHERE table_schema=current_schema() AND table_name=:t ORDER BY ordinal_position"""),{"t":table}).all()]\n\ndef _v732_fetch_row(engine,table,source_pk):\n    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$",table or ""): return None\n    cols=_v732_columns(engine,table)\n    if not cols:return None\n    candidates=[x for x in ["id","source_id","message_id","listing_id","property_id","requirement_id","pk"] if x in cols]\n    for key in candidates:\n        try:\n            with engine.connect() as c:\n                row=c.execute(text(f\'SELECT * FROM "{table}" WHERE CAST("{key}" AS TEXT)=:v LIMIT 1\'),{"v":str(source_pk)}).mappings().first()\n            if row:return _safe(dict(row))\n        except Exception: pass\n    return None\n\ndef _v732_pick(d,names):\n    low={str(k).lower():k for k in (d or {}).keys()}\n    for n in names:\n        if n.lower() in low:\n            v=d.get(low[n.lower()])\n            if v not in (None,"",[],{}):return v\n    return None\n\ndef _v732_table_exists(engine,t):\n    try:\n        with engine.connect() as c:return bool(c.execute(text("SELECT to_regclass(:t) IS NOT NULL"),{"t":t}).scalar())\n    except Exception:return False\n\ndef _v732_follow_whatsapp(engine,row):\n    if not isinstance(row,dict):return []\n    out=[];ids=[]\n    for k in ["source_message_id","raw_message_id","message_id","whatsapp_message_id"]:\n        v=_v732_pick(row,[k])\n        if v not in (None,""):ids.append(v)\n    if _v732_table_exists(engine,"wai_raw_messages"):\n        cols=_v732_columns(engine,"wai_raw_messages")\n        keys=[x for x in ["id","message_id"] if x in cols]\n        for mid in ids:\n            for key in keys:\n                try:\n                    with engine.connect() as c:\n                        rr=c.execute(text(f\'SELECT * FROM "wai_raw_messages" WHERE CAST("{key}" AS TEXT)=:v LIMIT 1\'),{"v":str(mid)}).mappings().first()\n                    if rr:\n                        out.append({"table":"wai_raw_messages","row":_safe(dict(rr))});break\n                except Exception:pass\n    lid=_v732_pick(row,["listing_id","wai_listing_id"])\n    if lid and _v732_table_exists(engine,"wai_listings"):\n        lr=_v732_fetch_row(engine,"wai_listings",lid)\n        if lr:\n            out.append({"table":"wai_listings","row":lr})\n            mid=_v732_pick(lr,["source_message_id","raw_message_id","message_id"])\n            if mid and _v732_table_exists(engine,"wai_raw_messages"):\n                rr=_v732_fetch_row(engine,"wai_raw_messages",mid)\n                if rr:out.append({"table":"wai_raw_messages","row":rr})\n    seen=set();ded=[]\n    for x in out:\n        sig=(x["table"],json.dumps(x["row"],sort_keys=True,default=str))\n        if sig not in seen:seen.add(sig);ded.append(x)\n    return ded\n\ndef _v732_evidence(engine,cid):\n    links=_source_links(engine,cid);blocks=[]\n    for link in links:\n        table=str(link.get("source_table") or "");pk=link.get("source_pk")\n        row=_v732_fetch_row(engine,table,pk)\n        lineage=_v732_follow_whatsapp(engine,row) if row else []\n        candidates=([{"table":table,"row":row}] if row else [])+lineage\n        preferred=next((x for x in candidates if x.get("table")=="wai_raw_messages" and isinstance(x.get("row"),dict)),None)\n        if not preferred and candidates:preferred=candidates[0]\n        d=(preferred or {}).get("row") or {}\n        blocks.append({"link":link,"source_row":row,"lineage":lineage,"display":{\n          "source_type":link.get("source_type"),\n          "group":_v732_pick(d,["group_name","chat_name","conversation_name","source_group","group_title"]),\n          "sender":_v732_pick(d,["sender_name","push_name","contact_name","sender","author_name","name"]),\n          "sender_phone":_v732_pick(d,["sender_phone","phone","phone_number","sender_number","contact_phone","author_phone"]),\n          "sender_jid":_v732_pick(d,["sender_jid","jid","author","participant","remote_jid"]),\n          "message_timestamp":_v732_pick(d,["message_timestamp","timestamp","sent_at","message_date","datetime","created_at","received_at"]),\n          "full_message":_v732_pick(d,["message_text","text","body","content","message","raw_text","full_message","caption"])}})\n    return blocks\n\ndef _v732_evidence_html(engine,cid):\n    ev=_v732_evidence(engine,cid)\n    if not ev:return "<div class=\'card\'><h3>Original Source / WhatsApp Message</h3><p>No recoverable original-source row is linked to this canonical record yet.</p></div>"\n    out=[]\n    for i,e in enumerate(ev,1):\n        d=e["display"];link=e["link"];msg=d.get("full_message")\n        if isinstance(msg,(dict,list)):msg=json.dumps(msg,ensure_ascii=False,indent=2)\n        lineage_html=""\n        for ln in e.get("lineage") or []:\n            lineage_html+=f"<details><summary>{html.escape(str(ln.get(\'table\')))} raw lineage</summary><pre>{html.escape(json.dumps(ln.get(\'row\') or {},ensure_ascii=False,indent=2,default=str))}</pre></details>"\n        source_json=json.dumps(e.get("source_row") or {},ensure_ascii=False,indent=2,default=str)\n        out.append(f"""<div class=\'card\'>\n        <h3>Original Source / WhatsApp Message · Evidence {i}</h3>\n        <div class=\'grid\'>\n          <div><b>Source Type</b><br>{html.escape(str(d.get(\'source_type\') or \'\'))}</div>\n          <div><b>WhatsApp Group / Chat</b><br>{html.escape(str(d.get(\'group\') or \'Not captured\'))}</div>\n          <div><b>Sender Name</b><br>{html.escape(str(d.get(\'sender\') or \'Not captured\'))}</div>\n          <div><b>Sender Phone</b><br>{html.escape(str(d.get(\'sender_phone\') or \'Not captured\'))}</div>\n          <div><b>Sender JID / Identity</b><br>{html.escape(str(d.get(\'sender_jid\') or \'Not captured\'))}</div>\n          <div><b>Message Date & Time</b><br>{html.escape(str(d.get(\'message_timestamp\') or \'Not captured\'))}</div>\n        </div>\n        <h4>Full Original Message</h4>\n        <pre style=\'font-size:14px;background:#f8fafc;border:1px solid #e1e7ee;padding:14px;border-radius:9px\'>{html.escape(str(msg or \'Original message text not available in this source row\'))}</pre>\n        <p class=\'muted\'><b>Canonical evidence link:</b> {html.escape(str(link.get(\'source_table\') or \'\'))} · PK {html.escape(str(link.get(\'source_pk\') or \'\'))} · Hash {html.escape(str(link.get(\'source_row_hash\') or \'\'))}</p>\n        <details><summary>All original source fields</summary><pre>{html.escape(source_json)}</pre></details>\n        {lineage_html}</div>""")\n    return "".join(out)\n'

def main():
    if not MOD.exists() or not APP.exists():raise SystemExit("7.3 files missing")
    s=MOD.read_text(encoding="utf-8")
    if "7.3.1 BOOT FIX" not in s:raise SystemExit("Install 7.3.1 first")
    backup=ROOT/f"alliance_primary_workspace_v730-before-v732-{datetime.now().strftime('%Y%m%d-%H%M%S')}.py"
    shutil.copy2(MOD,backup)
    if "import re" not in s.splitlines()[:15]:
        s=s.replace("import html, json, threading, time","import html, json, re, threading, time",1)
    if "# 7.3.2 FULL SOURCE EVIDENCE" not in s:
        anchor='def _button(url,label,cls="mini"):\n'
        pos=s.find(anchor)
        if pos<0:raise SystemExit("helper anchor not found")
        s=s[:pos]+HELPERS+"\n"+s[pos:]
        prop_old="""        <div class='card'><h3>Source Evidence Links</h3><div class='tablebox'><table><tr><th>Source</th><th>Table</th><th>Source PK</th><th>Evidence Hash</th></tr>{source_html}</table></div></div>
        <div class='card'><h3>Canonical Record</h3><pre>{html.escape(json.dumps(p.get('clean_record') or {},ensure_ascii=False,indent=2))}</pre></div>"""
        prop_new="""        {_v732_evidence_html(engine,cid)}
        <div class='card'><h3>Source Evidence Links</h3><div class='tablebox'><table><tr><th>Source</th><th>Table</th><th>Source PK</th><th>Evidence Hash</th></tr>{source_html}</table></div></div>
        <div class='card'><h3>Canonical Record</h3><pre>{html.escape(json.dumps(p.get('clean_record') or {},ensure_ascii=False,indent=2))}</pre></div>"""
        if prop_old not in s:raise SystemExit("property detail anchor not found")
        s=s.replace(prop_old,prop_new,1)
        req_old="""        <div class='card'><h3>Canonical Requirement</h3><pre>{html.escape(json.dumps(r.get('clean_record') or {},ensure_ascii=False,indent=2))}</pre></div>"""
        req_new="""        {_v732_evidence_html(engine,cid)}
        <div class='card'><h3>Canonical Requirement</h3><pre>{html.escape(json.dumps(r.get('clean_record') or {},ensure_ascii=False,indent=2))}</pre></div>"""
        if req_old not in s:raise SystemExit("requirement detail anchor not found")
        s=s.replace(req_old,req_new,1)
        s=s.replace('VERSION="7.3.1-ALLIANCE-PRIMARY-WORKSPACE-ACTION-ENGINE-PERSISTED-CERT-BOOT-FIX"',
                    'VERSION="7.3.2-ALLIANCE-PRIMARY-WORKSPACE-FULL-SOURCE-EVIDENCE"',1)
    compile(s,str(MOD),"exec");MOD.write_text(s,encoding="utf-8")
    app=APP.read_text(encoding="utf-8")
    if MARKER not in app:
        app=app.rstrip()+"\n\n# "+MARKER+"\n# Property and requirement details now surface original source and WhatsApp evidence.\n"
        compile(app,str(APP),"exec");APP.write_text(app,encoding="utf-8")
    final=MOD.read_text(encoding="utf-8")
    for x in ["7.3.2 FULL SOURCE EVIDENCE","wai_raw_messages","Full Original Message","Message Date & Time","Sender JID / Identity","All original source fields"]:
        assert x in final,x
    compile(final,str(MOD),"exec");compile(APP.read_text(encoding="utf-8"),str(APP),"exec")
    print(VERSION)
    print("PROPERTY + REQUIREMENT DETAIL ENRICHED")
    print("Shows full original message, sender, sender phone/JID, group/chat, date/time and all source fields when available.")
    print("WhatsApp lineage follows to wai_raw_messages when available.")
    print("Missing facts display Not captured; nothing is invented.")
    print("No canonical/raw/Gold/Champion record is modified.")
    print("Backup:",backup)

if __name__=="__main__":main()
