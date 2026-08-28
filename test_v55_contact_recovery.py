import re

PHONE_RE = re.compile(r"(?<!\d)(?:\+?91[\s().-]*)?[6-9](?:[\s().-]*\d){9}(?!\d)")

def normalize_phone(raw):
    d=re.sub(r"\D","",str(raw or ""))
    if len(d)==12 and d.startswith("91"): d=d[2:]
    elif len(d)==11 and d.startswith("0"): d=d[1:]
    return d if len(d)==10 and d[0] in "6789" else ""

def extract_phones(*values):
    seen=[]
    for value in values:
        textv=str(value or "")
        for m in PHONE_RE.finditer(textv):
            p=normalize_phone(m.group(0))
            if p and p not in seen: seen.append(p)
        digits=re.findall(r"(?:\+?91)?[6-9]\d{9}",re.sub(r"[\s().-]","",textv))
        for d in digits:
            p=normalize_phone(d)
            if p and p not in seen: seen.append(p)
    return seen

assert extract_phones("Tilak Raj Sharma +919990007886")==["9990007886"]
assert extract_phones("9540-60-99-00/ 9718-30-99-00")==["9540609900","9718309900"]
assert extract_phones("9999472447 | 9643455579")==["9999472447","9643455579"]
assert extract_phones("Satya Pal Singh 98181 29113")==["9818129113"]
print("V5.5 CONTACT RECOVERY TESTS: PASS")
