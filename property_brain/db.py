
from __future__ import annotations
import json
from pathlib import Path
from sqlalchemy import text

DDL=r"""
CREATE TABLE IF NOT EXISTS pb_raw_evidence (
 raw_id UUID PRIMARY KEY,source_type TEXT NOT NULL,source_ref TEXT NOT NULL,raw_text TEXT NOT NULL,
 sender TEXT,sender_phone TEXT,source_group TEXT,captured_at TIMESTAMPTZ NOT NULL,status TEXT NOT NULL DEFAULT 'new',
 created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),UNIQUE(source_type,source_ref));
CREATE INDEX IF NOT EXISTS ix_pb_raw_status ON pb_raw_evidence(status);
CREATE TABLE IF NOT EXISTS pb_line_tags (
 id BIGSERIAL PRIMARY KEY,raw_id UUID NOT NULL REFERENCES pb_raw_evidence(raw_id),line_no INT NOT NULL,
 tag TEXT NOT NULL,line_text TEXT NOT NULL,created_at TIMESTAMPTZ DEFAULT NOW());
CREATE TABLE IF NOT EXISTS pb_bursts (
 burst_group_id UUID PRIMARY KEY,source_type TEXT NOT NULL,sender TEXT,sender_phone TEXT,source_group TEXT,
 captured_at TIMESTAMPTZ NOT NULL,raw_ids JSONB NOT NULL,burst_text TEXT NOT NULL,created_at TIMESTAMPTZ DEFAULT NOW());
CREATE TABLE IF NOT EXISTS pb_segments (
 segment_id UUID PRIMARY KEY,burst_group_id UUID NOT NULL REFERENCES pb_bursts(burst_group_id),raw_ids JSONB NOT NULL,
 segment_text TEXT NOT NULL,split_method TEXT NOT NULL,insufficient BOOLEAN NOT NULL DEFAULT FALSE,created_at TIMESTAMPTZ DEFAULT NOW());
CREATE TABLE IF NOT EXISTS pb_extractions (
 extraction_id UUID PRIMARY KEY,segment_id UUID NOT NULL REFERENCES pb_segments(segment_id),raw_ids JSONB NOT NULL,
 classification TEXT NOT NULL,fields JSONB NOT NULL,field_confidence JSONB NOT NULL,extraction_method TEXT NOT NULL,
 validation_flags JSONB NOT NULL DEFAULT '[]'::jsonb,gate_outcome TEXT,created_at TIMESTAMPTZ DEFAULT NOW());
CREATE TABLE IF NOT EXISTS pb_location_aliases (
 alias_norm TEXT PRIMARY KEY,canonical_name TEXT NOT NULL,city TEXT,project_name TEXT,
 confidence NUMERIC(4,3) NOT NULL DEFAULT .95,status TEXT NOT NULL DEFAULT 'CONFIRMED',created_at TIMESTAMPTZ DEFAULT NOW());
CREATE TABLE IF NOT EXISTS pb_canonical_properties (
 property_id UUID PRIMARY KEY,fingerprint TEXT NOT NULL,transaction_type TEXT,property_family TEXT,property_subtype TEXT,
 city TEXT,locality TEXT,project_name TEXT,configuration TEXT,area_value NUMERIC,area_unit TEXT,area_sqft NUMERIC,
 rent_value NUMERIC,rent_period TEXT,sale_price_value NUMERIC,floor TEXT,furnishing TEXT,features JSONB NOT NULL DEFAULT '[]'::jsonb,
 contact_name TEXT,contact_numbers JSONB NOT NULL DEFAULT '[]'::jsonb,clean_description TEXT NOT NULL,
 overall_confidence NUMERIC(5,4) NOT NULL DEFAULT 0,verification_status TEXT NOT NULL DEFAULT 'UNVERIFIED',
 current_status TEXT NOT NULL DEFAULT 'ACTIVE',last_verified_at TIMESTAMPTZ,created_at TIMESTAMPTZ DEFAULT NOW(),updated_at TIMESTAMPTZ DEFAULT NOW());
CREATE INDEX IF NOT EXISTS ix_pb_can_loc_tx ON pb_canonical_properties(locality,transaction_type,property_family);
CREATE INDEX IF NOT EXISTS ix_pb_can_fp ON pb_canonical_properties(fingerprint);
CREATE TABLE IF NOT EXISTS pb_property_sources (
 id BIGSERIAL PRIMARY KEY,property_id UUID NOT NULL REFERENCES pb_canonical_properties(property_id),
 raw_id UUID NOT NULL REFERENCES pb_raw_evidence(raw_id),source_type TEXT NOT NULL,source_ref TEXT NOT NULL,
 captured_at TIMESTAMPTZ,contact_name TEXT,contact_numbers JSONB NOT NULL DEFAULT '[]'::jsonb,UNIQUE(property_id,raw_id));
CREATE TABLE IF NOT EXISTS pb_requirements (
 requirement_id UUID PRIMARY KEY,raw_text TEXT NOT NULL,transaction_type TEXT,property_family TEXT,intended_use TEXT,
 locality TEXT,acceptable_locations JSONB NOT NULL DEFAULT '[]'::jsonb,area_min_sqft NUMERIC,area_max_sqft NUMERIC,
 budget_min NUMERIC,budget_max NUMERIC,must_have JSONB NOT NULL DEFAULT '[]'::jsonb,preferred JSONB NOT NULL DEFAULT '[]'::jsonb,
 optional JSONB NOT NULL DEFAULT '[]'::jsonb,contact_name TEXT,contact_numbers JSONB NOT NULL DEFAULT '[]'::jsonb,
 confidence NUMERIC(5,4) NOT NULL DEFAULT 0,status TEXT NOT NULL DEFAULT 'NEW',created_at TIMESTAMPTZ DEFAULT NOW());
CREATE TABLE IF NOT EXISTS pb_corrections (
 correction_id UUID PRIMARY KEY,target_type TEXT NOT NULL,target_id UUID NOT NULL,field_name TEXT NOT NULL,
 previous_value JSONB,corrected_value JSONB NOT NULL,corrected_by TEXT,reason TEXT,created_at TIMESTAMPTZ DEFAULT NOW());
CREATE TABLE IF NOT EXISTS pb_feedback_outcomes (
 feedback_id UUID PRIMARY KEY,requirement_id UUID,property_id UUID,outcome TEXT NOT NULL,notes TEXT,actor TEXT,created_at TIMESTAMPTZ DEFAULT NOW());
CREATE TABLE IF NOT EXISTS pb_review_queue (
 review_id UUID PRIMARY KEY,queue_type TEXT NOT NULL,target_type TEXT NOT NULL,target_id UUID NOT NULL,payload JSONB NOT NULL,
 reason TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'OPEN',resolution JSONB,created_at TIMESTAMPTZ DEFAULT NOW(),resolved_at TIMESTAMPTZ);
CREATE INDEX IF NOT EXISTS ix_pb_review_open ON pb_review_queue(queue_type,status);
"""
def setup(engine):
    for stmt in [x.strip() for x in DDL.split(";") if x.strip()]:
        with engine.begin() as c:c.execute(text(stmt))
    seed_aliases(engine)
    return {"status":"OK","tables":"pb_*","startup_ddl":False}
def seed_aliases(engine):
    p=Path(__file__).parent/"config"/"location_aliases_seed.json"
    for alias,canonical in json.loads(p.read_text()).items():
        with engine.begin() as c:
            c.execute(text("""INSERT INTO pb_location_aliases(alias_norm,canonical_name,confidence,status)
            VALUES(:a,:c,.99,'CONFIRMED') ON CONFLICT(alias_norm) DO NOTHING"""),{"a":alias.upper().strip(),"c":canonical})
def table_exists(engine,name):
    try:
        with engine.connect() as c:return bool(c.execute(text("""SELECT 1 FROM information_schema.tables
          WHERE table_schema='public' AND table_name=:n"""),{"n":name}).first())
    except Exception:return False
