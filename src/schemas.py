"""Request bodies for the API. FastAPI validates these automatically (bad input -> 422)."""
from typing import Literal

from pydantic import BaseModel, Field


class ProfileRequest(BaseModel):
    company: str = Field(..., max_length=200, description="Company name typed by the advisor")
    wikidata_id: str | None = Field(None, max_length=20, description="Set when the advisor picks a specific match")


class PitchRequest(BaseModel):
    profile: dict = Field(..., description="The profile returned by /api/profile")
    policies: list[str] = Field(default_factory=list, max_length=4, description="Policy codes, e.g. ['HDFC', 'NIVA']")


class AuditRequest(BaseModel):
    pitch_id: str = Field(..., max_length=20, description="The id of a pitch returned by /api/pitch")


class ClaimActionRequest(BaseModel):
    action: Literal["approve", "reject", "reset", "edit", "revert"]
    text: str | None = Field(None, max_length=600, description="New wording (only for action='edit')")


class ExportRequest(BaseModel):
    audit_id: str = Field(..., max_length=20, description="The audit whose approved claims go into the deck")