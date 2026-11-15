"""Agent 2 rubric: the buying roles and the behavioral signals for each.

This is business configuration, not logic. Edit the definitions or add roles here, then choose
which ones are active with `--roles` (CLI) or `BuyingRoleStage(roles=...)`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


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
