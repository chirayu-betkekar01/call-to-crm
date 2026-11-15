"""Agent 3 - Enrichment + HubSpot Sync.

Two steps on the handoff from Agent 2:
  A. contact_enrichment: infer each buyer's company domain from the call, fill missing fields with
     a swappable provider (mock / Hunter / Apollo), and keep data from the call over enrichment.
  B. hubspot_sync: find or create the company and contacts, fill only empty fields, find the deal
     through HubSpot associations (never create one), and apply buying-role labels on contact->deal.
     Dry run unless write=True.
"""
from .enrich import ContactEnrichmentStage, anchor_domains
from .hubspot import HubSpotClient, HubSpotError, HubSpotSyncStage
from .providers import ENRICHERS, ApolloEnricher, EnrichError, HunterEnricher, MockEnricher, make_enricher

__all__ = [
    "ENRICHERS", "ApolloEnricher", "ContactEnrichmentStage", "EnrichError", "HubSpotClient", "HubSpotError",
    "HubSpotSyncStage", "HunterEnricher", "MockEnricher", "anchor_domains", "make_enricher",
]
