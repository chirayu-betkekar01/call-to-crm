"""Offline tests for stage 3a (enrichment) and 3b (HubSpot sync). HubSpot and Apollo are faked."""
from __future__ import annotations

from pathlib import Path

import pytest

from agents.enrichment_sync import ContactEnrichmentStage, HubSpotClient, HubSpotSyncStage, MockEnricher
from core import HandoffEnvelope
from agents.enrichment_sync.enrich import ApolloEnricher, EnrichError, EnrichQuery, EnrichResult, anchor_domains
from core.models import CompanyContext, Evidence, LLMExtraction, RoleAssignment, SourceRef
from agents.attendee_reconciler.resolver import resolve
from agents.attendee_reconciler.sources import TxtSource

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def _p(name, **kw):
    base = dict(canonical_name=name, first_name=None, last_name=None, aliases=[], title=None, title_source="none",
                company=None, side="unknown", presence="mentioned_only", matched_attendee_email=None,
                buying_role="unknown", relationships=[], evidence=[], confidence=0.9, notes=None, ambiguity_note=None)
    base.update(kw)
    return base


def _role(role, conf=0.9):
    return RoleAssignment(role=role, confidence=conf, rationale="r", evidence=[Evidence(quote="q")])


def _envelope() -> HandoffEnvelope:
    t = next(TxtSource(SAMPLES / "acme_discovery_call.txt").fetch())
    ext = LLMExtraction.model_validate({"people": [
        _p("Priya Raman", side="internal", presence="spoke", company="Northwind"),
        _p("Matthew Collins", side="external", presence="spoke", title="Director of RevOps",
           title_source="stated_by_self", company="Acme Corp"),
        _p("Mark Davis", side="external", title="VP of Sales", title_source="stated_by_other", company="Acme Corp"),
        _p("Erica", side="external", presence="spoke"),                      # first name only, no company
        _p("Tom", side="external", company="Clarivue"),                       # competitor rep -> out of scope
    ]})
    people, stats = resolve(ext, t)
    env = HandoffEnvelope(source=SourceRef(type="txt", id=t.source_id, title=t.title), transcript=t,
                          people=people, stats=stats,
                          company_context=CompanyContext(internal_company="Northwind",
                                                         external_companies=["Acme Corp", "Clarivue"]))
    by = {p.full_name: p for p in env.people}
    by["Mark Davis"].buying_roles = [_role("decision_maker")]
    by["Matthew Collins"].buying_roles = [_role("champion"), _role("decision_maker", 0.7)]
    by["Erica"].buying_roles = [_role("champion", 0.8)]
    return env


def _by(env):
    return {p.full_name: p for p in env.people}


# =========================================================== enrichment
def test_anchor_domain_and_scope():
    env = _envelope()
    assert anchor_domains(env) == {"acmecorp.com": "Acme Corp"}
    ContactEnrichmentStage(MockEnricher()).run(env)
    by = _by(env)
    assert by["Priya Raman"].enrichment == {"status": "out_of_scope", "reason": "seller side"}
    assert by["Tom"].enrichment["status"] == "out_of_scope" and "Clarivue" in by["Tom"].enrichment["reason"]
    assert by["Erica"].enrichment["domain"] == "acmecorp.com"
    assert by["Erica"].enrichment["domain_reason"] == "single buyer domain on call"
    assert by["Mark Davis"].enrichment["domain_reason"] == "company name"
    s = env.stats["contact_enrichment"]
    # Matthew, Mark, Erica + Samantha/Rachel (invitees added by resolver)
    assert s["in_scope"] == 5 and s["enriched"] == 5 and s["out_of_scope"] == 3  # Priya, Daniel (seller), Tom


def test_mock_is_deterministic_and_clearly_fake():
    q = EnrichQuery(full_name="Erica", first_name="Erica", domain="acmecorp.com", company="Acme Corp")
    a, b = MockEnricher().enrich(q), MockEnricher().enrich(q)
    assert a == b and a.is_mock
    assert a.email.endswith("@acmecorp.com") and a.email.startswith("erica.")
    assert a.mobile_phone.startswith("+1-202-555-01") and a.linkedin_url.startswith("https://www.linkedin.com/in/erica-")


def test_call_data_wins_and_conflicts_are_logged():
    class Fixed:
        name = "fixed"

        def enrich(self, q):
            return EnrichResult(provider="fixed", email="matt@newjob.com", title="Chief Revenue Officer",
                                linkedin_url="https://linkedin.com/in/matt", mobile_phone="+1-202-555-0100")

    env = _envelope()
    ContactEnrichmentStage(Fixed()).run(env)
    m = _by(env)["Matthew Collins"].enrichment
    assert m["resolved"]["email"] == "matthew.collins@acmecorp.com" and m["sources"]["email"] == "call"
    assert m["resolved"]["title"] == "Director of RevOps"                     # stated on call -> kept
    assert m["resolved"]["linkedin_url"] == "https://linkedin.com/in/matt" and m["sources"]["linkedin_url"] == "fixed"
    assert any("possible job change" in c for c in m["conflicts"])
    assert any("kept stated title" in c for c in m["conflicts"])


def test_lookup_cap_and_per_person_errors():
    class Flaky:
        name = "flaky"
        n = 0

        def enrich(self, q):
            Flaky.n += 1
            if Flaky.n == 1:
                raise RuntimeError("boom")
            return None

    env = _envelope()
    ContactEnrichmentStage(Flaky(), max_lookups=3).run(env)
    s = env.stats["contact_enrichment"]
    assert (s["error"], s["not_found"], s["skipped_cap"]) == (1, 2, 2)


def test_auth_error_stops_stage():
    class Bad:
        name = "bad"

        def enrich(self, q):
            raise EnrichError("401")

    with pytest.raises(EnrichError):
        ContactEnrichmentStage(Bad()).run(_envelope())


# ----------------------------------------------------------- apollo adapter
class _Resp:
    def __init__(self, status=200, data=None, headers=None):
        self.status_code, self._d, self.headers = status, data, headers or {}
        self.text = "" if data is None else "x"

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return self._d


class _Session:
    def __init__(self, responses):
        self.headers, self.responses, self.calls = {}, list(responses), []

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json))
        return self.responses.pop(0)

    def request(self, method, url, json=None, params=None, timeout=None):
        self.calls.append((method, url, json, params))
        return self.responses.pop(0)


APOLLO_PERSON = {"person": {
    "first_name": "Erica", "last_name": "Stone", "title": "HubSpot Admin", "email": "erica.stone@acmecorp.com",
    "linkedin_url": "http://www.linkedin.com/in/ericastone",
    "phone_numbers": [{"sanitized_number": "+12025550111", "type": "mobile"}],
    "organization": {"name": "Acme Corp", "primary_domain": "acmecorp.com", "sanitized_phone": "+12025550199"},
}}


def test_apollo_maps_response_and_sends_key():
    s = _Session([_Resp(200, APOLLO_PERSON)])
    r = ApolloEnricher(api_key="k", session=s).enrich(
        EnrichQuery(full_name="Erica", first_name="Erica", domain="acmecorp.com", company="Acme Corp"))
    assert s.headers["x-api-key"] == "k"
    url, body = s.calls[0]
    assert url.endswith("/people/match") and body["name"] == "Erica" and body["domain"] == "acmecorp.com"
    assert "last_name" not in body
    assert (r.last_name, r.title, r.mobile_phone, r.office_phone) == ("Stone", "HubSpot Admin", "+12025550111", "+12025550199")


def test_apollo_locked_email_not_found_and_auth():
    locked = {"person": {**APOLLO_PERSON["person"], "email": "email_not_unlocked@domain.com"}}
    assert ApolloEnricher.map_response(locked).email is None
    assert ApolloEnricher.map_response({"person": None}) is None
    s = _Session([_Resp(403, {"error": "plan"})])
    with pytest.raises(EnrichError, match="API access"):
        ApolloEnricher(api_key="k", session=s).enrich(EnrichQuery(full_name="X Y", domain="a.com"))


# =========================================================== HubSpot
class FakeHubSpot:
    """In-memory stand-in for HubSpotClient (same method signatures)."""

    def __init__(self):
        self.objects = {"contacts": {}, "companies": {}, "deals": {}}
        self.assoc: dict[tuple, set] = {}
        self.label_defs = [{"category": "HUBSPOT_DEFINED", "typeId": 4, "label": None},
                           {"category": "USER_DEFINED", "typeId": 101, "label": "Decision Maker"}]
        self.props = {"email", "firstname", "lastname", "jobtitle", "mobilephone", "phone", "company"}
        self.writes, self._id = [], 1000

    def _new(self, kind, props):
        self._id += 1
        self.objects[kind][str(self._id)] = dict(props)
        return str(self._id)

    def link(self, a_type, a_id, b_type, b_id, type_ids=(None,)):
        self.assoc.setdefault((a_type, a_id, b_type, b_id), set()).update(type_ids)
        self.assoc.setdefault((b_type, b_id, a_type, a_id), set()).update(type_ids)

    def search(self, kind, filters, properties, limit=10):
        hits = [{"id": i, "properties": p} for i, p in self.objects[kind].items()
                if all(str(p.get(k, "")).lower() == str(v).lower() for k, v in filters.items())]
        return hits[:limit]

    def create(self, kind, props):
        self.writes.append(("create", kind, props))
        return self._new(kind, props)

    def update(self, kind, oid, props):
        self.writes.append(("update", kind, oid, props))
        self.objects[kind][oid].update(props)

    def batch_read(self, kind, ids, properties):
        return [{"id": i, "properties": self.objects[kind][i]} for i in ids if i in self.objects[kind]]

    def associated_ids(self, f, fid, t):
        return [k[3] for k in self.assoc if k[0] == f and k[1] == fid and k[2] == t]

    def associate_default(self, f, fid, t, tid):
        self.writes.append(("assoc", f, fid, t, tid))
        self.link(f, fid, t, tid)

    def associate_labeled(self, f, fid, t, tid, types):
        self.writes.append(("assoc_labeled", f, fid, t, tid, [x["associationTypeId"] for x in types]))
        self.link(f, fid, t, tid, [x["associationTypeId"] for x in types])

    def labels(self, f, t):
        return list(self.label_defs)

    def create_label(self, f, t, label, name):
        # Like real HubSpot: the response holds BOTH directions, and the reverse one may come first.
        self.writes.append(("label", label))
        n = 200 + 2 * len(self.label_defs)
        forward = {"category": "USER_DEFINED", "typeId": n, "label": label}          # contact -> deal
        reverse = {"category": "USER_DEFINED", "typeId": n + 1, "label": label}      # deal -> contact
        self.label_defs.append(forward)
        self.reverse_ids = getattr(self, "reverse_ids", set()) | {n + 1}
        return [reverse, forward]

    def get_property(self, kind, name):
        return {"name": name} if name in self.props else None

    def create_property(self, kind, body):
        self.writes.append(("property", body["name"]))
        self.props.add(body["name"])


def _portal():
    """Like the video's demo portal: one contact with only an email, on one deal, at one company."""
    hs = FakeHubSpot()
    comp = hs._new("companies", {"name": "Acme Corp", "domain": "acmecorp.com"})
    matt = hs._new("contacts", {"email": "matthew.collins@acmecorp.com", "jobtitle": "RevOps Lead"})
    deal = hs._new("deals", {"dealname": "Test Deal", "hs_is_closed": "false", "hs_lastmodifieddate": "2026-11-10"})
    old = hs._new("deals", {"dealname": "Old Deal", "hs_is_closed": "true", "hs_is_closed_won": "false",
                            "hs_lastmodifieddate": "2026-12-01"})
    hs.link("contacts", matt, "deals", deal)
    hs.link("companies", comp, "deals", old)
    return hs, {"company": comp, "matt": matt, "deal": deal}


def _enriched():
    env = _envelope()
    ContactEnrichmentStage(MockEnricher()).run(env)
    return env


def test_dry_run_reads_only_and_plans():
    hs, ids = _portal()
    env = HubSpotSyncStage(hs, write=False).run(_enriched())
    assert hs.writes == []
    by = _by(env)
    assert by["Matthew Collins"].crm["action"] == "would_update" and by["Matthew Collins"].crm["contact_id"] == ids["matt"]
    assert by["Erica"].crm["action"] == "would_create" and by["Erica"].crm["contact_id"] is None
    assert by["Tom"].crm == {} and by["Priya Raman"].crm == {}
    s = env.stats["hubspot_sync"]
    assert s["mode"] == "dry_run" and s["deal"]["name"] == "Test Deal"
    assert s["contacts"] == {"would_create": 4, "would_update": 1, "unchanged": 0}
    assert "would create Champion" in s["labels_created"] and "would create enrichment_source" in s["properties_created"]
    assert by["Erica"].crm["deal"]["labels"] == ["Champion"] and not by["Erica"].crm["deal"]["applied"]
    assert HubSpotSyncStage(hs).next_stage == "hubspot_sync"          # dry run leaves the step pending
    assert HubSpotSyncStage(hs, write=True).next_stage is None


def test_write_creates_fills_gaps_and_labels_deal():
    hs, ids = _portal()
    env = HubSpotSyncStage(hs, write=True).run(_enriched())
    by = _by(env)

    matt = hs.objects["contacts"][ids["matt"]]
    assert matt["jobtitle"] == "RevOps Lead"                         # existing value never overwritten
    assert matt["firstname"] == "Matthew" and matt["mobilephone"].startswith("+1-202-555-01")
    assert matt["enrichment_source"] == "mock (demo data)"
    assert by["Matthew Collins"].crm["kept_existing"] == {"jobtitle": "RevOps Lead"}

    erica_id = by["Erica"].crm["contact_id"]
    erica = hs.objects["contacts"][erica_id]
    assert erica["email"].endswith("@acmecorp.com") and erica["linkedin_url"].startswith("https://www.linkedin.com/in/")
    assert ("contacts", erica_id, "companies", ids["company"]) in hs.assoc

    # labels: Decision Maker existed (101); Champion created; Blocker unused -> not created
    assert [w for w in hs.writes if w[0] == "label"] == [("label", "Champion")]
    champion = next(l["typeId"] for l in hs.label_defs if l["label"] == "Champion")
    assert hs.assoc[("contacts", ids["matt"], "deals", ids["deal"])] >= {4, champion, 101}
    assert hs.assoc[("contacts", erica_id, "deals", ids["deal"])] >= {4, champion}
    applied = {t for w in hs.writes if w[0] == "assoc_labeled" for t in w[5]}
    assert not applied & hs.reverse_ids                                # never the deal->contact direction
    mark_id = by["Mark Davis"].crm["contact_id"]
    assert 101 in hs.assoc[("contacts", mark_id, "deals", ids["deal"])]
    assert not any(k[3] == "1004" for k in hs.assoc if k[0] == "contacts" and k[2] == "deals")  # old closed-lost deal untouched

    assert len(hs.objects["deals"]) == 2                              # no deals created
    s = env.stats["hubspot_sync"]
    assert s["contacts"] == {"created": 4, "updated": 1, "unchanged": 0} and s["contacts_labeled"] == 3
    assert set(s["properties_created"]) == {"enrichment_source", "linkedin_url"}


def test_rerun_is_idempotent():
    hs, _ = _portal()
    HubSpotSyncStage(hs, write=True).run(_enriched())
    n_contacts = len(hs.objects["contacts"])
    env = HubSpotSyncStage(hs, write=True).run(_enriched())
    assert len(hs.objects["contacts"]) == n_contacts
    assert env.stats["hubspot_sync"]["contacts"] == {"created": 0, "updated": 0, "unchanged": 5}


def test_no_deal_means_no_labels_and_no_deal_created():
    hs = FakeHubSpot()
    env = HubSpotSyncStage(hs, write=True).run(_enriched())
    assert env.stats["hubspot_sync"]["deal"] is None
    assert not hs.objects["deals"] and not any(w[0] in ("label", "assoc_labeled") for w in hs.writes)
    assert all(p.crm.get("deal") is None for p in env.people if p.crm)
    assert len(hs.objects["companies"]) == 1                          # company created and linked


def test_deal_override():
    hs, ids = _portal()
    env = HubSpotSyncStage(hs, write=True, deal_id=ids["deal"]).run(_enriched())
    assert env.stats["hubspot_sync"]["deal"]["reason"] == "override (--deal-id)"
    with pytest.raises(ValueError, match="not found"):
        HubSpotSyncStage(hs, write=True, deal_id="999").run(_enriched())


# ----------------------------------------------------------- HTTP client
def test_client_builds_requests_and_retries(monkeypatch):
    monkeypatch.setattr("agents.enrichment_sync.hubspot.time.sleep", lambda s: None)
    s = _Session([_Resp(429), _Resp(200, {"results": [{"id": "1"}]}), _Resp(404), _Resp(403, {"x": 1})])
    c = HubSpotClient(token="t", session=s)
    assert c.search("contacts", {"email": "a@b.com"}, ["email"]) == [{"id": "1"}]
    method, url, body, _ = s.calls[1]
    assert s.headers["Authorization"] == "Bearer t" and url.endswith("/crm/v3/objects/contacts/search")
    assert body["filterGroups"][0]["filters"] == [{"propertyName": "email", "operator": "EQ", "value": "a@b.com"}]
    assert c.get_property("contacts", "hs_linkedin_url") is None
    with pytest.raises(Exception, match="missing scopes"):
        c.labels("contacts", "deals")
