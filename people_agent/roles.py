"""Stage 2: Buying-Role Classifier.

Reads the call and the resolved roster from stage 1 and labels each BUYER-SIDE person with
zero or more buying roles (decision maker, champion, blocker, ...), judged mainly by how they
behave on the call. Job title counts, but less.

The model proposes roles. Code then makes the result safe for the CRM:
  * only buyer-side people from the roster can get a role (internal people and made-up ids are dropped)
  * every evidence quote must appear in the transcript, and a role with no verified quote is dropped
  * roles below `min_confidence` are dropped and logged in `notes` for a human
  * champion + blocker on the same person is flagged for review
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

from pydantic import BaseModel, Field

from .extractor import JSONModel
from .models import Evidence, HandoffEnvelope, Person, RoleAssignment
from .pipeline import Stage
from .prompts import _EVIDENCE


# --------------------------------------------------------------------------- #
# Rubric (business-specific, so it's data, not code)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RoleDef:
    key: str
    label: str  # human / HubSpot label
    definition: str
    signals: tuple[str, ...]
    not_this: tuple[str, ...] = ()


ROLE_LIBRARY: dict[str, RoleDef] = {
    "decision_maker": RoleDef(
        key="decision_maker",
        label="Decision Maker",
        definition="Has the authority to approve, sign or make the final call on this purchase.",
        signals=(
            "Others defer to them or ask what they think before committing to anything.",
            "Talks about approvals, budget sign-off, timelines or priorities, often without saying much else.",
            "Others name them as the approver: 'X makes the final call', 'we'll need to run it by X', 'X signs anything over...'.",
            "Title supports it (CEO, founder, owner, VP or head who owns the budget), but a title alone is not enough.",
            "Can be absent from the call (mentioned_only). Evidence is then what others say about them.",
        ),
        not_this=(
            "Senior people who defer to someone else for the final decision.",
            "People who say they'll need approval themselves. The approver they name is the decision maker.",
        ),
    ),
    "champion": RoleDef(
        key="champion",
        label="Champion",
        definition="Actively pushes this deal forward inside the buyer's organization.",
        signals=(
            "Usually invited AND attended: often the person who set up the call or owns the evaluation.",
            "Brings colleagues in or prompts them to take part ('Erica, do you want to ask about...').",
            "Sells the value internally: explains their own team's pain, ties the product to goals, offers to brief the decision maker.",
            "Moves things along: asks about next steps, offers to share materials internally, volunteers to coordinate.",
        ),
        not_this=(
            "Someone who is just friendly, polite or enthusiastic without taking any action to move the deal forward.",
        ),
    ),
    "blocker": RoleDef(
        key="blocker",
        label="Blocker",
        definition="Likely to slow down or stop this deal.",
        signals=(
            "Keeps asking skeptical or challenging questions and isn't satisfied by the answers.",
            "Raises objections (cost, risk, security, current vendor, bad timing) or argues for the status quo or a competitor.",
            "Seems disengaged or unexcited about the opportunity, or says they'll push back.",
            "Others warn about them: 'X has been known to hold things up', 'X will want to kill this'.",
        ),
        not_this=(
            "Someone asking one tough due-diligence question (security, pricing) and then engaging normally. That is just evaluation.",
            "A gatekeeper with a slow but neutral process, unless people say they actively hold deals up.",
        ),
    ),
    "power_user": RoleDef(
        key="power_user",
        label="Power User",
        definition="Hands-on, day-to-day user or owner of the tool or workflow being bought.",
        signals=(
            "Asks detailed, in-the-weeds questions about workflows, setup, fields, integrations or edge cases.",
            "Describes doing the work themselves ('I'm the one living in the CRM all day').",
        ),
        not_this=("Managers asking only high-level questions about outcomes.",),
    ),
}

DEFAULT_ROLES: tuple[str, ...] = ("decision_maker", "champion", "blocker")
CONFLICTING_ROLES: tuple[frozenset[str], ...] = (frozenset({"champion", "blocker"}),)


# --------------------------------------------------------------------------- #
# Prompt + schema
# --------------------------------------------------------------------------- #
_SYSTEM_TEMPLATE = """\
You are the Buying-Role Classifier, step 2 of a GTM pipeline. Step 1 already found and \
de-duplicated everyone on a sales call. You get the transcript plus a ROSTER of the \
buyer-side people. For each person on the roster, decide which buying roles they hold in \
THIS deal, if any. Your labels are written to the CRM as contact-to-deal association labels, \
so a wrong label is worse than a missing one.

How to judge
- Behavior over titles. Judge mostly by what people do on the call: who others defer to, who \
  asks the hard questions, who is clearly selling this internally, who gets colleagues \
  involved. A title is supporting evidence only. A VP can sit silent while an analyst runs the room.
- Not everyone gets a role. An empty `roles` list is a correct and common answer. Don't fill \
  quotas: a call can have no blocker, or no clear decision maker.
- One person can hold several roles (a founder can be both champion and decision maker).
- People who were mentioned but absent can hold roles, especially decision maker ("I'll have \
  to run it by Dana"). Use what others say about them as evidence.
- Only score the buying organization. If someone on the roster is clearly not part of it (a \
  partner, consultant or another vendor's rep), give them no roles. Never score the seller's team \
  (listed as SELLER SIDE): they are context only.

Roles you may assign (use these keys only)
{rubric}

Evidence and confidence
- `evidence`: 1-3 short quotes copied VERBATIM from the transcript, with the timestamp and \
  speaker as shown. Copy exact wording. Don't paraphrase, fix grammar or join separate \
  sentences. Code checks every quote against the transcript. Quotes that don't match are \
  discarded, and a role left with no matching quote is discarded.
- `rationale`: one sentence naming the behavior pattern (not a restatement of the role).
- `confidence` (0.0-1.0):
  0.9+  stated outright ("Mark makes the final call") or a strong pattern across the call
  0.7-0.85  clear behavior seen in more than one moment
  0.5-0.65  one moderate signal
  below 0.5  don't include the role
- Return exactly one assessment per roster person_id, in roster order, even when `roles` is empty.

Return only the JSON object required by the schema."""


def render_rubric(roles: Iterable[RoleDef]) -> str:
    out = []
    for r in roles:
        out.append(f"\n`{r.key}` ({r.label}): {r.definition}")
        out.append("  Signals:")
        out.extend(f"  - {s}" for s in r.signals)
        if r.not_this:
            out.append("  Not this role:")
            out.extend(f"  - {s}" for s in r.not_this)
    return "\n".join(out).strip("\n")


def build_schema(role_keys: list[str]) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["assessments"],
        "properties": {
            "assessments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["person_id", "roles"],
                    "properties": {
                        "person_id": {"type": "string"},
                        "roles": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["role", "confidence", "rationale", "evidence"],
                                "properties": {
                                    "role": {"type": "string", "enum": role_keys},
                                    "confidence": {"type": "number"},
                                    "rationale": {"type": "string"},
                                    "evidence": {"type": "array", "items": _EVIDENCE},
                                },
                            },
                        },
                    },
                },
            }
        },
    }


class _LLMRole(BaseModel):
    role: str
    confidence: float = 0.5
    rationale: str = ""
    evidence: list[Evidence] = Field(default_factory=list)


class _LLMAssessment(BaseModel):
    person_id: str
    roles: list[_LLMRole] = Field(default_factory=list)


class _LLMRoleOutput(BaseModel):
    assessments: list[_LLMAssessment] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Deterministic helpers
# --------------------------------------------------------------------------- #
_QUOTE_CHARS = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "—": " ", "–": " "})


def _norm(text: str) -> str:
    text = text.translate(_QUOTE_CHARS).lower()
    text = re.sub(r"[^a-z0-9' ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def quote_in_transcript(quote: str, corpus: str) -> bool:
    """True if every fragment of `quote` (split on ellipses) appears in the normalized corpus.
    Case, punctuation and whitespace are ignored, but the words must match exactly."""
    fragments = [_norm(f) for f in re.split(r"\.\.\.|…", quote)]
    fragments = [f for f in fragments if f]
    return bool(fragments) and all(f in corpus for f in fragments)


def is_buyer_side(p: Person, external_companies: Iterable[str]) -> bool:
    if p.side == "external":
        return True
    ext = {c.lower() for c in external_companies}
    return p.side == "unknown" and bool(p.company) and p.company.lower() in ext


def _roster_line(p: Person) -> str:
    bits = [f"person_id: {p.person_id}", p.full_name]
    if p.aliases:
        bits.append("aka " + ", ".join(p.aliases))
    bits.append(f"title: {p.title} ({p.title_source})" if p.title else "title: none")
    bits.append(f"company: {p.company or 'unknown'}")
    bits.append(f"presence: {p.presence}")
    bits.append(f"on calendar invite: {'yes' if p.in_attendee_list else 'no'}")
    if p.relationships:
        bits.append("relationships: " + "; ".join(p.relationships))
    return "- " + " | ".join(bits)


# --------------------------------------------------------------------------- #
# Stage
# --------------------------------------------------------------------------- #
class BuyingRoleStage(Stage):
    name = "buying_role_classification"
    next_stage = "contact_enrichment"

    def __init__(
        self,
        llm: JSONModel,
        roles: Iterable[str] = DEFAULT_ROLES,
        min_confidence: float = 0.6,
        max_tokens: int = 8000,
    ):
        roles = list(dict.fromkeys(roles))
        unknown = [r for r in roles if r not in ROLE_LIBRARY]
        if unknown or not roles:
            raise ValueError(f"Unknown buying role(s) {unknown}. Choose from: {', '.join(ROLE_LIBRARY)}")
        self.llm = llm
        self.roles = [ROLE_LIBRARY[r] for r in roles]
        self.role_keys = [r.key for r in self.roles]
        self.min_confidence = min_confidence
        self.max_tokens = max_tokens
        self.system_prompt = _SYSTEM_TEMPLATE.format(rubric=render_rubric(self.roles))
        self.schema = build_schema(self.role_keys)

    # -- prompt -------------------------------------------------------------
    def eligible(self, env: HandoffEnvelope) -> list[Person]:
        return [p for p in env.people if is_buyer_side(p, env.company_context.external_companies)]

    def build_user_prompt(self, env: HandoffEnvelope, eligible: Optional[list[Person]] = None) -> str:
        if env.transcript is None:
            raise ValueError(
                "Envelope has no transcript (it was saved before schema 1.1). Re-run people extraction on this call."
            )
        eligible = self.eligible(env) if eligible is None else eligible
        ids = {p.person_id for p in eligible}
        seller = [p for p in env.people if p.person_id not in ids and p.side == "internal"]
        cc = env.company_context
        lines = [
            "EXTERNAL COMPANIES MENTIONED (the buyer, plus any partners/competitors): "
            + (", ".join(cc.external_companies) or "unknown"),
            f"SELLER (our company): {cc.internal_company or 'unknown'}",
            "",
            "ROSTER (buyer side, score these):",
            *[_roster_line(p) for p in eligible],
            "",
            "SELLER SIDE (context only, never score):",
            *([f"- {p.full_name}" + (f", {p.title}" if p.title else "") for p in seller] or ["- (none identified)"]),
            "",
            "<call>",
            env.transcript.render(),
            "</call>",
        ]
        return "Assign buying roles to the roster, following your instructions.\n\n" + "\n".join(lines)

    # -- run ----------------------------------------------------------------
    def run(self, env: HandoffEnvelope) -> HandoffEnvelope:
        eligible = self.eligible(env)
        stats: dict = {"roles_configured": self.role_keys, "min_confidence": self.min_confidence,
                       "scored_people": len(eligible)}
        for p in env.people:  # idempotent re-runs
            p.buying_roles = []
        if not eligible:
            stats["skipped"] = "no buyer-side people on this call"
            env.stats["buying_roles"] = stats
            return env

        prompt = self.build_user_prompt(env, eligible)
        raw = self.llm.complete_json(self.system_prompt, prompt, self.schema, "buying_roles", self.max_tokens)
        out = _LLMRoleOutput.model_validate(raw)
        stats.update(self._apply(out, eligible, env))
        stats["model"] = getattr(self.llm, "model", None)
        stats["usage"] = getattr(self.llm, "last_usage", {})
        env.stats["buying_roles"] = stats
        env.stats["needs_review"] = sum(p.needs_review for p in env.people)
        return env

    def _apply(self, out: _LLMRoleOutput, eligible: list[Person], env: HandoffEnvelope) -> dict:
        corpus = _norm(" ".join(u.text for u in env.transcript.utterances))
        by_id = {p.person_id: p for p in eligible}
        seen: set[str] = set()
        c = {"ignored_person_ids": [], "quotes_discarded": 0, "roles_no_evidence": 0,
             "roles_below_threshold": 0, "roles_not_in_rubric": 0}

        for a in out.assessments:
            p = by_id.get(a.person_id)
            if p is None:
                c["ignored_person_ids"].append(a.person_id)
                continue
            if a.person_id in seen:
                continue
            seen.add(a.person_id)

            kept: dict[str, RoleAssignment] = {}
            for r in a.roles:
                if r.role not in self.role_keys:
                    c["roles_not_in_rubric"] += 1
                    continue
                ev = [e for e in r.evidence if quote_in_transcript(e.quote, corpus)]
                c["quotes_discarded"] += len(r.evidence) - len(ev)
                if not ev:
                    c["roles_no_evidence"] += 1
                    p.notes.append(f"buying role '{r.role}' discarded: no verbatim evidence in transcript")
                    continue
                conf = max(0.0, min(1.0, r.confidence))
                if conf < self.min_confidence:
                    c["roles_below_threshold"] += 1
                    p.notes.append(f"possible {r.role} ({conf:.2f}, below threshold): {r.rationale}")
                    continue
                if r.role in kept and kept[r.role].confidence >= conf:
                    continue
                kept[r.role] = RoleAssignment(role=r.role, confidence=conf, rationale=r.rationale, evidence=ev[:3])

            p.buying_roles = sorted(kept.values(), key=lambda x: -x.confidence)
            for pair in CONFLICTING_ROLES:
                if pair <= kept.keys():
                    p.needs_review = True
                    p.review_reasons.append("conflicting buying roles: " + " + ".join(sorted(pair)))

        counts = {k: 0 for k in self.role_keys}
        for p in eligible:
            for r in p.buying_roles:
                counts[r.role] += 1
        c["unassessed_person_ids"] = [pid for pid in by_id if pid not in seen]
        c["people_with_roles"] = sum(bool(p.buying_roles) for p in eligible)
        c["role_counts"] = counts
        return c
