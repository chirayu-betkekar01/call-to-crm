# Pipeline Spec: Buying-Role Tagging for HubSpot

Taken from a build walkthrough video (three Claude agents built with Claude Code: Fathom → HubSpot).
This file records what that system does and compares it with what this repo has already built.

## The problem

Reps take calls with several stakeholders, but only the person they invited ends up in the CRM.
People who join uninvited (from the conference room, or added at the last minute) never get
logged. That often includes the real decision maker. Nobody labels buying roles by hand because
it's tedious, and job titles are a poor guide anyway: a VP can stay silent while an analyst runs
the meeting. Once a call is over, anyone the CRM didn't capture is lost, unless you rebuild the
list from the transcript. That's what this pipeline does.

```
Fathom transcript ──► Agent 1: Attendee Reconciliation ──► Agent 2: Buying-Role Classifier ──► Agent 3: Enrichment + HubSpot Sync
```

Any recorder with an API for transcripts works as the source (Fathom, Granola, Gong). Fathom
is free and its free plan includes API access.

---

## Agent 1: Attendee Reconciliation

**Goal:** work out exactly who was on the call.

Fathom gives three sets of people that overlap but don't match:
1. **Invitees** (from the calendar)
2. **Speakers** (labels in the transcript)
3. **People mentioned but absent** (named in the conversation, never invited, never spoke)

Requirements:
- Filter out non-people: notetaker bots, call recorders, conference-room devices, product names
  picked up as speakers.
- Filter out fictional and hypothetical names. In the demo call, people called their ChatGPT
  assistant "Andy", used "Bob" as a hypothetical employee and joked about "Tony Stark". All
  three were correctly dropped.
- Merge duplicates and name variants (Sam = Samuel, Matt = Matthew).
- Pull real names and emails from the call itself where possible.
- Flag **"spoke but not invited"** people. These are the main thing the pipeline is looking for.
- Approach: heuristic pass, then LLM reconciliation, then merge (LLM roster cross-checked against
  the heuristics) with actionable flags.
- V1 input: one meeting, looked up by ID.
- A cheaper model may be enough for this stage.

Known follow-ups from the video:
- Mark items the LLM caught as non-people correctly (they were defaulting to `source: speaker`).
- Fathom `get meetings` paging: recent meetings are on page 1. Old meetings mean paging through
  many pages full of transcripts. Optimize only if that becomes a real use case.

## Agent 2: Buying-Role Classifier

**Goal:** give each **buyer-side** person a buying role based on **how they behave on the
call**. Title is a supporting signal only.

Inputs: the Fathom payload (invitees and attendees) plus Agent 1's reconciled roster.

| Role | Signals |
|---|---|
| **Decision Maker** | Says little; others defer to them and ask what they think; talks about approvals and timelines. Title counts as a signal (a CEO is likely a DM) but doesn't decide it. |
| **Champion** | Almost always both an invitee and an attendee, since they're the one moving the deal forward. Gets teammates to take part. Is clearly selling the deal internally. |
| **Blocker** | Asks hard or skeptical questions; seems unenthusiastic about the opportunity. |

(The narration also mentions **Advocate** and **Power User**: the person asking detailed,
in-the-weeds questions or the hands-on owner of the tool.)

Rules:
- **Buyer side only.** Never score internal or seller-side people.
- **Not everyone gets a role.** Don't force one. In the demo, no blocker was assigned.
- **One person can hold several roles** (for example Champion and Decision Maker).
- Give a confidence score and evidence for each role.
- The rubric depends on the business. Treat it as configuration.
- Scope used in the demo: buyer side only, for stakeholder/kickoff calls.

Demo result: Barry was labeled Champion and Decision Maker. Erica was labeled Champion (the
hands-on HubSpot owner). Erica was **not invited** and wasn't in the CRM, but she spoke and
asked questions.

## Agent 3: Enrichment + HubSpot Sync

**Goal:** fill in missing contact details, create missing contacts, and write buying roles to HubSpot.

Company inference (runs before any enrichment):
- **Anchor domain** = the email domain of an external invitee. Calls almost never include
  people from several outside companies, so someone known only by first name is assumed to
  work at that company.

Flow for each person:
1. Resolve their company from the anchor domain.
2. Look them up in HubSpot.
3. Find what's missing: first name, last name, title, LinkedIn URL, mobile phone, office phone, email.
4. Fill those gaps from **FullEnrich**. Even a first name plus company is enough to search.
5. Upsert into HubSpot. Create the contact if it doesn't exist. **Don't overwrite existing
   values** (keep the email already in HubSpot even if enrichment returns a different one).
6. Associate the contact with the company and the deal, and add the buying-role labels.

Deal handling:
- **Check HubSpot directly** for the contact's deal associations. Don't rely only on Fathom's
  CRM match data. (In the demo, the dry run missed an existing "Test Deal" because it trusted
  Fathom alone.)
- **Never create deals.** Not every call has one, and there's no filter yet to keep internal
  calls out of the pipeline. If no deal is found, skip labeling.

Labels:
- Buying-role labels go on **contact→deal** associations, not contact→company.
- HubSpot already has native Decision Maker / Champion / Blocker labels on contact→deal. Create
  any that are missing automatically (the demo portal was missing some, and the agent created them).

Run mode:
- Do a **dry run first** on any live portal. The demo skipped it only because it ran on a test portal.

### HubSpot service key scopes

Set up under Development → Service Keys (this replaces legacy private apps).

| Scope | Why |
|---|---|
| `crm.objects.contacts.read` / `.write` | find and create contacts |
| `crm.schemas.contacts.read` / `.write` | contact association labels |
| `crm.objects.deals.read` / `.write` | read deal associations, write labeled associations |
| `crm.objects.companies.read` / `.write` | company lookup and contact→company association |
| `crm.schemas.companies.read` | added after the first live run failed |
| (likely) `crm.schemas.deals.read` / `.write` | defining contact→deal labels |

The first live run failed because of missing deals and companies scopes. Start with all of the above.

### Env vars introduced

`ANTHROPIC_API_KEY` (or the LLM key you use), `FATHOM_API_KEY`, `HUBSPOT_TOKEN`, `FULLENRICH_API_KEY`.
Gotcha from the video: a leading `#` left in `.env` commented out the HubSpot token.

### Demo end-to-end outcome

Before the run, the test deal had one contact with only an email. After the run: 2 new
contacts were created (fresh email, title, mobile, LinkedIn); the existing contact gained a
title, mobile and LinkedIn while keeping its email; and association labels were applied on the
deal. One enrichment email came back at a different domain, probably because the person had
recently changed jobs. Also: FullEnrich ran out of credits partway through and had to be topped up.

---

## Operations and roadmap

- **Backfill:** because everything runs from raw transcripts, you can point the pipeline at old
  calls. The video ran 300+ transcripts in under 15 minutes and mapped every buying group back to day one.
- **Ongoing:** run on every new call. Roles change over time: new decision makers appear and
  power users become visible.
- Triggers to consider: batch ingestion, or watching Google Calendar or Fathom for new meetings.
- Hosting: push to GitHub and deploy on an always-on service (for example Railway) so it
  doesn't depend on a laptop being on.
- Habit: when you reach a milestone, update the README and architecture docs so new sessions
  can get up to speed quickly.
- Never paste API keys into the chat. Put them in `.env` yourself.

---

## Comparison with this repo

| Area | Video | This repo today |
|---|---|---|
| Language | TypeScript / Node | Python |
| LLM access | Anthropic API directly | OpenRouter (`anthropic/claude-sonnet-5.5`) |
| Agent 1 | Built | **Built.** Nickname merging, bot and speaker-label handling, attendee matching, mentioned-only people, invented emails dropped, `needs_review` flags, fictional people excluded (`prompts.py:21`). Also does batch Fathom ingestion (`--since`, `--limit`). |
| Agent 2 | Separate behavioral classifier, buyer side only, several roles per person | **Built** (`people_agent/roles.py`, `BuyingRoleStage`). The rubric can be configured, evidence quotes are checked against the transcript, and roles below a confidence threshold are dropped. Re-run on a saved file with `python run.py roles <file>`. |
| Agent 3 | FullEnrich + HubSpot upsert + deal labels | **Built.** `enrich.py` has pluggable providers: a free mock (the default, since this is a portfolio project) and Apollo, replacing FullEnrich. `hubspot.py` does the sync: dry run by default, never overwrites values, never creates deals, finds deals through HubSpot associations, and creates missing labels. Not yet run against a live HubSpot portal. |
| Handoff | JSON from one agent to the next | `HandoffEnvelope` v1.0 with one `NN_<stage>.json` per stage |

Suggested next steps:
All three stages are built. Remaining: a live test against a HubSpot test portal, a live Apollo
test, batch backfill, and hosting (GitHub + Railway).

Original plan, for reference:
1. ~~`BuyingRoleStage`~~ done.
2. ~~`ContactEnrichmentStage`~~ done (mock + Apollo instead of FullEnrich). Anchor-domain inference, then FullEnrich, filling only missing fields.
3. ~~`HubSpotSyncStage`~~ done. Find or create contacts, find deals through HubSpot associations (never
   create deals), make sure labels exist, apply contact→deal labels. Dry run by default.
