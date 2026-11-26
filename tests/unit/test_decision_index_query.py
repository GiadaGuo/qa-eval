import json

from qa_eval.application.evidence.index import (
    ArtifactIndexSpec,
    ArtifactRecordIndexService,
)
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)


def test_decision_categories_and_paging_deduplicate_within_the_filter(tmp_path):
    store = LocalFileSystemArtifactStore(tmp_path)
    relative = "decisions.jsonl"
    staged = store.prepare_write(relative)
    records = [
        ("a", "first", "NOT_EVALUATED", "OTHER"),
        ("b", "first", "NOT_EVALUATED", "A|A|OTHER"),
        ("c", "first", "NOT_EVALUATED", "A"),
        ("d", "second", "NOT_EVALUATED", "A"),
        ("e", "third", "1", "A"),
    ]
    staged.write_text(
        "".join(
            json.dumps(
                {"row_id": key, "sample_id": sample, "status": status, "reason": reason}
            )
            + "\n"
            for key, sample, status, reason in records
        )
    )
    artifact = store.commit(relative)
    spec = ArtifactIndexSpec(
        artifact.uri,
        f"sha256:{artifact.sha256}",
        "row_id",
        "EVIDENCE",
        decision_filters=True,
    )
    service = ArtifactRecordIndexService(store)
    service.build(spec)

    assert service.decision_summary(spec) == {
        "total": 3,
        "undecided": 2,
        "reasons": [{"code": "A", "records": 2}, {"code": "OTHER", "records": 1}],
    }
    filters = {
        "limit": 1,
        "status_filter": "NOT_EVALUATED",
        "reason": "A",
        "unique_samples": True,
    }
    first, cursor = service.query(spec, **filters)
    assert [row["row_id"] for row in first] == ["b"]
    assert cursor
    second, cursor = service.query(spec, cursor=cursor, **filters)
    assert [row["row_id"] for row in second] == ["d"]
    assert cursor is None
    groups = service.decision_preview(spec, "A")
    assert [row["row_id"] for row in groups[0]["rows"]] == ["b", "d"]
    assert list(service.iter_decision_export(spec, "A")) == [
        ("first", "A"),
        ("second", "A"),
    ]
    assert [
        (sample, set(reasons.split(",")))
        for sample, reasons in service.iter_decision_export(spec, None)
    ] == [
        ("first", {"A", "OTHER"}),
        ("second", {"A"}),
    ]


def test_preview_budget_covers_categories_before_repeating(tmp_path, monkeypatch):
    store = LocalFileSystemArtifactStore(tmp_path)
    path = store.prepare_write("decisions.jsonl")
    codes = [f"R{index:02}" for index in range(55)]
    path.write_text(
        "".join(
            json.dumps(
                {
                    "row_id": str(index),
                    "sample_id": str(index),
                    "status": "NOT_EVALUATED",
                    "reason": "|".join(codes),
                }
            )
            + "\n"
            for index in range(4)
        )
    )
    artifact = store.commit("decisions.jsonl")
    spec = ArtifactIndexSpec(
        artifact.uri,
        f"sha256:{artifact.sha256}",
        "row_id",
        "EVIDENCE",
        decision_filters=True,
    )
    service = ArtifactRecordIndexService(store)
    service.build(spec)
    reads = []
    read_range = store.read_range

    def track_read(*args):
        reads.append(args)
        return read_range(*args)

    monkeypatch.setattr(store, "read_range", track_read)
    assert len(service.decision_summary(spec)["reasons"]) == 55
    plan = service.decision_preview(spec, None, plan=True)
    assert plan == [{"code": code, "sample_count": 1} for code in codes[:50]]
    assert reads == []
    assert len(service.decision_preview(spec, None, group=codes[0])[0]["rows"]) == 1
    assert len(reads) == 1
    assert service.decision_preview(spec, None, group=codes[-1]) == []
    assert len(service.decision_preview(spec, codes[-1])[0]["rows"]) == 3
    assert len(reads) == 4
