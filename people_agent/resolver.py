"""Deterministic clean-up after the LLM pass.

The model does the heavy lifting on name resolution. This step makes the result safe to
hand to enrichment/CRM:
  * merges leftover duplicates (nickname-aware, conservative)
  * attaches attendee emails and adds attendees the model missed
  * drops emails the model invented
  * fixes internal/external side from email domains
  * flags anything a human should check (`needs_review`)
"""
from __future__ import annotations

import hashlib
from typing import Optional

from .models import Attendee, LLMExtraction, LLMPerson, Person, Transcript
from .nicknames import first_names_match, last_names_match, normalize, split_name

_TITLE_RANK = {"attendee_list": 4, "stated_by_self": 4, "stated_by_other": 3, "inferred": 1, "none": 0}
_PRESENCE_RANK = {"spoke": 2, "attended_silent": 1, "mentioned_only": 0}
_GENERIC_SPEAKERS = {"unknown", "speaker", "guest", "conference room", "iphone", "android", "zoom user"}


def _domain(email: Optional[str]) -> Optional[str]:
    return email.split("@", 1)[1].lower() if email and "@" in email else None


def _to_person(p: LLMPerson) -> Person:
    first, last = p.first_name, p.last_name
    if not first and not last:
        first, last = split_name(p.canonical_name)
    return Person(
        person_id="",
        full_name=p.canonical_name.strip(),
        first_name=first,
        last_name=last,
        aliases=list(p.aliases),
        title=p.title,
        title_source=p.title_source if p.title else "none",
        company=p.company,
        email=(p.matched_attendee_email or "").lower() or None,
        side=p.side,
        presence=p.presence,
        buying_role=p.buying_role,
        relationships=list(p.relationships),
        evidence=list(p.evidence),
        confidence=max(0.0, min(1.0, p.confidence)),
        notes=[p.notes] if p.notes else [],
        review_reasons=[f"uncertain: {p.ambiguity_note}"] if p.ambiguity_note else [],
    )


def _all_names(p: Person) -> set[str]:
    return {normalize(n) for n in [p.full_name, *p.aliases] if n}


def _companies_compatible(a: Person, b: Person) -> bool:
    if not a.company or not b.company:
        return True
    ca, cb = normalize(a.company), normalize(b.company)
    return ca in cb or cb in ca


def _same_person(a: Person, b: Person, people: list[Person]) -> bool:
    if a.email and b.email:
        return a.email == b.email
    if a.side != "unknown" and b.side != "unknown" and a.side != b.side:
        return False
    if not _companies_compatible(a, b):
        return False
    if normalize(a.full_name) == normalize(b.full_name):
        return True
    if not first_names_match(a.first_name, b.first_name):
        # Speaker label / alias overlap (e.g. both entries list "Speaker 2").
        overlap = (_all_names(a) & _all_names(b)) - {"", *_GENERIC_SPEAKERS}
        return bool(overlap) and not (a.last_name and b.last_name and not last_names_match(a.last_name, b.last_name))
    if a.last_name and b.last_name:
        return last_names_match(a.last_name, b.last_name)
    # One side has only a first name: merge only if it's unambiguous and nobody flagged doubt.
    if any(r.startswith("uncertain:") for r in a.review_reasons + b.review_reasons):
        return False
    short, full = (a, b) if not a.last_name else (b, a)
    candidates = [p for p in people if p is not short and first_names_match(p.first_name, short.first_name)]
    return len(candidates) == 1 and candidates[0] is full


def _merge(into: Person, other: Person) -> None:
    # Name: keep the more complete one.
    if (other.last_name and not into.last_name) or (
        bool(other.last_name) == bool(into.last_name) and len(other.full_name) > len(into.full_name)
    ):
        into.aliases.append(into.full_name)
        into.full_name, into.first_name, into.last_name = other.full_name, other.first_name, other.last_name
    else:
        into.aliases.append(other.full_name)
    into.aliases.extend(other.aliases)

    # Title: keep the best-sourced one, flag conflicts.
    if other.title:
        if not into.title or _TITLE_RANK[other.title_source] > _TITLE_RANK[into.title_source]:
            if into.title and normalize(into.title) != normalize(other.title):
                into.review_reasons.append(f"conflicting titles: '{into.title}' vs '{other.title}'")
            into.title, into.title_source = other.title, other.title_source
        elif normalize(into.title) != normalize(other.title):
            into.review_reasons.append(f"conflicting titles: '{into.title}' vs '{other.title}'")

    into.company = into.company or other.company
    into.email = into.email or other.email
    if into.side == "unknown":
        into.side = other.side
    if _PRESENCE_RANK[other.presence] > _PRESENCE_RANK[into.presence]:
        into.presence = other.presence
    if into.buying_role == "unknown":
        into.buying_role = other.buying_role
    into.relationships.extend(r for r in other.relationships if r not in into.relationships)
    into.evidence.extend(other.evidence)
    into.confidence = max(into.confidence, other.confidence)
    into.notes.extend(other.notes)
    into.review_reasons.extend(other.review_reasons)


def _match_attendee(att: Attendee, people: list[Person]) -> Optional[Person]:
    if att.email:
        for p in people:
            if p.email == att.email:
                return p
    labels = {normalize(x) for x in (att.name, att.speaker_label) if x}
    for p in people:
        if labels & _all_names(p):
            return p
    a_first, a_last = split_name(att.name)
    matches = [
        p for p in people
        if first_names_match(p.first_name, a_first)
        and (last_names_match(p.last_name, a_last) if (p.last_name and a_last) else not a_last or not p.last_name)
    ]
    return matches[0] if len(matches) == 1 else None


def _dedupe_aliases(p: Person) -> None:
    seen = {normalize(p.full_name)}
    out = []
    for a in p.aliases:
        n = normalize(a)
        if n and n not in seen:
            seen.add(n)
            out.append(a.strip())
    p.aliases = out


def _person_id(p: Person, source_id: str) -> str:
    key = p.email or f"{normalize(p.full_name)}|{normalize(p.company)}|{source_id}"
    return "p_" + hashlib.sha1(key.encode()).hexdigest()[:10]


def resolve(extraction: LLMExtraction, transcript: Transcript) -> tuple[list[Person], dict]:
    people = [_to_person(p) for p in extraction.people if p.canonical_name.strip()]
    attendee_emails = {a.email for a in transcript.attendees if a.email}
    if transcript.recorded_by and transcript.recorded_by.email:
        attendee_emails.add(transcript.recorded_by.email.lower())
    internal = {d.lower() for d in transcript.internal_domains}
    stats: dict = {"llm_people": len(people), "merged_duplicates": 0, "attendees_added": 0, "invented_emails_dropped": 0}

    # 1. Never keep an email that isn't in the source metadata.
    for p in people:
        if p.email and p.email not in attendee_emails:
            p.review_reasons.append(f"model suggested email '{p.email}' not on attendee list; removed")
            p.email = None
            stats["invented_emails_dropped"] += 1

    # 2. Merge leftover duplicates.
    i = 0
    while i < len(people):
        j = i + 1
        while j < len(people):
            if _same_person(people[i], people[j], people):
                _merge(people[i], people.pop(j))
                stats["merged_duplicates"] += 1
            else:
                j += 1
        i += 1

    # 3. Attendees: attach emails, add anyone missing.
    attendees = list(transcript.attendees)
    if transcript.recorded_by and transcript.recorded_by.email and not any(
        a.email == transcript.recorded_by.email for a in attendees
    ):
        attendees.append(transcript.recorded_by)
    for att in attendees:
        match = _match_attendee(att, people)
        if match:
            match.in_attendee_list = True
            match.email = match.email or att.email
            if att.name and normalize(att.name) not in _all_names(match):
                match.aliases.append(att.name)
            continue
        first, last = split_name(att.name or (att.email or "").split("@")[0].replace(".", " ").title())
        people.append(
            Person(
                person_id="",
                full_name=att.name or f"{first or ''} {last or ''}".strip() or (att.email or "Unknown"),
                first_name=first,
                last_name=last,
                email=att.email,
                presence="attended_silent",
                in_attendee_list=True,
                confidence=0.6,
                review_reasons=["on attendee list but not identified in the transcript (no-show or silent)"],
            )
        )
        stats["attendees_added"] += 1

    # 4. Side from hard data beats the model's guess.
    ext_by_email = {a.email: a.is_external for a in attendees if a.email and a.is_external is not None}
    for p in people:
        dom = _domain(p.email)
        hard_side = None
        if p.email in ext_by_email:
            hard_side = "external" if ext_by_email[p.email] else "internal"
        elif dom and internal:
            hard_side = "internal" if dom in internal else "external"
        if hard_side and p.side != hard_side:
            if p.side != "unknown":
                p.review_reasons.append(f"side corrected from '{p.side}' to '{hard_side}' using email domain")
            p.side = hard_side

    # 5. Review flags + ids.
    for p in people:
        if not p.last_name:
            p.review_reasons.append("no last name - enrichment may struggle to identify")
        if p.confidence < 0.6:
            p.review_reasons.append(f"low confidence ({p.confidence:.2f})")
        if p.title_source == "inferred":
            p.review_reasons.append("title inferred from context, not stated")
        if p.side == "unknown":
            p.review_reasons.append("could not tell if internal or external")
        if p.side == "external" and not p.company:
            p.review_reasons.append("external person with unknown company")
        _dedupe_aliases(p)
        p.evidence = p.evidence[:5]
        p.review_reasons = list(dict.fromkeys(p.review_reasons))
        p.notes = list(dict.fromkeys(p.notes))
        p.needs_review = bool(p.review_reasons)
        p.person_id = _person_id(p, transcript.source_id)

    # 6. Speakers we couldn't map to anyone (useful when debugging).
    known = set().union(*(_all_names(p) for p in people)) if people else set()
    known |= {normalize(p.first_name) for p in people if p.first_name}
    speakers = {u.speaker for u in transcript.utterances}
    stats["unmapped_speakers"] = sorted(s for s in speakers if normalize(s) not in known and normalize(s) not in _GENERIC_SPEAKERS)

    order = {"spoke": 0, "attended_silent": 1, "mentioned_only": 2}
    people.sort(key=lambda p: (p.side != "external", order[p.presence], p.full_name))
    stats.update(
        total_people=len(people),
        external=sum(p.side == "external" for p in people),
        internal=sum(p.side == "internal" for p in people),
        mentioned_only=sum(p.presence == "mentioned_only" for p in people),
        not_on_attendee_list=sum(not p.in_attendee_list for p in people),
        needs_review=sum(p.needs_review for p in people),
    )
    return people, stats
