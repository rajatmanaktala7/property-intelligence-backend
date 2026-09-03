from __future__ import annotations

from typing import List, Set

from sqlalchemy import text

import alliance_property_brain_foundation_v1 as foundation


PATCH_VERSION = "1.9L-GOLD-BATCH2-FRESH-REFILL"
MAX_FETCH_PER_SOURCE = 15000


def _existing_gold_fingerprints(engine) -> Set[str]:
    with engine.connect() as conn:
        return {
            str(row[0])
            for row in conn.execute(
                text("SELECT source_fingerprint FROM alliance_gold_source_messages")
            ).all()
            if row[0]
        }


def _fresh_fetch_distinct_text(
    engine,
    table_name: str,
    column_name: str,
    limit: int,
) -> List[str]:
    qt = foundation._safe_identifier(table_name)
    qc = foundation._safe_identifier(column_name)
    requested = max(1, int(limit))
    fetch_limit = min(max(requested * 80, 2500), MAX_FETCH_PER_SOURCE)
    existing = _existing_gold_fingerprints(engine)

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f'''
                SELECT {qc}::text AS raw_text
                FROM {qt}
                WHERE {qc} IS NOT NULL
                  AND length(trim({qc}::text)) >= 20
                ORDER BY md5({qc}::text)
                LIMIT :n
                '''
            ),
            {"n": fetch_limit},
        ).all()

    seen: Set[str] = set()
    fresh: List[str] = []
    for row in rows:
        raw = str(row[0] or "").strip()
        if not raw:
            continue
        fp = foundation._fingerprint(raw)
        if fp in existing or fp in seen:
            continue
        seen.add(fp)
        fresh.append(raw)
        if len(fresh) >= requested:
            break
    return fresh


def _patch_gold_lab_ui() -> None:
    ui = foundation.LAB_UI

    progress_marker = '<div id="progress" class="small" style="margin-top:10px">Loading progress...</div>'
    progress_replacement = progress_marker + '''
<div class="actions" style="margin-top:12px">
<button id="refillFreshBtn" class="primary" type="button" onclick="refillFreshBatch()">Load Fresh Batch</button>
<span id="refillStatus" class="small"></span>
</div>'''
    if 'id="refillFreshBtn"' not in ui and progress_marker in ui:
        ui = ui.replace(progress_marker, progress_replacement, 1)

    js_marker = 'async function refreshProgress(){'
    js_function = r'''
async function refillFreshBatch(){
  const btn=document.getElementById("refillFreshBtn");
  const status=document.getElementById("refillStatus");
  const msg=document.getElementById("msg");
  try{
    if(btn) btn.disabled=true;
    if(status) status.innerText="Loading fresh Academy candidates...";
    if(msg) msg.innerText="";
    const r=await fetch("/api/property-brain-foundation/sources/import-curriculum",{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({total_messages:70})
    });
    const raw=await r.text();
    let d={};
    try{d=JSON.parse(raw)}catch(e){throw new Error("Backend returned non-JSON response")}
    if(!r.ok) throw new Error(d.detail||d.message||"Fresh batch refill failed");
    const inserted=Number(d.inserted_proposed_spans||0);
    const sources=Number(d.inserted_sources||0);
    if(status) status.innerText = inserted>0
      ? `Fresh batch ready: ${sources} sources / ${inserted} spans added.`
      : "No new trusted source evidence was available.";
    skippedSpanIds=[];
    await refreshProgress();
    await loadNext();
  }catch(e){
    if(status) status.innerText="ERROR: "+e.message;
  }finally{
    if(btn) btn.disabled=false;
  }
}

'''
    if 'async function refillFreshBatch()' not in ui and js_marker in ui:
        ui = ui.replace(js_marker, js_function + js_marker, 1)

    foundation.LAB_UI = ui


def install_patch() -> None:
    foundation._fetch_distinct_text = _fresh_fetch_distinct_text
    foundation.VERSION = "1.9.11-ALLIANCE-PROPERTY-BRAIN-FOUNDATION"
    foundation.MODE = "GOLD_BATCH2_FRESH_REFILL_1_9L"
    _patch_gold_lab_ui()


install_patch()

# ---------------------------------------------------------------------------
# V18.1 property edit/media/area/rent-sale hotfix
# Installs before production_entrypoint loads the core application.
# ---------------------------------------------------------------------------
import alliance_optional_property_modules as _optional_property_modules_v181

_original_optional_register_v181 = _optional_property_modules_v181.register

def _register_optional_with_property_v181(wrapped):
    result = _original_optional_register_v181(wrapped)
    try:
        import alliance_property_edit_hotfix_v181 as _property_v181
        v181_result = _property_v181.register(wrapped)
        if isinstance(result, dict):
            result["property_edit_v181"] = v181_result
        try:
            import alliance_unified_data_organizer_v182 as _data_v182
            v182_result = _data_v182.register(wrapped)
            if isinstance(result, dict):
                result["unified_data_v182"] = v182_result
        except Exception as exc:
            if isinstance(result, dict):
                result["unified_data_v182"] = {"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}
            print("[unified-data-v182] warning:", type(exc).__name__, str(exc))
    except Exception as exc:
        if isinstance(result, dict):
            result["property_edit_v181"] = {
                "status": "ERROR",
                "error": f"{type(exc).__name__}: {exc}",
                "fail_safe": True,
            }
        print("[property-edit-v181] warning:", type(exc).__name__, str(exc))
    return result

_optional_property_modules_v181.register = _register_optional_with_property_v181

# V18.3 upload/clipboard repair
import alliance_optional_property_modules as _opm183
_old183=_opm183.register
def _reg183(wrapped):
    result=_old183(wrapped)
    try:
        import alliance_upload_clipboard_fix_v183 as _m183
        r=_m183.register(wrapped)
        if isinstance(result,dict): result["upload_clipboard_v183"]=r
    except Exception as exc:
        if isinstance(result,dict): result["upload_clipboard_v183"]={"status":"ERROR","error":f"{type(exc).__name__}: {exc}","fail_safe":True}
        print("[v183]",type(exc).__name__,str(exc))
    return result
_opm183.register=_reg183

from production_entrypoint import app  # noqa: E402,F401
