from pathlib import Path
from datetime import datetime
import py_compile
import shutil
import sys

TARGET = Path("alliance_property_brain_foundation_v1.py")

if not TARGET.exists():
    raise SystemExit("ERROR: Run this from the property-intelligence-backend repository root.")

src = TARGET.read_text(encoding="utf-8")

EXPECTED_VERSION = 'VERSION = "1.9.29-ALLIANCE-PROPERTY-BRAIN-FOUNDATION"'
EXPECTED_MODE = 'MODE = "RESTORED_ALL_GOLD_FIXES_PLUS_FLOOR_SPLIT_1_9Z"'

if EXPECTED_VERSION not in src or EXPECTED_MODE not in src:
    raise SystemExit("ERROR: Expected verified Foundation 1.9.29 / 1.9Z baseline.")

required = {
    "1.9T3 repaired-source flow": "FOUNDATION_1_9T3_STAY_ON_REPAIRED_SOURCE",
    "1.9V project+BHK split": "FOUNDATION_1_9V_PROJECT_BHK_INVENTORY_SPLIT",
    "1.9W shared contacts": "FOUNDATION_1_9W_SHARED_TAIL_CONTACT_RECOVERY",
    "1.9X mixed pin+asset split": "FOUNDATION_1_9X_MIXED_PIN_ASSET_HEADING_SPLIT",
    "1.9Y same-source save/split flow": "loadNextInSourceOrGlobal",
    "1.9Z builder-floor split": "FOUNDATION_1_9Z_BUILDER_FLOOR_OPTION_SPLIT",
}
missing = [name for name, token in required.items() if token not in src]
if missing:
    raise SystemExit("ERROR: Prior fix missing: " + ", ".join(missing))

MARKER = "// FOUNDATION_1_9Z2_REFRESH_STAYS_ON_CURRENT_SPAN"
if MARKER in src:
    print("FOUNDATION_1_9Z2_ALREADY_INSTALLED")
    sys.exit(0)

old_sig = '''def next_span(
    engine,
    labeler_id: Optional[str] = None,
    skip_span_ids: Optional[str] = None,
    source_message_id: Optional[str] = None,
) -> Dict[str, Any]:
'''
new_sig = '''def next_span(
    engine,
    labeler_id: Optional[str] = None,
    skip_span_ids: Optional[str] = None,
    source_message_id: Optional[str] = None,
    span_id: Optional[str] = None,
) -> Dict[str, Any]:
'''
if old_sig not in src:
    raise SystemExit("ERROR: next_span signature baseline not found.")
src = src.replace(old_sig, new_sig, 1)

old_source_validation = '''    source_filter = str(source_message_id or "").strip()
    if source_filter and not re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
        r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}",
        source_filter,
    ):
        source_filter = ""

    with engine.connect() as conn:
'''
new_source_validation = '''    source_filter = str(source_message_id or "").strip()
    if source_filter and not re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
        r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}",
        source_filter,
    ):
        source_filter = ""

    # Foundation 1.9Z2: exact current-span restore after browser refresh.
    span_filter = str(span_id or "").strip()
    if span_filter and not re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
        r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}",
        span_filter,
    ):
        span_filter = ""

    with engine.connect() as conn:
'''
if old_source_validation not in src:
    raise SystemExit("ERROR: source filter validation block not found.")
src = src.replace(old_source_validation, new_source_validation, 1)

old_where = '''                  AND (
                      :source_filter = ''
                      OR sp.source_message_id::text = :source_filter
                  )
                  AND (
                      :skip_csv = ''
'''
new_where = '''                  AND (
                      :source_filter = ''
                      OR sp.source_message_id::text = :source_filter
                  )
                  AND (
                      :span_filter = ''
                      OR sp.span_id::text = :span_filter
                  )
                  AND (
                      :skip_csv = ''
'''
if old_where not in src:
    raise SystemExit("ERROR: next_span SQL filter block not found.")
src = src.replace(old_where, new_where, 1)

old_params = '''            {
                "skip_csv": skip_csv,
                "source_filter": source_filter,
            },
'''
new_params = '''            {
                "skip_csv": skip_csv,
                "source_filter": source_filter,
                "span_filter": span_filter,
            },
'''
if old_params not in src:
    raise SystemExit("ERROR: next_span SQL params block not found.")
src = src.replace(old_params, new_params, 1)

old_route = '''    @app.get("/api/property-brain-foundation/next-span")
    def next_span_route(
        labeler_id: Optional[str] = Query(None),
        skip_span_ids: Optional[str] = Query(None),
        source_message_id: Optional[str] = Query(None),
    ):
        return _json_response(
            next_span(
                engine,
                labeler_id,
                skip_span_ids=skip_span_ids,
                source_message_id=source_message_id,
            )
        )
'''
new_route = '''    @app.get("/api/property-brain-foundation/next-span")
    def next_span_route(
        labeler_id: Optional[str] = Query(None),
        skip_span_ids: Optional[str] = Query(None),
        source_message_id: Optional[str] = Query(None),
        span_id: Optional[str] = Query(None),
    ):
        return _json_response(
            next_span(
                engine,
                labeler_id,
                skip_span_ids=skip_span_ids,
                source_message_id=source_message_id,
                span_id=span_id,
            )
        )
'''
if old_route not in src:
    raise SystemExit("ERROR: next-span API route baseline not found.")
src = src.replace(old_route, new_route, 1)

old_state = '''let current=null;
let splitDraft=[];
let skippedSpanIds=[];
'''
new_state = '''let current=null;
let splitDraft=[];
let skippedSpanIds=[];

// FOUNDATION_1_9Z2_REFRESH_STAYS_ON_CURRENT_SPAN
const GOLD_LAST_SPAN_KEY="alliance_gold_lab_last_span_id";
const GOLD_LAST_SOURCE_KEY="alliance_gold_lab_last_source_id";
'''
if old_state not in src:
    raise SystemExit("ERROR: Gold Lab JS state block not found.")
src = src.replace(old_state, new_state, 1)

old_load_sig = '''async function loadNext(preferredSourceId=null){
'''
new_load_sig = '''async function loadNext(preferredSourceId=null, preferredSpanId=null){
'''
if old_load_sig not in src:
    raise SystemExit("ERROR: loadNext JS signature not found.")
src = src.replace(old_load_sig, new_load_sig, 1)

old_params_js = '''    if(preferredSourceId){
      params.set("source_message_id", preferredSourceId);
    }
    const query=params.toString() ? "?"+params.toString() : "";
'''
new_params_js = '''    if(preferredSourceId){
      params.set("source_message_id", preferredSourceId);
    }
    if(preferredSpanId){
      params.set("span_id", preferredSpanId);
    }
    const query=params.toString() ? "?"+params.toString() : "";
'''
if old_params_js not in src:
    raise SystemExit("ERROR: Gold Lab next-span query block not found.")
src = src.replace(old_params_js, new_params_js, 1)

old_current = '''  current=d.span;
  splitDraft=[];
'''
new_current = '''  current=d.span;
  try{
    localStorage.setItem(GOLD_LAST_SPAN_KEY,String(current.span_id||""));
    localStorage.setItem(GOLD_LAST_SOURCE_KEY,String(current.source_message_id||""));
  }catch(e){}
  splitDraft=[];
'''
if old_current not in src:
    raise SystemExit("ERROR: current=d.span JS block not found.")
src = src.replace(old_current, new_current, 1)

old_boot = '''refreshProgress();
loadNext();
</script>
'''
new_boot = '''async function restoreGoldLabPosition(){
  await refreshProgress();

  let savedSpan="";
  let savedSource="";
  try{
    savedSpan=localStorage.getItem(GOLD_LAST_SPAN_KEY)||"";
    savedSource=localStorage.getItem(GOLD_LAST_SOURCE_KEY)||"";
  }catch(e){}

  if(savedSpan){
    await loadNext(savedSource||null,savedSpan);
    if(current) return;
  }

  if(savedSource){
    await loadNext(savedSource);
    if(current) return;
  }

  await loadNext();
}
restoreGoldLabPosition();
</script>
'''
if old_boot not in src:
    raise SystemExit("ERROR: Gold Lab initial boot block not found.")
src = src.replace(old_boot, new_boot, 1)

src = src.replace(
    EXPECTED_VERSION,
    'VERSION = "1.9.30-ALLIANCE-PROPERTY-BRAIN-FOUNDATION"',
    1,
)
src = src.replace(
    EXPECTED_MODE,
    'MODE = "GOLD_REFRESH_STAYS_ON_CURRENT_SPAN_1_9Z2"',
    1,
)

backup = TARGET.with_name(
    TARGET.name + ".before-1_9Z2-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".bak"
)
shutil.copy2(TARGET, backup)

try:
    TARGET.write_text(src, encoding="utf-8")
    py_compile.compile(str(TARGET), doraise=True)

    final = TARGET.read_text(encoding="utf-8")

    for name, token in required.items():
        if token not in final:
            raise RuntimeError("Prior fix lost after patch: " + name)

    checks = {
        "refresh marker": MARKER,
        "exact span API filter": "span_filter",
        "span query parameter": 'span_id: Optional[str] = Query(None)',
        "browser span storage": "GOLD_LAST_SPAN_KEY",
        "browser source storage": "GOLD_LAST_SOURCE_KEY",
        "restore function": "restoreGoldLabPosition()",
        "exact restore call": "await loadNext(savedSource||null,savedSpan);",
        "same-source fallback": "await loadNext(savedSource);",
    }
    for name, token in checks.items():
        if token not in final:
            raise RuntimeError("1.9Z2 validation failed: " + name)

    inside_script = False
    for line_no, line in enumerate(final.splitlines(), start=1):
        low = line.lower()
        if "<script" in low:
            inside_script = True
        if inside_script and line.lstrip().startswith("#"):
            raise RuntimeError(
                f"Invalid Python-style # comment inside JavaScript at line {line_no}"
            )
        if "</script>" in low:
            inside_script = False

except Exception:
    shutil.copy2(backup, TARGET)
    raise

print("FOUNDATION_1_9Z2_INSTALL_PASS")
print("Refresh behavior: exact current Gold span restored")
print("If exact span is no longer pending: same source restored")
print("Only when source is complete: global queue resumes")
print("All prior Gold fixes: PRESERVED")
print("1.9Z builder-floor split: PRESERVED")
print("1.9Y same-source save/split flow: PRESERVED")
print("1.9W shared contacts: PRESERVED")
print("Gold Lab JS regression guard: PASS")
print("Version: 1.9.30-ALLIANCE-PROPERTY-BRAIN-FOUNDATION")
print("Mode: GOLD_REFRESH_STAYS_ON_CURRENT_SPAN_1_9Z2")
print("Backup:", backup)
