from __future__ import annotations
import re
VERSION="4.0.0-ALLIANCE-BABY-USE-CASE-MARKET-INTELLIGENCE"
def _n(v): return re.sub(r"\s+"," ",str(v or "").strip()).upper()
def _txt(r): return " ".join(str(r.get(k) or "") for k in ("raw_text","category","purpose","property_type","retailer_name","brand_name"))
def _g():
 import alliance_micromarket_knowledge_v1 as g; return g
def category(r):
 t=_n(_txt(r))
 if any(x in t for x in ("GROCERY","SUPERMARKET","DAILY NEED","CONVENIENCE")): return "GROCERY"
 if any(x in t for x in ("GARMENT","APPAREL","FASHION","CLOTHING")): return "FASHION"
 if any(x in t for x in ("JEWELLERY","JEWELRY","GOLD","DIAMOND")): return "JEWELLERY"
 if any(x in t for x in ("LUXURY RETAIL","DESIGNER","LUXURY STORE")): return "LUXURY"
 if "OFFICE" in t: return "OFFICE"
 if any(x in t for x in ("RESTAURANT","F&B","FNB","BAR","LOUNGE","QSR","FOOD OUTLET")): return "FNB"
 if "CAFE" in t or "CAFÉ" in t: return "FNB"
 return "RETAIL"
FASHION={"LAJPAT NAGAR","KAMLA NAGAR","RAJOURI GARDEN","SOUTH EXTENSION","CONNAUGHT PLACE","KAROL BAGH","SECTOR 18 NOIDA","VASANT KUNJ"}
JEWELLERY={"KAROL BAGH","SOUTH EXTENSION","CONNAUGHT PLACE","RAJOURI GARDEN","LAJPAT NAGAR"}
LUXURY={"KHAN MARKET","SOUTH EXTENSION","CONNAUGHT PLACE","GK-1 M BLOCK","GALLERIA MARKET"}
FNB_TYPES={"FNB","FNB_HIGH_STREET","DESTINATION_FNB","TOURISM_FNB","LIFESTYLE_FNB","PREMIUM_FNB","HIGH_STREET_FNB"}
def _dist(a,b):
 g=_g(); x=g.market(a);y=g.market(b);return g.km(x,y) if x and y else None
def _direct(a,b):
 g=_g();x=g.market(a);y=g.market(b);return bool(x and y and any(_n(z)==_n(y[1]) for z in (x[7] or [])))
def classify(req,target,evidence=None):
 origin=req.get("locality") or req.get("location") or ""; ev=evidence or {}; c=category(req);g=_g();tm=g.market(target);d=_dist(origin,target)
 if not origin or not target:return {"class":"SEARCH_TARGET","score":0,"category":c,"reasons":["location incomplete"]}
 if _n(origin)==_n(target):return {"class":"EXACT","score":100,"category":c,"reasons":["exact requested market"]}
 score=0;reasons=[];ok=False
 if c in ("GROCERY","OFFICE"):
  if d is not None and d<=3:
   if c=="OFFICE" and tm and "OFFICE" not in set(tm[6] or []): reasons.append("nearby but not office-use market")
   else: score=90;ok=True;reasons.append("within 3 km use-case radius")
 elif c=="FASHION":
  if _n(target) in FASHION:score=70;ok=True;reasons.append("fashion/apparel cluster")
  bc=int(ev.get("relevant_brand_count") or 0)
  if bc>=3:score=min(100,score+20);ok=True;reasons.append(f"{bc} relevant brands evidenced")
 elif c=="JEWELLERY":
  if _n(target) in JEWELLERY:score=75;ok=True;reasons.append("jewellery cluster")
 elif c=="LUXURY":
  if _n(target) in LUXURY:score=80;ok=True;reasons.append("premium/luxury cluster")
 elif c=="FNB":
  cnt=int(ev.get("restaurant_count") or 0)
  if cnt>=4:score=min(100,70+cnt);ok=True;reasons.append(f"restaurant cluster evidenced: {cnt}")
  elif tm and tm[3] in FNB_TYPES and _direct(origin,target):score=65;ok=True;reasons.append("curated adjacent F&B cluster")
  else:reasons.append("needs >=4 restaurant/cafe evidence or curated adjacent F&B cluster")
 else:
  if tm and "RETAIL" in set(tm[6] or []):score+=55;reasons.append("retail-use market")
  if _direct(origin,target):score+=20;reasons.append("direct curated adjacency")
  ok=score>=65
 return {"class":"USE_CASE_COMPARABLE" if ok else "SEARCH_TARGET","score":score,"category":c,"distance_km":None if d is None else round(d,2),"reasons":reasons,"recommendable":ok}
def evidence_card(req,target,evidence=None):
 e=evidence or {};x=classify(req,target,e)
 return {**x,"market":target,"restaurant_count":int(e.get("restaurant_count") or 0),"relevant_brand_count":int(e.get("relevant_brand_count") or 0),"source_urls":list(e.get("source_urls") or []),"truth_rule":"Market suitability is not property availability; property remains subject to Master verification."}
def exam():
 import alliance_baby_cre_copilot_v1 as b
 cases=[
 ("grocery near","Need grocery store in Saket","Malviya Nagar","USE_CASE_COMPARABLE",{}),
 ("grocery far","Need grocery store in Saket","Rajouri Garden","SEARCH_TARGET",{}),
 ("office near","Need office on lease in DLF Cyber City","MG Road Gurugram","USE_CASE_COMPARABLE",{}),
 ("office far","Need office in BKC","Lower Parel","SEARCH_TARGET",{}),
 ("fashion","Need garment store in Saket","Lajpat Nagar","USE_CASE_COMPARABLE",{}),
 ("fashion west","Need garment store in Saket","Rajouri Garden","USE_CASE_COMPARABLE",{}),
 ("fashion wrong","Need garment store in Saket","Hauz Khas","SEARCH_TARGET",{}),
 ("fnb evidence","Need restaurant in Saket","Defence Colony","USE_CASE_COMPARABLE",{"restaurant_count":8}),
 ("fnb low","Need restaurant in Saket","Karol Bagh","SEARCH_TARGET",{"restaurant_count":3}),
 ("fnb threshold","Need restaurant in Saket","Karol Bagh","USE_CASE_COMPARABLE",{"restaurant_count":4}),
 ("luxury","Need luxury retail store in Saket","Khan Market","USE_CASE_COMPARABLE",{}),
 ("jewellery","Need jewellery showroom in Saket","Karol Bagh","USE_CASE_COMPARABLE",{})]
 out=[]
 for name,q,target,want,e in cases:
  req=b._parse_free_text(q);got=classify(req,target,e);out.append({"name":name,"pass":got["class"]==want,"got":got["class"],"want":want})
 p=sum(x["pass"] for x in out);return {"status":"PASS" if p==len(out) else "FAIL","tested":len(out),"passed":p,"failed":len(out)-p,"tests":out}
def self_test():
 r=exam();assert r["status"]=="PASS",r;return True
