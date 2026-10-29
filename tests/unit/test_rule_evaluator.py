from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from qa_eval.application.evaluation.rule_evaluator import evaluate_contract_item
from qa_eval.contracts.metrics import MetricResultV1
from qa_eval.contracts.quality import QualityContractVersionV1

NOW = datetime(2026, 10, 27, tzinfo=UTC)


def _contract(threshold=0.01, on_missing="UNKNOWN") -> QualityContractVersionV1:
    return QualityContractVersionV1.model_validate(
        {
            "schema_version": "1.0",
            "contract_id": uuid4(),
            "contract_version_id": uuid4(),
            "version_label": "1.0.0",
            "name": "user-contract",
            "status": "PUBLISHED",
            "applicability": {"modalities": ["text"], "task_types": []},
            "items": [
                {
                    "key": "exact-gate",
                    "metric": {
                        "key": "02080103",
                        "version": "1.0.0",
                        "measurement": "exact_redundant_record_ratio",
                    },
                    "scope": "DATASET",
                    "calculation": {},
                    "rule": {"operator": "LTE", "threshold": threshold},
                    "severity": "ERROR",
                    "on_missing": on_missing,
                }
            ],
            "gate": {
                "unknown_policy": "FAIL",
                "fail_on_severities": ["ERROR", "CRITICAL"],
            },
            "fingerprint": f"sha256:{'a' * 64}",
            "published_at": NOW,
        }
    )


def _result(status="OK") -> MetricResultV1:
    observations = (
        [
            {
                "key": "exact_redundant_record_ratio",
                "value": 0.003,
                "value_type": "NUMBER",
                "unit": "ratio",
                "numerator": 3,
                "denominator": 1000,
            }
        ]
        if status == "OK"
        else []
    )
    return MetricResultV1.model_validate(
        {
            "schema_version": "1.0",
            "metric_result_id": uuid4(),
            "run_id": uuid4(),
            "stage_run_id": uuid4(),
            "dataset_version_id": uuid4(),
            "metric": {"key": "02080103", "version": "1.0.0"},
            "subject": {"scope": "DATASET", "id": "dataset"},
            "status": status,
            "observations": observations,
            "evidence": [],
            "created_at": NOW,
        }
    )


def _fixed_id() -> UUID:
    return UUID("019a2a10-0000-7000-8000-000000000999")


def test_rule_evaluator_uses_user_threshold_and_copies_observed_value():
    contract = _contract(threshold=0.01)
    result = _result()
    evaluation = evaluate_contract_item(
        contract,
        contract.items[0],
        result,
        id_factory=_fixed_id,
        evaluated_at=NOW,
    )
    assert evaluation.status == "PASS"
    assert evaluation.rule.expected == 0.01
    assert evaluation.observed is not None
    assert evaluation.observed.value == 0.003


def test_rule_evaluator_fails_against_stricter_user_threshold():
    contract = _contract(threshold=0.001)
    evaluation = evaluate_contract_item(
        contract,
        contract.items[0],
        _result(),
        id_factory=_fixed_id,
        evaluated_at=NOW,
    )
    assert evaluation.status == "FAIL"
    assert evaluation.reason_code == "THRESHOLD_VIOLATED"


def test_observe_only_preserves_value_without_pass_or_fail():
    data = _contract().model_dump(mode="python")
    data["items"][0]["rule"] = {"strategy": "OBSERVE_ONLY"}
    contract = QualityContractVersionV1.model_validate(data)
    evaluation = evaluate_contract_item(
        contract,
        contract.items[0],
        _result(),
        id_factory=_fixed_id,
        evaluated_at=NOW,
    )
    assert evaluation.status == "SKIPPED"
    assert evaluation.reason_code == "OBSERVED_ONLY"
    assert evaluation.observed is not None
    assert evaluation.observed.value == 0.003


@pytest.mark.parametrize(
    ("result_status", "expected_status", "reason"),
    [
        ("MISSING", "UNKNOWN", "METRIC_MISSING"),
        ("ERROR", "UNKNOWN", "METRIC_ERROR"),
        ("NOT_APPLICABLE", "SKIPPED", "NOT_APPLICABLE"),
    ],
)
def test_non_ok_metric_result_never_becomes_rule_fail(
    result_status, expected_status, reason
):
    contract = _contract()
    evaluation = evaluate_contract_item(
        contract,
        contract.items[0],
        _result(result_status),
        id_factory=_fixed_id,
        evaluated_at=NOW,
    )
    assert evaluation.status == expected_status
    assert evaluation.reason_code == reason
    assert evaluation.observed is None


def test_rule_evaluator_rejects_wrong_measurement_type():
    contract_data = _contract().model_dump(mode="python")
    contract_data["items"][0]["rule"] = {"operator": "LT", "threshold": 1}
    contract = QualityContractVersionV1.model_validate(contract_data)
    result_data = _result().model_dump(mode="python")
    result_data["observations"][0]["value"] = "not-a-number"
    result_data["observations"][0]["value_type"] = "STRING"
    result = MetricResultV1.model_validate(result_data)
    with pytest.raises(ValueError, match="incompatible"):
        evaluate_contract_item(
            contract,
            contract.items[0],
            result,
            id_factory=_fixed_id,
            evaluated_at=NOW,
        )
