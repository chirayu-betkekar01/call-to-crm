"""JSON-schema pieces shared by agents that ask the LLM for structured output."""
from __future__ import annotations


def nullable(t: str) -> dict:
    return {"type": [t, "null"]}


EVIDENCE_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["timestamp", "speaker", "quote"],
    "properties": {"timestamp": nullable("string"), "speaker": nullable("string"), "quote": {"type": "string"}},
}

SIDE_SCHEMA: dict = {"type": "string", "enum": ["internal", "external", "unknown"]}
