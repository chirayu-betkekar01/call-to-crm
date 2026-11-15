"""Hunter Email Finder provider. Works on Hunter's free plan (50 credits/month; misses are free)."""
from __future__ import annotations

import os
import time
from typing import Optional

import requests

from .base import EnrichError, EnrichQuery, EnrichResult


# --------------------------------------------------------------------------- #
# Hunter provider (free plan includes API access, ~50 credits/month)
# --------------------------------------------------------------------------- #
HUNTER_FINDER_URL = "https://api.hunter.io/v2/email-finder"


class HunterEnricher:
    """Hunter Email Finder: first + last name + domain -> email, position, LinkedIn, phone.
    One credit per lookup that finds an email. Hunter needs a last name, so people known only by
    first name are skipped without spending a credit."""

    name = "hunter"

    def __init__(self, api_key: Optional[str] = None, session=None, retries: int = 2, timeout: int = 30):
        key = api_key or os.getenv("HUNTER_API_KEY")
        if not key:
            raise EnrichError("HUNTER_API_KEY is not set (put it in .env), or use --enricher mock.")
        self.session = session or requests.Session()
        self.session.headers.update({"X-API-KEY": key, "accept": "application/json"})
        self.retries, self.timeout = retries, timeout

    def enrich(self, q: EnrichQuery) -> Optional[EnrichResult]:
        if not (q.first_name and q.last_name and q.domain):
            return None
        params = {"domain": q.domain, "first_name": q.first_name, "last_name": q.last_name}
        # Hunter's status codes: 401 = bad key, 403 = rate limit (retry), 429 = monthly usage limit reached.
        for attempt in range(self.retries + 1):
            r = self.session.get(HUNTER_FINDER_URL, params=params, timeout=self.timeout)
            if r.status_code == 403 and attempt < self.retries:
                time.sleep(float(r.headers.get("Retry-After") or 2 ** (attempt + 1)))
                continue
            if r.status_code == 429:
                raise EnrichError(f"Hunter 429: {self._why(r)}")
            if r.status_code == 401:
                raise EnrichError(f"Hunter 401: {r.text[:300]} (check HUNTER_API_KEY)")
            if r.status_code == 403:
                raise RuntimeError(f"Hunter 403 (rate limit) after retries: {r.text[:300]}")
            if r.status_code in (400, 404, 422, 451):
                return None
            if not r.ok:
                raise RuntimeError(f"Hunter {r.status_code}: {r.text[:300]}")
            return self.map_response(r.json())
        raise RuntimeError("Hunter kept rate-limiting after retries")

    @staticmethod
    def _why(r) -> str:
        """Hunter's own error id/details, e.g. 'restricted_account: Your account was restricted...'."""
        try:
            err = (r.json().get("errors") or [{}])[0]
        except Exception:
            err = {}
        if err.get("id") == "restricted_account":
            return f"restricted_account: {err.get('details')} (log in at hunter.io to lift the restriction)"
        if err:
            return f"{err.get('id')}: {err.get('details')}"
        return f"{r.text[:300]} (monthly usage limit reached)"

    @staticmethod
    def map_response(data: dict) -> Optional[EnrichResult]:
        d = (data or {}).get("data") or {}
        if not d.get("email"):
            return None
        return EnrichResult(
            provider="hunter", first_name=d.get("first_name"), last_name=d.get("last_name"),
            email=d["email"], title=d.get("position"), linkedin_url=d.get("linkedin_url"),
            office_phone=d.get("phone_number"), company=d.get("company"), company_domain=d.get("domain"),
        )
