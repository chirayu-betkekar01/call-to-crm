# Agent 1: Attendee Reconciler

**Input:** a call transcript (Fathom API or `.txt`). **Output:** `00_people_extraction.json`, the
full list of people involved in the call.

You invite one person, five join, and someone who isn't on the call makes the decision. This
agent rebuilds who was really involved:

- **speakers**, including people who joined from a conference room or a colleague's laptop and
  aren't on the invite
- **silent attendees** who were on the invite but never spoke
- **people who were only mentioned** ("my boss Mark, our VP of Sales, signs anything over 50K")
- **unnamed roles** ("the CFO has to bless the budget"), kept so a later step can look them up

It drops bots, recorders, conference-room devices and fictional or hypothetical names. It
merges nicknames and duplicates (Matt = Matthew, Sam ≠ Sam when there are two of them), matches
invitee emails, and tags each person internal (seller) or external (buyer).

## Run

```bash
python -m agents.attendee_reconciler txt samples/acme_discovery_call.txt
python -m agents.attendee_reconciler fathom --recording-id 123456789
python -m agents.attendee_reconciler fathom --since 2026-11-01 --until-date 2026-11-08 --limit 5
python -m agents.attendee_reconciler txt samples/ --dry-run      # print what the model sees, no API call
```

Options: `--internal-domains northwind.io` (your company's email domains), `--model`,
`--include-internal` (Fathom: also internal-only meetings), `--recorded-by rep@you.com`.
Needs `OPENROUTER_API_KEY`, plus `FATHOM_API_KEY` for Fathom.

## How name resolution works

1. **LLM pass** (OpenRouter, Claude Sonnet 5.5 by default, strict JSON schema in `prompts.py`).
   The model merges every reference to one person: nicknames, first name only, surname only,
   speaker labels like `Speaker 2` or `Acme Conf Room B`, and transcription typos. It keeps people
   apart when it isn't sure, and explains why.
2. **Deterministic pass** (`resolver.py`), so the CRM doesn't depend on the model alone:
   - merges leftover duplicates using a nickname table (`nicknames.py`) plus last-name and company
     checks. A first name with no last name is merged only when exactly one person matches.
   - attaches invitee emails and adds invitees the model missed (as `attended_silent`)
   - **drops any email the model made up**. Emails only come from source metadata.
   - corrects internal/external from email domains
   - sets `needs_review` with reasons (no last name, inferred title, conflicting titles, low
     confidence, ambiguous reference, ...)

Add your own nickname groups in `agents/attendee_reconciler/nicknames_extra.txt`, one
comma-separated group per line.

## TXT format

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

Lines can be `[HH:MM:SS] Name: text`, `MM:SS Name: text` or just `Name: text`. Lines that don't
start with a speaker are joined onto the line above.

## Files

| File | What it does |
|---|---|
| `agent.py` | `AttendeeReconcilerAgent`: transcript → LLM → resolver → envelope |
| `prompts.py` | system prompt and strict output schema |
| `resolver.py` | deterministic merge, attendee matching, invented-email removal, review flags |
| `nicknames.py` | nickname equivalence table |
| `sources/` | `TxtSource` and `FathomSource` (`/meetings`, `/recordings/{id}/transcript`) |
| `cli.py` | `python -m agents.attendee_reconciler` |
