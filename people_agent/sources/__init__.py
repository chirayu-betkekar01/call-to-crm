"""Transcript sources. Each source has a `.fetch()` that yields normalized Transcript objects."""
from .fathom_source import FathomError, FathomSource, meeting_to_transcript
from .txt_source import TxtSource, parse_txt

__all__ = ["FathomError", "FathomSource", "TxtSource", "meeting_to_transcript", "parse_txt"]
