"""Request bodies for the API. FastAPI validates these automatically (bad input -> 422)."""
from pydantic import BaseModel, Field


class ProfileRequest(BaseModel):
    company: str = Field(..., max_length=200, description="Company name typed by the advisor")
    wikidata_id: str | None = Field(None, max_length=20, description="Set when the advisor picks a specific match")


class PitchRequest(BaseModel):
    profile: dict = Field(..., description="The profile returned by /api/profile")
    policies: list[str] = Field(default_factory=list, max_length=4, description="Policy codes, e.g. ['HDFC', 'NIVA']")


class ExportRequest(BaseModel):
    pitch: dict = Field(..., description="The pitch returned by /api/pitch")
    decisions: dict = Field(default_factory=dict, description="Advisor decisions per claim id (used from Phase 5)")


class AuditRequest(BaseModel):
    pitch: dict = Field(..., description="The pitch returned by /api/pitch")


class ClaimAuditRequest(BaseModel):
    pitch: dict = Field(..., description="The pitch the claim belongs to")
    claim_id: str = Field(..., max_length=20, description="e.g. S3-2")
    text: str = Field(..., max_length=600, description="The advisor's edited wording")