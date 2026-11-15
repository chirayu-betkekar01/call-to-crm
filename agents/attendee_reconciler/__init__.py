"""Agent 1 - Attendee Reconciler.

Reads a call transcript (Fathom API or .txt) and works out who was really involved: speakers,
silent attendees, people who joined uninvited and people who were only mentioned. It merges
nicknames and duplicates, drops bots and fictional names, matches invitee emails, and writes
the first handoff file (`00_people_extraction.json`) for the agents after it.
"""
from .agent import AttendeeReconcilerAgent, PeopleExtractionAgent
from .sources import FathomSource, TxtSource

__all__ = ["AttendeeReconcilerAgent", "FathomSource", "PeopleExtractionAgent", "TxtSource"]
