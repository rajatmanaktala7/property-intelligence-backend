from pathlib import Path
import shutil,time,py_compile
ROOT=Path(__file__).resolve().parent
m=ROOT/'alliance_magazine_fresh_v822.py'; g=ROOT/'alliance_magazine_safe_gateway_v660.py'
if not m.exists() or not g.exists(): raise SystemExit('Required Magazine files missing.')
ms=m.read_text(encoding='utf-8'); gs=g.read_text(encoding='utf-8')
if '8.2.7-MULTIMODEL-OPENROUTER-FALLBACK' not in ms: raise SystemExit('SAFETY STOP: expected 8.2.7 baseline not found.')
stamp=time.strftime('%Y%m%d-%H%M%S')
mb=ROOT/f'alliance_magazine_fresh_v822.py.before-v828-{stamp}.bak'; gb=ROOT/f'alliance_magazine_safe_gateway_v660.py.before-v828-{stamp}.bak'
shutil.copy2(m,mb); shutil.copy2(g,gb)

anchor="10. extraction_confidence is 0-100."
addition='''10. extraction_confidence is 0-100.
OUTPUT CONTRACT:
Return JSON only. No markdown and no commentary.
The top-level object MUST contain a "properties" array.
Each property object MUST use these keys:
section_heading, original_description, exact_address, locality, city, property_type,
transaction_type, area_value, area_unit, floor, amount_raw, contact_name,
contact_number, extraction_confidence.
Use null for unknown fields. original_description must remain exact visible text.'''
if anchor not in ms: raise SystemExit('SAFETY STOP: extraction prompt anchor missing.')
ms=ms.replace(anchor,addition,1)
ms=ms.replace("VERSION='8.2.7-MULTIMODEL-OPENROUTER-FALLBACK'","VERSION='8.2.8-SCHEMA-CONTRACT-TRANSIENT-RETRY'",1)
ms=ms.replace('Fresh Magazine PDF Database · CRE OS 8.2.7','Fresh Magazine PDF Database · CRE OS 8.2.8')
ms=ms.replace('Multi-model Gemini · OpenRouter vision fallback · safe checkpoint resume','Schema-safe extraction · multi-model fallback · checkpoint resume')

old='''            except Exception as exc:
                if _is_quota(exc):
                    self._mark_quota(p,exc);continue
                self.events.append({"provider":p["label"],"event":"ERROR","error":f"{type(exc).__name__}: {exc}"[:1200]})
                continue'''
new='''            except Exception as exc:
                if _is_quota(exc):
                    self._mark_quota(p,exc);continue
                raw=str(exc)
                if "503" in raw and ("UNAVAILABLE" in raw.upper() or "HIGH DEMAND" in raw.upper()):
                    self.events.append({"provider":p["label"],"event":"TRANSIENT_503","error":raw[:1200]})
                    for delay in (3,8):
                        if self.calls>=self.max_calls: break
                        time.sleep(delay); self.calls+=1
                        try:
                            data=self._call_gemini(p,img,prompt) if p["kind"]=="gemini" else self._call_openrouter(p,img,prompt)
                            self.events.append({"provider":p["label"],"event":"SUCCESS_AFTER_RETRY"})
                            return data,{"status":"OK","provider":p["label"],"retried":True}
                        except Exception as retry_exc:
                            if _is_quota(retry_exc):
                                self._mark_quota(p,retry_exc); break
                            self.events.append({"provider":p["label"],"event":"RETRY_ERROR","error":f"{type(retry_exc).__name__}: {retry_exc}"[:1200]})
                    continue
                self.events.append({"provider":p["label"],"event":"ERROR","error":f"{type(exc).__name__}: {exc}"[:1200]})
                continue'''
if old not in gs: raise SystemExit('SAFETY STOP: gateway exception block missing.')
gs=gs.replace(old,new,1)
gs=gs.replace('VERSION="6.6.1-ALLIANCE-MAGAZINE-MULTIMODEL-OPENROUTER-GATEWAY"','VERSION="6.6.2-ALLIANCE-MAGAZINE-SCHEMA-TRANSIENT-RETRY-GATEWAY"',1)
m.write_text(ms,encoding='utf-8'); g.write_text(gs,encoding='utf-8')
try:
    py_compile.compile(str(m),doraise=True); py_compile.compile(str(g),doraise=True); py_compile.compile(str(ROOT/'production_entrypoint.py'),doraise=True)
except Exception:
    shutil.copy2(mb,m); shutil.copy2(gb,g); print('COMPILE FAILED - restored.'); raise
mc=m.read_text(encoding='utf-8'); gc=g.read_text(encoding='utf-8')
for x in ['8.2.8-SCHEMA-CONTRACT-TRANSIENT-RETRY','OUTPUT CONTRACT:','properties" array']:
    if x not in mc:
        shutil.copy2(mb,m); shutil.copy2(gb,g); raise SystemExit('Validation failed; restored.')
if 'SUCCESS_AFTER_RETRY' not in gc:
    shutil.copy2(mb,m); shutil.copy2(gb,g); raise SystemExit('Gateway retry validation failed; restored.')
print('PASS: CRE OS 8.2.8 schema + transient retry fix installed.')
print('Stored PDF/database untouched. Resume remains checkpoint-safe.')
print('Backups:',mb.name,gb.name)
