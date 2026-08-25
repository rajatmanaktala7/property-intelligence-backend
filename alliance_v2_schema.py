from sqlalchemy import text

VERSION="2.0.0-NONDESTRUCTIVE"
PROTECTED=["pi_operational_properties","pi_operational_property_media","pi_operational_requirements","pi_properties","pi_requirements","pi_property_media","pi_sources","pi_newspaper_sources","pi_newspaper_properties"]
SCHEMA=[
"CREATE TABLE IF NOT EXISTS ai_source_history(id BIGSERIAL PRIMARY KEY,table_name TEXT NOT NULL,record_pk TEXT,operation TEXT NOT NULL,old_record JSONB,changed_at TIMESTAMPTZ DEFAULT NOW())",
"CREATE TABLE IF NOT EXISTS ai_property_identity(property_identity_id TEXT PRIMARY KEY,canonical_label TEXT,first_seen_at TIMESTAMPTZ DEFAULT NOW(),last_seen_at TIMESTAMPTZ DEFAULT NOW())",
"CREATE TABLE IF NOT EXISTS ai_contact_identity(contact_id TEXT PRIMARY KEY,normalized_phone TEXT UNIQUE,display_name TEXT,company_name TEXT,confidence NUMERIC(5,2) DEFAULT 0,updated_at TIMESTAMPTZ DEFAULT NOW())",
"CREATE TABLE IF NOT EXISTS ai_property_match_index(match_index_id BIGSERIAL PRIMARY KEY,property_identity_id TEXT NOT NULL,source_type TEXT NOT NULL,source_name TEXT,source_table TEXT NOT NULL,source_record_id TEXT NOT NULL,property_name TEXT,canonical_property_type TEXT,city TEXT,location_raw TEXT,location_normalized TEXT,transaction_type TEXT,area_min_sqft NUMERIC(14,2),area_max_sqft NUMERIC(14,2),rent_original TEXT,rent_psf_month NUMERIC(14,2),monthly_rent NUMERIC(16,2),sale_price NUMERIC(18,2),verification_status TEXT,availability_status TEXT,contact_reference_id TEXT,data_completeness_score NUMERIC(5,2) DEFAULT 0,data_confidence_score NUMERIC(5,2) DEFAULT 0,source_confidence_score NUMERIC(5,2) DEFAULT 0,freshness_score NUMERIC(5,2) DEFAULT 100,match_eligible BOOLEAN DEFAULT FALSE,original_payload JSONB,normalization_version TEXT DEFAULT '2.0.0',updated_at TIMESTAMPTZ DEFAULT NOW(),UNIQUE(source_table,source_record_id))",
"CREATE TABLE IF NOT EXISTS ai_requirement_index(requirement_index_id BIGSERIAL PRIMARY KEY,source_table TEXT NOT NULL,source_record_id TEXT NOT NULL,requirement_code TEXT,source_type TEXT,source_name TEXT,client_name TEXT,company_name TEXT,transaction_type TEXT,requirement_types JSONB DEFAULT '[]'::jsonb,preferred_locations_raw TEXT,minimum_area_sqft NUMERIC(14,2),maximum_area_sqft NUMERIC(14,2),maximum_monthly_rent NUMERIC(16,2),verification_status TEXT,status TEXT,match_eligible BOOLEAN DEFAULT FALSE,original_payload JSONB,normalization_version TEXT DEFAULT '2.0.0',updated_at TIMESTAMPTZ DEFAULT NOW(),UNIQUE(source_table,source_record_id))",
"CREATE TABLE IF NOT EXISTS ai_requirement_location(id BIGSERIAL PRIMARY KEY,requirement_index_id BIGINT NOT NULL,location_raw TEXT NOT NULL,location_normalized TEXT NOT NULL,priority TEXT DEFAULT 'PREFERRED',UNIQUE(requirement_index_id,location_normalized))",
"CREATE TABLE IF NOT EXISTS ai_match_v2(match_id BIGSERIAL PRIMARY KEY,requirement_index_id BIGINT NOT NULL,match_index_id BIGINT NOT NULL,match_score NUMERIC(5,2),location_score NUMERIC(5,2),area_score NUMERIC(5,2),rent_score NUMERIC(5,2),type_score NUMERIC(5,2),hard_rule_pass BOOLEAN DEFAULT TRUE,rejection_reasons JSONB DEFAULT '[]'::jsonb,positive_reasons JSONB DEFAULT '[]'::jsonb,status TEXT,matcher_version TEXT DEFAULT '2.0.0',updated_at TIMESTAMPTZ DEFAULT NOW(),UNIQUE(requirement_index_id,match_index_id))",
"CREATE INDEX IF NOT EXISTS ix_ai_match_loc ON ai_property_match_index(location_normalized)",
"CREATE INDEX IF NOT EXISTS ix_ai_match_eligible ON ai_property_match_index(match_eligible)"
]
PRESERVE="""CREATE OR REPLACE FUNCTION ai_preserve_before_change() RETURNS trigger AS $$ BEGIN INSERT INTO ai_source_history(table_name,record_pk,operation,old_record) VALUES(TG_TABLE_NAME,COALESCE(to_jsonb(OLD)->>'id',to_jsonb(OLD)->>'property_code',to_jsonb(OLD)->>'property_id',to_jsonb(OLD)->>'requirement_code',to_jsonb(OLD)->>'requirement_id',to_jsonb(OLD)->>'record_id'),TG_OP,to_jsonb(OLD)); IF TG_OP='DELETE' THEN RAISE EXCEPTION 'DELETE blocked by Alliance non-destructive data policy on table %',TG_TABLE_NAME; END IF; RETURN NEW; END; $$ LANGUAGE plpgsql"""

def exists(c,t): return bool(c.execute(text("SELECT to_regclass(:t)"),{"t":t}).scalar())
def setup(engine):
    with engine.begin() as c:
        for s in SCHEMA: c.execute(text(s))
        c.execute(text(PRESERVE))
        for t in PROTECTED:
            if exists(c,t):
                g="trg_ai_preserve_"+t
                c.execute(text(f'DROP TRIGGER IF EXISTS "{g}" ON "{t}"'))
                c.execute(text(f'CREATE TRIGGER "{g}" BEFORE UPDATE OR DELETE ON "{t}" FOR EACH ROW EXECUTE FUNCTION ai_preserve_before_change()'))
