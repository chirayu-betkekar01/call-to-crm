"""The three agents. Each one is independent: it imports only `core` and talks to the others
through the saved handoff file.

  attendee_reconciler  (Agent 1)  transcript      -> who was really on / named in the call
  role_classifier      (Agent 2)  roster          -> Decision Maker / Champion / Blocker
  enrichment_sync      (Agent 3)  labeled roster  -> enriched contacts + labeled deal in HubSpot
"""
