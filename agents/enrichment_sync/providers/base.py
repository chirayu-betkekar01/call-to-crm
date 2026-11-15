"""Provider interface for Agent 3 enrichment. A provider is any object with `name` and
`enrich(EnrichQuery) -> EnrichResult | None`."""
from __future__ import annotations

from typing import Optional, Protocol

from pydantic import BaseModel


# --------------------------------------------------------------------------- #
# Provider protocol
# --------------------------------------------------------------------------- #
class EnrichQuery(BaseModel):
    full_name: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None
    domain: Optional[str] = None
    company: Optional[str] = None
    title: Optional[str] = None


class EnrichResult(BaseModel):
    provider: str
    is_mock: bool = False
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    title: Optional[str] = None
    email: Optional[str] = None
    linkedin_url: Optional[str] = None
    mobile_phone: Optional[str] = None
    office_phone: Optional[str] = None
    company: Optional[str] = None
    company_domain: Optional[str] = None


class EnrichError(RuntimeError):
    """Fatal provider problem (bad key, plan without API access). Stops the stage."""


class Enricher(Protocol):
    name: str

    def enrich(self, q: EnrichQuery) -> Optional[EnrichResult]: ...
