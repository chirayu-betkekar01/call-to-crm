"""GTM pipeline: transcripts -> people (1) -> buying roles (2) -> enrichment (3a) -> HubSpot sync (3b)."""
from .enrich import ApolloEnricher, ContactEnrichmentStage, MockEnricher, make_enricher
from .extractor import PeopleExtractionAgent
from .hubspot import HubSpotClient, HubSpotSyncStage
from .models import HandoffEnvelope, Person, RoleAssignment, Transcript
from .pipeline import Pipeline, Stage, load_envelope, register_stage
from .roles import DEFAULT_ROLES, ROLE_LIBRARY, BuyingRoleStage

__all__ = [
    "ApolloEnricher", "BuyingRoleStage", "ContactEnrichmentStage", "DEFAULT_ROLES", "HandoffEnvelope",
    "HubSpotClient", "HubSpotSyncStage", "MockEnricher", "PeopleExtractionAgent", "Person", "Pipeline",
    "ROLE_LIBRARY", "RoleAssignment", "Stage", "Transcript", "load_envelope", "make_enricher", "register_stage",
]
