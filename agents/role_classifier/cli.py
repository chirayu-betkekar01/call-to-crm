"""Run Agent 2 on its own, on a saved Agent 1 handoff file.

    python -m agents.role_classifier output/<run>/00_people_extraction.json
    python -m agents.role_classifier output/<run>/latest.json --roles decision_maker,champion,blocker,power_user
    python -m agents.role_classifier output/<run>/00_people_extraction.json --dry-run   # print the prompt

Writes NN_buying_role_classification.json next to the input. Pass that file to Agent 3.
"""
from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

from core import OpenRouterClient, failed, load_envelope, run_saved, summary

from .agent import BuyingRoleStage
from .rubric import DEFAULT_ROLES, ROLE_LIBRARY


def add_role_args(p: argparse.ArgumentParser) -> None:
    """Shared with the run-all orchestrator."""
    p.add_argument("--roles", default=os.getenv("BUYING_ROLES", ",".join(DEFAULT_ROLES)),
                   help=f"comma-separated roles to assign. Available: {', '.join(ROLE_LIBRARY)}")
    p.add_argument("--min-confidence", type=float, default=float(os.getenv("ROLE_MIN_CONFIDENCE", "0.6")),
                   help="drop buying roles below this confidence (default 0.6)")


def build_stage(a, llm) -> BuyingRoleStage:
    roles = [x.strip() for x in a.roles.split(",") if x.strip()]
    return BuyingRoleStage(llm, roles=roles, min_confidence=a.min_confidence,
                           max_tokens=getattr(a, "max_tokens", 8000))


def main(argv=None) -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(prog="python -m agents.role_classifier",
                                 description="Agent 2: label buyer-side people Decision Maker / Champion / Blocker.")
    ap.add_argument("envelope", help="saved handoff file from Agent 1")
    add_role_args(ap)
    ap.add_argument("--model", default=None, help="OpenRouter model id")
    ap.add_argument("--max-tokens", type=int, default=8000)
    ap.add_argument("--out", default=os.getenv("OUTPUT_DIR", "output"))
    ap.add_argument("--dry-run", action="store_true", help="print the prompt and exit (no API call)")
    a = ap.parse_args(argv)

    if a.dry_run:
        stage = build_stage(a, llm=None)
        print(stage.system_prompt, "\n\n----- USER -----\n", stage.build_user_prompt(load_envelope(a.envelope)), sep="")
        return 0
    env, path = run_saved(a.envelope, [build_stage(a, OpenRouterClient(model=a.model))], a.out)
    print(summary(env, path))
    if not failed(env):
        print(f"next: python -m agents.enrichment_sync {path}")
    return 1 if failed(env) else 0



def run() -> None:
    """Console entry point (python -m agents.<name>)."""
    sys.exit(main())
