"""Stage 3b: HubSpot Sync.

For every in-scope buyer-side person (from contact_enrichment):
  1. find the company by domain (create it if missing)
  2. find the contact by email, else by first + last name; create it if missing
  3. fill only EMPTY HubSpot properties. Existing values are never overwritten
  4. associate the contact with the company
  5. find the deal THROUGH HUBSPOT ASSOCIATIONS (existing contacts' deals + the company's deals).
     Deals are never created. If no deal is found, labeling is skipped.
  6. make sure the buying-role association labels exist on contact->deal (create missing ones),
     then associate each contact with the deal using its role labels

The default is a dry run: it reads from HubSpot and records what it *would* write in
`person.crm` and `stats.hubspot_sync`. Pass write=True (CLI: --write) to apply.

Scopes the service key needs: crm.objects.contacts.read/write, crm.objects.companies.read/write,
crm.objects.deals.read/write, crm.schemas.contacts.read/write (custom properties + labels),
crm.schemas.companies.read, crm.schemas.deals.read/write.
"""
from __future__ import annotations

import os
import time
from typing import Iterable, Optional

import requests

from .models import HandoffEnvelope, Person
from .pipeline import Stage
from .roles import ROLE_LIBRARY

HUBSPOT_BASE = "https://api.hubapi.com"
CONTACT_TO_DEAL_DEFAULT = {"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 4}

FIELD_TO_PROP = {"first_name": "firstname", "last_name": "lastname", "email": "email", "title": "jobtitle",
                 "mobile_phone": "mobilephone", "office_phone": "phone", "company": "company"}
SOURCE_PROP = "enrichment_source"
LINKEDIN_NATIVE = "hs_linkedin_url"
LINKEDIN_CUSTOM = "linkedin_url"
CUSTOM_PROPS = {
    SOURCE_PROP: {"label": "Enrichment Source", "description": "Which provider filled this contact (mock = demo data)."},
    LINKEDIN_CUSTOM: {"label": "LinkedIn URL", "description": "LinkedIn profile URL."},
}
DEAL_PROPS = ["dealname", "dealstage", "hs_is_closed", "hs_is_closed_won", "hs_lastmodifieddate"]


class HubSpotError(RuntimeError):
    def __init__(self, status: int, body: str, method: str = "", path: str = ""):
        hint = " (missing scopes on the service key?)" if status == 403 else ""
        super().__init__(f"HubSpot {status} on {method} {path}{hint}: {body[:500]}")
        self.status = status


# --------------------------------------------------------------------------- #
# Thin client: only the calls this stage needs
# --------------------------------------------------------------------------- #
class HubSpotClient:
    def __init__(self, token: Optional[str] = None, session=None, base: str = HUBSPOT_BASE,
                 retries: int = 3, timeout: int = 30):
        token = token or os.getenv("HUBSPOT_TOKEN")
        if not token:
            raise ValueError("HUBSPOT_TOKEN is not set (put your service key in .env).")
        self.s = session or requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        self.base, self.retries, self.timeout = base.rstrip("/"), retries, timeout

    def _req(self, method: str, path: str, json=None, params=None, ok404: bool = False):
        for attempt in range(self.retries + 1):
            r = self.s.request(method, self.base + path, json=json, params=params, timeout=self.timeout)
            if r.status_code in (429, 502, 503, 504) and attempt < self.retries:
                time.sleep(float(r.headers.get("Retry-After") or 2 ** attempt))
                continue
            if r.status_code == 404 and ok404:
                return None
            if not r.ok:
                raise HubSpotError(r.status_code, r.text, method, path)
            return r.json() if (r.text or "").strip() else {}
        raise HubSpotError(429, "rate-limited after retries", method, path)

    # objects
    def search(self, object_type: str, filters: dict, properties: list[str], limit: int = 10) -> list[dict]:
        body = {"filterGroups": [{"filters": [{"propertyName": k, "operator": "EQ", "value": v}
                                              for k, v in filters.items()]}],
                "properties": properties, "limit": limit}
        return self._req("POST", f"/crm/v3/objects/{object_type}/search", json=body).get("results", [])

    def create(self, object_type: str, properties: dict) -> str:
        return str(self._req("POST", f"/crm/v3/objects/{object_type}", json={"properties": properties})["id"])

    def update(self, object_type: str, object_id: str, properties: dict) -> None:
        self._req("PATCH", f"/crm/v3/objects/{object_type}/{object_id}", json={"properties": properties})

    def archive(self, object_type: str, object_id: str) -> None:
        """Move a record to the recycle bin (restorable in HubSpot for 90 days). Used only by the demo seed script."""
        self._req("DELETE", f"/crm/v3/objects/{object_type}/{object_id}")

    def batch_read(self, object_type: str, ids: Iterable[str], properties: list[str]) -> list[dict]:
        ids = list(ids)
        if not ids:
            return []
        body = {"inputs": [{"id": i} for i in ids], "properties": properties}
        return self._req("POST", f"/crm/v3/objects/{object_type}/batch/read", json=body).get("results", [])

    # associations
    def associated_ids(self, from_type: str, from_id: str, to_type: str) -> list[str]:
        out, after = [], None
        while True:
            params = {"limit": 500, **({"after": after} if after else {})}
            data = self._req("GET", f"/crm/v4/objects/{from_type}/{from_id}/associations/{to_type}", params=params)
            out += [str(x["toObjectId"]) for x in data.get("results", [])]
            after = ((data.get("paging") or {}).get("next") or {}).get("after")
            if not after:
                return out

    def associate_default(self, from_type: str, from_id: str, to_type: str, to_id: str) -> None:
        self._req("PUT", f"/crm/v4/objects/{from_type}/{from_id}/associations/default/{to_type}/{to_id}")

    def associate_labeled(self, from_type: str, from_id: str, to_type: str, to_id: str, types: list[dict]) -> None:
        self._req("PUT", f"/crm/v4/objects/{from_type}/{from_id}/associations/{to_type}/{to_id}", json=types)

    def labels(self, from_type: str, to_type: str) -> list[dict]:
        return self._req("GET", f"/crm/v4/associations/{from_type}/{to_type}/labels").get("results", [])

    def create_label(self, from_type: str, to_type: str, label: str, name: str) -> list[dict]:
        body = {"label": label, "name": name}
        return self._req("POST", f"/crm/v4/associations/{from_type}/{to_type}/labels", json=body).get("results", [])

    # properties
    def get_property(self, object_type: str, name: str) -> Optional[dict]:
        return self._req("GET", f"/crm/v3/properties/{object_type}/{name}", ok404=True)

    def create_property(self, object_type: str, body: dict) -> None:
        self._req("POST", f"/crm/v3/properties/{object_type}", json=body)


# --------------------------------------------------------------------------- #
# Stage
# --------------------------------------------------------------------------- #
def _filled(v) -> bool:
    return v is not None and str(v).strip() != ""


class HubSpotSyncStage(Stage):
    name = "hubspot_sync"
    next_stage = None

    def __init__(self, client: HubSpotClient, write: bool = False, deal_id: Optional[str] = None,
                 role_labels: Optional[dict[str, str]] = None):
        self.client = client
        self.write = write
        # A dry run doesn't finish the pipeline: leave next_stage pointing here so `resume --write` works.
        self.next_stage = None if write else self.name
        self.deal_id = str(deal_id) if deal_id else None
        self.role_labels = role_labels or {k: r.label for k, r in ROLE_LIBRARY.items()}

    # -- run ----------------------------------------------------------------
    def run(self, env: HandoffEnvelope) -> HandoffEnvelope:
        people = [p for p in env.people
                  if p.enrichment.get("status") not in (None, "out_of_scope") and p.enrichment.get("domain")]
        self.stats = {"mode": "write" if self.write else "dry_run", "people": len(people),
                      "contacts": {"created": 0, "updated": 0, "unchanged": 0},
                      "companies": {}, "deal": None, "labels_created": [], "labels_missing": [],
                      "contacts_labeled": 0, "properties_created": []}
        env.stats["hubspot_sync"] = self.stats  # live reference: partial progress survives an error
        if not people:
            self.stats["skipped"] = "no in-scope buyer-side people (run contact_enrichment first)"
            env.stats["hubspot_sync"] = self.stats
            return env

        self.linkedin_prop = self._prepare_properties(people)
        companies = {}
        for dom in dict.fromkeys(p.enrichment["domain"] for p in people):
            name = next((p.enrichment.get("company") for p in people if p.enrichment["domain"] == dom), None)
            companies[dom] = self._ensure_company(dom, name)

        self._created_cache: dict[tuple, str] = {}
        for p in people:
            self._sync_contact(p, companies[p.enrichment["domain"]])

        deal = self._choose_deal(people, companies)
        self.stats["deal"] = deal
        if deal:
            self._label(people, deal)
        else:
            for p in people:
                p.crm["deal"] = None

        if not self.write:
            c = self.stats["contacts"]
            self.stats["contacts"] = {"would_create": c["created"], "would_update": c["updated"], "unchanged": c["unchanged"]}
        env.stats["hubspot_sync"] = self.stats
        return env

    # -- properties ---------------------------------------------------------
    def _prepare_properties(self, people: list[Person]) -> str:
        linkedin = LINKEDIN_NATIVE if self.client.get_property("contacts", LINKEDIN_NATIVE) else LINKEDIN_CUSTOM
        needed = [SOURCE_PROP]
        if linkedin == LINKEDIN_CUSTOM and any(p.enrichment.get("resolved", {}).get("linkedin_url") for p in people):
            needed.append(LINKEDIN_CUSTOM)
        for name in needed:
            if self.client.get_property("contacts", name):
                continue
            if self.write:
                meta = CUSTOM_PROPS[name]
                self.client.create_property("contacts", {
                    "name": name, "label": meta["label"], "description": meta["description"],
                    "type": "string", "fieldType": "text", "groupName": "contactinformation"})
            self.stats["properties_created"].append(name if self.write else f"would create {name}")
        return linkedin

    # -- company ------------------------------------------------------------
    def _ensure_company(self, domain: str, name: Optional[str]) -> dict:
        found = self.client.search("companies", {"domain": domain}, ["name", "domain"], limit=1)
        if found:
            rec = {"id": str(found[0]["id"]), "action": "found", "name": found[0].get("properties", {}).get("name")}
        elif self.write:
            rec = {"id": self.client.create("companies", {"name": name or domain, "domain": domain}),
                   "action": "created", "name": name or domain}
        else:
            rec = {"id": None, "action": "would_create", "name": name or domain}
        self.stats["companies"][domain] = rec
        return rec

    # -- contact ------------------------------------------------------------
    def _desired(self, p: Person) -> tuple[dict, dict]:
        """HubSpot properties we'd like set, and the source ('call' / provider) of each."""
        resolved, sources = p.enrichment.get("resolved", {}), p.enrichment.get("sources", {})
        props, src = {}, {}
        for field, prop in {**FIELD_TO_PROP, "linkedin_url": self.linkedin_prop}.items():
            if _filled(resolved.get(field)):
                props[prop], src[prop] = resolved[field], sources.get(field, "call")
        return props, src

    def _find_contact(self, props: dict, domain: str) -> Optional[dict]:
        want = list(dict.fromkeys([*props, SOURCE_PROP, "hs_email_domain"]))
        if props.get("email"):
            hits = self.client.search("contacts", {"email": props["email"]}, want, limit=1)
            if hits:
                return hits[0]
        first, last = props.get("firstname"), props.get("lastname")
        if first and last:
            hits = self.client.search("contacts", {"firstname": first, "lastname": last}, want, limit=10)
            hits = [h for h in hits if (h.get("properties", {}).get("hs_email_domain") or domain).lower() == domain]
            if len(hits) == 1:
                return hits[0]
        return None

    def _sync_contact(self, p: Person, company: dict) -> None:
        domain = p.enrichment["domain"]
        props, src = self._desired(p)
        provider = p.enrichment.get("provider")
        source_value = "mock (demo data)" if p.enrichment.get("is_mock") else provider
        key = (props.get("email", "").lower(), props.get("firstname"), props.get("lastname"), domain)

        found = None if key in self._created_cache else self._find_contact(props, domain)
        crm: dict = {"company_id": company["id"]}
        if key in self._created_cache:
            crm.update(contact_id=self._created_cache[key], action="unchanged", pre_existing=False,
                       properties_written={}, kept_existing={})
        elif found:
            current = found.get("properties") or {}
            to_write = {k: v for k, v in props.items() if not _filled(current.get(k))}
            kept = {k: current[k] for k, v in props.items()
                    if _filled(current.get(k)) and str(current[k]).strip().lower() != str(v).strip().lower()}
            if any(src[k] != "call" for k in to_write) and not _filled(current.get(SOURCE_PROP)):
                to_write[SOURCE_PROP] = source_value
            cid = str(found["id"])
            if to_write and self.write:
                self.client.update("contacts", cid, to_write)
            action = "updated" if to_write else "unchanged"
            crm.update(contact_id=cid, action=action if self.write or action == "unchanged" else "would_update",
                       pre_existing=True, properties_written=to_write, kept_existing=kept)
            self.stats["contacts"][action] += 1
        else:
            to_write = dict(props)
            if any(s != "call" for s in src.values()):
                to_write[SOURCE_PROP] = source_value
            cid = self.client.create("contacts", to_write) if self.write else None
            if cid:
                self._created_cache[key] = cid
            crm.update(contact_id=cid, action="created" if self.write else "would_create", pre_existing=False,
                       properties_written=to_write, kept_existing={})
            self.stats["contacts"]["created"] += 1

        if self.write and crm["contact_id"] and company["id"]:
            self.client.associate_default("contacts", crm["contact_id"], "companies", company["id"])
            crm["company_associated"] = True
        p.crm = crm

    # -- deal ---------------------------------------------------------------
    def _choose_deal(self, people: list[Person], companies: dict) -> Optional[dict]:
        if self.deal_id:
            got = self.client.batch_read("deals", [self.deal_id], DEAL_PROPS)
            if not got:
                raise ValueError(f"Deal {self.deal_id} not found in HubSpot")
            return self._deal_rec(got[0], "override (--deal-id)", 1)

        ids: set[str] = set()
        for p in people:
            if p.crm.get("pre_existing") and p.crm.get("contact_id"):
                ids |= set(self.client.associated_ids("contacts", p.crm["contact_id"], "deals"))
        for c in companies.values():
            if c["action"] == "found":
                ids |= set(self.client.associated_ids("companies", c["id"], "deals"))
        if not ids:
            return None
        deals = self.client.batch_read("deals", sorted(ids), DEAL_PROPS)
        props = lambda d: d.get("properties") or {}  # noqa: E731
        open_ = [d for d in deals if str(props(d).get("hs_is_closed")).lower() != "true"]
        won = [d for d in deals if str(props(d).get("hs_is_closed_won")).lower() == "true"]
        pool, why = (open_, "most recently updated open deal") if open_ else (won, "most recently updated closed-won deal")
        if not pool:
            return None
        best = max(pool, key=lambda d: props(d).get("hs_lastmodifieddate") or "")
        return self._deal_rec(best, why, len(ids))

    @staticmethod
    def _deal_rec(d: dict, why: str, candidates: int) -> dict:
        pr = d.get("properties") or {}
        return {"id": str(d["id"]), "name": pr.get("dealname"), "stage": pr.get("dealstage"),
                "reason": why, "candidates": candidates}

    # -- labels -------------------------------------------------------------
    def _ensure_labels(self, roles: set[str]) -> dict[str, dict]:
        def by_label():
            # GET .../contacts/deals/labels lists only the contact->deal direction (the one we apply).
            return {(l.get("label") or "").strip().lower(): l for l in self.client.labels("contacts", "deals")}

        existing = by_label()
        wanted = {role: self.role_labels.get(role, role.replace("_", " ").title()) for role in sorted(roles)}
        missing = [r for r, label in wanted.items() if label.lower() not in existing]
        if missing and self.write:
            # Creating a label returns BOTH directions (contact->deal and deal->contact), so don't trust
            # the create response's ordering: create all, then re-read the contact->deal definitions.
            for role in missing:
                self.client.create_label("contacts", "deals", wanted[role], role)
                self.stats["labels_created"].append(wanted[role])
            existing = by_label()

        out = {}
        for role, label in wanted.items():
            hit = existing.get(label.lower())
            if hit:
                out[role] = {"associationCategory": hit.get("category", "USER_DEFINED"),
                             "associationTypeId": hit["typeId"], "label": label}
            else:
                self.stats["labels_missing" if self.write else "labels_created"].append(
                    label if self.write else f"would create {label}")
        return out

    def _label(self, people: list[Person], deal: dict) -> None:
        roles = {r.role for p in people for r in p.buying_roles}
        types = self._ensure_labels(roles) if roles else {}
        for p in people:
            labels = [types[r.role]["label"] if r.role in types else self.role_labels.get(r.role, r.role)
                      for r in p.buying_roles]
            body = [CONTACT_TO_DEAL_DEFAULT] + [
                {k: v for k, v in types[r.role].items() if k != "label"} for r in p.buying_roles if r.role in types]
            cid = p.crm.get("contact_id")
            applied = bool(self.write and cid)
            if applied:
                self.client.associate_labeled("contacts", cid, "deals", deal["id"], body)
                self.stats["contacts_labeled"] += bool(labels)
            p.crm["deal"] = {"deal_id": deal["id"], "deal_name": deal.get("name"), "labels": labels,
                             "applied": applied}
