import json
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from apps.api.dependencies import get_artifact_store, get_db_session
from apps.api.main import create_app
from qa_eval.application.services.evaluation_run_service import EvaluationRunService
from qa_eval.application.services.metric_registry_service import MetricRegistryService
from qa_eval.application.services.quality_contract_service import (
    QualityContractPublishRequest,
    QualityContractService,
)
from qa_eval.application.services.quality_result_service import QualityResultService
from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.db.models.dataset import DatasetModel
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.db.models.evaluation_run import (
    EvaluationRunModel,
    MetricResultModel,
    PhysicalEvaluationPlanModel,
    StageRunModel,
)
from qa_eval.infrastructure.db.models.quality_contract import (
    QualityContractVersionModel,
)
from qa_eval.infrastructure.db.models.quality_result import (
    QualityIssueModel,
    QualityReportModel,
    RuleEvaluationModel,
)
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)


def _fk_enforcing_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def _sample(version_id, sample_id: str, text: str) -> str:
    return json.dumps(
        {
            "schema_version": "2.0",
            "dataset_version_id": str(version_id),
            "sample_id": sample_id,
            "contents": [
                {
                    "content_id": "body",
                    "modality": "text",
                    "role": "article.body.plain",
                    "presence": "PRESENT",
                    "payload": {"type": "inline_text", "text": text, "format": "plain"},
                    "attributes": {},
                }
            ],
            "annotations": [],
            "metadata": {},
            "source": {
                "source_artifact_id": str(uuid4()),
                "source_record_id": sample_id,
                "source_row": 1,
                "locator": None,
                "attributes": {},
            },
            "lineage": {"input_fingerprint": f"sha256:{'a' * 64}", "transforms": []},
        },
        ensure_ascii=False,
    )


def _seed_contract(
    session: Session,
    store: LocalFileSystemArtifactStore,
    *,
    measurement: str = "exact_affected_record_ratio",
):
    dataset_id, version_id = uuid4(), uuid4()
    session.add(DatasetModel(id=dataset_id, name="articles", modality=["text"]))
    relative = f"datasets/{dataset_id}/{version_id}/sample_records.jsonl"
    content = (
        "\n".join(
            [
                _sample(version_id, "a", "重复正文"),
                _sample(version_id, "b", "重复正文"),
                _sample(version_id, "c", "独立正文"),
            ]
        )
        + "\n"
    )
    staging = store.prepare_write(relative)
    staging.write_text(content)
    normalized = store.commit(relative)
    session.add(
        DatasetVersionModel(
            id=version_id,
            dataset_id=dataset_id,
            version="v1",
            status="PUBLISHED",
            content_fingerprint=f"sha256:{normalized.sha256}",
            artifact_refs={
                "manifest": {"uri": "datasets/manifest.json"},
                "normalized": {"uri": normalized.uri, "sha256": normalized.sha256},
                "field_mapping": {
                    "mappings": [
                        {
                            "action": "MAP",
                            "target": {"kind": "CONTENT", "role": "article.body.plain"},
                        }
                    ]
                },
            },
        )
    )
    session.flush()
    MetricRegistryService(session).sync()
    contract = QualityContractService(session).publish(
        QualityContractPublishRequest.model_validate(
            {
                "name": "duplicate gate",
                "version_label": "v1",
                "dataset_version_id": version_id,
                "metric_key": "02080103",
                "metric_version": "1.0.0",
                "measurement": measurement,
                "calculator_key": "text_duplicate_degree",
                "calculator_version": "2.0.0",
                "parameters": {"content_role": "article.body.plain"},
                "rule": {
                    "operator": "LTE",
                    "threshold": 0.5,
                    "severity": "ERROR",
                    "on_missing": "UNKNOWN",
                },
            }
        )
    )
    session.commit()
    return contract.contract.contract_version_id


def _create_run(engine, store, contract_version_id):
    with Session(engine) as session:
        service = EvaluationRunService(session, store)
        run = service.create(contract_version_id)
        session.commit()
        return run.run_id


@contextmanager
def _client(engine, store) -> Generator[TestClient, None, None]:
    app = create_app()

    def override_session() -> Generator[Session, None, None]:
        with Session(engine) as session:
            yield session
            session.commit()

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_artifact_store] = lambda: store
    with TestClient(app) as client:
        yield client


def test_completed_run_returns_report_and_issues(tmp_path: Path):
    engine = _fk_enforcing_engine()
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")

    with Session(engine) as session:
        contract_version_id = _seed_contract(session, store)
        service = EvaluationRunService(session, store)
        run = service.create(contract_version_id)
        session.commit()
        service.execute(run.run_id)

    with _client(engine, store) as client:
        report_response = client.get(
            f"/api/v1/evaluation-runs/{run.run_id}/quality-report"
        )
        issues_response = client.get(
            f"/api/v1/evaluation-runs/{run.run_id}/quality-issues?limit=100"
        )

    assert report_response.status_code == 200
    report = report_response.json()
    assert report["run_id"] == str(run.run_id)
    assert report["execution_status"] == "SUCCEEDED"

    assert issues_response.status_code == 200
    body = issues_response.json()
    assert body["total"] == (
        report["issue_summary"]["confirmed_count"]
        + report["issue_summary"]["candidate_count"]
    )


def test_report_404_for_run_without_report_and_unknown_run(tmp_path: Path):
    engine = _fk_enforcing_engine()
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")

    with Session(engine) as session:
        contract_version_id = _seed_contract(session, store)
        run_id = _create_run(engine, store, contract_version_id)

    with _client(engine, store) as client:
        pending = client.get(f"/api/v1/evaluation-runs/{run_id}/quality-report")
        missing_run = client.get(f"/api/v1/evaluation-runs/{uuid4()}/quality-report")
        issues = client.get(f"/api/v1/evaluation-runs/{run_id}/quality-issues")

    assert pending.status_code == 404
    assert pending.json()["detail"] == "QualityReport not found"
    assert missing_run.status_code == 404
    assert missing_run.json()["detail"] == "QualityReport not found"
    assert issues.status_code == 200
    assert issues.json()["total"] == 0


def test_finalize_endpoint_recovers_run_after_finalization_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    engine = _fk_enforcing_engine()
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")

    with Session(engine) as session:
        contract_version_id = _seed_contract(session, store)
        service = EvaluationRunService(session, store)
        run = service.create(contract_version_id)
        session.commit()

        broken = {"yes": True}
        original = QualityResultService.finalize

        def flaky_finalize(self, run_id):
            if broken["yes"]:
                raise ValueError("simulated finalization outage")
            return original(self, run_id)

        monkeypatch.setattr(QualityResultService, "finalize", flaky_finalize)
        completed = service.execute(run.run_id)
        broken["yes"] = False

        # The failed finalization is recorded on the run and left no partial rows.
        assert completed.status == "PARTIAL_SUCCEEDED"
        assert completed.error["phase"] == "QUALITY_RESULT_FINALIZATION"
        assert session.scalars(select(QualityReportModel)).all() == []
        assert session.scalars(select(QualityIssueModel)).all() == []
        assert session.scalars(select(RuleEvaluationModel)).all() == []

    with _client(engine, store) as client:
        before = client.get(f"/api/v1/evaluation-runs/{run.run_id}/quality-report")
        assert before.status_code == 404

        finalized = client.post(f"/api/v1/evaluation-runs/{run.run_id}/finalize")
        assert finalized.status_code == 200
        report = finalized.json()
        assert report["run_id"] == str(run.run_id)

        run_after = client.get(f"/api/v1/evaluation-runs/{run.run_id}").json()
        assert run_after["status"] == "SUCCEEDED"
        assert run_after["error"] is None

        again = client.post(f"/api/v1/evaluation-runs/{run.run_id}/finalize")
        assert again.status_code == 200
        assert again.json()["report_id"] == report["report_id"]

        fetched = client.get(f"/api/v1/evaluation-runs/{run.run_id}/quality-report")
        assert fetched.status_code == 200
        assert fetched.json()["report_id"] == report["report_id"]

    with Session(engine) as session:
        assert len(session.scalars(select(QualityReportModel)).all()) == 1
        issues = session.scalars(select(QualityIssueModel)).all()
        assert issues
        assert all(issue.run_id == run.run_id for issue in issues)


def test_finalize_rejects_unknown_and_non_terminal_runs(tmp_path: Path):
    engine = _fk_enforcing_engine()
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")

    with Session(engine) as session:
        contract_version_id = _seed_contract(session, store)
        run_id = _create_run(engine, store, contract_version_id)

    with _client(engine, store) as client:
        unknown = client.post(f"/api/v1/evaluation-runs/{uuid4()}/finalize")
        pending = client.post(f"/api/v1/evaluation-runs/{run_id}/finalize")

    assert unknown.status_code == 404
    assert unknown.json()["detail"] == "EvaluationRun not found"
    assert pending.status_code == 422
    assert "terminal" in pending.json()["detail"]


def _sequential_issue_uuid(index: int) -> UUID:
    """Deterministic ascending UUID whose hex contains a letter.

    A purely numeric hex string gets coerced to INTEGER by SQLite's NUMERIC
    affinity for the bare `UUID` column type, breaking UUID round-trips.
    """
    return UUID(f"00000000-0000-0000-0000-{0xA00000000000 + index:012x}")


def _issue_document(
    index: int,
    run_id,
    version_id,
    metric_result_id,
    determination: str,
    seq_base: int = 0,
) -> dict:
    now = "2026-10-28T00:00:00+00:00"
    return {
        "issue_id": str(_sequential_issue_uuid(seq_base + index)),
        "run_id": str(run_id),
        "dataset_version_id": str(version_id),
        "source": {
            "metric_result_id": str(metric_result_id),
            "metric_key": "02080103",
            "metric_version": "1.0.0",
            "measurement": "exact_redundant_record_ratio",
            "rule_evaluation_id": None,
        },
        "detector": {"key": "pagination_test_detector", "version": "1.0.0"},
        "category": "DUPLICATE_CONTENT",
        "code": "EXACT_DUPLICATE_REDUNDANT",
        "determination": determination,
        "severity": "WARNING",
        "scope": "SAMPLE",
        "subject": {"type": "SAMPLE", "sample_id": f"s-{index}"},
        "related_subjects": [],
        "message": f"分页测试问题 {index}",
        "evidence": [
            {"evidence_id": str(uuid4()), "locator": {"group_id": f"g-{index}"}}
        ],
        "dedup_key": f"pagination:{index}",
        "status": "OPEN",
        "created_at": now,
        "updated_at": now,
    }


def _seed_run_with_issues(session: Session, count: int, seq_base: int = 0):
    """Insert a terminal run with exactly `count` issues (deterministic issue_id order)."""
    dataset_id, version_id, contract_id, plan_id, run_id = (uuid4() for _ in range(5))
    stage_run_id, metric_result_id = uuid4(), uuid4()
    session.add(
        DatasetModel(id=dataset_id, name=f"articles-{count}", modality=["text"])
    )
    session.add(
        DatasetVersionModel(
            id=version_id,
            dataset_id=dataset_id,
            version="v1",
            status="PUBLISHED",
            content_fingerprint=f"sha256:{'0' * 64}",
            artifact_refs={"manifest": {"uri": "datasets/manifest.json"}},
        )
    )
    session.flush()
    session.add(
        QualityContractVersionModel(
            contract_version_id=contract_id,
            contract_id=uuid4(),
            target_dataset_version_id=version_id,
            version_label="v1",
            name=f"contract-{count}",
            status="PUBLISHED",
            fingerprint=f"sha256:{count:064x}",
            document={},
            published_at=datetime.now(UTC),
        )
    )
    session.flush()
    session.add(
        PhysicalEvaluationPlanModel(
            physical_plan_id=plan_id,
            dataset_version_id=version_id,
            contract_version_id=contract_id,
            semantic_fingerprint=f"sha256:{count:064x}",
            document={},
        )
    )
    session.flush()
    session.add(
        EvaluationRunModel(
            run_id=run_id,
            physical_plan_id=plan_id,
            dataset_version_id=version_id,
            contract_version_id=contract_id,
            status="SUCCEEDED",
            document={},
        )
    )
    session.flush()
    session.add(
        StageRunModel(
            stage_run_id=stage_run_id,
            run_id=run_id,
            physical_plan_id=plan_id,
            stage_id="metric-0",
            attempt=1,
            status="SUCCEEDED",
            document={},
        )
    )
    session.flush()
    session.add(
        MetricResultModel(
            metric_result_id=metric_result_id,
            run_id=run_id,
            stage_run_id=stage_run_id,
            dataset_version_id=version_id,
            metric_key="02080103",
            metric_version="1.0.0",
            status="OK",
            document={},
        )
    )
    session.flush()
    for index in range(count):
        determination = "CONFIRMED" if index % 2 == 0 else "CANDIDATE"
        session.add(
            QualityIssueModel(
                issue_id=_sequential_issue_uuid(seq_base + index),
                run_id=run_id,
                metric_result_id=metric_result_id,
                determination=determination,
                severity="WARNING",
                code="EXACT_DUPLICATE_REDUNDANT",
                dedup_key=f"pagination:{index}",
                document=_issue_document(
                    index,
                    run_id,
                    version_id,
                    metric_result_id,
                    determination,
                    seq_base,
                ),
            )
        )
    session.commit()
    return run_id


def test_issue_pagination_covers_all_rows_without_gaps_or_duplicates(tmp_path: Path):
    engine = _fk_enforcing_engine()
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")

    expected_pages_by_count = {
        0: [0],
        7: [7],
        10: [10],
        11: [10, 1],
        105: [10] * 10 + [5],
    }
    with Session(engine) as session:
        service = QualityResultService(session, store)
        seq_base = 0
        for count, expected_page_sizes in expected_pages_by_count.items():
            run_id = _seed_run_with_issues(session, count, seq_base)
            seq_base += count

            collected: list[UUID] = []
            page_sizes: list[int] = []
            offset = 0
            while True:
                total, page = service.list_issues(run_id, limit=10, offset=offset)
                assert total == count
                page_sizes.append(len(page))
                collected.extend(item.issue_id for item in page)
                offset += 10
                if len(collected) >= total:
                    break  # all rows collected

            assert page_sizes == expected_page_sizes
            assert len(set(collected)) == count  # no duplicates
            assert collected == sorted(collected)  # stable order, no gaps


def test_issue_pagination_applies_determination_filter_consistently(tmp_path: Path):
    engine = _fk_enforcing_engine()
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")

    with Session(engine) as session:
        service = QualityResultService(session, store)
        run_id = _seed_run_with_issues(session, 11)  # 6 CONFIRMED, 5 CANDIDATE

        confirmed_total, confirmed_page = service.list_issues(
            run_id, determination="CONFIRMED", limit=10, offset=0
        )
        assert confirmed_total == 6
        assert len(confirmed_page) == 6
        assert all(item.determination == "CONFIRMED" for item in confirmed_page)

        candidate_total, candidate_page = service.list_issues(
            run_id, determination="CANDIDATE", limit=10, offset=0
        )
        assert candidate_total == 5
        assert len(candidate_page) == 5

        assert confirmed_total + candidate_total == 11


def test_issue_pagination_endpoint_returns_page_metadata(tmp_path: Path):
    engine = _fk_enforcing_engine()
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")

    with Session(engine) as session:
        run_id = _seed_run_with_issues(session, 105)

    with _client(engine, store) as client:
        first = client.get(
            f"/api/v1/evaluation-runs/{run_id}/quality-issues?limit=10&offset=0"
        )
        second = client.get(
            f"/api/v1/evaluation-runs/{run_id}/quality-issues?limit=10&offset=10"
        )
        last = client.get(
            f"/api/v1/evaluation-runs/{run_id}/quality-issues?limit=10&offset=100"
        )
        beyond = client.get(
            f"/api/v1/evaluation-runs/{run_id}/quality-issues?limit=10&offset=110"
        )

    assert first.status_code == 200
    assert first.json()["total"] == 105
    assert first.json()["limit"] == 10
    assert first.json()["offset"] == 0
    assert len(first.json()["issues"]) == 10

    assert second.json()["offset"] == 10
    assert [issue["issue_id"] for issue in second.json()["issues"]] == [
        str(_sequential_issue_uuid(index + 10)) for index in range(10)
    ]

    assert last.json()["offset"] == 100
    assert len(last.json()["issues"]) == 5

    assert beyond.json()["total"] == 105
    assert beyond.json()["issues"] == []


def test_report_shows_only_contract_measurement_as_primary(
    tmp_path: Path,
):
    """同一个 Calculator 输出多个诊断字段时，报告只把契约选中的 measurement 作为主结果。"""

    engine = _fk_enforcing_engine()
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")

    with Session(engine) as session:
        contract_version_id = _seed_contract(
            session,
            store,
            measurement="word_ngram_repetition_ratio",
        )
        service = EvaluationRunService(session, store)
        run = service.create(contract_version_id)
        session.commit()
        service.execute(run.run_id)

    with _client(engine, store) as client:
        response = client.get(f"/api/v1/evaluation-runs/{run.run_id}/quality-report")

    assert response.status_code == 200
    report = response.json()
    assert len(report["metrics"]) == 1
    metric = report["metrics"][0]
    assert metric["measurement"]["key"] == "word_ngram_repetition_ratio"
    diagnostic_keys = [item["key"] for item in metric["diagnostics"]]
    assert "word_ngram_repetition_ratio" not in diagnostic_keys
    assert "exact_affected_record_ratio" in diagnostic_keys
    # 全量观测仍保留 6 条，兼容既有消费者
    assert len(metric["observations"]) == 6


def test_different_contracts_same_metric_select_different_measurements(
    tmp_path: Path,
):
    """同一 Metric Definition 下，不同契约选择不同 measurement，报告展示对应主结果。"""

    engine = _fk_enforcing_engine()
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")

    def run_for_measurement(measurement: str):
        with Session(engine) as session:
            contract_version_id = _seed_contract(
                session, store, measurement=measurement
            )
            service = EvaluationRunService(session, store)
            run = service.create(contract_version_id)
            session.commit()
            service.execute(run.run_id)
            return run.run_id

    first_run_id = run_for_measurement("word_ngram_repetition_ratio")
    second_run_id = run_for_measurement("exact_redundant_record_ratio")

    with _client(engine, store) as client:
        first = client.get(
            f"/api/v1/evaluation-runs/{first_run_id}/quality-report"
        ).json()
        second = client.get(
            f"/api/v1/evaluation-runs/{second_run_id}/quality-report"
        ).json()

    assert first["metrics"][0]["measurement"]["key"] == "word_ngram_repetition_ratio"
    assert second["metrics"][0]["measurement"]["key"] == "exact_redundant_record_ratio"
    assert first["contract_version_id"] != second["contract_version_id"]


def _seed_run_with_two_metric_results(session: Session):
    """Insert a terminal run with two metric results and split issues between them."""
    dataset_id, version_id, contract_id, plan_id, run_id = (uuid4() for _ in range(5))
    session.add(DatasetModel(id=dataset_id, name="articles-two-metrics", modality=["text"]))
    session.add(
        DatasetVersionModel(
            id=version_id,
            dataset_id=dataset_id,
            version="v1",
            status="PUBLISHED",
            content_fingerprint=f"sha256:{'0' * 64}",
            artifact_refs={"manifest": {"uri": "datasets/manifest.json"}},
        )
    )
    session.flush()
    session.add(
        QualityContractVersionModel(
            contract_version_id=contract_id,
            contract_id=uuid4(),
            target_dataset_version_id=version_id,
            version_label="v1",
            name="contract-two-metrics",
            status="PUBLISHED",
            fingerprint=f"sha256:{'1' * 64}",
            document={},
            published_at=datetime.now(UTC),
        )
    )
    session.flush()
    session.add(
        PhysicalEvaluationPlanModel(
            physical_plan_id=plan_id,
            dataset_version_id=version_id,
            contract_version_id=contract_id,
            semantic_fingerprint=f"sha256:{'1' * 64}",
            document={},
        )
    )
    session.flush()
    session.add(
        EvaluationRunModel(
            run_id=run_id,
            physical_plan_id=plan_id,
            dataset_version_id=version_id,
            contract_version_id=contract_id,
            status="SUCCEEDED",
            document={},
        )
    )
    session.flush()

    metric_result_ids = []
    for metric_index in range(2):
        stage_run_id = uuid4()
        metric_result_id = uuid4()
        metric_result_ids.append(metric_result_id)
        session.add(
            StageRunModel(
                stage_run_id=stage_run_id,
                run_id=run_id,
                physical_plan_id=plan_id,
                stage_id=f"metric-{metric_index}",
                attempt=1,
                status="SUCCEEDED",
                document={},
            )
        )
        session.flush()
        session.add(
            MetricResultModel(
                metric_result_id=metric_result_id,
                run_id=run_id,
                stage_run_id=stage_run_id,
                dataset_version_id=version_id,
                metric_key="02080103",
                metric_version="1.0.0",
                status="OK",
                document={},
            )
        )
        session.flush()

    for index in range(6):
        metric_index = 0 if index < 4 else 1
        metric_result_id = metric_result_ids[metric_index]
        determination = "CONFIRMED" if index % 2 == 0 else "CANDIDATE"
        session.add(
            QualityIssueModel(
                issue_id=_sequential_issue_uuid(index),
                run_id=run_id,
                metric_result_id=metric_result_id,
                determination=determination,
                severity="WARNING",
                code="EXACT_DUPLICATE_REDUNDANT",
                dedup_key=f"two-metrics:{index}",
                document=_issue_document(
                    index,
                    run_id,
                    version_id,
                    metric_result_id,
                    determination,
                    seq_base=0,
                ),
            )
        )
    session.commit()
    return run_id, metric_result_ids


def test_issue_pagination_applies_metric_result_id_filter(tmp_path: Path):
    """按 metric_result_id 筛选只返回对应指标的质量问题。"""
    engine = _fk_enforcing_engine()
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")

    with Session(engine) as session:
        run_id, metric_result_ids = _seed_run_with_two_metric_results(session)

    first_metric_result_id, second_metric_result_id = metric_result_ids

    with _client(engine, store) as client:
        all_issues = client.get(
            f"/api/v1/evaluation-runs/{run_id}/quality-issues?limit=100"
        )
        first = client.get(
            f"/api/v1/evaluation-runs/{run_id}/quality-issues?metric_result_id={first_metric_result_id}&limit=100"
        )
        second = client.get(
            f"/api/v1/evaluation-runs/{run_id}/quality-issues?metric_result_id={second_metric_result_id}&limit=100"
        )
        combined = client.get(
            f"/api/v1/evaluation-runs/{run_id}/quality-issues?metric_result_id={first_metric_result_id}&metric_result_id={second_metric_result_id}&limit=100"
        )

    assert all_issues.status_code == 200
    assert all_issues.json()["total"] == 6

    assert first.status_code == 200
    assert first.json()["total"] == 4
    assert all(
        issue["source"]["metric_result_id"] == str(first_metric_result_id)
        for issue in first.json()["issues"]
    )

    assert second.status_code == 200
    assert second.json()["total"] == 2
    assert all(
        issue["source"]["metric_result_id"] == str(second_metric_result_id)
        for issue in second.json()["issues"]
    )

    # FastAPI 同名查询参数默认取最后一个值；这里验证只要有一个筛选生效即可。
    assert combined.status_code == 200
    assert combined.json()["total"] in {2, 4}


def test_get_sample_by_id_endpoint_returns_normalized_record(tmp_path: Path):
    """可通过 sample_id 从 normalized artifact 读取单条样本原文。"""
    engine = _fk_enforcing_engine()
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")

    with Session(engine) as session:
        contract_version_id = _seed_contract(session, store)
        service = EvaluationRunService(session, store)
        run = service.create(contract_version_id)
        session.commit()
        service.execute(run.run_id)

    with Session(engine) as session:
        run_row = session.get(EvaluationRunModel, run.run_id)
        version_id = run_row.dataset_version_id

    with _client(engine, store) as client:
        existing = client.get(
            f"/api/v1/dataset-versions/{version_id}/samples/a"
        )
        missing = client.get(
            f"/api/v1/dataset-versions/{version_id}/samples/nonexistent"
        )
        unknown_version = client.get(
            f"/api/v1/dataset-versions/{uuid4()}/samples/a"
        )

    assert existing.status_code == 200
    assert existing.json()["sample_id"] == "a"
    assert existing.json()["dataset_version_id"] == str(version_id)
    assert any(
        content["payload"]["type"] == "inline_text"
        for content in existing.json()["contents"]
    )

    assert missing.status_code == 404
    assert missing.json()["detail"] == "Sample not found"

    assert unknown_version.status_code == 404
    assert unknown_version.json()["detail"] == "DatasetVersion not found"
