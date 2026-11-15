"""Offline tests: no OpenRouter or Fathom calls are made (both are faked)."""
from __future__ import annotations

from pathlib import Path

import pytest

from people_agent import HandoffEnvelope, PeopleExtractionAgent, Pipeline, Stage, load_envelope
from people_agent.llm import _parse_json
from people_agent.models import LLMExtraction
from people_agent.nicknames import first_names_match, split_name
from people_agent.resolver import resolve
from people_agent.sources import FathomSource, TxtSource, meeting_to_transcript, parse_txt

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


# ----------------------------------------------------------------- nicknames
@pytest.mark.parametrize("a,b", [("Matt", "Matthew"), ("Sam", "Samuel"), ("Sam", "Samantha"), ("Bill", "William"),
                                 ("Liz", "Elizabeth"), ("Tony", "Anthony"), ("Deb", "Deborah"), ("Jess", "Jessica")])
def test_nicknames_match(a, b):
    assert first_names_match(a, b)


@pytest.mark.parametrize("a,b", [("Matt", "Mark"), ("Sam", "Sarah"), ("Liz", "Lisa"), ("Chris", "Craig")])
def test_nicknames_dont_overmatch(a, b):
    assert not first_names_match(a, b)


def test_split_name():
    assert split_name("Dr. Matthew J. Collins Jr.") == ("Matthew", "Collins")
    assert split_name("Mark") == ("Mark", None)


# ----------------------------------------------------------------- txt source
def test_parse_txt_with_header():
    t = next(TxtSource(SAMPLES / "acme_discovery_call.txt").fetch())
    assert t.title.startswith("Acme Corp")
    assert t.internal_domains == ["northwind.io"]
    assert len(t.attendees) == 5
    sides = {a.name: a.is_external for a in t.attendees}
    assert sides["Priya Raman"] is False and sides["Matthew Collins"] is True
    assert t.utterances[0].timestamp == "00:00:04"
    assert {"Matt", "Acme Conf Room B", "Jordan"} <= {u.speaker for u in t.utterances}


def test_parse_txt_without_header():
    t = next(TxtSource(SAMPLES / "globex_followup_call.txt", internal_domains=["northwind.io"]).fetch())
    assert t.attendees == []
    assert t.utterances[0].speaker == "Speaker 1" and t.utterances[0].timestamp == "00:00:00"
    assert len(t.utterances) == 14


def test_wrapped_lines_join():
    t = parse_txt("Bob: hello\nthere friend\nAlice: hi")
    assert [u.text for u in t.utterances] == ["hello there friend", "hi"]


# ----------------------------------------------------------------- fathom source
FATHOM_MEETING = {
    "title": "Acme sync", "meeting_title": "Acme <> Northwind", "recording_id": 4242,
    "share_url": "https://fathom.video/share/x", "recording_start_time": "2026-11-03T15:00:00Z",
    "recorded_by": {"name": "Priya Raman", "email": "priya.raman@northwind.io", "email_domain": "northwind.io", "team": "Sales"},
    "calendar_invitees": [
        {"name": "Priya Raman", "email": "priya.raman@northwind.io", "email_domain": "northwind.io", "is_external": False, "matched_speaker_display_name": "Priya Raman"},
        {"name": "Matthew Collins", "email": "Matthew.Collins@acmecorp.com", "email_domain": "acmecorp.com", "is_external": True, "matched_speaker_display_name": "Matt C"},
    ],
    "transcript": [
        {"speaker": {"display_name": "Priya Raman", "matched_calendar_invitee_email": "priya.raman@northwind.io"}, "text": "Hi Matt", "timestamp": "00:00:01"},
        {"speaker": {"display_name": "Matt C", "matched_calendar_invitee_email": "matthew.collins@acmecorp.com"}, "text": "Hi, Mark our VP Sales says hi", "timestamp": "00:00:03"},
    ],
}


def test_fathom_meeting_to_transcript():
    t = meeting_to_transcript(FATHOM_MEETING)
    assert t.source_type == "fathom" and t.source_id == "4242"
    assert t.internal_domains == ["northwind.io"]
    assert t.attendees[1].email == "matthew.collins@acmecorp.com" and t.attendees[1].speaker_label == "Matt C"
    assert "Matt C (matthew.collins@acmecorp.com): Hi, Mark" in t.render()


class _FakeResp:
    def __init__(self, data, status=200):
        self._d, self.status_code, self.headers, self.text = data, status, {}, ""

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return self._d


class _FakeSession:
    def __init__(self):
        self.headers, self.calls = {}, []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        if url.endswith("/meetings"):
            cursor = dict(params).get("cursor")
            if cursor is None:
                return _FakeResp({"items": [{**FATHOM_MEETING, "recording_id": 1}], "next_cursor": "c2"})
            return _FakeResp({"items": [FATHOM_MEETING], "next_cursor": None})
        if url.endswith("/recordings/4242/transcript"):
            return _FakeResp({"transcript": FATHOM_MEETING["transcript"]})
        return _FakeResp({}, 404)


def test_fathom_source_paginates_and_sends_key():
    s = _FakeSession()
    got = list(FathomSource("k", session=s, limit=5).fetch())
    assert [t.source_id for t in got] == ["1", "4242"]
    assert s.headers["X-Api-Key"] == "k"
    assert ("include_transcript", "true") in s.calls[0][1]
    assert ("calendar_invitees_domains_type", "one_or_more_external") in s.calls[0][1]


def test_fathom_single_recording_uses_transcript_endpoint():
    s = _FakeSession()
    t = next(FathomSource("k", session=s, recording_id=4242).fetch())
    assert t.title == "Acme <> Northwind" and len(t.attendees) == 2 and len(t.utterances) == 2
    assert s.calls[-1][0].endswith("/recordings/4242/transcript")


# ----------------------------------------------------------------- resolver
def _p(name, **kw):
    base = dict(canonical_name=name, first_name=None, last_name=None, aliases=[], title=None, title_source="none",
                company=None, side="unknown", presence="mentioned_only", matched_attendee_email=None,
                buying_role="unknown", relationships=[], evidence=[], confidence=0.9, notes=None, ambiguity_note=None)
    base.update(kw)
    return base


def _acme():
    return next(TxtSource(SAMPLES / "acme_discovery_call.txt").fetch())


def test_resolver_merges_nickname_duplicates_and_attaches_email():
    ext = LLMExtraction.model_validate({"people": [
        _p("Matthew Collins", title="Director of RevOps", title_source="stated_by_self", company="Acme", side="external", presence="spoke"),
        _p("Matt", aliases=["Matt"], company="Acme", side="external", presence="spoke"),
        _p("Mark Davis", title="VP of Sales", title_source="stated_by_other", company="Acme", side="external"),
        _p("Mark", company="Acme", side="external", buying_role="decision_maker"),
    ]})
    people, stats = resolve(ext, _acme())
    by = {p.full_name: p for p in people}
    assert "Matt" not in by and "Mark" not in by
    assert by["Matthew Collins"].email == "matthew.collins@acmecorp.com" and by["Matthew Collins"].in_attendee_list
    assert "Matt" in by["Matthew Collins"].aliases
    assert by["Mark Davis"].title == "VP of Sales" and by["Mark Davis"].buying_role == "decision_maker"
    assert not by["Mark Davis"].in_attendee_list
    assert stats["merged_duplicates"] == 2


def test_resolver_keeps_two_sams_apart():
    ext = LLMExtraction.model_validate({"people": [
        _p("Samantha Ortiz", side="external", company="Globex"),
        _p("Sam Patel", side="external", company="Globex"),
        _p("Sam", side="external", company="Globex"),
    ]})
    t = parse_txt("Speaker 1: hi", internal_domains=["northwind.io"])
    people, _ = resolve(ext, t)
    names = {p.full_name for p in people}
    assert {"Samantha Ortiz", "Sam Patel", "Sam"} == names  # ambiguous "Sam" is not force-merged
    assert next(p for p in people if p.full_name == "Sam").needs_review


def test_resolver_adds_missing_attendees_and_drops_invented_email():
    ext = LLMExtraction.model_validate({"people": [
        _p("Matthew Collins", side="external", presence="spoke", matched_attendee_email="matt@made-up.com"),
    ]})
    people, stats = resolve(ext, _acme())
    by = {p.full_name: p for p in people}
    assert by["Matthew Collins"].email == "matthew.collins@acmecorp.com"
    assert stats["invented_emails_dropped"] == 1
    assert by["Rachel Gomez"].presence == "attended_silent" and by["Rachel Gomez"].side == "external"
    assert by["Priya Raman"].side == "internal"


def test_notes_do_not_trigger_review_but_doubts_do():
    ext = LLMExtraction.model_validate({"people": [
        _p("Matthew Collins", side="external", company="Acme", presence="spoke",
           notes="Speaker label 'Matt' matched via self-introduction."),
        _p("Tom Reyes", side="external", company="Clarivue",
           ambiguity_note="Name uncertain: 'I think his name was Tom'."),
    ]})
    people, _ = resolve(ext, _acme())
    by = {p.full_name: p for p in people}
    assert not by["Matthew Collins"].needs_review
    assert by["Matthew Collins"].notes == ["Speaker label 'Matt' matched via self-introduction."]
    assert by["Tom Reyes"].needs_review and by["Tom Reyes"].review_reasons[0].startswith("uncertain:")


def test_resolver_corrects_side_from_domain():
    ext = LLMExtraction.model_validate({"people": [_p("Priya Raman", side="external", presence="spoke")]})
    people, _ = resolve(ext, _acme())
    priya = next(p for p in people if p.full_name == "Priya Raman")
    assert priya.side == "internal" and any("side corrected" in r for r in priya.review_reasons)


# ----------------------------------------------------------------- agent + pipeline + handoff
class FakeLLM:
    model = "fake"
    last_usage = {"prompt_tokens": 1}

    def __init__(self, payload):
        self.payload, self.prompts = payload, []

    def complete_json(self, system, user, schema, schema_name="x", max_tokens=1):
        self.prompts.append(user)
        return self.payload


class EchoEnrichment(Stage):
    name = "contact_enrichment"
    next_stage = "crm_sync"

    def run(self, env: HandoffEnvelope) -> HandoffEnvelope:
        for p in env.people:
            p.enrichment = {"status": "pending", "linkedin": None}
        return env


class RoutingLLM(FakeLLM):
    """Returns a different payload per stage (keyed by schema_name)."""

    def __init__(self, payloads):
        super().__init__(None)
        self.payloads = payloads

    def complete_json(self, system, user, schema, schema_name="x", max_tokens=1):
        self.prompts.append(user)
        p = self.payloads[schema_name]
        return p(user) if callable(p) else p


def test_pipeline_end_to_end_handoff(tmp_path):
    from people_agent import BuyingRoleStage

    payload = {"company_context": {"internal_company": "Northwind", "external_companies": ["Acme Corp"]},
               "people": [_p("Jordan Pierce", aliases=["Jordan", "Acme Conf Room B"], title="SDR team lead",
                             title_source="stated_by_self", company="Acme Corp", side="external", presence="spoke")],
               "unnamed_roles": [{"description": "CFO at Acme, approves budget", "title": "CFO", "company": "Acme Corp",
                                  "side": "external", "evidence": []}]}
    llm = RoutingLLM({"people_extraction": payload, "buying_roles": {"assessments": []}})
    pipe = Pipeline(PeopleExtractionAgent(llm), stages=[BuyingRoleStage(llm), EchoEnrichment()], output_dir=tmp_path)
    [(env, path)] = pipe.run(TxtSource(SAMPLES / "acme_discovery_call.txt").fetch())

    assert "CALENDAR ATTENDEE LIST" in llm.prompts[0] and "Acme Conf Room B: And this is Jordan" in llm.prompts[0]
    jordan = next(p for p in env.people if p.full_name == "Jordan Pierce")
    assert not jordan.in_attendee_list and jordan.enrichment["status"] == "pending"
    assert env.current_stage == "contact_enrichment" and env.next_stage == "crm_sync"
    assert [h.stage for h in env.history] == ["people_extraction", "buying_role_classification", "contact_enrichment"]
    assert env.unnamed_roles[0].title == "CFO"

    files = sorted(p.name for p in path.parent.iterdir())
    assert files == ["00_people_extraction.json", "01_buying_role_classification.json",
                     "02_contact_enrichment.json", "latest.json"]
    reloaded = load_envelope(path.parent / "00_people_extraction.json")
    assert reloaded.next_stage == "buying_role_classification" and reloaded.schema_version == "1.1"
    assert reloaded.transcript is not None and len(reloaded.transcript.utterances) == 22


def test_parse_json_handles_fences():
    assert _parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _parse_json('Sure! {"a": 2} hope that helps') == {"a": 2}
