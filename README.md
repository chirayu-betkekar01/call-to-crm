# Transcript People Agent

A GTM pipeline that turns sales-call transcripts into a clean HubSpot buying group (full spec:
[PIPELINE_SPEC.md](PIPELINE_SPEC.md)):

```
[Fathom API | .txt files] -> 1. People Extraction -> 2. Buying-Role Classification
                          -> 3a. Contact Enrichment -> 3b. HubSpot Sync
```

It reads a sales-call transcript and returns **every person involved**: speakers, silent
attendees, people who joined but weren't invited, and people only *mentioned* ("my boss
Mark, our VP of Sales"). It records each person's title, company, side (internal/external)
and role in the deal, then writes a JSON handoff file for the next agent.

## Setup

```bash
pip install -r requirements.txt
```

```bash
cp .env.example .env
```

Fill in `OPENROUTER_API_KEY`, `FATHOM_API_KEY` (only needed for Fathom),
`INTERNAL_DOMAINS` (your company's email domains, used to tag people as internal), and
`HUBSPOT_TOKEN` for step 3b. Enrichment defaults to the free mock provider, so no key is needed.

## Run

By default a run goes through steps 1, 2 and 3a. Add `--hubspot` to also sync. The sync is a
**dry run** (reads only) unless you add `--write`.

```bash
python run.py txt samples/acme_discovery_call.txt              # steps 1-3a
python run.py txt samples/ --hubspot                           # + HubSpot dry run
python run.py txt samples/ --hubspot --write                   # + HubSpot, apply changes
python run.py fathom --recording-id 123456789 --hubspot --write
python run.py fathom --since 2026-11-01 --until-date 2026-11-08 --limit 5
```

Continue or re-run a saved handoff file without repeating earlier steps:

```bash
python run.py resume output/<run>/latest.json --hubspot --write        # from its next_stage
python run.py resume output/<run>/latest.json --from contact_enrichment --enricher apollo
python run.py roles  output/<run>/00_people_extraction.json            # only step 2
```

Options:

| Flag | Meaning |
|---|---|
| `--until <step>` | last step to run (`people_extraction`, `buying_role_classification`, `contact_enrichment` (default), `hubspot_sync`) |
| `--hubspot` / `--write` / `--deal-id N` | include the sync / apply changes / label against this deal instead of looking it up |
| `--enricher mock\|hunter\|apollo`, `--max-enrich 25` | enrichment provider; maximum lookups per call |
| `--roles ...`, `--min-confidence 0.6` | buying-role rubric and threshold |
| `--no-roles` | stop after step 1 |
| `--internal-domains`, `--model`, `--include-internal`, `--recorded-by` | source and model options |
| `--dry-run` | print what the model would see, with no API calls |

`.env` can set the defaults: `ENRICHER`, `MAX_ENRICH`, `BUYING_ROLES`, `ROLE_MIN_CONFIDENCE`.

## Step 2: buying roles

Labels each **buyer-side** person (side `external`) with zero or more roles, judged mostly by
how they behave on the call. Title counts, but less. Rubric (`people_agent/roles.py`, `ROLE_LIBRARY`):

| Role | Signals |
|---|---|
| `decision_maker` | others defer to them; approvals, budget, timelines; named as the approver. Can be absent from the call. |
| `champion` | moves the deal forward: brings in colleagues, sells internally, owns next steps |
| `blocker` | ongoing skepticism or objections; others warn they hold things up |
| `power_user` (off by default) | hands-on user asking in-the-weeds questions |

Rules: not everyone gets a role, and one person can have several. The seller's team is never scored.
After the model answers, code checks every evidence quote against the transcript (ignoring case
and punctuation; `...` joins fragments). A role with no matching quote is dropped. Roles below
`--min-confidence` are dropped and logged in `notes`. Champion + blocker on the same person sets
`needs_review`. Results go in `person.buying_roles`. The old single `buying_role` field is
step 1's first guess.

## Step 3a: contact enrichment

1. **Anchor domain.** The buying company's domain is the email domain of the outside invitees.
   Buyer-side people with no email (mentioned only, first name only) are assumed to work there.
   People at other companies (a competitor's rep who gets mentioned) and the seller's team are
   `out_of_scope`. They aren't enriched or synced.
2. **Provider lookup.** Each provider implements `Enricher.enrich(EnrichQuery) -> EnrichResult | None`:
   - `mock` (default): free and offline. It generates realistic but fake title, email, LinkedIn
     URL and phones (fictional `555-01XX` numbers), the same way every time, and marks them `is_mock`.
     Good for demos and screen recordings.
   - `hunter`: Hunter Email Finder (`HUNTER_API_KEY`). Works on Hunter's free plan (about 50
     credits a month). Returns email, title, LinkedIn and sometimes a phone number. Hunter needs a
     last name, so first-name-only people are skipped without spending a credit.
   - `apollo`: Apollo People Match (`APOLLO_API_KEY`). Needs a paid Apollo plan, because the
     free plan doesn't include this endpoint.
   - `--max-enrich` caps lookups per call for both.
   - To add a provider (Hunter, FullEnrich, ...), write a class with `name` and `enrich()` and
     register it in `make_enricher`.
3. **Merge. Call data wins.** An email from the invite or a title stated on the call is kept.
   Enrichment fills only what's missing. A different value (an email at another domain, which
   often means a job change) goes in `enrichment.conflicts` and `notes`.

Output: `person.enrichment = {status, provider, is_mock, domain, resolved{...}, sources{...}, conflicts, raw}`.

## Step 3b: HubSpot sync

For each in-scope person:
1. Find the company by domain, or create it.
2. Find the contact by email (or first + last name), or create it.
3. Fill **only empty** properties. Existing HubSpot values are never overwritten.
4. Associate the contact with the company.
5. Find the deal through **HubSpot associations** (existing contacts' deals plus the company's
   deals). Prefer the most recently updated open deal, then closed-won. **Deals are never
   created.** With no deal, labeling is skipped.
6. Make sure the contact-to-deal association labels exist (Decision Maker / Champion / Blocker;
   missing ones are created), then associate each contact with the deal using its role labels.

Contacts filled from enrichment get `enrichment_source` = `mock (demo data)` or `apollo`, so fake
demo data is easy to spot. LinkedIn goes in `hs_linkedin_url` if the portal has it, otherwise in
a custom `linkedin_url` property (created on first write). Results: `person.crm` and `stats.hubspot_sync`.

### HubSpot setup

Use a free developer test portal. Go to **Development → Service keys**, create a key, and put it
in `.env` as `HUBSPOT_TOKEN`. Scopes:

| Scope | Why |
|---|---|
| `crm.objects.contacts.read` / `.write` | find, create and fill contacts |
| `crm.objects.companies.read` / `.write` | find or create the company, link contacts |
| `crm.objects.deals.read` / `.write` | find deals through associations, add labeled associations |
| `crm.schemas.contacts.read` / `.write` | custom properties and contact-to-deal labels |
| `crm.schemas.companies.read`, `crm.schemas.deals.read` / `.write` | association label definitions |

A 403 from HubSpot names the missing scope. Add it to the key and run `resume` again.

### Demo portal

```bash
python scripts/seed_demo_portal.py            # Acme Corp + matthew.collins@acmecorp.com (email only) + "Test Deal"
python scripts/seed_demo_portal.py --reset    # archive the demo records first (recycle bin, 90 days), then seed
python scripts/seed_demo_portal.py --clear    # archive the demo records only
python run.py resume output/<run>/latest.json --hubspot          # dry run: shows the plan
python run.py resume output/<run>/latest.json --hubspot --write  # apply it
```

The seed script is separate from the pipeline (the pipeline never creates deals). A dry run
leaves `next_stage = hubspot_sync`, so `--write` can follow it straight away. Running the sync
again changes nothing: matching contacts are found and only empty fields are filled.

### TXT format

The header is optional. Everything above `---` is the header.

```
Title: Acme Corp - Discovery Call
Date: 2026-11-03
Internal domains: northwind.io
Attendees:
- Priya Raman <priya.raman@northwind.io>
- Matthew Collins <matthew.collins@acmecorp.com> (external)
---
[00:00:04] Priya Raman: Hi everyone...
Matt: Thanks Priya.
```

Lines can be `[HH:MM:SS] Name: text`, `MM:SS Name: text` or just `Name: text`. Lines that
don't start with a speaker are joined onto the line above.

## How name resolution works

1. **LLM pass** (OpenRouter, Claude Sonnet 5.5 by default, strict JSON schema). The model
   merges every reference to one person: nicknames (Matt = Matthew, Bill = William), first
   name only, surname only, speaker labels like `Speaker 2` or `Acme Conf Room B`, and
   transcription typos. It keeps people apart when it isn't sure (two different Sams) and
   explains why.
2. **Deterministic pass** (`resolver.py`), so the CRM doesn't rely on the model alone:
   - merges leftover duplicates using a nickname table (`nicknames.py`) plus last-name and
     company checks. A first name with no last name is merged only when exactly one person
     matches.
   - attaches attendee emails and adds attendees the model missed (as `attended_silent`).
   - **drops any email the model made up**. Emails only come from source metadata.
   - corrects internal/external from email domains.
   - sets `needs_review` with reasons (no last name, inferred title, conflicting titles,
     low confidence, ambiguous reference, and so on).

You can add your own nickname groups in `people_agent/nicknames_extra.txt`, one
comma-separated group per line.

## The handoff contract (`HandoffEnvelope`, schema v1.1)

v1.1 adds `transcript` (the normalized call, so later steps and re-runs can read it) and
`people[].buying_roles: [{role, confidence, rationale, evidence}]`. Files saved as v1.0 don't
include the transcript, so run step 1 again on those calls.

Written to `output/<run_id>_<title>/00_people_extraction.json` and `latest.json`:

```jsonc
{
  "schema_version": "1.1",
  "run_id": "a1b2c3d4e5f6",
  "current_stage": "buying_role_classification",
  "next_stage": "contact_enrichment",
  "source": {"type": "fathom", "id": "4242", "title": "...", "date": "...", "url": "..."},
  "company_context": {"internal_company": "Northwind", "external_companies": ["Acme Corp"]},
  "people": [{
    "person_id": "p_3f9a1c0b2d",
    "full_name": "Mark Davis", "first_name": "Mark", "last_name": "Davis",
    "aliases": ["Mark"],
    "title": "VP of Sales", "title_source": "stated_by_other",
    "company": "Acme Corp", "email": null,
    "side": "external", "presence": "mentioned_only", "in_attendee_list": false,
    "buying_role": "decision_maker",
    "buying_roles": [{"role": "decision_maker", "confidence": 0.95,
                      "rationale": "Named by his report as the final approver.",
                      "evidence": [{"timestamp": "00:01:05", "speaker": "Matt", "quote": "He'll make the final call, yeah."}]}],
    "relationships": ["Matthew Collins's manager", "signs anything over $50K"],
    "evidence": [{"timestamp": "00:00:47", "speaker": "Matt", "quote": "My boss, Mark, he's our VP of Sales"}],
    "confidence": 0.9, "needs_review": false, "review_reasons": [],
    "enrichment": {}, "crm": {}          // empty slots for the next agents
  }],
  "unnamed_roles": [{"description": "CFO at Acme, approves budget", "title": "CFO", "company": "Acme Corp", "side": "external", "evidence": [...]}],
  "stats": {"total_people": 11, "mentioned_only": 5, "not_on_attendee_list": 6, "needs_review": 3, "unmapped_speakers": [], "usage": {...}},
  "history": [{"stage": "people_extraction", "status": "ok", "started_at": "...", "finished_at": "..."}]
}
```

`unnamed_roles` lists roles that came up without a name ("the CFO"), so enrichment can look them up.

## Adding a stage

Subclass `Stage`, then add it to `STAGE_ORDER` / `build_stages` in `run.py`:

```python
from people_agent import Stage, HandoffEnvelope

class SlackDigestStage(Stage):
    name = "slack_digest"
    next_stage = None

    def run(self, env: HandoffEnvelope) -> HandoffEnvelope:
        ...  # read env.people[*].buying_roles / enrichment / crm
        return env

pipeline = Pipeline(agent, stages=[BuyingRoleStage(llm), ContactEnrichmentStage(MockEnricher()),
                                   HubSpotSyncStage(HubSpotClient(), write=False)])
```

`Pipeline(None, stages=[...]).resume(load_envelope(path))` runs stages on a saved file.

After each stage the pipeline writes `NN_<stage>.json` and records the stage in `history`.
A stage only runs when the envelope's `next_stage` matches its `name`. If a stage fails,
the error goes into `history` and the file is still saved. To run a stage on its own later,
load a saved file with `load_envelope(path)`.

## Tests

```bash
python -m pytest -q
```

The tests run offline. Fathom and the LLM are faked, so no API keys are needed.

## Layout

```
run.py                     CLI
people_agent/
  sources/txt_source.py    .txt parser
  sources/fathom_source.py Fathom API (/meetings, /recordings/{id}/transcript)
  prompts.py               system prompt + strict JSON schema
  llm.py                   OpenRouter client
  extractor.py             PeopleExtractionAgent
  resolver.py              deterministic merge / attendee matching / review flags
  roles.py                 step 2: role rubric, prompt, BuyingRoleStage, quote verification
  enrich.py                step 3a: anchor domain, Enricher providers (mock, apollo), merge
  hubspot.py               step 3b: HubSpotClient + HubSpotSyncStage
  nicknames.py             nickname equivalence table
  pipeline.py              Stage, Pipeline, handoff files
  models.py                Transcript, Person, HandoffEnvelope
samples/                   demo transcripts
tests/                     offline tests
```
