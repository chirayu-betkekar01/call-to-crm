# Loom Demo Plan (3–4 min)

Goal: show the full pipeline live, from a transcript to a labeled buying group in HubSpot.

## Before recording (~5 min)

1. Reset the portal to the starting point:
   ```bash
   python scripts/seed_demo_portal.py          # Acme Corp + matthew.collins@acmecorp.com (email only) + "Test Deal"
   ```
   Wait about 10 s for HubSpot search to pick up the new records.
2. Do one practice run with the commands below. Steps 1–2 use the LLM, so wording and confidence
   scores vary a little between runs. Then reset:
   ```bash
   python scripts/seed_demo_portal.py --reset   # archive demo records (recycle bin, 90 days) + re-seed
   ```
3. Screen setup:
   - VS Code, terminal enlarged (font ~16), `samples/acme_discovery_call.txt` open in a tab
   - Browser tab 1: HubSpot **Test Deal**. Tab 2: **Matthew's contact**
4. Optional: delete the Decision Maker / Champion / Blocker labels (Settings → Objects → Deals →
   Associations) so the run visibly creates them.

## Script

**0:00 – The problem (30 s).** Show Test Deal: one contact, Matthew, email only.
> "Five people were on this call, and a VP who wasn't on it makes the final decision. The CRM knows about one person, and only his email."

**0:30 – The transcript (30 s).** Scroll `acme_discovery_call.txt` and point at:
- Jordan joining from the conference room (not invited)
- *"He'll make the final call… He signs anything over fifty K"* (Mark, not on the call)
- *"Liz has been known to hold things up for a month"*

**1:00 – Run it (60 s).** Dry run. It takes about a minute for the LLM calls, so pause Loom while it runs.
```bash
python run.py txt samples/acme_discovery_call.txt --hubspot
```
Call out:
- everyone was found, including uninvited and mentioned-only people
- roles come with a rationale and a transcript quote ("quotes are checked against the transcript in code, so the model can't invent a reason")
- the seller's team and the competitor's rep (Tom, Clarivue) are excluded
- the last line says **DRY_RUN**: it shows what it would do, and nothing has been written

**2:00 – Apply it (20 s).** Copy the run folder from the `handoff ->` line:
```bash
python run.py resume output/<run>/latest.json --hubspot --write
```

**2:20 – The result in HubSpot (60 s).** Refresh Test Deal:
- 7 contacts, with Champion / Decision Maker / Blocker labels on the deal associations
- Matthew: title, phone and LinkedIn filled in, **email unchanged**
- `Enrichment Source` = "mock (demo data)"
> "It never overwrites existing data and never creates deals. It found this one through Matthew's existing association."

**3:20 – Wrap up (30 s).** 4 stages passing a saved handoff file (any stage can be re-run) ·
swappable providers (mock + a working Hunter integration) · dry run by default, and re-runs
change nothing · next step: run on every new Fathom call.

## Tips

- Only one take of `--write`: run `python scripts/seed_demo_portal.py --reset` to start over.
- Make the terminal wide enough that the summary table doesn't wrap.
- To skip the LLM wait, record only the `resume` steps on a run saved during practice. You'd
  lose the most impressive part on camera, though.
- `python scripts/seed_demo_portal.py --clear` empties the portal when you're done.

## Open ideas

- A one-page visual walkthrough (architecture diagram plus HubSpot before/after) for the end of
  the Loom or the description link. Offered, not built yet.

## Portal state (2026-11-14)

The demo records were cleared with `--clear`. The three contact→deal labels and the
`enrichment_source` property still exist in the portal. Run `python scripts/seed_demo_portal.py`
before recording.
