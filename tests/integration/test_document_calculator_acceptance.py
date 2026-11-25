import json
import re
import shutil
from pathlib import Path
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from apps.api.dependencies import get_db_session
from apps.api.main import create_app
from qa_eval.application.document_evaluation.parser import parse_document
from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.llm.client import (
    LLMTransportError,
    OpenAICompatibleLLMClient,
)

FIXTURES = Path(__file__).parents[1] / "fixtures/document-completeness"
KEYS = {
    "0101": "document_basic_information",
    "0102": "document_content_characteristics",
    "0103": "document_construction_process",
    "0104": "document_application_instructions",
}


class MockLLM:
    calls = 0
    invalid = False
    unavailable = False
    first_status = "SATISFIED"

    def close(self):
        pass

    def complete_json(self, *, system, user):
        self.calls += 1
        if self.unavailable:
            raise LLMTransportError("fixture gateway timeout")
        codes = re.findall(r"^### (\S+) ", user, re.MULTILINE)
        evidence = re.search(r"^(\d+)\|([^\n]*Scale:[^\n]*)", user, re.MULTILINE)
        return {
            "items": [
                {
                    "item_code": code,
                    "status": self.first_status
                    if evidence and index == 0
                    else "MISSING",
                    "reason": "deterministic fixture",
                    "document_evidence": [
                        {
                            "line_start": int(evidence[1]),
                            "line_end": int(evidence[1]),
                            "excerpt": "fabricated" if self.invalid else "Scale:",
                        }
                    ]
                    if evidence and index == 0
                    else [],
                }
                for index, code in enumerate(codes)
            ]
        }


@pytest.fixture
def registry_client(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    app = create_app()

    def session():
        with Session(engine) as db:
            yield db
            db.commit()

    app.dependency_overrides[get_db_session] = session
    mock = MockLLM()
    monkeypatch.setattr(
        OpenAICompatibleLLMClient, "from_settings", lambda settings, **options: mock
    )
    with TestClient(app) as client:
        assert client.post("/api/v1/registry/sync").status_code == 200
        yield client, mock
    engine.dispose()


def test_registered_documents_execute_independently(registry_client):
    client, _ = registry_client
    for code, key in KEYS.items():
        assert client.get(f"/api/v1/metrics/{code}/versions/1.0.0").status_code == 200
        descriptor = client.get(f"/api/v1/calculators/{key}/versions/1.0.0").json()
        assert descriptor["input_contract"]["logical"]["grain"] == "DOCUMENT"
        assert descriptor["supported_metrics"][0]["metric_key"] == code
        bindings = client.get(
            "/api/v1/metric-calculator-bindings", params={"metric_key": code}
        ).json()
        assert [(b["calculator_key"], b["enabled"]) for b in bindings] == [(key, True)]
        result = execute(client, key, "complete.md")
        assert result.status_code == 200, result.text
        assert [m["value"] for m in result.json()["measurements"]] == [1, 5, 0.2]


def execute(client, key, name, configuration=None, content=None):
    return client.post(
        f"/api/v1/calculators/{key}/versions/1.0.0/test-document",
        files={
            "document": (
                name,
                (FIXTURES / name).read_bytes() if content is None else content,
            )
        },
        data={
            "parameters": json.dumps(
                {"configuration": configuration} if configuration is not None else {}
            )
        },
    )


@pytest.mark.parametrize(
    ("suffix", "locator"), [("md", "line"), ("pdf", "page"), ("docx", "paragraph")]
)
def test_formats(registry_client, suffix, locator):
    if suffix == "pdf" and not shutil.which("pdftotext"):
        pytest.skip("pdftotext unavailable")
    filename = f"complete.{suffix}"
    content = (FIXTURES / filename).read_bytes()
    parsed = parse_document(content, f".{suffix}")
    assert parsed.blocks == parse_document(content, f".{suffix}").blocks
    client, _ = registry_client
    response = execute(client, KEYS["0101"], filename)
    assert response.status_code == 200, response.text
    result = response.json()
    assert [m["value"] for m in result["measurements"]] == [1, 5, 0.2]
    first, missing, *_ = result["evidence"][0]["records"]
    assert first["quotes"][0]["excerpt"] == "Scale:"
    assert locator in first["quotes"][0]["blocks"][0]["locator"]
    assert missing["quotes"] == [] and missing["inspected_scope"]["block_count"] == len(
        parsed.blocks
    )


def test_docx_table_locator(tmp_path):
    path = tmp_path / "table.docx"
    with ZipFile(path, "w") as archive:
        archive.writestr(
            "word/document.xml",
            """<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Table content</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>""",
        )
    (block,) = parse_document(path.read_bytes(), ".docx").blocks
    assert block.text == "Table content"
    assert block.locator == {"paragraph": 1, "table_path": [[1, 1, 1]]}


def test_dynamic_configuration_and_failures(registry_client):
    client, mock = registry_client
    key = KEYS["0101"]
    aspect = {
        "item_code": "custom",
        "item_name": "Scale",
        "satisfied_standard": "Number of records",
    }
    config = {
        "version": "1.1.0",
        "items": [aspect, {**aspect, "item_code": "excluded", "applicable": False}],
    }
    source = ("irrelevant\n" * 4500 + "Scale: 3 records").encode()
    response = execute(client, key, "long.md", config, source)
    assert response.status_code == 200, response.text
    result = response.json()
    assert [m["value"] for m in result["measurements"]] == [1, 1, 1]
    assert result["evidence"][0]["records"][1]["outcome"] == "NOT_APPLICABLE"
    assert result["status_detail"]["configuration_sha256"].startswith("sha256:")

    config["items"] = [config["items"][1]]
    calls = mock.calls
    response = execute(client, key, "complete.md", config)
    assert response.json()["status"] == "NOT_APPLICABLE"
    assert response.json()["measurements"] == [] and mock.calls == calls
    assert execute(client, key, "broken.docx", content=b"bad zip").status_code == 422
    assert mock.calls == calls

    mock.invalid = True
    response = execute(client, key, "complete.md")
    assert response.status_code == 502
    assert "measurements" not in response.json()
    assert mock.calls == calls + 3


def test_insufficient_counts_in_denominator_and_transport_failure_has_no_result(
    registry_client,
):
    client, mock = registry_client
    mock.first_status = "INSUFFICIENT"
    response = execute(client, KEYS["0101"], "complete.md")
    assert response.status_code == 200
    result = response.json()
    assert [item["value"] for item in result["measurements"]] == [0, 5, 0]
    assert result["evidence"][0]["records"][0]["outcome"] == "INSUFFICIENT"

    mock.unavailable = True
    response = execute(client, KEYS["0101"], "complete.md")
    assert response.status_code == 502
    assert set(response.json()) == {"detail"}
    assert mock.calls == 2
