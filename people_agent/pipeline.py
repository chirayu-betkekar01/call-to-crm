"""Stepped-agent pipeline and the handoff between agents.

    Transcript --[PeopleExtractionAgent]--> envelope --[Stage: buying_role_classification]--> envelope --[Stage: contact_enrichment]--> ...

Every downstream agent is a `Stage`: it takes the HandoffEnvelope, returns an updated one,
and sets `next_stage`. Each envelope is written to disk after every stage, so any stage can
also be run on its own later from the saved JSON (`HandoffEnvelope.model_validate_json`).
"""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional

from .extractor import PeopleExtractionAgent
from .models import HandoffEnvelope, StageRecord, Transcript


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Stage(ABC):
    """Base class for the next agents (enrichment, CRM sync, ...)."""

    name: str = "stage"
    next_stage: Optional[str] = None

    @abstractmethod
    def run(self, envelope: HandoffEnvelope) -> HandoffEnvelope: ...


# Registry so stages can be wired by name: STAGES["contact_enrichment"] = MyEnrichmentStage
STAGES: dict[str, Callable[[], Stage]] = {}


def register_stage(name: str):
    def deco(cls):
        cls.name = name
        STAGES[name] = cls
        return cls

    return deco


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50] or "call"


class Pipeline:
    def __init__(
        self,
        extractor: Optional[PeopleExtractionAgent],
        stages: Iterable[Stage] = (),
        output_dir: str | Path = "output",
        on_handoff: Optional[Callable[[HandoffEnvelope, Path], None]] = None,
    ):
        self.extractor = extractor
        self.stages = list(stages)
        self.output_dir = Path(output_dir)
        self.on_handoff = on_handoff

    def _write(self, env: HandoffEnvelope, title: str) -> Path:
        folder = self.output_dir / f"{env.run_id}_{_slug(title)}"
        folder.mkdir(parents=True, exist_ok=True)
        last = env.history[-1].stage if env.history else env.current_stage
        path = folder / f"{max(len(env.history) - 1, 0):02d}_{last}.json"
        path.write_text(env.model_dump_json(indent=2), encoding="utf-8")
        (folder / "latest.json").write_text(env.model_dump_json(indent=2), encoding="utf-8")
        return path

    def run_one(self, transcript: Transcript) -> tuple[HandoffEnvelope, Path]:
        if self.extractor is None:
            raise RuntimeError("Pipeline has no extractor; use resume() with a saved envelope instead.")
        title = transcript.title or transcript.source_id
        started = _now()
        try:
            env = self.extractor.extract(transcript)
        except Exception as e:  # record failure so the batch keeps going
            raise RuntimeError(f"[{self.extractor.name}] {title}: {e}") from e
        env.history.append(StageRecord(stage=self.extractor.name, status="ok", started_at=started, finished_at=_now()))
        path = self._write(env, title)
        return self._run_stages(env, title, path)

    def resume(self, env: HandoffEnvelope) -> tuple[HandoffEnvelope, Path]:
        """Run the configured stages on an envelope loaded from disk (see `load_envelope`)."""
        title = env.source.title or env.source.id
        return self._run_stages(env, title, None)

    def _run_stages(self, env: HandoffEnvelope, title: str, path: Optional[Path]) -> tuple[HandoffEnvelope, Path]:
        for stage in self.stages:
            if env.next_stage and env.next_stage != stage.name:
                env.history.append(
                    StageRecord(stage=stage.name, status="skipped", started_at=_now(), finished_at=_now(),
                                detail=f"envelope expects '{env.next_stage}'")
                )
                continue
            started = _now()
            try:
                env = stage.run(env)
                env.current_stage, env.next_stage = stage.name, stage.next_stage
                env.history.append(StageRecord(stage=stage.name, status="ok", started_at=started, finished_at=_now()))
            except Exception as e:
                env.history.append(
                    StageRecord(stage=stage.name, status="error", started_at=started, finished_at=_now(), detail=str(e))
                )
                path = self._write(env, title)
                break
            path = self._write(env, title)

        if path is None:  # resumed and nothing ran: still persist the skip records
            path = self._write(env, title)
        if self.on_handoff:
            self.on_handoff(env, path)
        return env, path

    def run(self, transcripts: Iterable[Transcript]) -> list[tuple[HandoffEnvelope, Path]]:
        results, errors = [], []
        for t in transcripts:
            try:
                results.append(self.run_one(t))
            except Exception as e:
                errors.append(str(e))
                print(f"ERROR: {e}")
        if errors and not results:
            raise RuntimeError("All transcripts failed:\n" + "\n".join(errors))
        return results


def load_envelope(path: str | Path) -> HandoffEnvelope:
    """For the next agent: pick up a saved handoff file."""
    return HandoffEnvelope.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
