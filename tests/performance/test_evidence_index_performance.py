import json
import os
import random
import time

import pytest

from qa_eval.application.evidence.index import (
    ArtifactIndexSpec,
    ArtifactRecordIndexService,
)
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_EVIDENCE_INDEX_PERFORMANCE") != "1",
    reason="set RUN_EVIDENCE_INDEX_PERFORMANCE=1 for the 321,902-row acceptance benchmark",
)


def test_321902_row_local_index_hot_random_read_p95_under_200ms(tmp_path):
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
    relative = "performance/evidence.jsonl"
    staging = store.prepare_write(relative)
    with staging.open("w", encoding="utf-8") as output:
        for index in range(321_902):
            output.write(
                json.dumps(
                    {
                        "row_id": f"row-{index:06d}",
                        "sample_id": f"sample-{index:06d}",
                        "content_id": "body",
                    }
                )
                + "\n"
            )
    stored = store.commit(relative)
    spec = ArtifactIndexSpec(
        source_uri=stored.uri,
        source_checksum=f"sha256:{stored.sha256}",
        key_field="row_id",
        index_kind="EVIDENCE",
    )
    service = ArtifactRecordIndexService(store)
    metadata = service.build(spec)
    assert metadata["record_count"] == 321_902

    durations = []
    randomizer = random.Random(20261111)
    for _ in range(100):
        key = f"row-{randomizer.randrange(321_902):06d}"
        started = time.perf_counter()
        assert service.get(spec, key)["row_id"] == key
        durations.append((time.perf_counter() - started) * 1000)
    p95 = sorted(durations)[94]
    assert p95 < 200, f"hot local random-read P95 was {p95:.2f} ms"
