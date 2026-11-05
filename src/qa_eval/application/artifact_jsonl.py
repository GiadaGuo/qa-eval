"""Memory-bounded readers for JSONL artifacts."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from qa_eval.domain.ports.artifact_store import ArtifactStore


def read_jsonl_page(
    artifact_store: ArtifactStore,
    uri: str,
    *,
    offset: int,
    limit: int,
) -> list[dict[str, Any]]:
    """Return a bounded page and stop scanning as soon as it is complete."""
    page: list[dict[str, Any]] = []
    record_index = 0
    with (
        _materialized_artifact(artifact_store, uri) as path,
        path.open("r", encoding="utf-8-sig") as source,
    ):
        for line in source:
            if not line.strip():
                continue
            if record_index >= offset:
                page.append(json.loads(line))
                if len(page) == limit:
                    break
            record_index += 1
    return page


def find_jsonl_record(
    artifact_store: ArtifactStore,
    uri: str,
    predicate: Callable[[dict[str, Any]], bool],
) -> dict[str, Any] | None:
    """Return the first matching record using a bounded-memory scan."""
    with (
        _materialized_artifact(artifact_store, uri) as path,
        path.open("r", encoding="utf-8-sig") as source,
    ):
        for line in source:
            if not line.strip():
                continue
            record = json.loads(line)
            if predicate(record):
                return record
    return None


class _materialized_artifact:
    def __init__(self, artifact_store: ArtifactStore, uri: str) -> None:
        self.artifact_store = artifact_store
        self.uri = uri
        self.path: Path | None = None

    def __enter__(self) -> Path:
        temporary = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
        temporary.close()
        self.path = Path(temporary.name)
        try:
            self.artifact_store.materialize(self.uri, self.path)
        except Exception:
            self.path.unlink(missing_ok=True)
            raise
        return self.path

    def __exit__(self, *_args: object) -> None:
        if self.path is not None:
            self.path.unlink(missing_ok=True)
