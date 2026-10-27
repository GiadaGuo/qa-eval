"""Publish a user-authored 02080103 request as a canonical contract version."""

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from qa_eval.application.identifiers import uuid7
from qa_eval.contracts.base import WireModel
from qa_eval.contracts.quality import (
    ContractApplicability,
    ContractCalculation,
    ContractGate,
    QualityContractVersionV1,
    Severity,
    ThresholdRule,
)

Measurement = Literal[
    "exact_affected_record_ratio",
    "exact_redundant_record_ratio",
    "char_ngram_repetition_ratio",
    "word_ngram_repetition_ratio",
    "near_duplicate_candidate_record_count",
    "paragraph_affected_ratio",
]


class MvpMetricSelector(WireModel):
    key: Literal["02080103"]
    version: Literal["1.0.0"]
    measurement: Measurement


class MvpContractItem(WireModel):
    key: str = Field(min_length=1)
    metric: MvpMetricSelector
    scope: Literal["DATASET"]
    calculation: ContractCalculation = Field(default_factory=ContractCalculation)
    rule: ThresholdRule
    severity: Severity
    on_missing: Literal["UNKNOWN", "SKIPPED"]


class ContractPublishRequest(WireModel):
    schema_version: Literal["1.0"] = "1.0"
    contract_id: UUID | None = None
    version_label: str = Field(min_length=1)
    name: str = Field(min_length=1)
    applicability: ContractApplicability
    items: list[MvpContractItem] = Field(min_length=1)
    gate: ContractGate

    @model_validator(mode="after")
    def validate_02080103_scope(self) -> "ContractPublishRequest":
        keys = [item.key for item in self.items]
        if len(keys) != len(set(keys)):
            raise ValueError("contract item keys must be unique")
        return self


def _semantic_payload(request: ContractPublishRequest) -> dict:
    applicability = request.applicability.model_dump(mode="json")
    applicability["modalities"] = sorted(set(applicability["modalities"]))
    applicability["task_types"] = sorted(set(applicability["task_types"]))
    gate = request.gate.model_dump(mode="json")
    gate["fail_on_severities"] = sorted(set(gate["fail_on_severities"]))
    return {
        "schema_version": "1.0",
        "applicability": applicability,
        "items": sorted(
            [item.model_dump(mode="json") for item in request.items],
            key=lambda item: item["key"],
        ),
        "gate": gate,
    }


def semantic_fingerprint(request: ContractPublishRequest) -> str:
    encoded = json.dumps(
        _semantic_payload(request),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def publish(
    request: ContractPublishRequest,
    *,
    published_at: datetime | None = None,
) -> QualityContractVersionV1:
    return QualityContractVersionV1.model_validate(
        {
            "schema_version": "1.0",
            "contract_id": request.contract_id or uuid7(),
            "contract_version_id": uuid7(),
            "version_label": request.version_label,
            "name": request.name,
            "status": "PUBLISHED",
            "applicability": request.applicability.model_dump(mode="json"),
            "items": [item.model_dump(mode="json") for item in request.items],
            "gate": request.gate.model_dump(mode="json"),
            "fingerprint": semantic_fingerprint(request),
            "published_at": published_at or datetime.now(UTC),
        }
    )


def render_request_schema() -> str:
    schema = ContractPublishRequest.model_json_schema(mode="validation")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = (
        "qa-eval-skill://dataset-data-quality-evaluator/"
        "02080103-contract-publish-request/1.0"
    )
    return json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("request", type=Path, nargs="?")
    parser.add_argument("output", type=Path, nargs="?")
    parser.add_argument("--write-request-schema", type=Path)
    args = parser.parse_args(argv)
    if args.write_request_schema:
        args.write_request_schema.parent.mkdir(parents=True, exist_ok=True)
        args.write_request_schema.write_text(render_request_schema(), encoding="utf-8")
        return 0
    if args.request is None or args.output is None:
        parser.error(
            "request and output are required unless --write-request-schema is used"
        )
    if args.output.exists():
        raise FileExistsError(f"output already exists: {args.output}")
    request = ContractPublishRequest.model_validate_json(args.request.read_text())
    contract = publish(request)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            contract.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "contract_id": str(contract.contract_id),
                "contract_version_id": str(contract.contract_version_id),
                "fingerprint": contract.fingerprint,
                "output": str(args.output),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
