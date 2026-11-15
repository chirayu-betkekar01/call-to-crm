# Agent 2: Buying-Role Classifier

**Input:** Agent 1's handoff file (roster and transcript). **Output:**
`NN_buying_role_classification.json`, with `person.buying_roles` set for buyer-side people.

Job titles mislead: a VP can sit silent while an analyst runs the room. This agent labels people
by **how they behave on the call**, and uses title only as supporting evidence.

| Role | Signals |
|---|---|
| `decision_maker` | others defer to them; talks about approvals, budget, timelines; named as the approver ("he'll make the final call"). **Can be absent from the call.** |
| `champion` | moves the deal forward: brings colleagues in, sells internally, owns next steps. Usually invited, but not always. |
| `blocker` | ongoing skepticism or objections; others warn they hold things up |
| `power_user` (off by default) | hands-on user asking in-the-weeds questions |

Rules: **buyer side only** (the seller's team and third parties like a competitor's rep are never
scored). **Not everyone gets a role, and one person can have several.**

## Code checks the model's answer

The model proposes roles. `agent.py` keeps a role only if it passes all of these:
- **Real buyer-side person:** the person id must be on the buyer roster. Internal people and
  made-up ids are dropped.
- **Verbatim evidence:** each quote must appear in the transcript, ignoring case and punctuation,
  with `...` joining fragments. A role with no matching quote is discarded.
- **Confidence threshold:** roles below `--min-confidence` (default 0.6) are dropped and logged
  in `notes` for a human.
- **Conflicts flagged:** Champion + Blocker on the same person sets `needs_review`.

## Run

```bash
python -m agents.role_classifier output/<run>/00_people_extraction.json
python -m agents.role_classifier output/<run>/00_people_extraction.json --roles decision_maker,champion,blocker,power_user
python -m agents.role_classifier output/<run>/00_people_extraction.json --dry-run   # print the prompt
```

Needs `OPENROUTER_API_KEY`. `BUYING_ROLES` and `ROLE_MIN_CONFIDENCE` in `.env` set defaults.

## Customizing the rubric

The roles and their signals are business configuration in `rubric.py` (`ROLE_LIBRARY`). Edit a
definition or add a `RoleDef`, then turn it on with `--roles`. The prompt and the output schema
are generated from the rubric.

## Files

| File | What it does |
|---|---|
| `rubric.py` | role definitions, signals and "not this role" guidance |
| `agent.py` | prompt, strict schema, `BuyingRoleStage`, quote verification |
| `cli.py` | `python -m agents.role_classifier` |
