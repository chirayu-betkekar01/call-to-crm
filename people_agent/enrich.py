"""Stage 3a: Contact Enrichment.

For each buyer-side person, work out their company domain from the call (the "anchor domain"),
then ask an enrichment provider to fill in missing fields: name, title, email, LinkedIn URL,
mobile phone and office phone.

Anchor domain: the email domain of the outside invitees. A call rarely includes people from
more than one outside company, so someone known only as "Erica" at Acme is assumed to be at
Acme's domain. People at other companies (a competitor's rep who gets mentioned, say) are out
of scope and are not enriched or synced.

Providers sit behind the small `Enricher` protocol:
  * MockEnricher  : free, offline, made-up but realistic data (clearly marked is_mock). Default.
  * HunterEnricher: Hunter Email Finder API (HUNTER_API_KEY). Works on Hunter's free plan.
  * ApolloEnricher: Apollo.io People Match API (APOLLO_API_KEY). Needs a paid Apollo plan.

Data from the call wins over enrichment: an email from the invite list or a title someone
stated on the call is kept, and any different value from enrichment is logged as a conflict.
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from collections import Counter
from typing import Optional, Protocol

import requests
from pydantic import BaseModel

from .models import HandoffEnvelope, Person
from .pipeline import Stage

FREE_EMAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "yahoo.com", "outlook.com", "hotmail.com", "live.com", "msn.com",
    "icloud.com", "me.com", "aol.com", "proton.me", "protonmail.com", "gmx.com", "zoho.com",
})
STATED_TITLE_SOURCES = {"stated_by_self", "stated_by_other", "attendee_list"}
_COMPANY_SUFFIXES = re.compile(r"\b(inc|incorporated|corp|corporation|co|company|llc|ltd|limited|gmbh|plc|group)\b")


# --------------------------------------------------------------------------- #
# Company / anchor-domain inference
# --------------------------------------------------------------------------- #
def email_domain(email: Optional[str]) -> Optional[str]:
    return email.rsplit("@", 1)[1].lower().strip() if email and "@" in email else None


def company_key(name: Optional[str]) -> str:
    s = re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower())
    return re.sub(r"\s+", " ", _COMPANY_SUFFIXES.sub(" ", s)).strip()


def anchor_domains(env: HandoffEnvelope) -> dict[str, Optional[str]]:
    """{buyer email domain: company name}, most-seen domain first."""
    internal = {d.lower() for d in (env.transcript.internal_domains if env.transcript else [])}
    internal |= {email_domain(p.email) for p in env.people if p.side == "internal" and p.email}
    counts: Counter[str] = Counter()
    attendees = env.transcript.attendees if env.transcript else []
    for a in attendees:
        d = email_domain(a.email)
        if d and a.is_external is not False and d not in internal and d not in FREE_EMAIL_DOMAINS:
            counts[d] += 1
    for p in env.people:
        d = email_domain(p.email)
        if p.side == "external" and d and d not in internal and d not in FREE_EMAIL_DOMAINS:
            counts[d] += 1

    out: dict[str, Optional[str]] = {}
    for d, _ in counts.most_common():
        names = Counter(p.company for p in env.people if p.company and email_domain(p.email) == d)
        out[d] = names.most_common(1)[0][0] if names else None
    ext = env.company_context.external_companies
    if len(out) == 1 and next(iter(out.values())) is None and ext:
        out[next(iter(out))] = ext[0]
    return out


def person_domain(p: Person, anchors: dict[str, Optional[str]]) -> tuple[Optional[str], str]:
    """(domain, reason). domain is None when the person is out of scope."""
    if p.side == "internal":
        return None, "seller side"
    d = email_domain(p.email)
    if d in anchors:
        return d, "email domain"
    if d and d not in FREE_EMAIL_DOMAINS:
        return None, f"email at {d}, not the buying company"
    if p.company:
        key = company_key(p.company)
        for dom, name in anchors.items():
            if name and company_key(name) == key:
                return dom, "company name"
        return None, f"works at {p.company}, not the buying company"
    if p.side == "external" and len(anchors) == 1:
        return next(iter(anchors)), "single buyer domain on call"
    return None, "company unknown"


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


ENRICHERS = ("mock", "hunter", "apollo")


def make_enricher(name: str) -> Enricher:
    name = (name or "mock").lower()
    if name == "mock":
        return MockEnricher()
    if name == "hunter":
        return HunterEnricher()
    if name == "apollo":
        return ApolloEnricher()
    raise ValueError(f"Unknown enricher '{name}'. Choose: {', '.join(ENRICHERS)}")


# --------------------------------------------------------------------------- #
# Merge (call data wins)
# --------------------------------------------------------------------------- #
CONTACT_FIELDS = ("first_name", "last_name", "email", "title", "linkedin_url", "mobile_phone", "office_phone", "company")


def resolve_fields(p: Person, r: Optional[EnrichResult]) -> tuple[dict, dict, list[str]]:
    """Returns (resolved values, source per field, conflict notes)."""
    e = r or EnrichResult(provider="none")
    title_stated = p.title_source in STATED_TITLE_SOURCES
    call = {"first_name": p.first_name, "last_name": p.last_name, "email": p.email,
            "title": p.title, "company": p.company}
    prefer_call = {"title": title_stated}
    resolved, sources = {}, {}
    for f in CONTACT_FIELDS:
        cv, ev = call.get(f), getattr(e, f)
        if cv and prefer_call.get(f, True):
            resolved[f], sources[f] = cv, "call"
        elif ev:
            resolved[f], sources[f] = ev, e.provider
        elif cv:
            resolved[f], sources[f] = cv, "call"

    conflicts = []
    if p.email and e.email and p.email.lower() != e.email.lower():
        conflicts.append(f"enrichment email {e.email} differs from call email {p.email}; kept call email "
                         "(possible job change)")
    loose = lambda s: re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()  # noqa: E731
    if title_stated and e.title and loose(e.title) != loose(p.title):
        conflicts.append(f"enrichment title '{e.title}' differs from stated title '{p.title}'; kept stated title")
    return resolved, sources, conflicts


# --------------------------------------------------------------------------- #
# Stage
# --------------------------------------------------------------------------- #
class ContactEnrichmentStage(Stage):
    name = "contact_enrichment"
    next_stage = "hubspot_sync"

    def __init__(self, enricher: Enricher, max_lookups: int = 25):
        self.enricher = enricher
        self.max_lookups = max_lookups

    def run(self, env: HandoffEnvelope) -> HandoffEnvelope:
        anchors = anchor_domains(env)
        stats = {"provider": self.enricher.name, "anchor_domains": anchors, "in_scope": 0, "out_of_scope": 0,
                 "enriched": 0, "not_found": 0, "error": 0, "skipped_cap": 0}
        lookups = 0
        for p in env.people:
            dom, reason = person_domain(p, anchors)
            if not dom:
                p.enrichment = {"status": "out_of_scope", "reason": reason}
                stats["out_of_scope"] += 1
                continue
            stats["in_scope"] += 1
            company = anchors.get(dom) or p.company
            q = EnrichQuery(full_name=p.full_name, first_name=p.first_name, last_name=p.last_name, email=p.email,
                            domain=dom, company=company, title=p.title if p.title_source in STATED_TITLE_SOURCES else None)

            result, status, error = None, "enriched", None
            if lookups >= self.max_lookups:
                status = "skipped_cap"
            else:
                lookups += 1
                try:
                    result = self.enricher.enrich(q)
                except EnrichError:
                    raise
                except Exception as e:  # one bad lookup shouldn't stop the call
                    status, error = "error", str(e)[:300]
                if status == "enriched" and result is None:
                    status = "not_found"

            resolved, sources, conflicts = resolve_fields(p, result)
            resolved.setdefault("company", company)
            resolved["company_domain"] = dom
            p.enrichment = {
                "status": status, "provider": self.enricher.name, "is_mock": bool(result and result.is_mock),
                "domain": dom, "domain_reason": reason, "company": company,
                "resolved": resolved, "sources": sources, "conflicts": conflicts,
                "raw": result.model_dump(exclude_none=True) if result else None,
            }
            if error:
                p.enrichment["error"] = error
            p.notes.extend(c for c in conflicts if c not in p.notes)
            stats[status] += 1

        env.stats["contact_enrichment"] = stats
        return env
