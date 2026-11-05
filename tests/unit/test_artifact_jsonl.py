import json
from pathlib import Path

from qa_eval.application.artifact_jsonl import find_jsonl_record, read_jsonl_page


class MaterializingStore:
    def __init__(self, source: Path) -> None:
        self.source = source
        self.materialized = 0

    def materialize(self, _uri: str, destination: Path) -> None:
        destination.write_bytes(self.source.read_bytes())
        self.materialized += 1

    def read(self, _uri: str) -> bytes:
        raise AssertionError("streaming readers must not call ArtifactStore.read")


def test_read_jsonl_page_stops_after_the_requested_records(tmp_path: Path):
    source = tmp_path / "records.jsonl"
    source.write_text(
        "\n".join(json.dumps({"sample_id": str(i)}) for i in range(4))
        + "\nnot-json-after-the-page\n",
        encoding="utf-8",
    )
    store = MaterializingStore(source)

    page = read_jsonl_page(store, "records.jsonl", offset=2, limit=2)

    assert [item["sample_id"] for item in page] == ["2", "3"]
    assert store.materialized == 1


def test_find_jsonl_record_stops_at_first_match(tmp_path: Path):
    source = tmp_path / "records.jsonl"
    source.write_text(
        '{"sample_id":"wanted"}\nnot-json-after-the-match\n', encoding="utf-8"
    )
    store = MaterializingStore(source)

    record = find_jsonl_record(
        store, "records.jsonl", lambda item: item.get("sample_id") == "wanted"
    )

    assert record == {"sample_id": "wanted"}
