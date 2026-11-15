"""CLI for the GTM pipeline.

    1 people_extraction -> 2 buying_role_classification -> 3a contact_enrichment -> 3b hubspot_sync

    python run.py txt samples/acme_discovery_call.txt            # steps 1-3a (mock enrichment)
    python run.py txt samples/ --hubspot                         # + HubSpot sync, DRY RUN (reads only)
    python run.py txt samples/ --hubspot --write                 # + HubSpot sync, applies changes
    python run.py fathom --recording-id 123456789 --hubspot --enricher apollo
    python run.py txt samples/ --until buying_role_classification
    python run.py txt samples/ --dry-run                         # print what the model would see, no API call

    python run.py resume output/<run>/latest.json --hubspot --write   # continue a saved run
    python run.py roles  output/<run>/00_people_extraction.json       # re-run only step 2
"""
from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

from people_agent.enrich import ENRICHERS, ContactEnrichmentStage, EnrichError, make_enricher
from people_agent.extractor import PeopleExtractionAgent
from people_agent.hubspot import HubSpotClient, HubSpotSyncStage
from people_agent.llm import OpenRouterClient
from people_agent.pipeline import Pipeline, load_envelope
from people_agent.roles import DEFAULT_ROLES, ROLE_LIBRARY, BuyingRoleStage
from people_agent.sources import FathomSource, TxtSource

STAGE_ORDER = ["buying_role_classification", "contact_enrichment", "hubspot_sync"]
UNTIL_CHOICES = ["people_extraction", *STAGE_ORDER]


# --------------------------------------------------------------------------- output
def _summary(env, path) -> str:
    rows = [f"\n=== {env.source.title} ({env.source.type}) ===", f"handoff -> {path}"]
    rows.append(f"{'NAME':26} {'TITLE':26} {'COMPANY':18} {'SIDE':9} {'PRESENCE':15} {'LIST':4} REVIEW")
    for p in env.people:
        rows.append(
            f"{p.full_name[:26]:26} {(p.title or '-')[:26]:26} {(p.company or '-')[:18]:18} {p.side:9} "
            f"{p.presence:15} {'yes' if p.in_attendee_list else 'no':4} {'!' if p.needs_review else ''}"
        )
        if p.aliases:
            rows.append(f"{'':26}   aka: {', '.join(p.aliases)}")
        for r in p.buying_roles:
            rows.append(f"{'':26}   ROLE {r.role} ({r.confidence:.2f}): {r.rationale}")
        e = p.enrichment
        if e.get("status") and e["status"] != "out_of_scope":
            filled = [f for f, s in e.get("sources", {}).items() if s != "call"]
            mock = " [MOCK DATA]" if e.get("is_mock") else ""
            rows.append(f"{'':26}   ENRICH {e['status']} via {e.get('provider')}{mock} @ {e.get('domain')}"
                        + (f": +{', '.join(filled)}" if filled else ""))
        if p.crm.get("action"):
            deal = p.crm.get("deal") or {}
            lab = f" | deal {deal.get('deal_name') or deal.get('deal_id')}: {', '.join(deal.get('labels') or []) or 'no label'}" \
                if deal else ""
            rows.append(f"{'':26}   HUBSPOT {p.crm['action']} contact {p.crm.get('contact_id') or '(new)'}"
                        f" ({len(p.crm.get('properties_written') or {})} props){lab}")

    for r in env.unnamed_roles:
        rows.append(f"  unnamed: {r.description}")
    s = env.stats
    br, ce, hs = s.get("buying_roles"), s.get("contact_enrichment"), s.get("hubspot_sync")
    if br:
        rows.append(f"buying roles: {br.get('role_counts') or br.get('skipped')} (scored {br.get('scored_people')} buyer-side people)")
    if ce:
        rows.append(f"enrichment ({ce['provider']}): anchor={list(ce['anchor_domains'])} in_scope={ce['in_scope']} "
                    f"enriched={ce['enriched']} not_found={ce['not_found']} errors={ce['error']}")
    if hs:
        deal = hs.get("deal")
        rows.append(f"hubspot [{hs['mode'].upper()}]: contacts={hs['contacts']} "
                    f"deal={(deal or {}).get('name') or (deal or {}).get('id') or 'none found (labels skipped)'} "
                    f"labels_created={hs['labels_created']} props={hs['properties_created']}")
    rows.append(
        f"people={s.get('total_people')} external={s.get('external')} mentioned_only={s.get('mentioned_only')} "
        f"not_on_list={s.get('not_on_attendee_list')} needs_review={s.get('needs_review')} next_stage={env.next_stage}"
    )
    latest = {}  # only report a stage's error if its most recent attempt failed
    for h in env.history:
        if h.status != "skipped":
            latest[h.stage] = h
    for h in latest.values():
        if h.status == "error":
            rows.append(f"ERROR in {h.stage}: {h.detail}")
    return "\n".join(rows)


# --------------------------------------------------------------------------- wiring
def _until(a) -> str:
    if getattr(a, "no_roles", False):
        return "people_extraction"
    if a.hubspot:
        return "hubspot_sync"
    return a.until


def build_stages(a, start: str, until: str, llm_factory) -> list:
    if until == "people_extraction":
        return []
    names = STAGE_ORDER[STAGE_ORDER.index(start): STAGE_ORDER.index(until) + 1]
    role_keys = [x.strip() for x in a.roles.split(",") if x.strip()]
    stages = []
    for n in names:
        if n == "buying_role_classification":
            stages.append(BuyingRoleStage(llm_factory(), roles=role_keys, min_confidence=a.min_confidence,
                                          max_tokens=a.max_tokens))
        elif n == "contact_enrichment":
            stages.append(ContactEnrichmentStage(make_enricher(a.enricher), max_lookups=a.max_enrich))
        elif n == "hubspot_sync":
            stages.append(HubSpotSyncStage(HubSpotClient(), write=a.write, deal_id=a.deal_id))
    return stages


def _add_common(p: argparse.ArgumentParser, with_until: bool = True) -> None:
    p.add_argument("--model", default=None, help="OpenRouter model id (default: OPENROUTER_MODEL or anthropic/claude-sonnet-5.5)")
    p.add_argument("--out", default=os.getenv("OUTPUT_DIR", "output"))
    p.add_argument("--max-tokens", type=int, default=16000)
    p.add_argument("--roles", default=os.getenv("BUYING_ROLES", ",".join(DEFAULT_ROLES)),
                   help=f"comma-separated roles to assign. Available: {', '.join(ROLE_LIBRARY)}")
    p.add_argument("--min-confidence", type=float, default=float(os.getenv("ROLE_MIN_CONFIDENCE", "0.6")),
                   help="drop buying roles below this confidence (default 0.6)")
    if with_until:
        p.add_argument("--until", choices=UNTIL_CHOICES, default="contact_enrichment",
                       help="last step to run (default contact_enrichment)")
        p.add_argument("--hubspot", action="store_true", help="run through hubspot_sync (same as --until hubspot_sync)")
        p.add_argument("--write", action="store_true", help="HubSpot: apply changes. Without it the sync is a dry run")
        p.add_argument("--deal-id", default=None, help="HubSpot: label against this deal instead of looking it up")
        p.add_argument("--enricher", default=os.getenv("ENRICHER", "mock"), choices=list(ENRICHERS),
                       help="enrichment provider (default mock: free, fake demo data)")
        p.add_argument("--max-enrich", type=int, default=int(os.getenv("MAX_ENRICH", "25")),
                       help="max enrichment lookups per call (protects provider credits)")


def main(argv=None) -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(description="Transcript -> people -> buying roles -> enrichment -> HubSpot.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("txt", help="read local .txt transcripts")
    t.add_argument("path", help=".txt file or folder of .txt files")
    f = sub.add_parser("fathom", help="pull transcripts from the Fathom API")
    f.add_argument("--recording-id", type=int)
    f.add_argument("--since", help="created_after, e.g. 2026-11-01 or 2026-11-01T00:00:00Z")
    f.add_argument("--until-date", dest="until_date", help="created_before")
    f.add_argument("--recorded-by", action="append", default=[], help="recorder email (repeatable)")
    f.add_argument("--include-internal", action="store_true", help="also process internal-only meetings")
    f.add_argument("--limit", type=int, default=10)
    for p in (t, f):
        _add_common(p)
        p.add_argument("--internal-domains", default=os.getenv("INTERNAL_DOMAINS", ""),
                       help="comma-separated email domains of YOUR company")
        p.add_argument("--no-roles", action="store_true", help="stop after people extraction")
        p.add_argument("--dry-run", action="store_true", help="print the rendered transcript and exit (no API calls)")

    rs = sub.add_parser("resume", help="continue a saved handoff file from its next_stage")
    rs.add_argument("envelope")
    rs.add_argument("--from", dest="start", choices=STAGE_ORDER, help="restart from this step instead")
    _add_common(rs)

    r = sub.add_parser("roles", help="re-run only buying-role classification on a saved handoff file")
    r.add_argument("envelope")
    r.add_argument("--dry-run", action="store_true", help="print the prompt and exit")
    _add_common(r, with_until=False)

    a = ap.parse_args(argv)
    llm_cache: list = []

    def llm_factory():
        if not llm_cache:
            llm_cache.append(OpenRouterClient(model=a.model))
        return llm_cache[0]

    # ---- re-run / continue a saved file
    if a.cmd in ("roles", "resume"):
        env = load_envelope(a.envelope)
        if a.cmd == "roles":
            start = until = "buying_role_classification"
            a.enricher, a.max_enrich, a.write, a.deal_id = "mock", 0, False, None
            if a.dry_run:
                stage = BuyingRoleStage(None, roles=[x.strip() for x in a.roles.split(",") if x.strip()])
                print(stage.system_prompt, "\n\n----- USER -----\n", stage.build_user_prompt(env), sep="")
                return 0
        else:
            start = a.start or env.next_stage
            until = _until(a)
            if start not in STAGE_ORDER:
                print(f"Nothing to resume: envelope next_stage is {env.next_stage!r}. Use --from.")
                return 1
            if STAGE_ORDER.index(start) > STAGE_ORDER.index(until):
                until = start
        env.next_stage = start
        stages = build_stages(a, start, until, llm_factory)
        env, path = Pipeline(None, stages=stages, output_dir=a.out).resume(env)
        print(_summary(env, path))
        return 1 if any(h.status == "error" for h in env.history[-len(stages):]) else 0

    # ---- fresh run from a source
    domains = [d.strip() for d in a.internal_domains.split(",") if d.strip()]
    if a.cmd == "txt":
        source = TxtSource(a.path, internal_domains=domains)
    else:
        since = a.since + "T00:00:00Z" if a.since and "T" not in a.since else a.since
        before = a.until_date + "T00:00:00Z" if a.until_date and "T" not in a.until_date else a.until_date
        source = FathomSource(
            recording_id=a.recording_id, created_after=since, created_before=before, recorded_by=a.recorded_by,
            external_only=not a.include_internal, limit=a.limit, internal_domains=domains,
        )

    if a.dry_run:
        for tr in source.fetch():
            print(f"\n----- {tr.source_id} -----\n{tr.render()}")
        return 0

    agent = PeopleExtractionAgent(llm_factory(), max_tokens=a.max_tokens)
    stages = build_stages(a, STAGE_ORDER[0], _until(a), llm_factory)
    results = Pipeline(agent, stages=stages, output_dir=a.out).run(source.fetch())
    for env, path in results:
        print(_summary(env, path))
    return 0 if results else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, EnrichError) as e:  # setup problems (missing keys, bad flags): no traceback
        print(f"ERROR: {e}")
        sys.exit(2)
