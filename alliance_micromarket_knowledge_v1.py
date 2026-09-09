from __future__ import annotations
import math,re
VERSION="1.0.0-MICROMARKET-KNOWLEDGE"
# Curated market centroids. Coordinates are approximate locality centroids, never parcel coordinates.
MARKETS=[
("DELHI_NCR","Saket","DELHI","MIXED",28.5244,77.2066,["RETAIL","RESTAURANT","CAFE","OFFICE"],["Malviya Nagar","GK-1 M Block","Hauz Khas","Nehru Place"]),
("DELHI_NCR","Malviya Nagar","DELHI","HIGH_STREET",28.5339,77.2110,["RETAIL","RESTAURANT","CAFE"],["Saket","Hauz Khas","GK-1 M Block"]),
("DELHI_NCR","GK-1 M Block","DELHI","HIGH_STREET",28.5493,77.2360,["RETAIL","RESTAURANT","CAFE"],["GK-2 M Block","Nehru Place","Saket"]),
("DELHI_NCR","GK-2 M Block","DELHI","HIGH_STREET",28.5332,77.2430,["RETAIL","RESTAURANT","CAFE"],["GK-1 M Block","Nehru Place"]),
("DELHI_NCR","Hauz Khas","DELHI","FNB",28.5494,77.2001,["RESTAURANT","CAFE","RETAIL"],["Green Park","Saket","Malviya Nagar"]),
("DELHI_NCR","Green Park","DELHI","HIGH_STREET",28.5580,77.2060,["RETAIL","RESTAURANT","CAFE"],["Hauz Khas","South Extension"]),
("DELHI_NCR","South Extension","DELHI","HIGH_STREET",28.5686,77.2205,["RETAIL","RESTAURANT","CAFE"],["Defence Colony","Green Park","Lajpat Nagar"]),
("DELHI_NCR","Defence Colony","DELHI","FNB",28.5735,77.2300,["RESTAURANT","CAFE","RETAIL"],["South Extension","Lajpat Nagar","Khan Market"]),
("DELHI_NCR","Khan Market","DELHI","PREMIUM_HIGH_STREET",28.6004,77.2270,["LUXURY_RETAIL","RESTAURANT","CAFE"],["Defence Colony","Connaught Place"]),
("DELHI_NCR","Connaught Place","DELHI","CBD_HIGH_STREET",28.6315,77.2167,["RETAIL","RESTAURANT","CAFE","OFFICE"],["Khan Market","Karol Bagh"]),
("DELHI_NCR","Lajpat Nagar","DELHI","HIGH_STREET",28.5700,77.2373,["RETAIL","RESTAURANT"],["South Extension","Defence Colony","Nehru Place"]),
("DELHI_NCR","Nehru Place","DELHI","DISTRICT_CENTRE",28.5491,77.2533,["OFFICE","RETAIL","RESTAURANT"],["GK-1 M Block","Kalkaji","Saket"]),
("DELHI_NCR","Rajouri Garden","DELHI","HIGH_STREET",28.6490,77.1220,["RETAIL","RESTAURANT","CAFE"],["Punjabi Bagh","Janakpuri"]),
("DELHI_NCR","Punjabi Bagh","DELHI","FNB_HIGH_STREET",28.6687,77.1290,["RESTAURANT","CAFE","RETAIL"],["Rajouri Garden"]),
("DELHI_NCR","Karol Bagh","DELHI","HIGH_STREET",28.6519,77.1909,["RETAIL","RESTAURANT"],["Connaught Place"]),
("DELHI_NCR","Aerocity","DELHI","DESTINATION",28.5504,77.1218,["RESTAURANT","RETAIL","HOTEL","OFFICE"],["Vasant Kunj","DLF Cyber City"]),
("DELHI_NCR","Vasant Kunj","DELHI","MALL_RETAIL",28.5200,77.1580,["RETAIL","RESTAURANT","CAFE"],["Aerocity","Saket"]),
("DELHI_NCR","DLF Cyber City","GURUGRAM","CBD",28.4949,77.0890,["OFFICE","RESTAURANT","RETAIL"],["Cyber Hub","MG Road Gurugram","Golf Course Road"]),
("DELHI_NCR","Cyber Hub","GURUGRAM","DESTINATION_FNB",28.4951,77.0880,["RESTAURANT","CAFE","RETAIL"],["DLF Cyber City","Galleria Market","Sector 29 Gurugram"]),
("DELHI_NCR","Galleria Market","GURUGRAM","PREMIUM_HIGH_STREET",28.4670,77.0810,["RETAIL","RESTAURANT","CAFE"],["Golf Course Road","Cyber Hub","MG Road Gurugram"]),
("DELHI_NCR","Sector 29 Gurugram","GURUGRAM","FNB",28.4680,77.0630,["RESTAURANT","CAFE","ENTERTAINMENT"],["Galleria Market","Cyber Hub"]),
("DELHI_NCR","Golf Course Road","GURUGRAM","PREMIUM_CORRIDOR",28.4480,77.0990,["RETAIL","RESTAURANT","OFFICE"],["Galleria Market"]),
("DELHI_NCR","MG Road Gurugram","GURUGRAM","MALL_HIGH_STREET",28.4790,77.0800,["RETAIL","RESTAURANT","OFFICE"],["Cyber Hub","Galleria Market"]),
("DELHI_NCR","Sector 18 Noida","NOIDA","HIGH_STREET_MALL",28.5708,77.3210,["RETAIL","RESTAURANT","CAFE"],["Sector 38A Noida","Sector 104 Noida"]),
("DELHI_NCR","Sector 38A Noida","NOIDA","DESTINATION_MALL",28.5677,77.3215,["RETAIL","RESTAURANT","ENTERTAINMENT"],["Sector 18 Noida"]),
("DELHI_NCR","Sector 104 Noida","NOIDA","FNB_HIGH_STREET",28.5410,77.3660,["RESTAURANT","CAFE","RETAIL"],["Sector 18 Noida","Noida Expressway"]),
("DELHI_NCR","Noida Expressway","NOIDA","OFFICE_GROWTH",28.5100,77.4100,["OFFICE","RETAIL","RESTAURANT"],["Sector 104 Noida"]),
("GOA","Panjim","GOA","CBD",15.4909,73.8278,["RETAIL","RESTAURANT","CAFE","OFFICE"],["Miramar","Porvorim","Dona Paula"]),
("GOA","Porvorim","GOA","GROWTH_CORRIDOR",15.5362,73.8294,["RETAIL","RESTAURANT","OFFICE"],["Panjim","Mapusa","Candolim"]),
("GOA","Mapusa","GOA","TOWN_MARKET",15.5915,73.8087,["RETAIL","RESTAURANT"],["Porvorim","Siolim","Calangute"]),
("GOA","Candolim","GOA","TOURISM_FNB",15.5180,73.7620,["RESTAURANT","CAFE","RETAIL","HOTEL"],["Calangute","Porvorim"]),
("GOA","Calangute","GOA","TOURISM_HIGH_STREET",15.5440,73.7550,["RESTAURANT","CAFE","RETAIL","HOTEL"],["Candolim","Baga","Mapusa"]),
("GOA","Baga","GOA","TOURISM_FNB",15.5553,73.7517,["RESTAURANT","CAFE","CLUB","HOTEL"],["Calangute","Anjuna"]),
("GOA","Anjuna","GOA","LIFESTYLE_FNB",15.5733,73.7418,["RESTAURANT","CAFE","CLUB","HOTEL"],["Vagator","Assagao","Baga"]),
("GOA","Vagator","GOA","LIFESTYLE_FNB",15.5970,73.7448,["RESTAURANT","CAFE","HOTEL"],["Anjuna","Assagao","Siolim"]),
("GOA","Assagao","GOA","PREMIUM_LIFESTYLE",15.6060,73.7690,["RESTAURANT","CAFE","BOUTIQUE_RETAIL","VILLA"],["Anjuna","Vagator","Siolim"]),
("GOA","Siolim","GOA","LIFESTYLE_RESIDENTIAL",15.6243,73.7679,["RESTAURANT","CAFE","VILLA","BOUTIQUE_RETAIL"],["Assagao","Vagator","Morjim","Mapusa"]),
("GOA","Morjim","GOA","BEACH_LIFESTYLE",15.6300,73.7390,["RESTAURANT","CAFE","HOTEL","VILLA"],["Siolim"]),
("GOA","Margao","GOA","TOWN_CBD",15.2832,73.9862,["RETAIL","RESTAURANT","OFFICE"],[]),
("MUMBAI","Bandra West","MUMBAI","PREMIUM_HIGH_STREET",19.0607,72.8362,["RETAIL","RESTAURANT","CAFE"],["Khar West","BKC"]),
("MUMBAI","Khar West","MUMBAI","PREMIUM_HIGH_STREET",19.0690,72.8340,["RETAIL","RESTAURANT","CAFE"],["Bandra West"]),
("MUMBAI","BKC","MUMBAI","CBD",19.0676,72.8697,["OFFICE","RESTAURANT","PREMIUM_RETAIL"],["Bandra West","Lower Parel"]),
("MUMBAI","Lower Parel","MUMBAI","MIXED_CBD",18.9988,72.8258,["OFFICE","RETAIL","RESTAURANT","CAFE"],["Worli","BKC"]),
("MUMBAI","Worli","MUMBAI","PREMIUM_MIXED",19.0178,72.8178,["OFFICE","RESTAURANT","PREMIUM_RETAIL"],["Lower Parel"]),
("MUMBAI","Powai","MUMBAI","MIXED_TECH_FNB",19.1176,72.9060,["OFFICE","RETAIL","RESTAURANT","CAFE"],["Andheri East"]),
("MUMBAI","Andheri West","MUMBAI","HIGH_STREET_FNB",19.1364,72.8296,["RETAIL","RESTAURANT","CAFE","ENTERTAINMENT"],["Juhu","Bandra West","Goregaon"]),
("MUMBAI","Andheri East","MUMBAI","OFFICE_CORRIDOR",19.1136,72.8697,["OFFICE","RETAIL","RESTAURANT"],["Powai","BKC"]),
("MUMBAI","Juhu","MUMBAI","PREMIUM_FNB",19.1075,72.8263,["RESTAURANT","CAFE","PREMIUM_RETAIL"],["Andheri West","Bandra West"]),
("MUMBAI","Goregaon","MUMBAI","MIXED_GROWTH",19.1663,72.8526,["OFFICE","RETAIL","RESTAURANT"],["Malad","Andheri West"]),
("MUMBAI","Malad","MUMBAI","MALL_OFFICE",19.1874,72.8484,["OFFICE","RETAIL","RESTAURANT"],["Goregaon"]),
("MUMBAI","Fort","MUMBAI","CBD",18.9340,72.8350,["OFFICE","RETAIL","RESTAURANT"],["Nariman Point"]),
("MUMBAI","Nariman Point","MUMBAI","CBD",18.9256,72.8242,["OFFICE","RESTAURANT","PREMIUM_RETAIL"],["Fort"]),
("MUMBAI","Vashi","NAVI_MUMBAI","CBD_RETAIL",19.0771,72.9986,["OFFICE","RETAIL","RESTAURANT"],[]),
]
ALIASES={"SAKET DISTRICT CENTRE":"Saket","SELECT CITYWALK":"Saket","CP":"Connaught Place","CYBERCITY":"DLF Cyber City","GALLERIA":"Galleria Market","NOIDA SECTOR 18":"Sector 18 Noida","PANAJI":"Panjim","ASSAGAON":"Assagao","BANDRA":"Bandra West","GK1":"GK-1 M Block","GK 1":"GK-1 M Block","GK2":"GK-2 M Block","GK 2":"GK-2 M Block"}
def norm(s): return re.sub(r"\s+"," ",str(s or "").strip()).upper()
def market(name):
    n=ALIASES.get(norm(name),name)
    return next((x for x in MARKETS if norm(x[1])==norm(n)),None)
def km(a,b):
    R=6371;p1,p2=math.radians(a[4]),math.radians(b[4]);dp=math.radians(b[4]-a[4]);dl=math.radians(b[5]-a[5])
    z=math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*R*math.asin(math.sqrt(z))
def use_tokens(req):
    n=norm(req)
    if any(x in n for x in ("RESTAURANT","CAFE","F&B","FNB","BAR","CLUB","LOUNGE")):return {"RESTAURANT","CAFE"}
    if any(x in n for x in ("SHOP","RETAIL","SHOWROOM","BRAND","STORE")):return {"RETAIL","LUXURY_RETAIL","BOUTIQUE_RETAIL","PREMIUM_RETAIL"}
    if "OFFICE" in n:return {"OFFICE"}
    if any(x in n for x in ("VILLA","RESIDENTIAL","APARTMENT","FLAT","HOUSE")):return {"VILLA"}
    return set()
def suggest(location,requirement="",limit=6):
    base=market(location)
    if not base:return {"status":"UNKNOWN_LOCATION","location":location,"suggestions":[]}
    wanted=use_tokens(requirement); candidates=[]
    for x in MARKETS:
        if x[1]==base[1] or x[0]!=base[0]:continue
        d=km(base,x);overlap=len(wanted & set(x[6])) if wanted else 1;same=x[2]==base[2];explicit=x[1] in base[7]
        score=(35 if explicit else 0)+(30 if overlap else -20)+(20 if same else 0)+max(0,25-min(d,25))
        candidates.append((score,d,x,overlap,explicit))
    candidates.sort(key=lambda z:(-z[0],z[1]))
    return {"status":"OK","location":base[1],"requirement":requirement,"suggestions":[{"location":x[1],"city":x[2],"market_type":x[3],"distance_km":round(d,1),"fit":"USE_MATCH" if overlap else "PROXIMITY_ONLY","score":round(score,1),"reason":("known comparable/adjacent market; " if explicit else "")+("requirement-use compatible; " if overlap else "")+f"approx. {d:.1f} km from {base[1]}"} for score,d,x,overlap,explicit in candidates[:limit]]}
