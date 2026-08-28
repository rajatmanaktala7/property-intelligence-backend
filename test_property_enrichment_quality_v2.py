from pathlib import Path
import importlib.util

FILE = Path(__file__).resolve().parent / "property_brain" / "stages" / "s8b_property_enrichment.py"
spec = importlib.util.spec_from_file_location("s8b", FILE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

sale = {
    "transaction_type": "SALE",
    "property_family": "RESIDENTIAL",
    "locality": "Panaji",
    "property_subtype": "Apartment",
    "configuration": "3 BHK",
    "area_sqft": 2583.34,
    "sale_price_value": 36000000,
    "floor": None,
    "furnishing": None,
    "project_name": None,
}
sale_missing = m._missing_fields(sale)
assert "sale_price_value" not in sale_missing
assert "rent_value" not in sale_missing
assert "project_name" in sale_missing

rent = {
    "transaction_type": "LEASE",
    "property_family": "COMMERCIAL",
    "locality": "Saket",
    "property_subtype": "Restaurant",
    "area_sqft": 3000,
    "rent_value": 140000,
}
rent_missing = m._missing_fields(rent)
assert "rent_value" not in rent_missing
assert "sale_price_value" not in rent_missing

assert m._format_indian_money(36000000) == "Rs 3.60 Cr"
assert m._format_indian_money(140000, "/month") == "Rs 1.40 Lakh/month"
assert m._format_indian_money(90000, "/month") == "Rs 90,000/month"

description = m.build_description(
    {
        "transaction_type": "SALE",
        "property_family": "RESIDENTIAL",
        "property_subtype": "Apartment",
        "locality": "Panaji",
        "configuration": "3 BHK",
        "area_sqft": 2583.34,
        "sale_price_value": 36000000,
        "contact_name": "Supriya",
        "contact_numbers": ["9607999436"],
        "verification_status": "UNVERIFIED",
    },
    ["project_name", "floor", "furnishing"],
)
assert "Rs 3.60 Cr" in description
assert "Rent" not in description
assert "Still Required:" in description

print("PROPERTY ENRICHMENT QUALITY V2 TESTS: PASS")
