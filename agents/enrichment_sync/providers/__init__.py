"""Swappable enrichment providers for Agent 3. Add one: write a class with `name` + `enrich()`,
then register it in `make_enricher`."""
from __future__ import annotations

from .apollo import ApolloEnricher
from .base import Enricher, EnrichError, EnrichQuery, EnrichResult
from .hunter import HunterEnricher
from .mock import MockEnricher

ENRICHERS = ("mock", "hunter", "apollo")


def make_enricher(name: str) -> Enricher:
    name = (name or "mock").lower()
    if name == "mock":
        return MockEnricher()
    if name == "hunter":
        return HunterEnricher()
    if name == "apollo":
        return ApolloEnricher()
    raise ValueError(f"Unknown enricher '{name}'. Choose: {', '.join(ENRICHERS)}")


__all__ = ["ENRICHERS", "ApolloEnricher", "EnrichError", "EnrichQuery", "EnrichResult", "Enricher",
           "HunterEnricher", "MockEnricher", "make_enricher"]
