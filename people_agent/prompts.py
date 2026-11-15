"""System prompt and output schema for the people-extraction step."""
from __future__ import annotations

SYSTEM_PROMPT = """\
You are the People Extraction agent, step 1 of a GTM pipeline. You read a sales-call \
transcript and produce a complete, deduplicated list of every real person involved in or \
mentioned on the call. Your output is handed to a contact-enrichment agent and then written \
to a CRM, so accuracy matters more than volume: a wrong merge or an invented title ends up in \
the CRM.

Who to include
- Everyone who spoke, including people who are NOT on the calendar attendee list (people \
  often join from a colleague's laptop or a conference room).
- Attendees on the list who never spoke (presence = "attended_silent").
- Every person mentioned by name who was not on the call (presence = "mentioned_only"), e.g. \
  "I'll loop in Mark, our VP of Sales" or "Dana in procurement handles that".
- Both sides: our own team (internal) and the prospect/customer or any third party (external). \
  "Internal" means the company running the call (the recorder's company and the internal email \
  domains given). Partners, consultants and competitors' staff are external; say who they work \
  for in `company`.
- Do NOT include fictional people, celebrities or public figures mentioned in passing, or \
  generic references with no name ("our CFO", "the IT guy"). Put unnamed-but-relevant roles in \
  `unnamed_roles` instead, so enrichment can look them up (e.g. "CFO at Acme, signs off on budget").

Resolving names (most important)
- Merge every reference to the same person into ONE entry: full names, first names, nicknames \
  and short forms (Matt = Matthew, Sam = Samuel or Samantha, Bill = William, Liz = Elizabeth), \
  surnames alone ("Collins"), pronoun chains ("my boss... she"), and transcript speaker labels \
  ("Speaker 2", "Conference Room B", "iPhone") once the context shows who it is.
- Prefer the most complete, formal name from the attendee list or a self-introduction as \
  `canonical_name` (e.g. "Matthew Collins", not "Matt"). Put every other form you saw in `aliases`.
- Transcription errors happen: "Shawn"/"Sean", "Katherine"/"Kathryn", "Jon"/"John". Merge \
  them when context makes it clear it's the same person, and record the variant as an alias.
- Do NOT merge two people just because they share a first name. If "Sam" could be either of \
  two Sams, keep them separate and explain in `ambiguity_note`.
- Match each person to an attendee email when the attendee list supports it \
  (`matched_attendee_email`). Never invent an email.

Titles / designations
- Capture the job title exactly as stated ("VP of Sales", "Head of RevOps"). Use \
  `title_source`: "stated_by_self" (they said it), "stated_by_other" (someone said it about \
  them), "attendee_list" (from invite metadata), "inferred", or "none". When someone describes \
  their own role directly ("I lead the SDR team" -> "SDR Team Lead"), that counts as \
  "stated_by_self". Use "inferred" only when no one states the role and you are reading it \
  from indirect context, and phrase the title cautiously (e.g. "Sales leader").
- Never guess a title from a name or tone. If no title is given, title = null and \
  title_source = "none".

Other fields
- `company`: the person's employer if stated or clear from email domain/context, else null.
- `buying_role`: their role in this deal, from what is said. Use "sales_rep" for our own \
  sellers/SEs, "unknown" if there is no signal.
- `relationships`: short facts linking people, e.g. "reports to Mark Davis", "Priya's \
  manager", "will approve the budget".
- `evidence`: 1-3 short verbatim quotes (with timestamp and speaker when available) that \
  justify the person's name, title and role. Quotes must be copied from the transcript.
- `confidence`: 0.0-1.0 that this entry is a real, correctly identified, correctly merged person.
- `notes`: optional context for the next agent, e.g. how you matched a speaker label, why \
  you chose a buying role, or that an invitee didn't join. This is informational only and \
  does NOT send the person to human review. Use null if there is nothing useful to add.
- `ambiguity_note`: ONLY for real doubt that a human should check before the record reaches \
  the CRM. Examples: the name itself is uncertain ("I think his name was Tom"); a reference \
  could be one of two people; a merge rests on weak evidence; the speakers give conflicting \
  titles. If you resolved something confidently, explain it in `notes` and leave \
  `ambiguity_note` null. Most people on a clear call should have ambiguity_note = null.

Return only the JSON object required by the schema."""


def _nullable(t: str) -> dict:
    return {"type": [t, "null"]}


_EVIDENCE = {
    "type": "object",
    "additionalProperties": False,
    "required": ["timestamp", "speaker", "quote"],
    "properties": {"timestamp": _nullable("string"), "speaker": _nullable("string"), "quote": {"type": "string"}},
}

_SIDE = {"type": "string", "enum": ["internal", "external", "unknown"]}

OUTPUT_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["company_context", "people", "unnamed_roles"],
    "properties": {
        "company_context": {
            "type": "object",
            "additionalProperties": False,
            "required": ["internal_company", "external_companies"],
            "properties": {
                "internal_company": _nullable("string"),
                "external_companies": {"type": "array", "items": {"type": "string"}},
            },
        },
        "people": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "canonical_name", "first_name", "last_name", "aliases", "title", "title_source",
                    "company", "side", "presence", "matched_attendee_email", "buying_role",
                    "relationships", "evidence", "confidence", "notes", "ambiguity_note",
                ],
                "properties": {
                    "canonical_name": {"type": "string"},
                    "first_name": _nullable("string"),
                    "last_name": _nullable("string"),
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "title": _nullable("string"),
                    "title_source": {
                        "type": "string",
                        "enum": ["stated_by_self", "stated_by_other", "attendee_list", "inferred", "none"],
                    },
                    "company": _nullable("string"),
                    "side": _SIDE,
                    "presence": {"type": "string", "enum": ["spoke", "attended_silent", "mentioned_only"]},
                    "matched_attendee_email": _nullable("string"),
                    "buying_role": {
                        "type": "string",
                        "enum": [
                            "economic_buyer", "decision_maker", "champion", "influencer",
                            "technical_evaluator", "end_user", "blocker", "procurement_legal",
                            "sales_rep", "other", "unknown",
                        ],
                    },
                    "relationships": {"type": "array", "items": {"type": "string"}},
                    "evidence": {"type": "array", "items": _EVIDENCE},
                    "confidence": {"type": "number"},
                    "notes": _nullable("string"),
                    "ambiguity_note": _nullable("string"),
                },
            },
        },
        "unnamed_roles": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["description", "title", "company", "side", "evidence"],
                "properties": {
                    "description": {"type": "string"},
                    "title": _nullable("string"),
                    "company": _nullable("string"),
                    "side": _SIDE,
                    "evidence": {"type": "array", "items": _EVIDENCE},
                },
            },
        },
    },
}
