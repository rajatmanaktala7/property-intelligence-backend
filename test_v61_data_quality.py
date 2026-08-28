from datetime import datetime
def ordinal(dt):
    day=dt.day
    suf="th" if 11<=day%100<=13 else {1:"st",2:"nd",3:"rd"}.get(day%10,"th")
    return f"{day}{suf} {dt.strftime('%b %Y')}"
assert ordinal(datetime(2026,8,28))=="28th Aug 2026"
assert ordinal(datetime(2026,8,1))=="1st Aug 2026"
assert ordinal(datetime(2026,8,2))=="2nd Aug 2026"
assert ordinal(datetime(2026,8,3))=="3rd Aug 2026"
assert ordinal(datetime(2026,8,11))=="11th Aug 2026"
print("V6.1 DATA QUALITY TESTS: PASS")
