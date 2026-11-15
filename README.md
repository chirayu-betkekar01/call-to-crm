# Call-to-CRM Agent Pipeline

Three AI agents that turn sales-call transcripts into a labeled HubSpot buying group: who was
really on the call, what role each person plays in the deal, and their contact details, synced to
the deal automatically.

▶ **[Watch the walkthrough video](https://drive.google.com/file/d/1LnM_1xb4E-TdySxOHEy3QspwrKayqj1M/view?usp=sharing)**

Reps take calls with several stakeholders, but only the person they invited ends up in the CRM.
The decision maker who joined from a conference room, or was only mentioned ("I'll need to run it
by Mark"), is never logged, and nobody labels buying roles by hand. These agents rebuild the
buying group from the transcript and write it into HubSpot, so reps only have to take the call.

```
 transcript ──► Agent 1                 ──► Agent 2                ──► Agent 3
 (Fathom/.txt)  Attendee Reconciler         Buying-Role Classifier     Enrichment + HubSpot Sync
                who was really on /         Decision Maker /           fill contact gaps, find the deal,
                named in the call           Champion / Blocker         apply role labels in HubSpot
                00_people_extraction.json   01_buying_role_...json     02_contact_enrichment / 03_hubspot_sync
```

| Agent | Folder | Run on its own | Details |
|---|---|---|---|
| 1. Attendee Reconciler | `agents/attendee_reconciler/` | `python -m agents.attendee_reconciler txt <file>` | [README](agents/attendee_reconciler/README.md) |
| 2. Buying-Role Classifier | `agents/role_classifier/` | `python -m agents.role_classifier <agent-1 file>` | [README](agents/role_classifier/README.md) |
| 3. Enrichment + HubSpot Sync | `agents/enrichment_sync/` | `python -m agents.enrichment_sync <agent-2 file>` | [README](agents/enrichment_sync/README.md) |

The agents are independent. Each one imports only `core/` (shared models, the handoff file, the
LLM client), and they pass work to each other through a saved JSON **handoff file**. You can run
them one at a time, re-run any one of them, or run all three with `run.py`.

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env        # add OPENROUTER_API_KEY; HUBSPOT_TOKEN for the sync
```

Run all three agents:

```bash
python run.py txt samples/acme_discovery_call.txt                    # agents 1-2 + enrichment
python run.py txt samples/acme_discovery_call.txt --hubspot          # + HubSpot DRY RUN (reads only)
python run.py txt samples/acme_discovery_call.txt --hubspot --write  # + HubSpot, apply changes
python run.py fathom --recording-id 123456789 --hubspot --write
python run.py resume output/<run>/latest.json --hubspot --write      # continue a saved run
python run.py show   output/<run>/01_buying_role_classification.json # print a saved step, no API calls
```

Or run them one at a time:

```bash
python -m agents.attendee_reconciler txt samples/acme_discovery_call.txt
python -m agents.role_classifier   output/<run>/00_people_extraction.json
python -m agents.enrichment_sync   output/<run>/01_buying_role_classification.json --write
```

`run.py` options: `--until <step>`, `--hubspot`, `--write`, `--deal-id`, `--enricher mock|hunter|apollo`,
`--max-enrich`, `--roles`, `--min-confidence`, plus the source options (`--internal-domains`, `--model`,
`--dry-run`, Fathom filters). Enrichment defaults to free mock data, so no provider key is needed.

## Example (sample call, live run)

| Person | Found how | Role | In HubSpot |
|---|---|---|---|
| Mark Davis, VP of Sales | only mentioned, never on the call | **Decision Maker** (0.95): *"He'll make the final call… He signs anything over fifty K."* | new contact, labeled on the deal |
| Matthew Collins | invited, spoke | **Champion** (0.82) | existing contact (email only), gaps filled, email kept |
| Elizabeth Novak | only mentioned | **Blocker** (0.62): *"Liz has been known to hold things up for a month."* | new contact, labeled |
| Jordan Pierce | joined from the conference room, not invited | (no role) | new contact, linked to the deal |
| Tom (Clarivue rep) | mentioned | (competitor, not scored) | not synced |

## Layout

```
core/                      shared by every agent
  models.py                Transcript, Person, RoleAssignment, HandoffEnvelope
  pipeline.py              Stage, Pipeline, run_saved, handoff files
  llm.py                   OpenRouter client + JSONModel protocol
  schemas.py, report.py    JSON-schema helpers, run summary
agents/
  attendee_reconciler/     Agent 1: agent, prompts, resolver, nicknames, sources (Fathom, txt)
  role_classifier/         Agent 2: agent, rubric
  enrichment_sync/         Agent 3: enrich, providers (mock, hunter, apollo), hubspot
run.py                     runs all three
scripts/seed_demo_portal.py  seed/reset a HubSpot test portal for demos
samples/                   demo transcripts
tests/                     offline tests, one file per agent
PIPELINE_SPEC.md           original spec;  DEMO.md: Loom demo plan
```

## The handoff file (`HandoffEnvelope`, schema v1.1)

Each step writes `output/<run_id>_<title>/NN_<step>.json` and `latest.json`. Each agent reads
the fields it needs and fills in its own:

```jsonc
{
  "schema_version": "1.1",
  "current_stage": "buying_role_classification",
  "next_stage": "contact_enrichment",
  "source": {"type": "fathom", "id": "4242", "title": "...", "date": "...", "url": "..."},
  "transcript": { ... },                         // normalized call, so any agent can re-read it
  "company_context": {"internal_company": "Northwind", "external_companies": ["Acme Corp"]},
  "people": [{
    "person_id": "p_3f9a1c0b2d", "full_name": "Mark Davis", "aliases": ["Mark"],
    "title": "VP of Sales", "title_source": "stated_by_other", "company": "Acme Corp", "email": null,
    "side": "external", "presence": "mentioned_only", "in_attendee_list": false,           // Agent 1
    "buying_roles": [{"role": "decision_maker", "confidence": 0.95, "rationale": "...",
                      "evidence": [{"timestamp": "00:01:05", "speaker": "Matt",
                                    "quote": "He'll make the final call, yeah."}]}],      // Agent 2
    "enrichment": {"status": "enriched", "provider": "mock", "resolved": {...}},         // Agent 3A
    "crm": {"contact_id": "...", "action": "created", "deal": {"labels": ["Decision Maker"]}}, // Agent 3B
    "needs_review": false, "review_reasons": [], "notes": []
  }],
  "unnamed_roles": [{"description": "CFO at Acme, approves budget", "title": "CFO"}],
  "stats": {...},
  "history": [{"stage": "people_extraction", "status": "ok", "started_at": "...", "finished_at": "..."}]
}
```

A step runs only when `next_stage` matches it. If a step fails, the error is recorded in
`history` and the file is still saved.

## Tests

```bash
python -m pytest -q
```

The tests run offline. The LLM, Fathom, Hunter, Apollo and HubSpot are all faked, so no API keys
are needed.
