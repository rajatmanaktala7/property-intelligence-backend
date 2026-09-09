from __future__ import annotations

VERSION = "1.0.0-ALLIANCE-CORE-CONTRACT"
PRIMARY_DASHBOARD = "/alliance/primary"

OLD_DASHBOARD_PATHS = {
    "/team-dashboard-v376",
    "/team-dashboard",
    "/dashboard",
    "/dashboard.html",
    "/alliance-dashboard",
    "/alliance/dashboard",
}

ROUTE_REGISTRY = {
    "dashboard": {"path": "/alliance/primary", "authority": "TEAM", "lifecycle": "ACTIVE"},
    "properties": {"path": "/alliance/primary/properties", "authority": "MASTER_PROPERTY", "lifecycle": "ACTIVE"},
    "property_manual": {"path": "/property-manual", "authority": "OPERATIONAL_TO_MASTER", "lifecycle": "ACTIVE"},
    "requirements": {"path": "/alliance/primary/requirements", "authority": "MASTER_REQUIREMENT", "lifecycle": "ACTIVE"},
    "requirement_manual": {"path": "/requirement-manual", "authority": "OPERATIONAL_TO_MASTER", "lifecycle": "ACTIVE"},
    "availability": {"path": "/alliance/primary/availability", "authority": "PROPERTY_AVAILABILITY", "lifecycle": "ACTIVE"},
    "matcher": {"path": "/alliance/primary/matcher", "authority": "MASTER_PROPERTY_AND_REQUIREMENT", "lifecycle": "ACTIVE"},
    "deal_match": {"path": "/deal-match-ai-v60", "authority": "MASTER_PROPERTY", "lifecycle": "ACTIVE"},
    "followups": {"path": "/alliance/primary/followups", "authority": "WORKFLOW", "lifecycle": "ACTIVE"},
    "whatsapp": {"path": "/whatsapp-live", "authority": "SOURCE_EVIDENCE", "lifecycle": "ACTIVE"},
    "newspaper": {"path": "/capture-intelligence", "authority": "SOURCE_EVIDENCE", "lifecycle": "ACTIVE"},
    "property_discovery": {"path": "/property-discovery", "authority": "DISCOVERY", "lifecycle": "ACTIVE"},
    "hospitality": {"path": "/hospitality-intelligence", "authority": "INTELLIGENCE", "lifecycle": "ACTIVE"},
    "retail": {"path": "/retail-expansion", "authority": "INTELLIGENCE", "lifecycle": "ACTIVE"},
    "commercial": {"path": "/commercial-intelligence", "authority": "INTELLIGENCE", "lifecycle": "ACTIVE"},
    "requirement_discovery": {"path": "/requirement-discovery", "authority": "DISCOVERY", "lifecycle": "ACTIVE"},
    "marketing_contacts": {"path": "/marketing-contacts", "authority": "INTELLIGENCE", "lifecycle": "ACTIVE"},
    "ai_control": {"path": "/alliance/primary/ai-control", "authority": "SYSTEM", "lifecycle": "ACTIVE"},
    "data_health": {"path": "/alliance/primary/data-health", "authority": "SYSTEM", "lifecycle": "ACTIVE"},
    "system_doctor": {"path": "/alliance/system-doctor", "authority": "SYSTEM", "lifecycle": "ACTIVE"},
    "workspace": {"path": "/workspace", "authority": "ADMIN", "lifecycle": "ADMIN_ONLY"},
}

PROTECTED_INVARIANTS = {
    "dashboard_authority": PRIMARY_DASHBOARD,
    "matcher_authority": "pi_properties",
    "whatsapp_role": "SOURCE_EVIDENCE_ONLY",
    "client_contact_exposure": False,
    "historical_backfill": "NOT_AUTHORIZED",
    "property_verification_separate_from_availability": True,
    "bottom_dashboard_navigation": False,
}
