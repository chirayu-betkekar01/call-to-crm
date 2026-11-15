"""Load sales-call transcripts from the Fathom API.

Docs: https://developers.fathom.ai  (base URL https://api.fathom.ai/external/v1, header X-Api-Key)

  GET /meetings?include_transcript=true&created_after=...&cursor=...
  GET /recordings/{recording_id}/transcript
"""
from __future__ import annotations

import os
import time
from typing import Any, Iterable, Iterator, Optional

import requests

from core.models import Attendee, Transcript, Utterance

BASE_URL = "https://api.fathom.ai/external/v1"


class FathomError(RuntimeError):
    pass


def meeting_to_transcript(meeting: dict[str, Any], internal_domains: Iterable[str] = ()) -> Transcript:
    """Convert one Fathom `meetings` item into a normalized Transcript."""
    domains = {d.lower() for d in internal_domains}
    rb = meeting.get("recorded_by") or {}
    if rb.get("email_domain"):
        domains.add(rb["email_domain"].lower())

    attendees = [
        Attendee(
            name=inv.get("name"),
            email=(inv.get("email") or None) and inv["email"].lower(),
            is_external=inv.get("is_external"),
            speaker_label=inv.get("matched_speaker_display_name"),
        )
        for inv in meeting.get("calendar_invitees") or []
    ]
    # Internal invitees' domains are internal too.
    for a in attendees:
        if a.is_external is False and a.email and "@" in a.email:
            domains.add(a.email.split("@", 1)[1])

    utterances = [
        Utterance(
            speaker=(item.get("speaker") or {}).get("display_name") or "Unknown",
            speaker_email=(item.get("speaker") or {}).get("matched_calendar_invitee_email"),
            text=item.get("text") or "",
            timestamp=item.get("timestamp"),
        )
        for item in meeting.get("transcript") or []
    ]

    return Transcript(
        source_type="fathom",
        source_id=str(meeting.get("recording_id")),
        title=meeting.get("meeting_title") or meeting.get("title"),
        date=meeting.get("recording_start_time") or meeting.get("scheduled_start_time") or meeting.get("created_at"),
        url=meeting.get("share_url") or meeting.get("url"),
        recorded_by=Attendee(name=rb.get("name"), email=rb.get("email"), is_external=False) if rb else None,
        attendees=attendees,
        internal_domains=sorted(domains),
        utterances=utterances,
    )


class FathomSource:
    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        recording_id: Optional[int] = None,
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
        recorded_by: Optional[list[str]] = None,
        external_only: bool = True,
        limit: int = 10,
        internal_domains: Iterable[str] = (),
        session: Optional[requests.Session] = None,
    ):
        self.api_key = api_key or os.getenv("FATHOM_API_KEY")
        if not self.api_key:
            raise FathomError("FATHOM_API_KEY is not set (put it in .env or pass --fathom-key).")
        self.recording_id = recording_id
        self.created_after = created_after
        self.created_before = created_before
        self.recorded_by = recorded_by or []
        self.external_only = external_only
        self.limit = limit
        self.internal_domains = list(internal_domains)
        self.http = session or requests.Session()
        self.http.headers.update({"X-Api-Key": self.api_key, "Accept": "application/json"})

    # -- HTTP ------------------------------------------------------------- #
    def _get(self, path: str, params: Any = None, retries: int = 4) -> dict:
        for attempt in range(retries + 1):
            r = self.http.get(f"{BASE_URL}{path}", params=params, timeout=60)
            if r.status_code == 429 or r.status_code >= 500:
                if attempt == retries:
                    break
                wait = float(r.headers.get("Retry-After") or 2 ** attempt)
                time.sleep(min(wait, 60))
                continue
            if r.status_code == 401:
                raise FathomError("Fathom rejected the API key (401). Check FATHOM_API_KEY.")
            if not r.ok:
                raise FathomError(f"Fathom {path} failed: {r.status_code} {r.text[:300]}")
            return r.json()
        raise FathomError(f"Fathom {path} kept failing after {retries} retries: {r.status_code}")

    def _iter_meetings(self, include_transcript: bool) -> Iterator[dict]:
        params: list[tuple[str, str]] = [("include_transcript", str(include_transcript).lower())]
        if self.created_after:
            params.append(("created_after", self.created_after))
        if self.created_before:
            params.append(("created_before", self.created_before))
        if self.external_only:
            params.append(("calendar_invitees_domains_type", "one_or_more_external"))
        for email in self.recorded_by:
            params.append(("recorded_by[]", email))
        cursor = None
        while True:
            page = self._get("/meetings", params + ([("cursor", cursor)] if cursor else []))
            yield from page.get("items") or []
            cursor = page.get("next_cursor")
            if not cursor:
                return

    # -- Public ----------------------------------------------------------- #
    def fetch(self) -> Iterator[Transcript]:
        if self.recording_id is not None:
            yield self._fetch_one(self.recording_id)
            return
        count = 0
        for meeting in self._iter_meetings(include_transcript=True):
            if not meeting.get("transcript"):
                continue
            yield meeting_to_transcript(meeting, self.internal_domains)
            count += 1
            if count >= self.limit:
                return

    def _fetch_one(self, recording_id: int) -> Transcript:
        # Metadata (attendees, recorder) only comes from /meetings, so look it up there first.
        meta: dict = {}
        for m in self._iter_meetings(include_transcript=False):
            if m.get("recording_id") == recording_id:
                meta = m
                break
        data = self._get(f"/recordings/{recording_id}/transcript")
        meta = {**meta, "recording_id": recording_id, "transcript": data.get("transcript") or []}
        if not meta["transcript"]:
            raise FathomError(f"Recording {recording_id} has no transcript yet.")
        return meeting_to_transcript(meta, self.internal_domains)
