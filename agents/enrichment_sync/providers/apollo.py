"""Apollo People Match provider. Needs a paid Apollo plan (the free plan has no API access to this endpoint)."""
from __future__ import annotations

import os
import time
from typing import Optional

import requests

from .base import EnrichError, EnrichQuery, EnrichResult


# --------------------------------------------------------------------------- #
# Apollo provider
# --------------------------------------------------------------------------- #
APOLLO_MATCH_URL = "https://api.apollo.io/api/v1/people/match"


class ApolloEnricher:
    """Apollo People Match. One call per person (costs Apollo credits when a match is found).
    Phone reveal is async (webhook) on Apollo, so only phones already in the match response are used."""

    name = "apollo"

    def __init__(self, api_key: Optional[str] = None, session=None, retries: int = 3, timeout: int = 30):
        key = api_key or os.getenv("APOLLO_API_KEY")
        if not key:
            raise EnrichError("APOLLO_API_KEY is not set (put it in .env), or use --enricher mock.")
        self.session = session or requests.Session()
        self.session.headers.update({"x-api-key": key, "Content-Type": "application/json",
                                     "Cache-Control": "no-cache", "accept": "application/json"})
        self.retries, self.timeout = retries, timeout

    def enrich(self, q: EnrichQuery) -> Optional[EnrichResult]:
        body = {"first_name": q.first_name, "last_name": q.last_name,
                "name": None if q.last_name else q.full_name, "email": q.email,
                "domain": q.domain, "organization_name": q.company, "reveal_personal_emails": False}
        body = {k: v for k, v in body.items() if v not in (None, "")}
        for attempt in range(self.retries + 1):
            r = self.session.post(APOLLO_MATCH_URL, json=body, timeout=self.timeout)
            if r.status_code == 429 and attempt < self.retries:
                time.sleep(float(r.headers.get("Retry-After") or 2 ** (attempt + 1)))
                continue
            if r.status_code in (401, 403):
                raise EnrichError(f"Apollo {r.status_code}: {r.text[:300]} "
                                  "(check the key; some Apollo plans don't include API access)")
            if r.status_code in (404, 422):
                return None
            if not r.ok:
                raise RuntimeError(f"Apollo {r.status_code}: {r.text[:300]}")
            return self.map_response(r.json())
        raise RuntimeError("Apollo kept rate-limiting after retries")

    @staticmethod
    def map_response(data: dict) -> Optional[EnrichResult]:
        p = (data or {}).get("person")
        if not p:
            return None
        org = p.get("organization") or {}
        email = p.get("email")
        if email and ("not_unlocked" in email or "@" not in email):
            email = None
        mobile = next((ph.get("sanitized_number") or ph.get("raw_number")
                       for ph in p.get("phone_numbers") or []
                       if "mobile" in str(ph.get("type") or ph.get("type_cd") or "").lower()), None)
        return EnrichResult(
            provider="apollo", first_name=p.get("first_name"), last_name=p.get("last_name"),
            title=p.get("title"), email=email, linkedin_url=p.get("linkedin_url"), mobile_phone=mobile,
            office_phone=org.get("sanitized_phone") or org.get("phone"),
            company=org.get("name"), company_domain=org.get("primary_domain"),
        )
