from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from qa_eval.contracts.metrics import EvidenceArtifact
from qa_eval.domain.ports.artifact_store import ArtifactStore


class EvidenceReader:
    """Checksum-verifying reader for existing immutable Evidence artifacts."""

    def __init__(self, artifact_store: ArtifactStore) -> None:
        self.artifact_store = artifact_store

    def read_bytes(self, evidence: EvidenceArtifact) -> bytes:
        content = self.artifact_store.read(evidence.artifact.uri)
        actual = hashlib.sha256(content).hexdigest()
        if actual != evidence.artifact.checksum.value:
            raise ValueError(f"{evidence.kind} evidence checksum mismatch")
        return content

    def read_json(self, evidence: EvidenceArtifact) -> Any:
        return json.loads(self.read_bytes(evidence))

    def iter_jsonl(self, evidence: EvidenceArtifact) -> Iterator[dict[str, Any]]:
        """Materialize once, verify checksum, then parse rows with bounded memory."""
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as temporary:
            path = Path(temporary.name)
        try:
            self.artifact_store.materialize(evidence.artifact.uri, path)
            digest = hashlib.sha256()
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != evidence.artifact.checksum.value:
                raise ValueError(f"{evidence.kind} evidence checksum mismatch")
            with path.open(encoding="utf-8-sig") as source:
                for line_number, line in enumerate(source, 1):
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise TypeError(
                            f"{evidence.kind} evidence row {line_number} must be an object"
                        )
                    yield row
        finally:
            path.unlink(missing_ok=True)
