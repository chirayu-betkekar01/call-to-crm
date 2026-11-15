"""Load sales-call transcripts from plain .txt files.

Supported layout (header is optional; everything before a line of `---` is header):

    Title: Acme Corp - Discovery Call
    Date: 2026-11-03
    Internal domains: northwind.io
    Attendees:
    - Priya Raman <priya@northwind.io>
    - Matthew Collins <matt.collins@acmecorp.com> (external)
    ---
    [00:00:05] Priya Raman: Thanks for joining, everyone.
    Matt: Happy to be here.

Transcript lines may be `[HH:MM:SS] Speaker: text`, `HH:MM:SS Speaker: text`, or
`Speaker: text`. Lines that don't start with a speaker are appended to the previous
utterance (wrapped lines).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Iterator

from ..models import Attendee, Transcript, Utterance

_HEADER_KEY = re.compile(r"^\s*([A-Za-z][A-Za-z _-]{1,30}):\s*(.*)$")
_ATTENDEE = re.compile(
    r"^\s*[-*]\s*(?P<name>[^<(\[]+?)?\s*(?:<(?P<email>[^>]+)>)?\s*(?:[(\[](?P<tag>[^)\]]+)[)\]])?\s*$"
)
_LINE = re.compile(
    r"^\s*(?:\[?(?P<ts>\d{1,2}:\d{2}(?::\d{2})?)\]?\s*[-–]?\s*)?"
    r"(?P<speaker>[A-Z][\w.'\- ]{0,40}?|Speaker \d+)\s*:\s*(?P<text>.+)$"
)
_SEPARATOR = re.compile(r"^\s*-{3,}\s*$")


def _norm_ts(ts: str | None) -> str | None:
    if not ts:
        return None
    parts = ts.split(":")
    if len(parts) == 2:
        parts = ["00", *parts]
    return ":".join(p.zfill(2) for p in parts)


def _domain(email: str | None) -> str | None:
    return email.split("@", 1)[1].lower() if email and "@" in email else None


def parse_txt(text: str, source_id: str = "inline", internal_domains: Iterable[str] = ()) -> Transcript:
    lines = text.splitlines()
    sep_idx = next((i for i, l in enumerate(lines) if _SEPARATOR.match(l)), None)
    header_lines, body_lines = (lines[:sep_idx], lines[sep_idx + 1 :]) if sep_idx is not None else ([], lines)

    title = date = url = None
    domains = {d.strip().lower() for d in internal_domains if d.strip()}
    attendees: list[Attendee] = []
    in_attendees = False
    for raw in header_lines:
        if in_attendees and raw.strip().startswith(("-", "*")):
            m = _ATTENDEE.match(raw)
            if m and (m.group("name") or m.group("email")):
                tag = (m.group("tag") or "").lower()
                is_ext = True if "external" in tag else False if "internal" in tag else None
                attendees.append(
                    Attendee(
                        name=(m.group("name") or "").strip() or None,
                        email=(m.group("email") or "").strip().lower() or None,
                        is_external=is_ext,
                    )
                )
            continue
        in_attendees = False
        m = _HEADER_KEY.match(raw)
        if not m:
            continue
        key, val = m.group(1).strip().lower(), m.group(2).strip()
        if key == "title":
            title = val
        elif key == "date":
            date = val
        elif key == "url":
            url = val
        elif key in ("internal domains", "internal domain", "our domains"):
            domains |= {d.strip().lower() for d in val.split(",") if d.strip()}
        elif key in ("attendees", "participants", "invitees"):
            in_attendees = True

    # Fill in side from email domain when the file didn't tag it.
    for a in attendees:
        if a.is_external is None and domains and _domain(a.email):
            a.is_external = _domain(a.email) not in domains

    utterances: list[Utterance] = []
    for raw in body_lines:
        if not raw.strip():
            continue
        m = _LINE.match(raw)
        if m:
            utterances.append(
                Utterance(speaker=m.group("speaker").strip(), text=m.group("text").strip(), timestamp=_norm_ts(m.group("ts")))
            )
        elif utterances:
            utterances[-1].text += " " + raw.strip()
        else:
            utterances.append(Utterance(speaker="Unknown", text=raw.strip()))

    return Transcript(
        source_type="txt",
        source_id=source_id,
        title=title or Path(source_id).stem.replace("_", " "),
        date=date,
        url=url,
        attendees=attendees,
        internal_domains=sorted(domains),
        utterances=utterances,
    )


class TxtSource:
    """Yields one Transcript per .txt file (a single file or every .txt in a folder)."""

    def __init__(self, path: str | Path, internal_domains: Iterable[str] = ()):
        self.path = Path(path)
        self.internal_domains = list(internal_domains)

    def fetch(self) -> Iterator[Transcript]:
        files = sorted(self.path.glob("*.txt")) if self.path.is_dir() else [self.path]
        if not files:
            raise FileNotFoundError(f"No .txt transcripts found at {self.path}")
        for f in files:
            yield parse_txt(f.read_text(encoding="utf-8"), source_id=str(f), internal_domains=self.internal_domains)
