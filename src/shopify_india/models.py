from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Evidence(BaseModel):
    kind: str
    value: str
    source_url: str
    weight: int
    strength: Literal["strong", "supporting", "weak"] = "supporting"


class FetchRecord(BaseModel):
    requested_url: str
    final_url: str
    status_code: int
    fetched_at: str = Field(default_factory=utc_now)
    content_type: str = ""
    body: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    redirect_history: list[str] = Field(default_factory=list)
    robots_allowed: bool = True
    error: str | None = None


class StoreRecord(BaseModel):
    domain_url: str
    contacts: dict[str, list[str]] = Field(default_factory=lambda: {"emails": [], "phones": []})
    socials: dict[str, list[str]] = Field(default_factory=dict)
    category: str = ""
    description: str = ""
    logo_url: str = ""
    state: str = ""
    shopify_score: int = 0
    india_score: int = 0
    accepted: bool = False
    rejection_reasons: list[str] = Field(default_factory=list)
    shopify_evidence: list[Evidence] = Field(default_factory=list)
    india_evidence: list[Evidence] = Field(default_factory=list)
    extraction_sources: dict[str, str] = Field(default_factory=dict)
    redirect_history: list[str] = Field(default_factory=list)
    checked_at: str = Field(default_factory=utc_now)
    source_names: list[str] = Field(default_factory=list)

    def audit_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")
