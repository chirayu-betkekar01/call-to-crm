"""Seed a HubSpot TEST portal with the demo starting point (same as the walkthrough video).

    python scripts/seed_demo_portal.py           # create what's missing (safe to re-run)
    python scripts/seed_demo_portal.py --reset   # archive demo records first, then seed again
    python scripts/seed_demo_portal.py --clear   # archive demo records only

Starting point:
  * Company  "Acme Corp" (acmecorp.com)
  * Contact  matthew.collins@acmecorp.com: email ONLY, so the pipeline has gaps to fill
  * Deal     "Test Deal", linked to the contact and the company

This script is separate from the pipeline on purpose: the pipeline never creates deals.
--reset archives every contact at acmecorp.com, the Acme company and "Test Deal". Archived
records can be restored from HubSpot's recycle bin for 90 days. Only use it on a test portal.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

from agents.enrichment_sync.hubspot import HubSpotClient  # noqa: E402

DOMAIN = "acmecorp.com"
COMPANY = {"name": "Acme Corp", "domain": DOMAIN}
CONTACT = {"email": "matthew.collins@acmecorp.com"}
DEAL = {"dealname": "Test Deal", "pipeline": "default", "dealstage": "appointmentscheduled"}


def find_one(c: HubSpotClient, kind: str, filters: dict, props: list[str]):
    hits = c.search(kind, filters, props, limit=1)
    return str(hits[0]["id"]) if hits else None


def reset(c: HubSpotClient) -> None:
    contacts = c.search("contacts", {"hs_email_domain": DOMAIN}, ["email"], limit=100)
    deals = c.search("deals", {"dealname": DEAL["dealname"]}, ["dealname"], limit=100)
    companies = c.search("companies", {"domain": DOMAIN}, ["name"], limit=100)
    for kind, rows in (("contacts", contacts), ("deals", deals), ("companies", companies)):
        for r in rows:
            pr = r.get("properties") or {}
            print(f"  archiving {kind[:-1]:8} {r['id']}  {pr.get('email') or pr.get('dealname') or pr.get('name')}")
            c.archive(kind, str(r["id"]))
        print(f"archived {len(rows)} {kind}")


def seed(c: HubSpotClient) -> None:
    company = find_one(c, "companies", {"domain": DOMAIN}, ["name"])
    if not company:
        company = c.create("companies", COMPANY)
        print(f"created company  {company}  Acme Corp")
    else:
        print(f"found company    {company}  Acme Corp")

    contact = find_one(c, "contacts", {"email": CONTACT["email"]}, ["email"])
    if not contact:
        contact = c.create("contacts", CONTACT)
        print(f"created contact  {contact}  {CONTACT['email']} (email only)")
    else:
        print(f"found contact    {contact}  {CONTACT['email']}")

    deal = find_one(c, "deals", {"dealname": DEAL["dealname"]}, ["dealname"])
    if not deal:
        deal = c.create("deals", DEAL)
        print(f"created deal     {deal}  Test Deal")
    else:
        print(f"found deal       {deal}  Test Deal")

    c.associate_default("contacts", contact, "companies", company)
    c.associate_default("contacts", contact, "deals", deal)
    c.associate_default("companies", company, "deals", deal)
    print("linked contact <-> company <-> deal")


def main() -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(description="Seed a HubSpot test portal with the demo starting point.")
    ap.add_argument("--reset", action="store_true", help="archive existing demo records first")
    ap.add_argument("--clear", action="store_true", help="archive the demo records and stop (no re-seed)")
    a = ap.parse_args()
    c = HubSpotClient()
    if a.reset or a.clear:
        reset(c)
    if not a.clear:
        seed(c)
    return 0


if __name__ == "__main__":
    sys.exit(main())
