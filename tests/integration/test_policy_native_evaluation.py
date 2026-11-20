import json
import os
from pathlib import Path

import pytest
from sqlalchemy.orm import Session
from test_policy_effectiveness_run import (
    EXPECTED,
    LEGACY,
    OBSERVE_POLICY,
    ROOT,
    _select_review_records,
    seed_review_subset,
)
from test_quality_result_api import _client, _fk_enforcing_engine

from qa_eval.application.evaluation.rule_evaluator import evaluate_native_item
from qa_eval.application.services.evaluation_run_service import EvaluationRunService
from qa_eval.application.services.quality_result_service import QualityResultService
from qa_eval.contracts.quality import QualityContractVersionV1
from qa_eval.infrastructure.db.base import Base
from qa_eval.infrastructure.storage.local_file_system_artifact_store import (
    LocalFileSystemArtifactStore,
)
from qa_eval.infrastructure.uuid7 import uuid7


def evaluation_item(key, threshold):
    return {
        **OBSERVE_POLICY,
        "key": key,
        "schema_version": "1.1",
        "strategy": "EVALUATE",
        "statistic_ref": {
            "contract_item_key": key,
            "output_slot_id": key,
            "calculator_version": "1.0.0",
            "statistics_key": "expired_evidence_rate",
        },
        "rule": {"operator": "LTE", "threshold": threshold},
        "severity": "ERROR",
    }


def test_policy_formal_evaluation(tmp_path):
    sample_file = os.environ.get("QA_EVAL_POLICY_REVIEW_RECORDS")
    manifest_file = os.environ.get("QA_EVAL_POLICY_REVIEW_MANIFEST")
    if not sample_file or not manifest_file:
        pytest.skip("provide published policy SampleRecords and manifest")
    review_path = ROOT / "docs/development/0205-effectiveness-audit/phase1_review.jsonl"
    reviewed = {
        row["record_id"]: row
        for row in map(json.loads, review_path.read_text().splitlines())
    }
    samples = tmp_path / "reviewed.jsonl"
    _select_review_records(Path(sample_file), samples, reviewed)
    engine = _fk_enforcing_engine()
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
    with Session(engine) as session:
        version = seed_review_subset(session, store, samples, Path(manifest_file))
        session.commit()

    with _client(engine, store) as client:
        path = "/api/v1/quality-contract-versions/multi"
        common = {
            "name": "Phase 2C",
            "version_label": "1.0",
            "dataset_version_id": str(version),
        }
        formal = evaluation_item("policy-rule", 0.25)
        for field, bad in (
            ("statistics_key", "traceable_record_ratio"),
            ("calculator_version", "2.0.0"),
            ("contract_item_key", "wrong"),
            ("output_slot_id", "other"),
        ):
            invalid = {
                **formal,
                "statistic_ref": {**formal["statistic_ref"], field: bad},
            }
            assert (
                client.post(
                    path, json={**common, "observation_items": [invalid]}
                ).status_code
                == 422
            )
        for bad in ("0.5", True, 1.2):
            invalid = {**formal, "rule": {"operator": "LTE", "threshold": bad}}
            assert (
                client.post(
                    path, json={**common, "observation_items": [invalid]}
                ).status_code
                == 422
            )
        definitions = [
            ([LEGACY], [OBSERVE_POLICY]),
            ([LEGACY], [formal, OBSERVE_POLICY]),
            ([], [evaluation_item("policy-rule", 0.5)]),
            (
                [],
                [
                    {
                        **formal,
                        "parameters": {
                            **formal["parameters"],
                            "reference_date": "2026-11-19",
                        },
                    }
                ],
            ),
        ]
        contracts = []
        for items, observations in definitions:
            response = client.post(
                path, json={**common, "items": items, "observation_items": observations}
            )
            assert response.status_code == 201, response.text
            contracts.append(
                QualityContractVersionV1.model_validate(response.json()["contract"])
            )
        runs = []
        with Session(engine) as session:
            services = EvaluationRunService(session, store)
            for contract in contracts:
                run = services.create(contract.contract_version_id)
                session.commit()
                completed = services.execute(run.run_id)
                assert completed.status == "SUCCEEDED", completed.error
                runs.append(completed)
            _, mixed, _, repeat = runs
            filtered = services.list_calculator_results(mixed.run_id, "policy-rule")
            [result] = filtered
            [again] = services.list_calculator_results(repeat.run_id)
            assert result.statistics == again.statistics == EXPECTED
            assert (
                result.decision_artifact_refs[0].artifact.checksum
                == again.decision_artifact_refs[0].artifact.checksum
            )
            service = QualityResultService(session, store)
            assert (
                service.finalize(mixed.run_id).report_id
                == service.finalize(mixed.run_id).report_id
            )
            item = contracts[1].observation_items[0]
            zeros = dict.fromkeys(EXPECTED, 0)
            zeros.update(expired_evidence_rate=None, decidable_coverage=None)
            zero_result = type(result).model_validate(
                {
                    **result.model_dump(mode="json"),
                    "status": "NOT_APPLICABLE",
                    "statistics": zeros,
                }
            )
            zero = evaluate_native_item(
                contracts[1],
                item,
                mixed,
                zero_result,
                id_factory=uuid7,
                evaluated_at=mixed.created_at,
            )
            assert (
                zero.status,
                zero.reason_code,
                zero.numerator,
                zero.denominator,
                zero.metric_value,
            ) == (
                "NOT_EVALUATED",
                "ZERO_DENOMINATOR",
                0,
                0,
                None,
            )
            missing = evaluate_native_item(
                contracts[1],
                item,
                mixed,
                None,
                id_factory=uuid7,
                evaluated_at=mixed.created_at,
            )
            assert missing.status == "NOT_EVALUATED"
            with pytest.raises(ValueError, match="published native rule"):
                evaluate_native_item(
                    contracts[1],
                    item,
                    mixed,
                    services.list_calculator_results(
                        mixed.run_id, OBSERVE_POLICY["key"]
                    )[0],
                    id_factory=uuid7,
                    evaluated_at=mixed.created_at,
                )

        base, evaluated, success, repeated = [
            f"/api/v1/evaluation-runs/{run.run_id}" for run in runs
        ]
        assert client.get(f"{base}/quality-issues").json()["total"] == 0
        report = client.get(f"{evaluated}/quality-report").json()
        original = client.get(f"{base}/quality-report").json()
        assert report["gate_status"] == "FAIL"
        assert report["scoring"] == original["scoring"]
        assert report["coverage"] == original["coverage"]
        scores = [
            client.post(f"{prefix}/quality-report/operational-scoring/rebuild")
            for prefix in (base, evaluated)
        ]
        assert all(response.status_code == 200 for response in scores)
        for key in ("overall_estimated_score", "estimated_dimension_coverage", "radar"):
            assert scores[0].json()[key] == scores[1].json()[key]
        assert len(report["metrics"]) == 1
        [section] = report["native_evaluations"]
        assert (
            section["affected_record_count"],
            section["issue_count"],
            section["evidence_count"],
        ) == (6, 6, 6)
        rule = section["evaluation"]
        assert (
            rule["status"],
            rule["numerator"],
            rule["denominator"],
            rule["metric_value"],
        ) == ("FAIL", 6, 14, 6 / 14)
        assert len(client.get(f"{evaluated}/rule-evaluations").json()) == 2
        assert client.get(f"{success}/quality-report").json()["gate_status"] == "PASS"
        assert (
            client.get(f"{repeated}/quality-report").json()["native_evaluations"][0][
                "evaluation"
            ]["metric_value"]
            == 6 / 14
        )

        issues = client.get(
            f"{evaluated}/quality-issues",
            params={
                "calculator_result_id": str(result.calculator_result_id),
                "determination": "SUSPECTED",
            },
        ).json()
        assert issues["total"] == 6
        assert {i["subject"]["sample_id"] for i in issues["issues"]} == {
            key
            for key, row in reviewed.items()
            if row["decision"] == "EXPIRED_EVIDENCED"
        }
        for issue in issues["issues"]:
            assert issue["source"]["rule_evaluation_id"] == rule["rule_evaluation_id"]
            assert issue["source"]["decision_status"] == "EXPIRED_EVIDENCED"
            assert issue["code"] == "POLICY_SELF_EXPIRATION_EVIDENCED"
            detail = f"/api/v1/quality-issues/{issue['issue_id']}"
            evidence = client.get(f"{detail}/evidence")
            assert evidence.status_code == 200, evidence.text
            assert (
                evidence.json()["rows"][0]["sample_id"] == issue["subject"]["sample_id"]
            )
            locations = client.get(f"{detail}/locations")
            assert locations.status_code == 200, locations.text
            assert all(
                loc["highlight"] and loc["sample_id"] == issue["subject"]["sample_id"]
                for loc in locations.json()["locations"]
            )
        assert client.delete(evaluated).status_code == 204
        assert client.get(f"{evaluated}/quality-issues").json()["total"] == 0


def test_zero_denominator_persists_not_evaluated(tmp_path, monkeypatch):
    from test_native_calculator_results import (
        OBSERVATION,
        ObservationFixture,
        register_fixture,
    )
    from test_quality_result_api import _seed_contract

    from qa_eval.application.services.quality_contract_service import (
        QualityContractMultiPublishRequest,
        QualityContractService,
    )
    from qa_eval.calculators.protocol import EvidenceOutput, NativeCalculatorOutput
    from qa_eval.contracts.calculator_results import DECISION_SCHEMA_REF
    from qa_eval.infrastructure.db.models.quality_contract import (
        QualityContractVersionModel,
    )

    def no_eligible_records(self, context, parameters):
        context.output_dir.mkdir()
        path = context.output_dir / "decisions.jsonl"
        path.touch()
        return NativeCalculatorOutput(
            {"input_count": 0, "observed": 0, "other": 0, "rate": None, "states": {}},
            status="NOT_APPLICABLE",
            decisions=EvidenceOutput(path, "DECISIONS", DECISION_SCHEMA_REF, 0),
        )

    monkeypatch.setattr(ObservationFixture, "execute", no_eligible_records)
    engine = _fk_enforcing_engine()
    Base.metadata.create_all(engine)
    store = LocalFileSystemArtifactStore(tmp_path / "artifacts")
    with Session(engine) as session:
        contract_id = _seed_contract(session, store)
        version = session.get(
            QualityContractVersionModel, contract_id
        ).target_dataset_version_id
        register_fixture(session, tmp_path, monkeypatch)
        formal = evaluation_item("zero", 0.5)
        formal["calculator_key"] = OBSERVATION["calculator_key"]
        formal["parameters"] = {}
        formal["statistic_ref"]["statistics_key"] = "rate"
        request = QualityContractMultiPublishRequest.model_validate(
            {
                "name": "zero ratio",
                "version_label": "1.0",
                "dataset_version_id": version,
                "observation_items": [formal],
            }
        )
        contract = QualityContractService(session).publish_multi(request)
        service = EvaluationRunService(session, store)
        run = service.create(contract.contract.contract_version_id)
        session.commit()
        completed = service.execute(run.run_id)
        assert completed.status == "SUCCEEDED", completed.error
    with _client(engine, store) as client:
        prefix = f"/api/v1/evaluation-runs/{run.run_id}"
        rule = client.get(f"{prefix}/rule-evaluations").json()[0]
        assert (rule["status"], rule["reason_code"], rule["metric_value"]) == (
            "NOT_EVALUATED",
            "ZERO_DENOMINATOR",
            None,
        )
        assert (rule["numerator"], rule["denominator"]) == (0, 0)
        assert client.get(f"{prefix}/quality-report").json()["gate_status"] == "UNKNOWN"
        assert client.get(f"{prefix}/quality-issues").json()["total"] == 0
