"""Offline tests for the Hunter enrichment adapter (HTTP is faked)."""
from __future__ import annotations

import pytest

from people_agent.enrich import EnrichError, EnrichQuery, HunterEnricher, make_enricher


class _Resp:
    def __init__(self, status=200, data=None):
        self.status_code, self._d, self.headers, self.text = status, data, {}, "x"

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return self._d


class _Session:
    def __init__(self, responses):
        self.headers, self.responses, self.calls = {}, list(responses), []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return self.responses.pop(0)


FOUND = {"data": {"first_name": "Mark", "last_name": "Davis", "email": "mark.davis@acmecorp.com", "score": 91,
                  "domain": "acmecorp.com", "position": "VP of Sales", "company": "Acme Corp",
                  "linkedin_url": "https://www.linkedin.com/in/markdavis", "phone_number": "+1 202 555 0142"}}
Q = EnrichQuery(full_name="Mark Davis", first_name="Mark", last_name="Davis", domain="acmecorp.com")


def test_hunter_maps_email_finder_response():
    s = _Session([_Resp(200, FOUND)])
    r = HunterEnricher(api_key="k", session=s).enrich(Q)
    assert s.headers["X-API-KEY"] == "k"
    url, params = s.calls[0]
    assert url.endswith("/v2/email-finder")
    assert params == {"domain": "acmecorp.com", "first_name": "Mark", "last_name": "Davis"}
    assert (r.email, r.title, r.linkedin_url, r.office_phone) == (
        "mark.davis@acmecorp.com", "VP of Sales", "https://www.linkedin.com/in/markdavis", "+1 202 555 0142")


def test_hunter_skips_first_name_only_without_spending_a_credit():
    s = _Session([])
    assert HunterEnricher(api_key="k", session=s).enrich(
        EnrichQuery(full_name="Erica", first_name="Erica", domain="acmecorp.com")) is None
    assert s.calls == []


def test_hunter_not_found_and_errors(monkeypatch):
    monkeypatch.setattr("people_agent.enrich.time.sleep", lambda s: None)
    assert HunterEnricher.map_response({"data": {"email": None}}) is None
    assert HunterEnricher(api_key="k", session=_Session([_Resp(404, {})])).enrich(Q) is None
    with pytest.raises(EnrichError, match="HUNTER_API_KEY"):
        HunterEnricher(api_key="k", session=_Session([_Resp(401, {})])).enrich(Q)
    restricted = {"errors": [{"id": "restricted_account", "code": 429, "details": "Your account was restricted."}]}
    with pytest.raises(EnrichError, match="restricted_account.*log in at hunter.io"):
        HunterEnricher(api_key="k", session=_Session([_Resp(429, restricted)])).enrich(Q)
    s = _Session([_Resp(429, {})])
    with pytest.raises(EnrichError, match="usage limit"):                    # usage limit: stop at once
        HunterEnricher(api_key="k", session=s).enrich(Q)
    assert len(s.calls) == 1
    s = _Session([_Resp(403, {}), _Resp(200, FOUND)])                        # rate limit: retried
    assert HunterEnricher(api_key="k", session=s).enrich(Q).email == "mark.davis@acmecorp.com"
    assert len(s.calls) == 2


def test_make_enricher_hunter_needs_key(monkeypatch):
    monkeypatch.delenv("HUNTER_API_KEY", raising=False)
    with pytest.raises(EnrichError, match="HUNTER_API_KEY is not set"):
        make_enricher("hunter")
