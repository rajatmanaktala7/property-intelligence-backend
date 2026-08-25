import uuid
from fastapi import Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

def install(router, engine, require_db, init_db, shell, esc):
    def ensure_upgrade():
        require_db(); init_db()
        with engine.begin() as c:
            for stmt in [
                "ALTER TABLE wai_listings ADD COLUMN IF NOT EXISTS triage_tier TEXT",
                "ALTER TABLE wai_listings ADD COLUMN IF NOT EXISTS listing_index_in_message INT DEFAULT 1",
                "ALTER TABLE wai_listings ADD COLUMN IF NOT EXISTS additional_locations JSONB DEFAULT '[]'::jsonb",
                "ALTER TABLE wai_listings ADD COLUMN IF NOT EXISTS budget_unit TEXT DEFAULT 'total'",
                "CREATE INDEX IF NOT EXISTS idx_wai_triage ON wai_listings(triage_tier)",
            ]: c.execute(text(stmt))
            c.execute(text("""UPDATE wai_listings SET triage_tier=CASE
                WHEN COALESCE(confidence_score,0)<40 THEN 'auto_reject'
                WHEN COALESCE(confidence_score,0)>=90 THEN 'quick_review'
                ELSE 'needs_edit' END
                WHERE triage_tier IS NULL OR triage_tier NOT IN ('auto_reject','quick_review','needs_edit')"""))
            c.execute(text("""UPDATE wai_listings SET status='rejected'
                WHERE COALESCE(confidence_score,0)<40 AND status='unverified'"""))

    @router.get("/triage",response_class=HTMLResponse)
    def triage(tier:str="quick_review"):
        ensure_upgrade()
        if tier not in ("quick_review","needs_edit","auto_reject"): tier="quick_review"
        with engine.begin() as c:
            counts=c.execute(text("""SELECT
              COUNT(*) FILTER(WHERE triage_tier='quick_review' AND status='unverified') quick,
              COUNT(*) FILTER(WHERE triage_tier='needs_edit' AND status='unverified') edit,
              COUNT(*) FILTER(WHERE triage_tier='auto_reject') rejected FROM wai_listings""")).mappings().first()
            where="l.triage_tier='auto_reject'" if tier=="auto_reject" else "l.triage_tier=:tier AND l.status='unverified'"
            params={} if tier=="auto_reject" else {"tier":tier}
            rows=c.execute(text(f"""SELECT l.*,ct.phone FROM wai_listings l
              LEFT JOIN wai_contacts ct ON ct.id=l.contact_id WHERE {where}
              ORDER BY l.confidence_score DESC,l.created_at DESC LIMIT 2000"""),params).mappings().all()
        chips=f"""<div style='display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px'>
        <a class='btn blue' href='?tier=quick_review'>Quick Review 90%+ ({counts['quick']})</a>
        <a class='btn' href='?tier=needs_edit'>Needs Edit 40-89% ({counts['edit']})</a>
        <a class='btn red' href='?tier=auto_reject'>Auto-Rejected &lt;40% ({counts['rejected']})</a></div>"""
        bulk="" if tier!="quick_review" else """<form method=post action='/whatsapp-capture/intelligence/triage/bulk-approve'
        onsubmit="return confirm('Approve all 90%+ Quick Review rows?')"><button class='btn green'>BULK APPROVE ALL 90%+</button></form><br>"""
        trs=""
        for r in rows:
            action="LOG ONLY" if tier=="auto_reject" else (
                f"<a class='btn green' href='/whatsapp-capture/intelligence/verify/{r['id']}/approve'>Approve</a> "
                f"<a class='btn blue' href='/whatsapp-capture/intelligence/triage/edit/{r['id']}'>Edit</a> "
                f"<a class='btn red' href='/whatsapp-capture/intelligence/verify/{r['id']}/reject'>Reject</a>")
            trs+=(f"<tr><td class=raw>{esc(r['raw_listing_text'] or r['summary'])}</td><td>{esc(r['phone'])}</td>"
                  f"<td>{esc(r['location'])}</td><td>{esc(r['property_type'])}</td><td>{esc(r['transaction'])}</td>"
                  f"<td>{float(r['confidence_score'] or 0):.0f}%</td><td>{esc(r['triage_tier'])}</td><td>{action}</td></tr>")
        body=f"""<h2>Verification Triage</h2>{chips}{bulk}<div class=scroll><table><tr><th>Raw Details</th>
        <th>Contact</th><th>Location</th><th>Type</th><th>Transaction</th><th>Confidence</th><th>Tier</th><th>Action</th></tr>{trs}</table></div>"""
        return HTMLResponse(shell("Verification Triage",body,"Verification"))

    @router.post("/triage/bulk-approve")
    def bulk_approve():
        ensure_upgrade()
        with engine.begin() as c:
            ids=[r[0] for r in c.execute(text("""SELECT id FROM wai_listings WHERE status='unverified'
                AND triage_tier='quick_review' AND confidence_score>=90""")).all()]
            c.execute(text("""UPDATE wai_listings SET status='verified',verified_at=NOW(),verified_by='team-bulk'
                WHERE status='unverified' AND triage_tier='quick_review' AND confidence_score>=90"""))
            for lid in ids:
                c.execute(text("""INSERT INTO wai_verification_log(id,listing_id,action,actor)
                    VALUES(:x,:l,'bulk_approved','team-bulk')"""),{"x":uuid.uuid4(),"l":lid})
        return RedirectResponse("/whatsapp-capture/intelligence/triage?tier=quick_review",303)

    @router.get("/triage/edit/{listing_id}",response_class=HTMLResponse)
    def edit_listing(listing_id:str):
        ensure_upgrade()
        with engine.begin() as c:
            r=c.execute(text("SELECT * FROM wai_listings WHERE id=:i"),{"i":listing_id}).mappings().first()
        if not r: raise HTTPException(404,"Listing not found")
        body=f"""<h2>Edit Before Approval</h2><div class=card><div class=raw>{esc(r['raw_listing_text'])}</div><hr>
        <form method=post action='/whatsapp-capture/intelligence/triage/edit/{r["id"]}'>
        <p>Location<br><input name=location value='{esc(r["location"])}' style='width:100%;padding:9px'></p>
        <p>Property Type<br><input name=property_type value='{esc(r["property_type"])}' style='width:100%;padding:9px'></p>
        <p>Transaction<br><input name=transaction value='{esc(r["transaction"])}' style='width:100%;padding:9px'></p>
        <p>Budget Text<br><input name=budget_text value='{esc(r["budget_text"])}' style='width:100%;padding:9px'></p>
        <p>Area Text<br><input name=area_text value='{esc(r["area_text"])}' style='width:100%;padding:9px'></p>
        <button class='btn green'>SAVE & APPROVE</button></form></div>"""
        return HTMLResponse(shell("Edit Listing",body,"Verification"))

    @router.post("/triage/edit/{listing_id}")
    def save_edit_listing(listing_id:str,location:str=Form(""),property_type:str=Form(""),
                          transaction:str=Form(""),budget_text:str=Form(""),area_text:str=Form("")):
        ensure_upgrade()
        with engine.begin() as c:
            c.execute(text("""UPDATE wai_listings SET location=:l,property_type=:p,transaction=:t,
              budget_text=:b,area_text=:a,status='verified',verified_at=NOW(),verified_by='team-edit' WHERE id=:i"""),
              {"l":location or None,"p":property_type or None,"t":transaction or None,
               "b":budget_text or None,"a":area_text or None,"i":listing_id})
            c.execute(text("""INSERT INTO wai_verification_log(id,listing_id,action,actor)
              VALUES(:x,:i,'edited_and_approved','team-edit')"""),{"x":uuid.uuid4(),"i":listing_id})
        return RedirectResponse("/whatsapp-capture/intelligence/triage?tier=needs_edit",303)

    @router.get("/group-health",response_class=HTMLResponse)
    def group_health():
        ensure_upgrade()
        with engine.begin() as c:
            rows=c.execute(text("""SELECT g.name,g.region,COUNT(r.id) raw_count,MAX(r.sent_at) last_source_message,
              MAX(r.ingested_at) last_ingested,CASE WHEN MAX(r.ingested_at) IS NULL THEN 'NEVER'
              WHEN MAX(r.ingested_at)<NOW()-INTERVAL '3 days' THEN 'STALE' ELSE 'HEALTHY' END health
              FROM wai_groups g LEFT JOIN wai_raw_messages r ON r.group_id=g.id
              GROUP BY g.id,g.name,g.region ORDER BY last_ingested DESC NULLS LAST,g.name""")).mappings().all()
        trs="".join(f"<tr><td>{esc(r['name'])}</td><td>{esc(r['region'])}</td><td>{r['raw_count']}</td>"
                    f"<td>{esc(r['last_source_message'])}</td><td>{esc(r['last_ingested'])}</td><td><b>{esc(r['health'])}</b></td></tr>" for r in rows)
        body=f"""<h2>Group Ingestion Health</h2><p class=muted>Shows whether low volume is real or an ingestion gap.</p>
        <div class=scroll><table><tr><th>Group</th><th>Region</th><th>Messages</th><th>Latest Source Message</th>
        <th>Last Ingested</th><th>Health</th></tr>{trs}</table></div>"""
        return HTMLResponse(shell("Group Health",body,"System Health"))

