from __future__ import annotations
VERSION="1.0.2-DECISION-SAFE-ADVISOR"

def _tasks(row):
    unknown=[str(x) for x in (row.get("unknown") or [])]
    mapping={"project":"verify project / development identity","location":"verify exact locality",
             "transaction":"verify transaction / tenure","property_family":"verify actual property type",
             "area":"verify usable / built-up area","price":"verify current asking price / rent",
             "budget":"verify commercial terms","tower":"verify tower","floor":"verify floor","unit":"verify unit"}
    out=[]
    for item in unknown:
        low=item.lower()
        for key,task in mapping.items():
            if key in low and task not in out:
                out.append(task); break
    return out[:6]

def build_advice(requirement, exact, strong, alternatives, role_counts):
    missing=[k for k in ("location","transaction","property_family") if not requirement.get(k)]
    rows=[]
    for tier,bucket in (("EXACT_OR_VERIFY",exact),("STRONG",strong),("ALTERNATIVE",alternatives)):
        for row in bucket:
            rows.append({"tier":tier,"record_id":row.get("record_id"),"score":row.get("score"),
                         "why":row.get("why") or row.get("reasons") or [],
                         "unknown":row.get("unknown") or [],"verification_tasks":_tasks(row),
                         "conflicts":row.get("conflicts") or []})
    if missing:
        decision="CLARIFY_CLIENT"; action="Clarify missing deal-critical requirement fields before sharing a shortlist."
    elif exact:
        decision="VERIFY_BEFORE_SHARE" if any(x["verification_tasks"] for x in rows[:3]) else "SHORTLIST_READY"
        action="Verify highlighted unknowns before sharing." if decision=="VERIFY_BEFORE_SHARE" else "Prepare the exact-match shortlist."
    elif strong:
        decision="VERIFY_BEFORE_SHARE"; action="Verify strongest candidates and explain each controlled deviation."
    elif alternatives:
        decision="CLIENT_APPROVAL_FOR_ALTERNATIVES"; action="Obtain client approval for stated relaxations before sharing alternatives."
    else:
        decision="NO_SAFE_MATCH"; action="Do not manufacture a result. Seek fresh inventory or clarify what may be relaxed."
    return {"version":VERSION,"decision":decision,"recommended_action":action,
            "client_requirement_missing":missing,"shortlist":rows[:5],
            "inventory_role_quality":{"supply_considered":int(role_counts.get("PROPERTY_SUPPLY",0)),
                                      "demand_excluded":int(role_counts.get("PROPERTY_DEMAND",0)),
                                      "ambiguous_excluded":int(role_counts.get("AMBIGUOUS",0)),
                                      "noise_excluded":int(role_counts.get("NOISE",0))},
            "safety":{"unknown_is_not_match":True,"unknown_is_not_conflict":True,
                      "no_fabricated_contacts":True,"no_fabricated_budget":True,
                      "no_fabricated_location":True,"automatic_send":False,
                      "human_review_before_share":True}}
