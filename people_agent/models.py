"""Data models shared by every stage of the GTM pipeline.

Three layers:
  1. Transcript      -- normalized input, whatever the source (TXT, Fathom, ...)
  2. LLM* models     -- the raw JSON the extraction model returns
  3. Person/Handoff  -- the cleaned, resolved output handed to the next agent
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.1"  # 1.1: Person.buying_roles, HandoffEnvelope.transcript

Side = Literal["internal", "external", "unknown"]
Presence = Literal["spoke", "attended_silent", "mentioned_only"]
BuyingRole = Literal[
    "economic_buyer",
    "decision_maker",
    "champion",
    "influencer",
    "technical_evaluator",
    "end_user",
    "blocker",
    "procurement_legal",
    "sales_rep",  # our own team selling
    "other",
    "unknown",
]
TitleSource = Literal["stated_by_self", "stated_by_other", "attendee_list", "inferred", "none"]


# --------------------------------------------------------------------------- #
# 1. Normalized transcript
# --------------------------------------------------------------------------- #
class Attendee(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    is_external: Optional[bool] = None  # None = not known from the source
    speaker_label: Optional[str] = None  # how this attendee appears in the transcript, if known


class Utterance(BaseModel):
    speaker: str
    text: str
    timestamp: Optional[str] = None  # "HH:MM:SS" relative to call start
    speaker_email: Optional[str] = None  # set when the source matched speaker -> invitee


class Transcript(BaseModel):
    source_type: str  # "txt" | "fathom"
    source_id: str  # file path or Fathom recording_id
    title: Optional[str] = None
    date: Optional[str] = None
    url: Optional[str] = None
    recorded_by: Optional[Attendee] = None
    attendees: list[Attendee] = Field(default_factory=list)
    internal_domains: list[str] = Field(default_factory=list)
    utterances: list[Utterance] = Field(default_factory=list)

    def render(self) -> str:
        """Render as plain text for the LLM, attendee list first."""
        lines: list[str] = []
        lines.append(f"MEETING TITLE: {self.title or '(unknown)'}")
        lines.append(f"DATE: {self.date or '(unknown)'}")
        if self.internal_domains:
            lines.append(f"OUR (INTERNAL) EMAIL DOMAINS: {', '.join(self.internal_domains)}")
        if self.recorded_by:
            rb = self.recorded_by
            lines.append(f"RECORDED BY (internal): {rb.name or '?'} <{rb.email or '?'}>")
        lines.append("")
        lines.append("CALENDAR ATTENDEE LIST:")
        if not self.attendees:
            lines.append("  (none provided)")
        for a in self.attendees:
            side = {True: "external", False: "internal", None: "side unknown"}[a.is_external]
            extra = f", appears in transcript as '{a.speaker_label}'" if a.speaker_label else ""
            lines.append(f"  - {a.name or '(no name)'} <{a.email or 'no email'}> [{side}{extra}]")
        lines.append("")
        lines.append("TRANSCRIPT:")
        for u in self.utterances:
            ts = f"[{u.timestamp}] " if u.timestamp else ""
            who = u.speaker
            if u.speaker_email:
                who += f" ({u.speaker_email})"
            lines.append(f"{ts}{who}: {u.text}")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 2. Raw LLM output
# --------------------------------------------------------------------------- #
class Evidence(BaseModel):
    timestamp: Optional[str] = None
    speaker: Optional[str] = None
    quote: str


class LLMPerson(BaseModel):
    canonical_name: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    aliases: list[str] = Field(default_factory=list)
    title: Optional[str] = None
    title_source: TitleSource = "none"
    company: Optional[str] = None
    side: Side = "unknown"
    presence: Presence = "mentioned_only"
    matched_attendee_email: Optional[str] = None
    buying_role: BuyingRole = "unknown"
    relationships: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: float = 0.5
    notes: Optional[str] = None  # informational, never triggers review
    ambiguity_note: Optional[str] = None  # real doubt -> needs_review


class UnnamedRole(BaseModel):
    description: str
    title: Optional[str] = None
    company: Optional[str] = None
    side: Side = "unknown"
    evidence: list[Evidence] = Field(default_factory=list)


class CompanyContext(BaseModel):
    internal_company: Optional[str] = None
    external_companies: list[str] = Field(default_factory=list)


class LLMExtraction(BaseModel):
    company_context: CompanyContext = Field(default_factory=CompanyContext)
    people: list[LLMPerson] = Field(default_factory=list)
    unnamed_roles: list[UnnamedRole] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# 3. Resolved output / handoff contract
# --------------------------------------------------------------------------- #
class RoleAssignment(BaseModel):
    """One buying role held by a buyer-side person, set by the buying-role stage."""

    role: str  # a key from the configured rubric, e.g. "decision_maker"
    confidence: float
    rationale: str
    evidence: list[Evidence] = Field(default_factory=list)  # verified verbatim quotes only


class Person(BaseModel):
    person_id: str
    full_name: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    aliases: list[str] = Field(default_factory=list)
    title: Optional[str] = None
    title_source: TitleSource = "none"
    company: Optional[str] = None
    email: Optional[str] = None
    side: Side = "unknown"
    presence: Presence = "mentioned_only"
    in_attendee_list: bool = False
    buying_role: BuyingRole = "unknown"  # stage-1 first guess; `buying_roles` is authoritative once set
    buying_roles: list[RoleAssignment] = Field(default_factory=list)  # set by buying_role_classification
    relationships: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: float = 0.5
    notes: list[str] = Field(default_factory=list)  # context for downstream agents; not a review signal
    needs_review: bool = False
    review_reasons: list[str] = Field(default_factory=list)
    # Reserved for downstream stages (enrichment / CRM). This stage leaves them empty.
    enrichment: dict = Field(default_factory=dict)
    crm: dict = Field(default_factory=dict)


class SourceRef(BaseModel):
    type: str
    id: str
    title: Optional[str] = None
    date: Optional[str] = None
    url: Optional[str] = None


class StageRecord(BaseModel):
    stage: str
    status: Literal["ok", "error", "skipped"]
    started_at: str
    finished_at: str
    detail: Optional[str] = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class HandoffEnvelope(BaseModel):
    """The JSON contract passed between agents in the GTM pipeline.

    Each stage reads an envelope, adds to it, appends a StageRecord to `history`
    and sets `next_stage`. Downstream agents should rely only on these fields.
    """

    schema_version: str = SCHEMA_VERSION
    run_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    created_at: str = Field(default_factory=_now)
    current_stage: str = "people_extraction"
    next_stage: Optional[str] = "buying_role_classification"
    source: SourceRef
    # The normalized call, so later stages (and re-runs from a saved file) can read the dialogue.
    transcript: Optional[Transcript] = None
    company_context: CompanyContext = Field(default_factory=CompanyContext)
    people: list[Person] = Field(default_factory=list)
    unnamed_roles: list[UnnamedRole] = Field(default_factory=list)
    stats: dict = Field(default_factory=dict)
    history: list[StageRecord] = Field(default_factory=list)
