from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from datetime import datetime, timezone
from uuid import uuid4

from property_brain.schemas import BurstGroup
from property_brain.stages.s3_segmentation import segment
from property_brain.stages.s4_extractor import extract
from property_brain.stages.s5_validator import validate
from property_brain.stages.s9_requirement_brain import parse_requirement


def run():
    burst = BurstGroup(
        burst_group_id=uuid4(),
        raw_ids=[uuid4()],
        source_type="whatsapp",
        sender="x",
        captured_at=datetime.now(timezone.utc),
        text="Available residential 4 BHK DLF Phase 4 Rent 17.50 Cr",
    )

    extracted = extract(segment(burst)[0])
    validation = validate(extracted)

    assert extracted.fields["transaction"] == "RENT"
    assert "money_scale_flagged" in validation.flags

    multi_property_burst = BurstGroup(
        burst_group_id=uuid4(),
        raw_ids=[uuid4()],
        source_type="whatsapp",
        sender="x",
        captured_at=datetime.now(timezone.utc),
        text=(
            "1. DLF Phase 1 3BHK Rent 1.25 L\n"
            "2. DLF Phase 4 4BHK Rent 1.60 L"
        ),
    )

    segments = segment(multi_property_burst)

    assert len(segments) == 2

    requirement = parse_requirement(
        "Looking for commercial restaurant on rent in Saket "
        "2500-3000 sqft budget 4-5 L"
    )

    assert requirement.transaction == "RENT"
    assert requirement.property_family == "COMMERCIAL"
    assert requirement.locality == "Saket"
    assert requirement.area_min_sqft == 2500
    assert requirement.area_max_sqft == 3000
    assert requirement.budget_max == 500000

    print("PROPERTY BRAIN V1 TESTS: PASS")


if __name__ == "__main__":
    run()
