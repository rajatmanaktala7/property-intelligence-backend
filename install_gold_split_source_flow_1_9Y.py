from pathlib import Path
from datetime import datetime
import py_compile
import shutil
import sys

TARGET = Path("alliance_property_brain_foundation_v1.py")

if not TARGET.exists():
    raise SystemExit("ERROR: alliance_property_brain_foundation_v1.py not found. Run from repo root.")

src = TARGET.read_text(encoding="utf-8")

if 'VERSION = "1.9.26-ALLIANCE-PROPERTY-BRAIN-FOUNDATION"' not in src:
    raise SystemExit("ERROR: expected Foundation 1.9.26 baseline.")
if 'MODE = "MIXED_PIN_ASSET_ATOMIC_SPLIT_1_9X"' not in src:
    raise SystemExit("ERROR: expected Foundation 1.9X mode.")
if "# FOUNDATION_1_9Y_STAY_ON_SPLIT_SOURCE" in src:
    print("FOUNDATION_1_9Y_ALREADY_INSTALLED")
    sys.exit(0)

loadnext_end = r'''  }catch(e){
    current=null;
    document.getElementById("source").innerText="Gold Lab runtime error. Do not label this record.";
    document.getElementById("span").innerText="";
    document.getElementById("msg").innerText="ERROR: "+e.message;
  }
}

async function repairCurrentSource(){
'''

loadnext_replacement = r'''  }catch(e){
    current=null;
    document.getElementById("source").innerText="Gold Lab runtime error. Do not label this record.";
    document.getElementById("span").innerText="";
    document.getElementById("msg").innerText="ERROR: "+e.message;
  }
}

# FOUNDATION_1_9Y_STAY_ON_SPLIT_SOURCE
async function loadNextInSourceOrGlobal(sourceId){
  const preferred=String(sourceId||"").trim();
  if(preferred){
    await loadNext(preferred);
    if(current) return;
  }
  await loadNext();
}

async function repairCurrentSource(){
'''

if loadnext_end not in src:
    raise SystemExit("ERROR: loadNext() end anchor not found.")
src = src.replace(loadnext_end, loadnext_replacement, 1)

old_confirm = r'''async function confirmAtomicSplit(){
  try{
    if(!current) throw new Error("No span loaded");
    const labeler=document.getElementById("labeler").value.trim(); if(!labeler) throw new Error("Enter Labeler ID / team member name");
    const children=[...document.querySelectorAll(".splitChild")].map((x,i)=>({
      text:x.value.trim(),
      context:(splitDraft[i]&&splitDraft[i].context)||{},
      proposal:(splitDraft[i]&&splitDraft[i].proposal)||{}
    })).filter(x=>x.text);
    if(children.length<2) throw new Error("At least two child spans are required");
    const payload={labeler_id:labeler,children:children,reason:document.getElementById("notes").value.trim()||"Human atomic split in Gold Lab",invalidate_existing_labels:false};
    const r=await fetch(`/api/property-brain-foundation/span/${current.span_id}/split`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    const d=await r.json(); if(!r.ok) throw new Error(d.detail||JSON.stringify(d));
    document.getElementById("msg").innerText=`Real split complete: ${d.child_count} active child spans created. Parent preserved as SUPERSEDED.`;
    cancelAtomicSplit(); await refreshProgress(); await loadNext();
  }catch(e){ document.getElementById("msg").innerText="ERROR: "+e.message; }
}
'''

new_confirm = r'''async function confirmAtomicSplit(){
  try{
    if(!current) throw new Error("No span loaded");
    const splitSourceId=String(current.source_message_id||"");
    const labeler=document.getElementById("labeler").value.trim(); if(!labeler) throw new Error("Enter Labeler ID / team member name");
    const children=[...document.querySelectorAll(".splitChild")].map((x,i)=>({
      text:x.value.trim(),
      context:(splitDraft[i]&&splitDraft[i].context)||{},
      proposal:(splitDraft[i]&&splitDraft[i].proposal)||{}
    })).filter(x=>x.text);
    if(children.length<2) throw new Error("At least two child spans are required");
    const payload={labeler_id:labeler,children:children,reason:document.getElementById("notes").value.trim()||"Human atomic split in Gold Lab",invalidate_existing_labels:false};
    const r=await fetch(`/api/property-brain-foundation/span/${current.span_id}/split`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    const d=await r.json(); if(!r.ok) throw new Error(d.detail||JSON.stringify(d));
    cancelAtomicSplit();
    await refreshProgress();
    await loadNextInSourceOrGlobal(splitSourceId);
    if(current && String(current.source_message_id||"")===splitSourceId){
      document.getElementById("msg").innerText=`Real split complete: ${d.child_count} active child spans created. Now reviewing the first unlabeled child from the same source.`;
    }else{
      document.getElementById("msg").innerText=`Real split complete: ${d.child_count} active child spans created. Source complete; resumed global queue.`;
    }
  }catch(e){ document.getElementById("msg").innerText="ERROR: "+e.message; }
}
'''

if old_confirm not in src:
    raise SystemExit("ERROR: confirmAtomicSplit() baseline block not found.")
src = src.replace(old_confirm, new_confirm, 1)

old_save_start = r'''async function save(){
  try{
    if(!current) throw new Error("No span loaded");
    const boundaryAction=document.getElementById("boundary").value;
'''

new_save_start = r'''async function save(){
  try{
    if(!current) throw new Error("No span loaded");
    const savedSourceId=String(current.source_message_id||"");
    const boundaryAction=document.getElementById("boundary").value;
'''

if old_save_start not in src:
    raise SystemExit("ERROR: save() start anchor not found.")
src = src.replace(old_save_start, new_save_start, 1)

old_save_tail = r'''    document.getElementById("requirementFields").value="{}";
    await refreshProgress();
    await loadNext();
  }catch(e){
'''

new_save_tail = r'''    document.getElementById("requirementFields").value="{}";
    await refreshProgress();
    await loadNextInSourceOrGlobal(savedSourceId);
    if(current && String(current.source_message_id||"")===savedSourceId){
      document.getElementById("msg").innerText="Saved to Gold Dataset. Loaded next unlabeled span from the same source.";
    }else if(current){
      document.getElementById("msg").innerText="Saved to Gold Dataset. Source complete; resumed global queue.";
    }else{
      document.getElementById("msg").innerText="Saved to Gold Dataset. No unlabeled spans remain.";
    }
  }catch(e){
'''

if old_save_tail not in src:
    raise SystemExit("ERROR: save() tail anchor not found.")
src = src.replace(old_save_tail, new_save_tail, 1)

src = src.replace(
    'VERSION = "1.9.26-ALLIANCE-PROPERTY-BRAIN-FOUNDATION"',
    'VERSION = "1.9.27-ALLIANCE-PROPERTY-BRAIN-FOUNDATION"',
    1,
)
src = src.replace(
    'MODE = "MIXED_PIN_ASSET_ATOMIC_SPLIT_1_9X"',
    'MODE = "SPLIT_SOURCE_LABELING_FLOW_1_9Y"',
    1,
)

backup = TARGET.with_name(
    TARGET.name + ".before-1_9Y-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".bak"
)
shutil.copy2(TARGET, backup)

try:
    TARGET.write_text(src, encoding="utf-8")
    py_compile.compile(str(TARGET), doraise=True)

    checks = {
        "helper": "async function loadNextInSourceOrGlobal(sourceId)" in src,
        "confirm_source": 'const splitSourceId=String(current.source_message_id||"");' in src,
        "confirm_flow": "await loadNextInSourceOrGlobal(splitSourceId);" in src,
        "save_source": 'const savedSourceId=String(current.source_message_id||"");' in src,
        "save_flow": "await loadNextInSourceOrGlobal(savedSourceId);" in src,
        "repair_preserved": "await loadNext(repairedSourceId);" in src,
        "skip_global_preserved": "async function skipNext()" in src and "await loadNext();" in src,
        "splitter_preserved": "# FOUNDATION_1_9X_MIXED_PIN_ASSET_HEADING_SPLIT" in src,
        "contacts_preserved": "# FOUNDATION_1_9W_SHARED_TAIL_CONTACT_RECOVERY" in src,
        "version": 'VERSION = "1.9.27-ALLIANCE-PROPERTY-BRAIN-FOUNDATION"' in src,
        "mode": 'MODE = "SPLIT_SOURCE_LABELING_FLOW_1_9Y"' in src,
    }
    failed = [k for k, ok in checks.items() if not ok]
    if failed:
        raise RuntimeError("1.9Y regression failed: " + ", ".join(failed))

except Exception:
    shutil.copy2(backup, TARGET)
    raise

print("FOUNDATION_1_9Y_INSTALL_PASS")
print("Confirm Split: stays on same source")
print("Gold Save: advances within same source")
print("Source complete: resumes global queue")
print("Skip / Next: remains global")
print("1.9X atomic splitter: preserved")
print("1.9W shared contacts: preserved")
print("Version: 1.9.27-ALLIANCE-PROPERTY-BRAIN-FOUNDATION")
print("Mode: SPLIT_SOURCE_LABELING_FLOW_1_9Y")
print("Backup:", backup)
