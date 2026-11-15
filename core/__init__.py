"""Shared foundation for the three agents: data models, the handoff envelope, pipeline plumbing,
the LLM client and reporting. Agents depend on `core`. They never depend on each other."""
from .llm import JSONModel, LLMError, OpenRouterClient
from .models import (CompanyContext, Evidence, HandoffEnvelope, Person, RoleAssignment, SourceRef,
                     Transcript)
from .pipeline import Extractor, Pipeline, Stage, load_envelope, register_stage, run_saved
from .report import failed, summary

__all__ = [
    "CompanyContext", "Evidence", "Extractor", "HandoffEnvelope", "JSONModel", "LLMError", "OpenRouterClient",
    "Person", "Pipeline", "RoleAssignment", "SourceRef", "Stage", "Transcript", "failed", "load_envelope",
    "register_stage", "run_saved", "summary",
]
