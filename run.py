"""Run all three agents in sequence (each one can also run on its own: see README).

    Agent 1 attendee_reconciler -> Agent 2 role_classifier -> Agent 3 enrichment_sync (enrich, then HubSpot)

    python run.py txt samples/acme_discovery_call.txt              # agents 1-2 + enrichment
    python run.py txt samples/acme_discovery_call.txt --hubspot    # + HubSpot DRY RUN
    python run.py txt samples/acme_discovery_call.txt --hubspot --write
    python run.py fathom --recording-id 123456789 --hubspot --write
    python run.py resume output/<run>/latest.json --hubspot --write   # continue a saved run
"""
from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

from agents.attendee_reconciler import AttendeeReconcilerAgent
from agents.attendee_reconciler.cli import add_source_args, build_source
from agents.enrichment_sync.cli import add_sync_args
from agents.enrichment_sync.cli import build_stages as agent3_stages
from agents.enrichment_sync.providers import EnrichError
from agents.role_classifier.cli import add_role_args
from agents.role_classifier.cli import build_stage as agent2_stage
from core import OpenRouterClient, Pipeline, failed, load_envelope, run_saved, summary

STAGE_ORDER = ["buying_role_classification", "contact_enrichment", "hubspot_sync"]
UNTIL_CHOICES = ["people_extraction", *STAGE_ORDER]


def _until(a) -> str:
    return "hubspot_sync" if a.hubspot else a.until


def build_stages(a, start: str, until: str, llm_factory) -> list:
    if until == "people_extraction":
        return []
    names = STAGE_ORDER[STAGE_ORDER.index(start): STAGE_ORDER.index(until) + 1]
    stages = []
    if "buying_role_classification" in names:
        stages.append(agent2_stage(a, llm_factory()))
    stages += agent3_stages(a, enrich="contact_enrichment" in names, sync="hubspot_sync" in names)
    return stages


def _add_pipeline_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--until", choices=UNTIL_CHOICES, default="contact_enrichment",
                   help="last step to run (default contact_enrichment)")
    p.add_argument("--hubspot", action="store_true", help="run through the HubSpot sync (= --until hubspot_sync)")
    add_role_args(p)
    add_sync_args(p)


def main(argv=None) -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(description="Run all three agents: transcript -> roles -> enrichment -> HubSpot.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for p in add_source_args(sub):
        _add_pipeline_args(p)
    rs = sub.add_parser("resume", help="continue a saved handoff file from its next step")
    rs.add_argument("envelope")
    rs.add_argument("--from", dest="start", choices=STAGE_ORDER, help="restart from this step instead")
    rs.add_argument("--model", default=None)
    rs.add_argument("--max-tokens", type=int, default=16000)
    rs.add_argument("--out", default=os.getenv("OUTPUT_DIR", "output"))
    _add_pipeline_args(rs)
    a = ap.parse_args(argv)

    llm_cache: list = []

    def llm_factory():
        if not llm_cache:
            llm_cache.append(OpenRouterClient(model=a.model))
        return llm_cache[0]

    if a.cmd == "resume":
        env = load_envelope(a.envelope)
        start = a.start or env.next_stage
        if start not in STAGE_ORDER:
            print(f"Nothing to resume: envelope next_stage is {env.next_stage!r}. Use --from.")
            return 1
        until = _until(a)
        if STAGE_ORDER.index(start) > STAGE_ORDER.index(until):
            until = start
        env, path = run_saved(env, build_stages(a, start, until, llm_factory), a.out, start=start)
        print(summary(env, path))
        return 1 if failed(env) else 0

    source = build_source(a)
    if a.dry_run:
        for tr in source.fetch():
            print(f"\n----- {tr.source_id} -----\n{tr.render()}")
        return 0
    agent1 = AttendeeReconcilerAgent(llm_factory(), max_tokens=a.max_tokens)
    stages = build_stages(a, STAGE_ORDER[0], _until(a), llm_factory)
    results = Pipeline(agent1, stages=stages, output_dir=a.out).run(source.fetch())
    for env, path in results:
        print(summary(env, path))
    return 0 if results else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, EnrichError) as e:  # setup problems (missing keys, bad flags): no traceback
        print(f"ERROR: {e}")
        sys.exit(2)
