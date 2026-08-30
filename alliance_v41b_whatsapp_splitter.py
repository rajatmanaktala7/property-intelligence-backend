from __future__ import annotations
import os, uuid
from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import create_engine, text
import alliance_phase4_whatsapp_purity as purity

VERSION="4.1.1-PHASE4.1-PURITY-SPLITTER"
classify_listing_vs_requirement=lambda raw:("REQUIREMENT" if purity.classify_text(raw)=="REQUIREMENT" else "LISTING")
split_multi_listing=purity.split_multi_listing
expand_specific_rent_variants=purity.expand_specific_rent_variants
group_message_bursts=purity.group_message_bursts
normalize_listing=purity.normalize_listing

def register(core):
    router=APIRouter();need_login=core.need_login
    @router.get("/api/v40/status")
    def status(req:Request):
        need_login(req)
        return {"version":VERSION,"status":"OK","rent_variant_split":False,"price_in_identity":False,
                "multi_property_boundary":"STRONG_ONLY","unsplittable_multi_property":"REVIEW_HOLD",
                "ambiguous_money":"REVIEW_HOLD","unknown_transaction_default":"Unknown"}
    core.app.include_router(router);return router
