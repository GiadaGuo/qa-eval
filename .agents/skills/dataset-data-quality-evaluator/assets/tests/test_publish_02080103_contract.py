from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "publish_02080103_contract.py"
SPEC = importlib.util.spec_from_file_location("publish_02080103_contract", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

REQUEST_PATH = (
    Path(__file__).parents[1] / "contracts" / "02080103-mvp-baseline.request.json"
)
REQUEST_SCHEMA_PATH = (
    Path(__file__).parents[1]
    / "schemas"
    / "02080103-contract-publish-request-1.0.schema.json"
)


def test_mvp_request_publishes_canonical_immutable_contract():
    payload = json.loads(REQUEST_PATH.read_text())
    request = MODULE.ContractPublishRequest.model_validate(payload)
    contract = MODULE.publish(
        request,
        published_at=datetime(2026, 10, 27, tzinfo=UTC),
    )

    assert contract.status == "PUBLISHED"
    assert contract.items[0].rule.threshold == 0.01
    assert contract.items[0].metric.measurement == "exact_redundant_record_ratio"
    assert contract.contract_id.version == 7
    assert contract.contract_version_id.version == 7
    assert contract.fingerprint.startswith("sha256:")
    assert len(contract.fingerprint) == 71


def test_new_version_can_reuse_logical_contract_id():
    payload = json.loads(REQUEST_PATH.read_text())
    contract_id = UUID("019a2a10-0000-7000-8000-000000000201")
    payload["contract_id"] = str(contract_id)
    contract = MODULE.publish(MODULE.ContractPublishRequest.model_validate(payload))
    assert contract.contract_id == contract_id
    assert contract.contract_version_id != contract_id


def test_semantic_fingerprint_changes_with_user_threshold_only():
    payload = json.loads(REQUEST_PATH.read_text())
    first = MODULE.ContractPublishRequest.model_validate(payload)
    payload["items"][0]["rule"]["threshold"] = 0.005
    second = MODULE.ContractPublishRequest.model_validate(payload)
    assert MODULE.semantic_fingerprint(first) != MODULE.semantic_fingerprint(second)


def test_request_rejects_unsupported_metric_or_measurement():
    payload = json.loads(REQUEST_PATH.read_text())
    payload["items"][0]["metric"]["key"] = "02010101"
    with pytest.raises(ValidationError):
        MODULE.ContractPublishRequest.model_validate(payload)


def test_checked_in_request_schema_matches_pydantic_and_validates_preset():
    assert REQUEST_SCHEMA_PATH.read_text() == MODULE.render_request_schema()
    schema = json.loads(REQUEST_SCHEMA_PATH.read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(json.loads(REQUEST_PATH.read_text()))
