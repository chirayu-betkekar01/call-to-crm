"""Run Agent 1 on its own.

    python -m agents.attendee_reconciler txt samples/acme_discovery_call.txt
    python -m agents.attendee_reconciler fathom --recording-id 123456789
    python -m agents.attendee_reconciler fathom --since 2026-11-01 --limit 5
    python -m agents.attendee_reconciler txt samples/ --dry-run     # print what the model sees

Writes output/<run>/00_people_extraction.json. Pass that file to Agent 2.
"""
from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

from core import OpenRouterClient, Pipeline, summary

from .agent import AttendeeReconcilerAgent
from .sources import FathomSource, TxtSource


def add_source_args(sub: argparse._SubParsersAction) -> tuple[argparse.ArgumentParser, argparse.ArgumentParser]:
    """Shared with the run-all orchestrator."""
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
        p.add_argument("--internal-domains", default=os.getenv("INTERNAL_DOMAINS", ""),
                       help="comma-separated email domains of YOUR company")
        p.add_argument("--model", default=None, help="OpenRouter model id (default: OPENROUTER_MODEL or anthropic/claude-sonnet-5.5)")
        p.add_argument("--out", default=os.getenv("OUTPUT_DIR", "output"))
        p.add_argument("--max-tokens", type=int, default=16000)
        p.add_argument("--dry-run", action="store_true", help="print the rendered transcript and exit (no API calls)")
    return t, f


def build_source(a):
    domains = [d.strip() for d in a.internal_domains.split(",") if d.strip()]
    if a.cmd == "txt":
        return TxtSource(a.path, internal_domains=domains)
    since = a.since + "T00:00:00Z" if a.since and "T" not in a.since else a.since
    before = a.until_date + "T00:00:00Z" if a.until_date and "T" not in a.until_date else a.until_date
    return FathomSource(recording_id=a.recording_id, created_after=since, created_before=before,
                        recorded_by=a.recorded_by, external_only=not a.include_internal, limit=a.limit,
                        internal_domains=domains)


def main(argv=None) -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(prog="python -m agents.attendee_reconciler",
                                 description="Agent 1: reconcile who was really on (or named in) a call.")
    add_source_args(ap.add_subparsers(dest="cmd", required=True))
    a = ap.parse_args(argv)
    source = build_source(a)
    if a.dry_run:
        for tr in source.fetch():
            print(f"\n----- {tr.source_id} -----\n{tr.render()}")
        return 0
    agent = AttendeeReconcilerAgent(OpenRouterClient(model=a.model), max_tokens=a.max_tokens)
    results = Pipeline(agent, stages=[], output_dir=a.out).run(source.fetch())
    for env, path in results:
        print(summary(env, path))
        print(f"next: python -m agents.role_classifier {path}")
    return 0 if results else 1



def run() -> None:
    """Console entry point (python -m agents.<name>)."""
    sys.exit(main())
