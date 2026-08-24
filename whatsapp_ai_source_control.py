import re, uuid, time, threading
from datetime import datetime, timezone
from fastapi import Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

_worker_started=False

def install(router, engine, require_db, init_db, shell, esc,
            ingest_current_whatsapp_source, rebuild_matches):

    def ensure_tables():
        require_db(); init_db()
        with engine.begin() as c:
            statements=[
                """CREATE TABLE IF NOT EXISTS wai_source_accounts(
                    id UUID PRIMARY KEY,
                    label TEXT NOT NULL,
                    account_phone TEXT UNIQUE NOT NULL,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )""",
                """CREATE TABLE IF NOT EXISTS wai_source_group_map(
                    id UUID PRIMARY KEY,
                    source_group_name TEXT UNIQUE NOT NULL,
                    account_phone TEXT,
                    is_active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )""",
                """CREATE TABLE IF NOT EXISTS wai_auto_settings(
                    id INTEGER PRIMARY KEY,
                    auto_process BOOLEAN DEFAULT TRUE,
                    interval_seconds INTEGER DEFAULT 120,
                    last_auto_run TIMESTAMPTZ,
                    last_auto_status TEXT,
                    last_auto_result TEXT,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )""",
                """INSERT INTO wai_auto_settings(id,auto_process,interval_seconds)
                   VALUES(1,TRUE,120) ON CONFLICT(id) DO NOTHING""",
                "CREATE INDEX IF NOT EXISTS idx_wai_source_map_phone ON wai_source_group_map(account_phone)",
            ]
            for stmt in statements:
                c.execute(text(stmt))

            known_accounts = [
                ("Main office","9811895500"),
                ("Priya","9811895527"),
                ("Priya 1","8076209947"),
                ("Zoya","9811891233"),
            ]
            for label, phone in known_accounts:
                c.execute(text("""
                  INSERT INTO wai_source_accounts(id,label,account_phone,is_active)
                  VALUES(:id,:label,:phone,TRUE)
                  ON CONFLICT(account_phone) DO UPDATE SET
                    label=COALESCE(NULLIF(wai_source_accounts.label,''),EXCLUDED.label),
                    updated_at=NOW()
                """),{
                    "id":uuid.uuid5(uuid.NAMESPACE_URL,"wai-account:"+phone),
                    "label":label,
                    "phone":phone
                })

    def clean_phone(v):
        d=re.sub(r"\D","",str(v or ""))
        if len(d)==12 and d.startswith("91"): d=d[2:]
        if len(d)==11 and d.startswith("0"): d=d[1:]
        return d if len(d)==10 and d[0] in "6789" else ""

    def discover_phone_column(c):
        cols=[r[0] for r in c.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema=current_schema() AND table_name='wa_sources'
        """)).all()]
        for cand in ["account_phone","mobile_number","mobile_no","whatsapp_number","whatsapp_phone","phone_number","source_phone","account_number","phone"]:
            if cand in cols: return cand
        return None

    def canonical_group_name(name):
        import unicodedata
        x=str(name or "").replace("\u00a0"," ")
        x=x.replace("WhatsApp Chat with ","").replace("whatsapp chat with ","")
        if x.lower().endswith(".txt"):
            x=x[:-4]
        x=unicodedata.normalize("NFKD",x)
        x="".join(ch for ch in x if ch.isalnum() or ch.isspace())
        return " ".join(x.lower().split())

    def group_similarity(a,b):
        from difflib import SequenceMatcher
        ca,cb=canonical_group_name(a),canonical_group_name(b)
        if not ca or not cb:
            return 0.0
        if ca==cb:
            return 1.0
        return SequenceMatcher(None,ca,cb).ratio()

    KNOWN_ACCOUNT_GROUPS = {
        "9811895500": [
            "Western line property 1",
            "Goa Brokers & Builders...",
            "Preleased/Prerented NCR",
            "WELCOME P -19",
            "HOTELS, BANQUETS & RESTRO",
            "Hotels & Resorts ~ Sale",
            "Real Estate Brokers 🏛️",
            "Hotel, Banquet & Restro B",
            "Commercial Properties GGN",
            "Janakpuri dealers",
            "Vke A1 comeplax showroom shops at D6 Vasant Kunj Delhi 70",
            "Priyankas Housing Delhi 2",
            "🏠ONLY FARM HOUSE FARM LAND",
            "LEASED/RENTED PROPERTIES FOR SALE",
            "Gurgaon Delhi Properties Only✅✅🙏🙏",
            "WEST DELHI 05 Real Estate Lisiting Group",
            "Briicx3 Reality",
            "Real#Estate South Delhi (4)",
            "PROPERTY PROPOSAL",
            "PRE-RENTED / PRE-LEASED properties only (No other deals pls)",
            "Kuber Prop latest",
            "GOA TOP REAL ESTATE AGENT",
        ],
        "8076209947": [
            "Omvira estates buy sell rent.",
            "D RANGE GROUP GOA-your ultimate service hub",
        ],
    }

    def auto_assign_known_groups(c):
        rows=c.execute(text("""
            SELECT source_group_name,account_phone
            FROM wai_source_group_map
            WHERE COALESCE(source_group_name,'')<>''
        """)).mappings().all()
        for row in rows:
            if row.get("account_phone"):
                continue
            grp=row["source_group_name"]
            best_phone=None
            best_score=0.0
            for phone,names in KNOWN_ACCOUNT_GROUPS.items():
                for known in names:
                    sc=group_similarity(grp,known)
                    if sc>best_score:
                        best_score=sc
                        best_phone=phone
            if best_phone and best_score>=0.74:
                c.execute(text("""
                    UPDATE wai_source_group_map
                    SET account_phone=:p,updated_at=NOW()
                    WHERE source_group_name=:g AND account_phone IS NULL
                """),{"p":best_phone,"g":grp})

    def sync_source_group_map():
        ensure_tables()
        with engine.begin() as c:
            col=discover_phone_column(c)
            if col:
                rows=c.execute(text(f"""
                    SELECT DISTINCT COALESCE(group_name,source_name,'Unknown') grp,
                           NULLIF(CAST({col} AS TEXT),'') phone
                    FROM wa_sources
                    WHERE COALESCE(group_name,source_name,'')<>''
                """)).mappings().all()
            else:
                rows=c.execute(text("""
                    SELECT DISTINCT COALESCE(group_name,source_name,'Unknown') grp
                    FROM wa_sources
                    WHERE COALESCE(group_name,source_name,'')<>''
                """)).mappings().all()

            for r in rows:
                grp=r["grp"]
                phone=clean_phone(r.get("phone")) if col else ""
                c.execute(text("""
                  INSERT INTO wai_source_group_map(id,source_group_name,account_phone)
                  VALUES(:id,:g,:p)
                  ON CONFLICT(source_group_name) DO UPDATE SET
                    account_phone=COALESCE(NULLIF(wai_source_group_map.account_phone,''),NULLIF(EXCLUDED.account_phone,'')),
                    updated_at=NOW()
                """),{
                    "id":uuid.uuid5(uuid.NAMESPACE_URL,"wai-group-map:"+grp),
                    "g":grp,"p":phone or None
                })
                if phone:
                    c.execute(text("""
                      INSERT INTO wai_source_accounts(id,label,account_phone,is_active)
                      VALUES(:id,:label,:p,TRUE)
                      ON CONFLICT(account_phone) DO NOTHING
                    """),{
                        "id":uuid.uuid5(uuid.NAMESPACE_URL,"wai-account:"+phone),
                        "label":"WhatsApp "+phone[-4:],"p":phone
                    })

            # Apply known group-to-number mapping after source sync.
            auto_assign_known_groups(c)

    def status_label(last_message, ai_last):
        if not last_message: return "NO DATA"
        now=datetime.now(timezone.utc)
        if getattr(last_message,"tzinfo",None):
            age=(now-last_message).total_seconds()
            if age>259200: return "STALE"
        if not ai_last: return "FETCHING / AI PENDING"
        return "LIVE + SEGREGATED" if ai_last>=last_message else "NEW DATA / AI PENDING"

    def account_stats():
        sync_source_group_map()
        with engine.begin() as c:
            accounts=c.execute(text("""
                SELECT * FROM wai_source_accounts ORDER BY is_active DESC,label,account_phone
            """)).mappings().all()
            groups=c.execute(text("""
              WITH source_stats AS (
                SELECT COALESCE(s.group_name,s.source_name,'Unknown') group_name,
                       COUNT(m.message_id) raw_messages,
                       MAX(m.created_at) last_message
                FROM wa_sources s
                LEFT JOIN wa_messages m ON m.source_id=s.source_id
                GROUP BY COALESCE(s.group_name,s.source_name,'Unknown')
              ),
              ai_stats AS (
                SELECT source_group_name,
                       COUNT(*) FILTER(WHERE transaction IN ('SALE','RENT')) inventory_count,
                       COUNT(*) FILTER(WHERE transaction='REQUIREMENT') requirement_count,
                       MAX(created_at) ai_last_processed
                FROM wai_listings
                GROUP BY source_group_name
              )
              SELECT gm.source_group_name,gm.account_phone,gm.is_active,
                     COALESCE(ss.raw_messages,0) raw_messages,ss.last_message,
                     COALESCE(ai.inventory_count,0) inventory_count,
                     COALESCE(ai.requirement_count,0) requirement_count,
                     ai.ai_last_processed
              FROM wai_source_group_map gm
              LEFT JOIN source_stats ss ON ss.group_name=gm.source_group_name
              LEFT JOIN ai_stats ai ON ai.source_group_name=gm.source_group_name
              ORDER BY gm.account_phone NULLS LAST,ss.last_message DESC NULLS LAST,gm.source_group_name
            """)).mappings().all()
            setting=c.execute(text("SELECT * FROM wai_auto_settings WHERE id=1")).mappings().first()
        return accounts,groups,setting

    @router.get("/accounts",response_class=HTMLResponse)
    def accounts_page():
        accounts,groups,setting=account_stats()
        by_phone={}
        unassigned=[]
        for g in groups:
            p=g.get("account_phone") or ""
            (by_phone.setdefault(p,[]) if p else unassigned).append(g)

        total_raw=sum(int(g["raw_messages"] or 0) for g in groups)
        total_inv=sum(int(g["inventory_count"] or 0) for g in groups)
        total_req=sum(int(g["requirement_count"] or 0) for g in groups)

        cards=f"""<div class=grid>
          <div class=card><div class=muted>Registered Numbers</div><div class=num>{len(accounts)}</div></div>
          <div class=card><div class=muted>WhatsApp Messages</div><div class=num>{total_raw}</div></div>
          <div class=card><div class=muted>AI Inventory</div><div class=num>{total_inv}</div></div>
          <div class=card><div class=muted>AI Requirements</div><div class=num>{total_req}</div></div>
        </div>"""

        auto=f"""<div class=card style='margin-top:14px'>
          <b>Automatic AI Segregation: {'ON' if setting and setting['auto_process'] else 'OFF'}</b><br>
          <span class=muted>Interval: {setting['interval_seconds'] if setting else 120} sec · Last run: {esc(setting['last_auto_run'] if setting else '')}
          · Status: {esc(setting['last_auto_status'] if setting else '')}</span><br>
          <div style='margin-top:8px;padding:9px;background:#f9fafb;border:1px solid #e5e7eb;border-radius:7px;white-space:pre-wrap'>
            <b>Last AI Run Detail:</b> {esc(setting['last_auto_result'] if setting else '')}
          </div><br>
          <a class='btn green' href='/whatsapp-capture/intelligence/accounts/auto/on'>AUTO ON</a>
          <a class='btn red' href='/whatsapp-capture/intelligence/accounts/auto/off'>AUTO OFF</a>
          <a class='btn blue' href='/whatsapp-capture/intelligence/accounts/process-now'>PROCESS PENDING NOW</a>
        </div>"""

        add=f"""<div class=card style='margin-top:14px'><h3>Add WhatsApp Number</h3>
          <form method=post action='/whatsapp-capture/intelligence/accounts/add'
                style='display:grid;grid-template-columns:1fr 1fr auto;gap:8px;align-items:end'>
            <div><label>Account Label</label><input name=label required placeholder='Main Office'></div>
            <div><label>WhatsApp Number</label><input name=account_phone required placeholder='9811895500'></div>
            <button class='btn green'>ADD NUMBER</button>
          </form>
          <p class=muted>Numbers stay saved in PostgreSQL. Linking a device still happens on the office bridge PC.</p>
        </div>"""

        blocks=[]
        for a in accounts:
            phone=a["account_phone"]
            gl=by_phone.get(phone,[])
            raw=sum(int(x["raw_messages"] or 0) for x in gl)
            inv=sum(int(x["inventory_count"] or 0) for x in gl)
            req=sum(int(x["requirement_count"] or 0) for x in gl)
            lm=max([x["last_message"] for x in gl if x["last_message"]],default=None)
            la=max([x["ai_last_processed"] for x in gl if x["ai_last_processed"]],default=None)
            gtrs="".join(
                f"<tr><td>{esc(g['source_group_name'])}</td><td>{g['raw_messages']}</td>"
                f"<td>{g['inventory_count']}</td><td>{g['requirement_count']}</td>"
                f"<td>{esc(g['last_message'])}</td><td>{esc(status_label(g['last_message'],g['ai_last_processed']))}</td>"
                f"<td><a class='btn red' href='/whatsapp-capture/intelligence/accounts/unassign?group={esc(g['source_group_name'])}'>Unassign</a></td></tr>"
                for g in gl
            )
            blocks.append(f"""<div class=card style='margin-top:14px'>
              <div style='display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap'>
                <div><h3 style='margin:0'>{esc(a['label'])} · {esc(phone)}</h3>
                <span class=muted>{'ACTIVE' if a['is_active'] else 'PAUSED'} · {esc(status_label(lm,la))}</span></div>
                <div><span class=pill>Groups {len(gl)}</span> <span class=pill>Messages {raw}</span>
                <span class=pill>Inventory {inv}</span> <span class=pill>Requirements {req}</span></div>
              </div><br>
              <a class='btn blue' href='/whatsapp-capture/intelligence/accounts/command/{phone}/RUN_CATCHUP'>RUN CATCH-UP</a>
              <a class='btn' href='/whatsapp-capture/intelligence/accounts/command/{phone}/LINK_ACCOUNT'>LINK / RELINK</a>
              <a class='btn red' href='/whatsapp-capture/intelligence/accounts/toggle/{phone}'>{'PAUSE' if a['is_active'] else 'ACTIVATE'}</a>
              <br><br><div class=scroll><table><tr><th>Selected Group</th><th>Messages</th><th>Inventory</th>
              <th>Requirements</th><th>Last Message</th><th>AI Status</th><th></th></tr>{gtrs}</table></div>
            </div>""")

        unassigned_html=""
        if unassigned and accounts:
            options="".join(f"<option value='{esc(a['account_phone'])}'>{esc(a['label'])} · {esc(a['account_phone'])}</option>" for a in accounts)
            rows="".join(
                f"""<tr><td>{esc(g['source_group_name'])}</td><td>{g['raw_messages']}</td><td>{g['inventory_count']}</td><td>{g['requirement_count']}</td>
                <td><form method=post action='/whatsapp-capture/intelligence/accounts/assign' style='display:flex;gap:6px'>
                <input type=hidden name=source_group_name value="{esc(g['source_group_name'])}">
                <select name=account_phone>{options}</select><button class='btn green'>ASSIGN</button></form></td></tr>"""
                for g in unassigned
            )
            unassigned_html=f"""<div class=card style='margin-top:14px'><h3>Unassigned / New WhatsApp Groups</h3>
              <p class=muted>Known groups are auto-assigned. Only genuinely new or uncertain groups remain here for one-time assignment.</p>
              <div class=scroll><table><tr><th>Group</th><th>Messages</th><th>Inventory</th><th>Requirements</th><th>Assign</th></tr>{rows}</table></div></div>"""

        body=f"""<h2>WhatsApp AI Source Control</h2>
        <p class=muted>See which numbers and groups are feeding AI, and how many records are being segregated into Inventory vs Requirements.</p>
        {cards}{auto}{add}{''.join(blocks)}{unassigned_html}"""
        return HTMLResponse(shell("WhatsApp AI Source Control",body,"WhatsApp Sources"))

    @router.post("/accounts/add")
    def add_account(label:str=Form(...),account_phone:str=Form(...)):
        ensure_tables()
        phone=clean_phone(account_phone)
        if not phone: raise HTTPException(400,"Enter a valid 10-digit Indian WhatsApp number.")
        with engine.begin() as c:
            c.execute(text("""INSERT INTO wai_source_accounts(id,label,account_phone,is_active)
                VALUES(:id,:l,:p,TRUE)
                ON CONFLICT(account_phone) DO UPDATE SET label=EXCLUDED.label,is_active=TRUE,updated_at=NOW()"""),
                {"id":uuid.uuid5(uuid.NAMESPACE_URL,"wai-account:"+phone),"l":label.strip(),"p":phone})
        return RedirectResponse("/whatsapp-capture/intelligence/accounts",303)

    @router.post("/accounts/assign")
    def assign_group(source_group_name:str=Form(...),account_phone:str=Form(...)):
        ensure_tables()
        with engine.begin() as c:
            c.execute(text("""UPDATE wai_source_group_map SET account_phone=:p,is_active=TRUE,updated_at=NOW()
                WHERE source_group_name=:g"""),{"p":clean_phone(account_phone),"g":source_group_name})
        return RedirectResponse("/whatsapp-capture/intelligence/accounts",303)

    @router.get("/accounts/unassign")
    def unassign_group(group:str):
        ensure_tables()
        with engine.begin() as c:
            c.execute(text("UPDATE wai_source_group_map SET account_phone=NULL,updated_at=NOW() WHERE source_group_name=:g"),{"g":group})
        return RedirectResponse("/whatsapp-capture/intelligence/accounts",303)

    @router.get("/accounts/toggle/{account_phone}")
    def toggle_account(account_phone:str):
        ensure_tables()
        with engine.begin() as c:
            c.execute(text("UPDATE wai_source_accounts SET is_active=NOT is_active,updated_at=NOW() WHERE account_phone=:p"),
                      {"p":clean_phone(account_phone)})
        return RedirectResponse("/whatsapp-capture/intelligence/accounts",303)

    @router.get("/accounts/command/{account_phone}/{command_type}")
    def queue_command(account_phone:str,command_type:str):
        ensure_tables()
        if command_type not in ("RUN_CATCHUP","LINK_ACCOUNT","PAUSE_ACCOUNT"):
            raise HTTPException(400,"Invalid command")
        with engine.begin() as c:
            c.execute(text("""INSERT INTO v8_bridge_commands(command_id,account_phone,command_type,status,requested_by)
              VALUES(:id,:p,:cmd,'PENDING','AI Source Control')"""),
              {"id":"CMD-"+uuid.uuid4().hex[:16].upper(),"p":clean_phone(account_phone),"cmd":command_type})
        return RedirectResponse("/whatsapp-capture/intelligence/accounts",303)

    def process_once():
        ensure_tables()
        try:
            result=ingest_current_whatsapp_source()
            rebuild_matches()
            try:
                from whatsapp_clean_database_final import refresh_clean_database
                clean_result=refresh_clean_database(False)
            except Exception as clean_error:
                clean_result={"status":"warning","error":str(clean_error)}
            with engine.begin() as c:
                c.execute(text("""UPDATE wai_auto_settings SET last_auto_run=NOW(),last_auto_status='SUCCESS',
                    last_auto_result=:r,updated_at=NOW() WHERE id=1"""),{"r":str(result)[:2000]})
            return result
        except Exception as e:
            with engine.begin() as c:
                c.execute(text("""UPDATE wai_auto_settings SET last_auto_run=NOW(),last_auto_status='ERROR',
                    last_auto_result=:r,updated_at=NOW() WHERE id=1"""),{"r":str(e)[:2000]})
            raise

    LOCK_KEY = 918811955

    def run_serialized():
        with engine.connect() as lock_conn:
            locked=bool(lock_conn.execute(text("SELECT pg_try_advisory_lock(:k)"),{"k":LOCK_KEY}).scalar())
            if not locked:
                with engine.begin() as c:
                    c.execute(text("""UPDATE wai_auto_settings SET
                        last_auto_run=NOW(),
                        last_auto_status='BUSY',
                        last_auto_result='Another AI segregation run is already active.',
                        updated_at=NOW()
                        WHERE id=1"""))
                return {"status":"busy"}
            try:
                return process_once()
            finally:
                try:
                    lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"),{"k":LOCK_KEY})
                    lock_conn.commit()
                except Exception:
                    pass

    @router.get("/accounts/process-now")
    def process_now():
        run_serialized()
        return RedirectResponse("/whatsapp-capture/intelligence/accounts",303)

    @router.get("/accounts/auto/{state}")
    def auto_toggle(state:str):
        ensure_tables()
        with engine.begin() as c:
            c.execute(text("UPDATE wai_auto_settings SET auto_process=:v,updated_at=NOW() WHERE id=1"),
                      {"v":state.lower()=="on"})
        return RedirectResponse("/whatsapp-capture/intelligence/accounts",303)

    def worker():
        while True:
            try:
                ensure_tables()
                with engine.begin() as c:
                    setting=c.execute(text("SELECT auto_process,interval_seconds FROM wai_auto_settings WHERE id=1")).mappings().first()
                if setting and setting["auto_process"]:
                    run_serialized()
                time.sleep(max(60,int(setting["interval_seconds"] if setting else 120)))
            except Exception as e:
                print("WAI auto source worker warning:",e)
                time.sleep(120)

    global _worker_started
    try:
        ensure_tables(); sync_source_group_map()
        if not _worker_started:
            _worker_started=True
            threading.Thread(target=worker,daemon=True,name="wai-auto-source-worker").start()
    except Exception as e:
        print("WAI account monitor init warning:",e)
