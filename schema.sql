CREATE TABLE IF NOT EXISTS pi_properties (
    id BIGSERIAL PRIMARY KEY,
    property_id VARCHAR(40) UNIQUE NOT NULL,
    property_name VARCHAR(255),
    entry_status VARCHAR(50) NOT NULL DEFAULT 'Active',
    availability_status VARCHAR(50) NOT NULL DEFAULT 'Available',
    property_type VARCHAR(100) NOT NULL,
    city VARCHAR(100) NOT NULL,
    location VARCHAR(255) NOT NULL,
    micro_market VARCHAR(255),
    address TEXT,
    google_maps_pin TEXT,
    area_sqft NUMERIC(14,2),
    available_area_sqft NUMERIC(14,2),
    minimum_area_sqft NUMERIC(14,2),
    maximum_area_sqft NUMERIC(14,2),
    floor VARCHAR(100),
    rent_or_sale VARCHAR(30),
    asking_rent_per_sqft NUMERIC(14,2),
    asking_sale_price NUMERIC(18,2),
    possession VARCHAR(100),
    nearby_brands TEXT,
    suitable_category TEXT,
    parking TEXT,
    ceiling_height VARCHAR(100),
    power_load VARCHAR(100),
    cam_per_sqft NUMERIC(14,2),
    security_deposit VARCHAR(100),
    frontage VARCHAR(100),
    owner_name VARCHAR(255),
    owner_contact VARCHAR(100),
    broker_name VARCHAR(255),
    broker_contact VARCHAR(100),
    verified_date DATE,
    verified_by VARCHAR(255),
    remarks TEXT,
    source VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pi_requirements (
    id BIGSERIAL PRIMARY KEY,
    requirement_id VARCHAR(40) UNIQUE NOT NULL,
    client_name VARCHAR(255),
    company_name VARCHAR(255),
    contact_phone VARCHAR(100),
    contact_email VARCHAR(255),
    requirement_type VARCHAR(100),
    property_type VARCHAR(100),
    city VARCHAR(100),
    preferred_locations TEXT,
    minimum_area_sqft NUMERIC(14,2),
    maximum_area_sqft NUMERIC(14,2),
    budget_min NUMERIC(18,2),
    budget_max NUMERIC(18,2),
    rent_or_sale VARCHAR(30),
    floor_preference VARCHAR(100),
    nearby_brands TEXT,
    suitable_category TEXT,
    parking_requirement TEXT,
    possession_timeline VARCHAR(100),
    additional_points TEXT,
    source VARCHAR(255),
    status VARCHAR(50) DEFAULT 'New',
    assigned_to VARCHAR(255),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pi_contacts (
    id BIGSERIAL PRIMARY KEY,
    contact_type VARCHAR(50) NOT NULL,
    name VARCHAR(255),
    company VARCHAR(255),
    phone VARCHAR(100),
    email VARCHAR(255),
    city VARCHAR(100),
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pi_sources (
    id BIGSERIAL PRIMARY KEY,
    source_type VARCHAR(50) NOT NULL,
    source_name VARCHAR(255),
    source_reference TEXT,
    original_filename VARCHAR(500),
    ingestion_status VARCHAR(50) DEFAULT 'Pending',
    processed_records INTEGER DEFAULT 0,
    error_message TEXT,
    ai_model VARCHAR(100),
    uploaded_at TIMESTAMPTZ DEFAULT NOW(),
    processed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS pi_media (
    id BIGSERIAL PRIMARY KEY,
    property_id VARCHAR(40) NOT NULL,
    media_type VARCHAR(30) NOT NULL,
    url TEXT NOT NULL,
    title VARCHAR(255),
    is_primary BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pi_matches (
    id BIGSERIAL PRIMARY KEY,
    requirement_id VARCHAR(40) NOT NULL,
    property_id VARCHAR(40) NOT NULL,
    match_score NUMERIC(5,2) DEFAULT 0,
    rank INTEGER,
    match_reasons JSONB DEFAULT '[]'::jsonb,
    exclusions JSONB DEFAULT '[]'::jsonb,
    status VARCHAR(50) DEFAULT 'READY_FOR_REVIEW',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pi_verification_log (
    id BIGSERIAL PRIMARY KEY,
    property_id VARCHAR(40),
    requirement_id VARCHAR(40),
    action VARCHAR(100) NOT NULL,
    performed_by VARCHAR(255),
    old_value JSONB,
    new_value JSONB,
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pi_properties_location ON pi_properties(city, location);
CREATE INDEX IF NOT EXISTS idx_pi_properties_availability ON pi_properties(availability_status);
CREATE INDEX IF NOT EXISTS idx_pi_properties_type ON pi_properties(property_type);
CREATE INDEX IF NOT EXISTS idx_pi_properties_area ON pi_properties(available_area_sqft);
CREATE INDEX IF NOT EXISTS idx_pi_requirements_status ON pi_requirements(status);
CREATE INDEX IF NOT EXISTS idx_pi_matches_requirement ON pi_matches(requirement_id, match_score DESC);
