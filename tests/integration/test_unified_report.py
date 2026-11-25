from dataclasses import replace
from datetime import UTC, datetime
from fractions import Fraction
from uuid import uuid4

import pytest
from test_scoring_consumer import native, persisted  # noqa: F401

from qa_eval.application.evaluation.scoring import Score
from qa_eval.application.evaluation.unified_report import (
    DOCUMENT_KEYS,
    DocumentMapping,
    project_document,
    report_dimensions,
    standard_verdict,
)
from qa_eval.application.services.unified_finalization import (
    FinalizationBlocked,
    UnifiedFinalization,
)
from qa_eval.contracts.document import DocumentQualityReportV1


@pytest.mark.parametrize(
    ("score", "verdict"),
    [
        (Score("OK", Fraction(90)), "PASS"),
        (Score("OK", Fraction(899999, 10000)), "FAIL"),
        (Score("NA"), "INDETERMINATE"),
        (Score("MISSING", blocking=True), "INDETERMINATE"),
        (Score("PARTIAL", Fraction(95), ("UNKNOWN_RECORDS",)), "PASS"),
        (
            Score("PARTIAL", Fraction(95), ("COVERAGE_THRESHOLD_UNCONFIRMED",)),
            "INDETERMINATE",
        ),
        (Score("OK", Fraction(95), ("COVERAGE_BELOW_MINIMUM",)), "FAIL"),
    ],
)
def test_standard_verdict(score, verdict):
    assert standard_verdict(score) == verdict


def test_document_projection():
    doc = DocumentQualityReportV1.model_validate(
        {
            "report_id": uuid4(),
            "task_id": uuid4(),
            "dataset_id": uuid4(),
            "document_version_id": uuid4(),
            "document": {
                "version": "v1",
                "filename": "card.md",
                "sha256": "0" * 64,
                "line_count": 1,
                "size_bytes": 1,
            },
            "evaluation_standard": {
                "name": "fixture",
                "version": "1",
                "sha256": "0" * 64,
            },
            "evaluator": {"version": "1", "model": "fixture", "prompt_version": "1"},
            "execution_status": "SUCCEEDED",
            "generated_at": datetime.now(UTC),
            "overall": {"score": 90, "scoring_policy": "fixture"},
            "metrics": [
                {
                    "metric": {"key": key, "version": "1"},
                    "metric_name": key,
                    "status": "OK",
                    "observations": [
                        {"key": "score", "value_type": "NUMBER", "value": 90}
                    ],
                    "items": [
                        {
                            "item_code": key,
                            "item_name": key,
                            "status": "SATISFIED",
                            "inspected_scope": "all",
                            "reason": "fixture",
                            "suggestion": "fixture",
                            "fixability": "AUTO_FIXABLE",
                            "document_evidence": [
                                {
                                    "line_start": 1,
                                    "line_end": 1,
                                    "excerpt": "sensitive to copying",
                                },
                            ],
                        }
                    ],
                }
                for key in DOCUMENT_KEYS
            ],
        }
    )
    mapping = DocumentMapping(
        version="1",
        report_id=doc.report_id,
        document_version_id=doc.document_version_id,
        dataset_id=doc.dataset_id,
        report_fingerprint="fixture",
        rubric_version="1",
        rubric_sha256="0" * 64,
        evaluator_version="1",
        observations=dict.fromkeys(DOCUMENT_KEYS, "score"),
    )
    dimension = project_document(doc, mapping)
    assert dimension.score == "90" and dimension.verdict == "PASS"
    assert dimension.metrics[0].evidence_paths == (
        "/metrics/0/items/0/document_evidence",
    )
    assert "sensitive to copying" not in dimension.model_dump_json()
    assert project_document(doc, None).status == "MISSING"
    na = mapping.model_copy(
        update={"approved_na": dict.fromkeys(DOCUMENT_KEYS, "approved")}
    )
    assert project_document(doc, na).verdict == "INDETERMINATE"


def test_p2_fixture_cannot_publish(native):  # noqa: F811
    consumer, fixture, selection, *_ = native
    inputs = consumer.read(fixture.run_id, selected_attempts=selection, fixture=fixture)
    assert report_dimensions(inputs, None, None)[1].status == "MISSING"
    finalizer = UnifiedFinalization(consumer.session)
    with pytest.raises(FinalizationBlocked, match="FULL"):
        finalizer.finalize(inputs, mode="FULL")
    with pytest.raises(FinalizationBlocked, match="UNAVAILABLE"):
        finalizer.finalize(inputs)


def test_checkpoint_resume_and_atomic_publish(native, monkeypatch):  # noqa: F811
    from qa_eval.application.services.quality_result_service import QualityResultService
    from qa_eval.application.services.scoring_consumer import ScoringInputs

    consumer, fixture, selection, *_ = native
    inputs = consumer.read(fixture.run_id, selected_attempts=selection, fixture=fixture)
    inputs = replace(
        inputs, nodes=tuple(node for node in inputs.nodes if node.metric.key == "0202")
    )
    consumer.session.rollback()

    # Exercise the internal publication mechanics in isolation. The unpatched
    # production gate is independently asserted above.
    monkeypatch.setattr(ScoringInputs, "formal_ready", property(lambda self: True))

    class Backend:
        verified = 0
        materialized = 0

        def verify_artifacts(self, inputs):
            self.verified += 1

        def materialize_rules_and_issues(self, inputs):
            self.materialized += 1
            if self.materialized == 1:
                raise RuntimeError("injected failure")

    backend = Backend()
    finalizer = UnifiedFinalization(consumer.session, backend)
    with pytest.raises(RuntimeError, match="injected"):
        finalizer.finalize(inputs)
    legacy = QualityResultService(consumer.session, consumer.evidence.artifact_store)
    assert legacy.get_report(inputs.run.run_id) is None
    with pytest.raises(ValueError, match="has not published"):
        legacy.finalize(inputs.run.run_id)
    consumer.session.rollback()
    report = finalizer.finalize(inputs)
    assert (backend.verified, backend.materialized) == (1, 2)
    assert report.overall_verdict == "INDETERMINATE"
    assert finalizer.finalize(inputs) == report
    assert legacy.get_report(inputs.run.run_id) == report
