# Agent 3: Enrichment + HubSpot Sync

**Input:** Agent 2's handoff file. **Output:** enriched contacts, plus the deal in HubSpot labeled
with buying roles.

A perfectly labeled list is useless if half the people on it are just a first name. This agent
fills in the gaps and writes everything to HubSpot. It runs in two steps.

## Step A: contact enrichment (`enrich.py`)

1. **Anchor domain.** The buying company's domain is the email domain of the outside invitees.
   Buyer-side people without an email (mentioned only, first name only) are assumed to work there.
   People at other companies (a competitor's rep who gets mentioned) and the seller's team are
   `out_of_scope`. They aren't enriched or synced.
2. **Provider lookup** (`providers/`). Every provider implements `enrich(EnrichQuery) -> EnrichResult | None`:

   | Provider | Cost | Notes |
   |---|---|---|
   | `mock` (default) | free, offline | realistic but fake title, email, LinkedIn and phones (fictional `555-01XX` numbers), the same every run, flagged `is_mock`. For demos. |
   | `hunter` | free plan (50 credits/month, misses are free) | Email Finder returns email, title, LinkedIn and sometimes a phone number. Needs a last name. `HUNTER_API_KEY` |
   | `apollo` | paid Apollo plan | People Match. `APOLLO_API_KEY` |

   `--max-enrich` caps lookups per call. To add a provider, write a class with `name` + `enrich()`
   and register it in `providers/__init__.py`.
3. **Merge. Call data wins.** An email from the invite or a title stated on the call is kept.
   Enrichment fills only what's missing. A different value (an email at another domain often means
   a job change) is logged in `enrichment.conflicts` and `notes`.

## Step B: HubSpot sync (`hubspot.py`)

**Dry run by default.** It reads HubSpot and records what it *would* write. `--write` applies it.

For each in-scope person:
1. Find the company by domain, or create it.
2. Find the contact by email (or first + last name), or create it.
3. Fill **only empty** properties. Existing HubSpot values are never overwritten.
4. Associate the contact with the company.
5. Find the deal through **HubSpot associations** (existing contacts' deals plus the company's
   deals). Prefer the most recently updated open deal, then closed-won. **Deals are never
   created.** With no deal, labeling is skipped.
6. Make sure the contact-to-deal labels exist (Decision Maker / Champion / Blocker; missing ones
   are created), then associate each contact with the deal using its role labels.

Contacts filled from enrichment get `enrichment_source` = `mock (demo data)`, `hunter` or
`apollo`, so fake demo data is easy to spot. LinkedIn goes in `hs_linkedin_url` if the portal has
it, otherwise in a custom `linkedin_url` property. A dry run leaves `next_stage = hubspot_sync`, so
`--write` can follow it. Running the sync again changes nothing.

## Run

```bash
python -m agents.enrichment_sync output/<run>/latest.json                  # enrich + HubSpot dry run
python -m agents.enrichment_sync output/<run>/latest.json --write          # enrich + HubSpot, apply
python -m agents.enrichment_sync output/<run>/latest.json --enrich-only --enricher hunter
python -m agents.enrichment_sync output/<run>/latest.json --sync-only --write --deal-id 123
```

## HubSpot setup

Use a free test portal. Go to **Development → Service keys** (or **Settings → Integrations →
Private Apps**), create a key, and put it in `.env` as `HUBSPOT_TOKEN`. Scopes:

| Scope | Why |
|---|---|
| `crm.objects.contacts.read` / `.write` | find, create and fill contacts |
| `crm.objects.companies.read` / `.write` | find or create the company, link contacts |
| `crm.objects.deals.read` / `.write` | find deals through associations, add labeled associations |
| `crm.schemas.contacts.read` / `.write` | custom properties and contact-to-deal labels |
| `crm.schemas.companies.read`, `crm.schemas.deals.read` / `.write` | association label definitions |

A 403 from HubSpot names the missing scope.

Gotcha: creating an association label returns **both** directions (contact→deal and
deal→contact). The sync re-reads the contact→deal definitions instead of trusting the order of the
create response.

## Demo portal

```bash
python scripts/seed_demo_portal.py           # Acme Corp + matthew.collins@acmecorp.com (email only) + "Test Deal"
python scripts/seed_demo_portal.py --reset   # archive demo records (recycle bin, 90 days), then seed again
python scripts/seed_demo_portal.py --clear   # archive demo records only
```

The seed script is separate from the agent. The agent never creates deals.

## Files

| File | What it does |
|---|---|
| `enrich.py` | anchor domain, scope, merge rules, `ContactEnrichmentStage` |
| `providers/` | `mock.py`, `hunter.py`, `apollo.py`, shared `base.py` interface |
| `hubspot.py` | `HubSpotClient` (thin REST client) + `HubSpotSyncStage` |
| `cli.py` | `python -m agents.enrichment_sync` |
