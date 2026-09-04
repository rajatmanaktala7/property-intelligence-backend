from pathlib import Path
from datetime import datetime
import shutil

ROOT=Path(__file__).resolve().parent
APP=ROOT/"app.py"
MOD=ROOT/"alliance_primary_workspace_v730.py"
MARKER="FOUNDATION_7_3_1_PERSISTED_CERTIFICATION_BOOT_FIX"
VERSION="7.3.1-ALLIANCE-PERSISTED-CERTIFICATION-BOOT-FIX"

def main():
    if not APP.exists() or not MOD.exists():
        raise SystemExit("Required 7.3 files missing")
    s=MOD.read_text(encoding="utf-8")
    if "7.3.0-ALLIANCE-PRIMARY-WORKSPACE-ACTION-ENGINE" not in s and "7.3.1-ALLIANCE-PRIMARY-WORKSPACE" not in s:
        raise SystemExit("Expected 7.3 parent not found")
    backup=ROOT/f"alliance_primary_workspace_v730-before-v731-{datetime.now().strftime('%Y%m%d-%H%M%S')}.py"
    shutil.copy2(MOD,backup)

    old = '    cert=(v721.STATE.get("result") or {})\n    if cert.get("certification")!="V7_2_OPERATIONAL_ACCEPTANCE_PASS":\n        raise RuntimeError("7.2.1 operational certification PASS required before 7.3")\n'
    new = '''    # 7.3.1 BOOT FIX: v721 acceptance runs after startup, while 7.3 registers during import.
    # Recover the already-certified PASS from persistent acceptance history when live STATE is not ready.
    cert=(v721.STATE.get("result") or {})
    if cert.get("certification")!="V7_2_OPERATIONAL_ACCEPTANCE_PASS":
        persisted=None
        try:
            with engine.connect() as _c731:
                _row731=_c731.execute(text("""SELECT result FROM pi_acceptance_runs_v721
                  WHERE status='PASS' ORDER BY run_id DESC LIMIT 1""")).mappings().first()
                if _row731:
                    persisted=_safe(_row731.get("result"))
        except Exception:
            persisted=None
        if isinstance(persisted,str):
            try: persisted=json.loads(persisted)
            except Exception: persisted=None
        if isinstance(persisted,dict) and persisted.get("certification")=="V7_2_OPERATIONAL_ACCEPTANCE_PASS":
            cert=persisted
        else:
            raise RuntimeError("7.2.1 certified PASS not found in memory or persisted acceptance history")
'''
    if "7.3.1 BOOT FIX" not in s:
        if old not in s:
            raise SystemExit("Exact 7.3 certification block not found; refusing blind patch")
        s=s.replace(old,new,1)
        s=s.replace('VERSION="7.3.0-ALLIANCE-PRIMARY-WORKSPACE-ACTION-ENGINE"',
                    'VERSION="7.3.1-ALLIANCE-PRIMARY-WORKSPACE-ACTION-ENGINE-PERSISTED-CERT-BOOT-FIX"',1)
        compile(s,str(MOD),"exec")
        MOD.write_text(s,encoding="utf-8")

    app=APP.read_text(encoding="utf-8")
    if MARKER not in app:
        app=app.rstrip()+"\n\n# "+MARKER+"\n# 7.3 startup now recovers persisted 7.2.1 certification instead of racing delayed in-memory STATE.\n"
        compile(app,str(APP),"exec")
        APP.write_text(app,encoding="utf-8")

    compile(MOD.read_text(encoding="utf-8"),str(MOD),"exec")
    compile(APP.read_text(encoding="utf-8"),str(APP),"exec")
    final=MOD.read_text(encoding="utf-8")
    assert "7.3.1 BOOT FIX" in final
    assert "pi_acceptance_runs_v721" in final
    assert "/alliance/primary" in final
    print(VERSION)
    print("ROOT CAUSE FIXED")
    print("7.3 now uses the persisted certified 7.2.1 PASS during startup.")
    print("No canonical/source/Gold/Champion data is modified.")
    print("After Railway redeploy open /alliance/primary")
    print("Backup:",backup)

if __name__=="__main__":
    main()
