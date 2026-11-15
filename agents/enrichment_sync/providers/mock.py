"""Mock provider: free, offline, deterministic, obviously fake (is_mock=True). Default for demos."""
from __future__ import annotations

import hashlib
import re
from typing import Optional

from .base import EnrichQuery, EnrichResult


# --------------------------------------------------------------------------- #
# Mock provider (free, deterministic, obviously fake)
# --------------------------------------------------------------------------- #
_MOCK_TITLES = ("Operations Manager", "Revenue Operations Manager", "Director of Finance", "Head of IT",
                "Sales Operations Analyst", "Procurement Lead", "VP of Operations", "Customer Success Lead")
_MOCK_LAST = ("Carter", "Nguyen", "Patel", "Morales", "Kim", "Fischer", "Okoro", "Lindqvist")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z]", "", s.lower())


class MockEnricher:
    """Same input -> same output. Phones use the reserved fictional 555-01XX range."""

    name = "mock"

    def enrich(self, q: EnrichQuery) -> Optional[EnrichResult]:
        h = hashlib.sha256(f"{q.full_name}|{q.domain}".lower().encode()).digest()
        first = q.first_name or q.full_name.split()[0]
        last = q.last_name or _MOCK_LAST[h[0] % len(_MOCK_LAST)]
        local = ".".join(x for x in (_slug(first), _slug(last)) if x)
        return EnrichResult(
            provider=self.name, is_mock=True,
            first_name=first, last_name=last,
            title=q.title or _MOCK_TITLES[h[1] % len(_MOCK_TITLES)],
            email=f"{local}@{q.domain}" if q.domain and local else None,
            linkedin_url=f"https://www.linkedin.com/in/{_slug(first)}-{_slug(last)}-{h[2:4].hex()}",
            mobile_phone=f"+1-202-555-01{h[4] % 100:02d}",
            office_phone=f"+1-202-555-01{h[5] % 100:02d}",
            company=q.company, company_domain=q.domain,
        )
