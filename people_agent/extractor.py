"""The People Extraction agent: Transcript -> HandoffEnvelope."""
from __future__ import annotations

from typing import Optional, Protocol

from .models import HandoffEnvelope, LLMExtraction, SourceRef, Transcript
from .prompts import OUTPUT_SCHEMA, SYSTEM_PROMPT
from .resolver import resolve


class JSONModel(Protocol):
    model: str
    last_usage: dict

    def complete_json(self, system: str, user: str, schema: dict, schema_name: str = ..., max_tokens: int = ...) -> dict: ...


class PeopleExtractionAgent:
    name = "people_extraction"
    next_stage = "buying_role_classification"

    def __init__(self, llm: JSONModel, max_tokens: int = 16000):
        self.llm = llm
        self.max_tokens = max_tokens

    def build_user_prompt(self, t: Transcript) -> str:
        return (
            "Extract every person from this sales call, following your instructions.\n\n"
            "<call>\n" + t.render() + "\n</call>"
        )

    def extract(self, t: Transcript, run_id: Optional[str] = None) -> HandoffEnvelope:
        if not t.utterances:
            raise ValueError(f"Transcript {t.source_id} has no utterances")
        raw = self.llm.complete_json(
            SYSTEM_PROMPT, self.build_user_prompt(t), OUTPUT_SCHEMA, "people_extraction", self.max_tokens
        )
        extraction = LLMExtraction.model_validate(raw)
        people, stats = resolve(extraction, t)
        stats["model"] = getattr(self.llm, "model", None)
        stats["usage"] = getattr(self.llm, "last_usage", {})
        env = HandoffEnvelope(
            source=SourceRef(type=t.source_type, id=t.source_id, title=t.title, date=t.date, url=t.url),
            transcript=t,
            company_context=extraction.company_context,
            people=people,
            unnamed_roles=extraction.unnamed_roles,
            stats=stats,
            current_stage=self.name,
            next_stage=self.next_stage,
        )
        if run_id:
            env.run_id = run_id
        return env
