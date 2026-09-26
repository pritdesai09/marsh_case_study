"""Request bodies for the API. FastAPI validates these automatically (bad input -> 422)."""
from pydantic import BaseModel, Field


class ProfileRequest(BaseModel):
    company: str = Field(..., max_length=200, description="Company name typed by the advisor")
    wikidata_id: str | None = Field(None, max_length=20, description="Set when the advisor picks a specific match")