# Loom Demo Plan (3–4 min, nothing runs live)

Everything was run ahead of time (2026-11-15): all three agents ran on the Acme sample call and
the result was written to the HubSpot test portal. The Loom walks through the saved results one
agent at a time, so there's no waiting, no API calls and nothing can break on camera.

## What's ready

**Saved run:** `output/demo_acme/` (gitignored, local only). One file per step, each the handoff
envelope after that agent:

| File | State after |
|---|---|
| `1_agent1_people.json` | Agent 1: the reconciled roster |
| `2_agent2_roles.json` | Agent 2: + buying roles |
| `3_agent3_enriched.json` | Agent 3A: + contact details (mock data) |
| `4_agent3_hubspot_plan.json` | Agent 3B dry run: what it *would* write, including "would create" labels |
| `5_agent3_hubspot_written.json` | Agent 3B write: what it did |

`python run.py show <file>` prints any of them instantly (no API calls).

**HubSpot (test portal):** Test Deal has 7 Acme contacts. Matthew is the Champion (email unchanged,
gaps filled), Mark the Decision Maker, Elizabeth the Blocker, and the other four are linked without
a label. All are marked `Enrichment Source = mock (demo data)`. Still exactly one deal.

## Screen setup

- VS Code: `samples/acme_discovery_call.txt` open; terminal wide (~160 cols), font ~16.
- Browser tab 1: HubSpot **Test Deal**. Tab 2: **Matthew's contact**. Tab 3 (optional): the GitHub repo.

## Script

**0:00 – The problem (25 s).** No HubSpot "before" state exists any more, so tell it from the call:
> "Reps take calls with several stakeholders, but the CRM only knows who was invited. On this call,
> one person was invited, five people were involved, and the person who signs never joined."

**0:25 – The transcript (30 s).** Scroll `acme_discovery_call.txt` and point at:
- line 15: Jordan joins "from the conference room" (not invited)
- line 19: *"He'll make the final call… He signs anything over fifty K"* (Mark, not on the call)
- line 26: *"Liz has been known to hold things up for a month"*

**0:55 – Agent 1 (30 s).**
```bash
python run.py show output/demo_acme/1_agent1_people.json
```
11 people found: Jordan has `LIST no` (uninvited), and Mark, Liz and Bill are `mentioned_only`. The
seller's team is tagged internal, and Tom (the competitor's rep) is tagged Clarivue.
> "It merges nicknames, drops bots and made-up names, and never invents an email."

**1:25 – Agent 2 (40 s).**
```bash
python run.py show output/demo_acme/2_agent2_roles.json
```
Read Mark's line aloud: Decision Maker 0.95, *"Absent VP explicitly named by his direct report as the
final approver."* Then Matthew as Champion and Liz as Blocker.
> "It judges behavior, not titles. Every role needs a quote that's really in the transcript, and code
> checks that, so the model can't invent a reason. It only scores the buyer side."

**2:05 – Agent 3 (40 s).**
```bash
python run.py show output/demo_acme/4_agent3_hubspot_plan.json
```
Point at the `ENRICH` lines (filled fields, `[MOCK DATA]`), then the last lines: **DRY_RUN**, 6 would be
created, 1 updated, "would create Decision Maker / Champion / Blocker".
> "By default it only plans. Nothing touches the CRM until you add --write."

Optionally flash `5_agent3_hubspot_written.json` to show `created` / `updated`.

**2:45 – HubSpot (50 s).** Test Deal tab:
- 7 contacts with the Champion / Decision Maker / Blocker labels on the deal associations
- Matthew's record: title, phones and LinkedIn filled in, **email unchanged**
- `Enrichment Source` = "mock (demo data)"
> "It never overwrites existing data and never creates deals. It found this one through Matthew."

**3:35 – Wrap up (25 s).** Three independent agents passing one handoff file · swappable enrichment
(mock, plus a working Hunter integration) · dry run by default · re-runs change nothing. Repo link
on screen.

## If you need to redo it

```bash
python scripts/seed_demo_portal.py --reset                            # back to: Matthew (email only) + Test Deal
python run.py txt samples/acme_discovery_call.txt --hubspot           # fresh run, ~50 s, dry run
python run.py resume output/<new-run>/latest.json --hubspot --write   # apply
```
To show labels being created, delete them first (HubSpot Settings → Objects → Deals → Associations).

## After recording

Send the Loom link. It goes into the call-to-crm README and the portfolio card
(`#p-calltocrm`, "Watch walkthrough" button).
