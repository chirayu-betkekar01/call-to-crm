"""Agent 3, step A - Contact Enrichment.

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

import re
from collections import Counter
from typing import Optional

from core.models import HandoffEnvelope, Person
from core.pipeline import Stage

from .providers import (ENRICHERS, ApolloEnricher, Enricher, EnrichError, EnrichQuery, EnrichResult,  # noqa: F401
                        HunterEnricher, MockEnricher, make_enricher)

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
