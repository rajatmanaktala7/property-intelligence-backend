
from __future__ import annotations
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID
from pydantic import BaseModel, Field

SourceType = Literal["whatsapp","newspaper","magazine","manual","master_db"]

class RawEvidence(BaseModel):
    raw_id: UUID
    source_type: SourceType
    source_ref: str
    raw_text: str
    sender: Optional[str] = None
    sender_phone: Optional[str] = None
    source_group: Optional[str] = None
    captured_at: datetime
    status: str = "new"

class LineTag(BaseModel):
    raw_id: UUID
    line_no: int
    tag: Literal["availability_signal","requirement_signal","noise","ambiguous"]
    line_text: str

class BurstGroup(BaseModel):
    burst_group_id: UUID
    raw_ids: List[UUID]
    source_type: SourceType
    sender: Optional[str] = None
    source_group: Optional[str] = None
    captured_at: datetime
    text: str

class Segment(BaseModel):
    segment_id: UUID
    raw_ids: List[UUID]
    text: str
    split_method: Literal["deterministic","llm","single"]
    burst_group_id: UUID
    insufficient: bool = False

class ExtractedProperty(BaseModel):
    extraction_id: UUID
    segment_id: UUID
    raw_ids: List[UUID]
    classification: Literal["AVAILABILITY","REQUIREMENT","NOISE","AMBIGUOUS"]
    fields: Dict[str, Any] = Field(default_factory=dict)
    field_confidence: Dict[str, float] = Field(default_factory=dict)
    extraction_method: Literal["regex","hybrid","llm"] = "regex"

class ValidationResult(BaseModel):
    extraction_id: UUID
    passed: bool
    flags: List[str] = Field(default_factory=list)
    corrected_fields: Dict[str, Any] = Field(default_factory=dict)

class LocationResolution(BaseModel):
    extraction_id: UUID
    city: Optional[str] = None
    locality_id: Optional[str] = None
    locality_name: Optional[str] = None
    project_id: Optional[str] = None
    project_name: Optional[str] = None
    resolution_confidence: float = 0.0
    resolution_method: Literal["alias_table","direct","unresolved","human_confirmed"] = "unresolved"

class GateResult(BaseModel):
    extraction_id: UUID
    outcome: Literal["clean","holding","rejected"]
    reasons: List[str] = Field(default_factory=list)

class Requirement(BaseModel):
    requirement_id: Optional[UUID] = None
    raw_text: str
    transaction: Optional[str] = None
    property_family: Optional[str] = None
    intended_use: Optional[str] = None
    locality: Optional[str] = None
    acceptable_locations: List[str] = Field(default_factory=list)
    area_min_sqft: Optional[float] = None
    area_max_sqft: Optional[float] = None
    budget_min: Optional[float] = None
    budget_max: Optional[float] = None
    must_have: List[str] = Field(default_factory=list)
    preferred: List[str] = Field(default_factory=list)
    optional: List[str] = Field(default_factory=list)
    contact_name: Optional[str] = None
    contact_numbers: List[str] = Field(default_factory=list)
    confidence: float = 0.0
