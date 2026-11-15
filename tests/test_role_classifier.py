"""Offline tests for stage 2 (buying-role classification). The LLM is faked."""
from __future__ import annotations

from pathlib import Path

import pytest

from agents.role_classifier import BuyingRoleStage
from core import HandoffEnvelope, Pipeline, load_envelope
from core.models import CompanyContext, LLMExtraction, SourceRef
from agents.attendee_reconciler.resolver import resolve
from agents.role_classifier.agent import _norm, quote_in_transcript
from agents.attendee_reconciler.sources import TxtSource

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def _p(name, **kw):
    base = dict(canonical_name=name, first_name=None, last_name=None, aliases=[], title=None, title_source="none",
                company=None, side="unknown", presence="mentioned_only", matched_attendee_email=None,
                buying_role="unknown", relationships=[], evidence=[], confidence=0.9, notes=None, ambiguity_note=None)
    base.update(kw)
    return base


def _envelope() -> HandoffEnvelope:
    t = next(TxtSource(SAMPLES / "acme_discovery_call.txt").fetch())
    ext = LLMExtraction.model_validate({"people": [
        _p("Priya Raman", side="internal", presence="spoke", title="Account Executive", company="Northwind"),
        _p("Matthew Collins", side="external", presence="spoke", title="Director of RevOps", company="Acme Corp"),
        _p("Mark Davis", side="external", title="VP of Sales", company="Acme Corp",
           relationships=["Matthew Collins's manager"]),
        _p("Elizabeth Novak", aliases=["Liz"], side="external", company="Acme Corp"),
    ]})
    people, stats = resolve(ext, t)
    return HandoffEnvelope(
        source=SourceRef(type="txt", id=t.source_id, title=t.title), transcript=t, people=people, stats=stats,
        company_context=CompanyContext(internal_company="Northwind", external_companies=["Acme Corp"]),
    )


class FakeLLM:
    model = "fake"
    last_usage = {}

    def __init__(self, payload):
        self.payload, self.calls = payload, []

    def complete_json(self, system, user, schema, schema_name="x", max_tokens=1):
        self.calls.append({"system": system, "user": user, "schema": schema})
        return self.payload(user) if callable(self.payload) else self.payload


def _ids(env):
    return {p.full_name: p.person_id for p in env.people}


def _role(role, conf, *quotes, rationale="r"):
    return {"role": role, "confidence": conf, "rationale": rationale,
            "evidence": [{"timestamp": None, "speaker": None, "quote": q} for q in quotes]}


# ----------------------------------------------------------------- quote verification
CORPUS = _norm("He'll make the final call, yeah. Mark Davis. He signs anything over fifty K.")


@pytest.mark.parametrize("q", [
    "He'll make the final call, yeah.",
    "he'll make the FINAL call yeah",                  # case / punctuation ignored
    "He’ll make the final call",                        # curly apostrophe
    "He'll make the final call ... signs anything over fifty K",  # ellipsis joins real fragments
])
def test_quote_verified(q):
    assert quote_in_transcript(q, CORPUS)


@pytest.mark.parametrize("q", [
    "He will make the final call",                     # paraphrase
    "Mark approves all budgets",                       # invented
    "He'll make the final call ... and he loves it",   # one fragment invented
    "...",
])
def test_quote_rejected(q):
    assert not quote_in_transcript(q, CORPUS)


# ----------------------------------------------------------------- stage behavior
def test_roster_is_buyer_side_only():
    env = _envelope()
    llm = FakeLLM({"assessments": []})
    BuyingRoleStage(llm).run(env)
    user = llm.calls[0]["user"]
    roster = user.split("ROSTER")[1].split("SELLER SIDE")[0]
    assert "Matthew Collins" in roster and "Mark Davis" in roster and "Elizabeth Novak" in roster
    assert "Priya Raman" not in roster
    assert "- Priya Raman, Account Executive" in user.split("SELLER SIDE")[1]
    assert "Mark Davis | title: VP of Sales" in roster and "presence: mentioned_only" in roster
    assert "Matt: He'll make the final call" in user  # transcript included
    assert llm.calls[0]["schema"]["properties"]["assessments"]["items"]["properties"]["roles"]["items"][
        "properties"]["role"]["enum"] == ["decision_maker", "champion", "blocker"]


def test_roles_are_validated_and_applied():
    env = _envelope()
    ids = _ids(env)
    payload = {"assessments": [
        {"person_id": ids["Mark Davis"], "roles": [
            _role("decision_maker", 0.95, "He'll make the final call, yeah.", "Mark approves all budgets"),
        ]},
        {"person_id": ids["Matthew Collins"], "roles": [
            _role("champion", 0.8, "And I'll give Mark a heads up.",
                  "My boss, Mark ... he's been asking for a clean forecast"),
            _role("blocker", 0.7, "Honestly our pipeline reporting is a mess."),
            _role("champion", 0.6, "Perfect."),  # duplicate role, lower confidence -> ignored
        ]},
        {"person_id": ids["Elizabeth Novak"], "roles": [
            _role("blocker", 0.9, "Liz will kill this deal"),                          # invented -> dropped
            _role("blocker", 0.5, "Liz has been known to hold things up for a month."),  # below threshold
        ]},
        {"person_id": ids["Priya Raman"], "roles": [_role("champion", 0.9, "Great, thanks everyone.")]},  # internal
        {"person_id": "p_made_up", "roles": []},
    ]}
    BuyingRoleStage(FakeLLM(payload)).run(env)
    by = {p.full_name: p for p in env.people}

    mark = by["Mark Davis"]
    assert [r.role for r in mark.buying_roles] == ["decision_maker"]
    assert [e.quote for e in mark.buying_roles[0].evidence] == ["He'll make the final call, yeah."]

    matt = by["Matthew Collins"]
    assert [r.role for r in matt.buying_roles] == ["champion", "blocker"]  # sorted by confidence
    assert matt.buying_roles[0].confidence == 0.8 and len(matt.buying_roles[0].evidence) == 2
    assert matt.needs_review and "conflicting buying roles: blocker + champion" in matt.review_reasons

    liz = by["Elizabeth Novak"]
    assert liz.buying_roles == []
    assert any("discarded: no verbatim evidence" in n for n in liz.notes)
    assert any(n.startswith("possible blocker (0.50") for n in liz.notes)

    assert by["Priya Raman"].buying_roles == []

    s = env.stats["buying_roles"]
    assert s["scored_people"] == 5 and s["people_with_roles"] == 2  # + Samantha, Rachel added from invite list
    assert s["role_counts"] == {"decision_maker": 1, "champion": 1, "blocker": 1}
    assert set(s["ignored_person_ids"]) == {ids["Priya Raman"], "p_made_up"}
    assert s["quotes_discarded"] == 2 and s["roles_no_evidence"] == 1 and s["roles_below_threshold"] == 1
    assert len(s["unassessed_person_ids"]) == 2  # Samantha, Rachel not in payload


def test_rerun_is_idempotent_and_reports_unassessed():
    env = _envelope()
    ids = _ids(env)
    BuyingRoleStage(FakeLLM({"assessments": [
        {"person_id": ids["Mark Davis"], "roles": [_role("decision_maker", 0.9, "He'll make the final call")]},
    ]})).run(env)
    BuyingRoleStage(FakeLLM({"assessments": []})).run(env)
    assert all(p.buying_roles == [] for p in env.people)
    assert len(env.stats["buying_roles"]["unassessed_person_ids"]) == 5


def test_custom_rubric_and_validation():
    stage = BuyingRoleStage(FakeLLM({"assessments": []}), roles=["decision_maker", "power_user"])
    assert "`power_user` (Power User)" in stage.system_prompt and "`blocker`" not in stage.system_prompt
    with pytest.raises(ValueError, match="Unknown buying role"):
        BuyingRoleStage(FakeLLM({}), roles=["kingmaker"])


def test_no_buyer_side_people_skips_llm():
    env = _envelope()
    env.people = [p for p in env.people if p.side == "internal"]
    llm = FakeLLM({"assessments": []})
    BuyingRoleStage(llm).run(env)
    assert llm.calls == [] and "skipped" in env.stats["buying_roles"]


def test_old_envelope_without_transcript_errors_cleanly(tmp_path):
    env = _envelope()
    env.transcript = None
    env.next_stage = "buying_role_classification"
    env, path = Pipeline(None, stages=[BuyingRoleStage(FakeLLM({}))], output_dir=tmp_path).resume(env)
    assert env.history[-1].status == "error" and "Re-run people extraction" in env.history[-1].detail


def test_resume_from_saved_file(tmp_path):
    env = _envelope()
    ids = _ids(env)
    saved = tmp_path / "in.json"
    saved.write_text(env.model_dump_json(), encoding="utf-8")

    stage = BuyingRoleStage(FakeLLM({"assessments": [
        {"person_id": ids["Mark Davis"], "roles": [_role("decision_maker", 0.9, "He signs anything over fifty K.")]},
    ]}))
    env2, path = Pipeline(None, stages=[stage], output_dir=tmp_path / "out").resume(load_envelope(saved))
    assert path.name == "00_buying_role_classification.json"
    assert env2.next_stage == "contact_enrichment"
    reloaded = load_envelope(path)
    assert reloaded.people[[p.full_name for p in reloaded.people].index("Mark Davis")].buying_roles[0].role == "decision_maker"
