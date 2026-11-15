"""Run Agent 3 on its own, on a saved Agent 2 handoff file.

    python -m agents.enrichment_sync output/<run>/latest.json                 # enrich + HubSpot DRY RUN
    python -m agents.enrichment_sync output/<run>/latest.json --write         # enrich + HubSpot, apply
    python -m agents.enrichment_sync output/<run>/latest.json --enrich-only --enricher hunter
    python -m agents.enrichment_sync output/<run>/latest.json --sync-only --write --deal-id 123
"""
from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

from core import failed, run_saved, summary

from .enrich import ContactEnrichmentStage
from .hubspot import HubSpotClient, HubSpotSyncStage
from .providers import ENRICHERS, EnrichError, make_enricher


def add_sync_args(p: argparse.ArgumentParser) -> None:
    """Shared with the run-all orchestrator."""
    p.add_argument("--enricher", default=os.getenv("ENRICHER", "mock"), choices=list(ENRICHERS),
                   help="enrichment provider (default mock: free, fake demo data)")
    p.add_argument("--max-enrich", type=int, default=int(os.getenv("MAX_ENRICH", "25")),
                   help="max enrichment lookups per call (protects provider credits)")
    p.add_argument("--write", action="store_true", help="HubSpot: apply changes (default is a dry run)")
    p.add_argument("--deal-id", default=None, help="HubSpot: label against this deal instead of looking it up")


def build_stages(a, enrich: bool = True, sync: bool = True) -> list:
    stages = []
    if enrich:
        stages.append(ContactEnrichmentStage(make_enricher(a.enricher), max_lookups=a.max_enrich))
    if sync:
        stages.append(HubSpotSyncStage(HubSpotClient(), write=a.write, deal_id=a.deal_id))
    return stages


def main(argv=None) -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(prog="python -m agents.enrichment_sync",
                                 description="Agent 3: enrich buyer-side contacts and sync them to HubSpot.")
    ap.add_argument("envelope", help="saved handoff file from Agent 2")
    add_sync_args(ap)
    only = ap.add_mutually_exclusive_group()
    only.add_argument("--enrich-only", action="store_true", help="skip the HubSpot step")
    only.add_argument("--sync-only", action="store_true", help="skip enrichment (file must already be enriched)")
    ap.add_argument("--out", default=os.getenv("OUTPUT_DIR", "output"))
    a = ap.parse_args(argv)

    stages = build_stages(a, enrich=not a.sync_only, sync=not a.enrich_only)
    env, path = run_saved(a.envelope, stages, a.out)
    print(summary(env, path))
    return 1 if failed(env) else 0



def run() -> None:
    """Console entry point (python -m agents.<name>)."""
    try:
        sys.exit(main())
    except (ValueError, EnrichError) as e:  # missing keys / bad flags
        print(f"ERROR: {e}")
        sys.exit(2)
