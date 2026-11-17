from __future__ import annotations

import hashlib
import html.parser
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from qa_eval.application.evidence.index import (
    ArtifactIndexSpec,
    ArtifactRecordIndexService,
)
from qa_eval.contracts.datasets import InlineTextPayload, SampleRecordV2
from qa_eval.contracts.metrics import EvidenceArtifact, MetricResultV1
from qa_eval.contracts.quality import QualityIssueV1
from qa_eval.domain.ports.artifact_store import ArtifactStore
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.db.models.evaluation_run import MetricResultModel
from qa_eval.infrastructure.db.models.quality_result import QualityIssueModel


class EvidenceQueryService:
    def __init__(self, session: Session, artifact_store: ArtifactStore) -> None:
        self.session = session
        self.artifact_store = artifact_store
        self.indexes = ArtifactRecordIndexService(artifact_store)

    def get_issue(self, issue_id: UUID) -> QualityIssueV1 | None:
        row = self.session.get(QualityIssueModel, issue_id)
        return None if row is None else QualityIssueV1.model_validate(row.document)

    def resolve_evidence(
        self, evidence_id: UUID
    ) -> tuple[EvidenceArtifact, MetricResultV1] | None:
        rows = self.session.scalars(select(MetricResultModel)).all()
        for row in rows:
            result = MetricResultV1.model_validate(row.document)
            for evidence in result.evidence:
                if evidence.evidence_id == evidence_id:
                    return evidence, result
        return None

    def evidence_metadata(self, evidence_id: UUID) -> dict[str, Any] | None:
        resolved = self.resolve_evidence(evidence_id)
        if resolved is None:
            return None
        evidence, result = resolved
        spec = self._evidence_spec(evidence)
        return {
            "evidence_id": evidence.evidence_id,
            "kind": evidence.kind,
            "schema_ref": evidence.schema_ref,
            "dataset_version_id": result.dataset_version_id,
            "metric_result_id": result.metric_result_id,
            "subject_scope": evidence.subject_scope,
            "summary": evidence.summary.model_dump(mode="json"),
            "redaction": evidence.redaction.model_dump(mode="json"),
            "index": self.indexes.status(spec),
        }

    def build_evidence_index(
        self, evidence_id: UUID, *, force: bool = False
    ) -> dict[str, Any] | None:
        resolved = self.resolve_evidence(evidence_id)
        if resolved is None:
            return None
        evidence, _ = resolved
        return self.indexes.build(self._evidence_spec(evidence), force=force)

    def list_evidence_rows(
        self,
        evidence_id: UUID,
        *,
        limit: int,
        cursor: str | None = None,
        row_id: str | None = None,
        sample_id: str | None = None,
        content_id: str | None = None,
        fact_code: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None] | None:
        resolved = self.resolve_evidence(evidence_id)
        if resolved is None:
            return None
        evidence, _ = resolved
        return self.indexes.query(
            self._evidence_spec(evidence),
            limit=limit,
            cursor=cursor,
            row_id=row_id,
            sample_id=sample_id,
            content_id=content_id,
            fact_code=fact_code,
        )

    def issue_evidence_rows(self, issue_id: UUID) -> dict[str, Any] | None:
        issue = self.get_issue(issue_id)
        if issue is None:
            return None
        rows: list[dict[str, Any]] = []
        for reference in issue.evidence:
            row_id = reference.locator.get("row_id")
            if not isinstance(row_id, str):
                continue
            resolved = self.list_evidence_rows(
                reference.evidence_id, limit=1, row_id=row_id
            )
            if resolved is None:
                continue
            matched, _ = resolved
            rows.extend(matched)
        return {"issue": issue.model_dump(mode="json"), "rows": rows}

    def get_sample_by_id(
        self, dataset_version_id: UUID, sample_id: str
    ) -> SampleRecordV2 | None:
        version = self.session.get(DatasetVersionModel, dataset_version_id)
        if version is None:
            raise ValueError("DatasetVersion not found")
        normalized_ref = (version.artifact_refs or {}).get("normalized")
        if normalized_ref is None:
            raise ValueError("DatasetVersion has no normalized artifact")
        spec = self._sample_spec(normalized_ref)
        if self.indexes.status(spec)["status"] != "READY":
            self.indexes.build(spec)
        record = self.indexes.get(spec, sample_id)
        if record is None:
            return None
        sample = SampleRecordV2.model_validate(record)
        if sample.dataset_version_id != dataset_version_id:
            raise ValueError("indexed SampleRecord belongs to another DatasetVersion")
        return sample

    def issue_locations(self, issue_id: UUID) -> dict[str, Any] | None:
        bundle = self.issue_evidence_rows(issue_id)
        if bundle is None:
            return None
        issue = QualityIssueV1.model_validate(bundle["issue"])
        locations = []
        for row in bundle["rows"]:
            sample_id = row.get("sample_id")
            content_id = row.get("content_id")
            if not isinstance(sample_id, str) or not isinstance(content_id, str):
                locations.append(
                    {"precision": "RECORD_ONLY", "reason": "EVIDENCE_HAS_NO_CONTENT"}
                )
                continue
            sample = self.get_sample_by_id(issue.dataset_version_id, sample_id)
            if sample is None:
                locations.append(
                    {
                        "sample_id": sample_id,
                        "content_id": content_id,
                        "precision": "UNAVAILABLE",
                        "reason": "SAMPLE_NOT_FOUND",
                    }
                )
                continue
            content = next(
                (item for item in sample.contents if item.content_id == content_id),
                None,
            )
            if content is None or not isinstance(content.payload, InlineTextPayload):
                locations.append(
                    {
                        "sample_id": sample_id,
                        "content_id": content_id,
                        "precision": "RECORD_ONLY",
                        "reason": "CONTENT_NOT_FOUND",
                    }
                )
                continue
            text = content.payload.text
            coordinate = row.get("coordinate_space")
            view = "content"
            if coordinate == "VISIBLE_TEXT_UNICODE_CODE_POINT":
                parser = _VisibleText()
                parser.feed(text)
                text = parser.text()
                view = "visible"
            expected_version = row.get("text_version")
            actual_version = f"sha256:{hashlib.sha256(text.encode()).hexdigest()}"
            start, end = row.get("start"), row.get("end")
            reason = None
            if expected_version and expected_version != actual_version:
                reason = "TEXT_VERSION_MISMATCH"
            elif not isinstance(start, int) or not isinstance(end, int):
                reason = "SPAN_MISSING"
            elif start < 0 or end <= start or end > len(text):
                reason = "SPAN_OUT_OF_RANGE"
            locations.append(
                {
                    "sample_id": sample_id,
                    "content_id": content_id,
                    "field_path": row.get("field_path"),
                    "view": view,
                    "text": text,
                    "highlight": None
                    if reason
                    else {
                        "start": start,
                        "end": end,
                        "unit": row.get("offset_unit", "UNICODE_CODE_POINT"),
                    },
                    "masked_excerpt": row.get("masked_excerpt"),
                    "precision": "FIELD_ONLY"
                    if reason
                    else row.get("mapping_precision"),
                    "reason": reason,
                }
            )
        return {"issue": issue.model_dump(mode="json"), "locations": locations}

    @staticmethod
    def _evidence_spec(evidence: EvidenceArtifact) -> ArtifactIndexSpec:
        return ArtifactIndexSpec(
            source_uri=evidence.artifact.uri,
            source_checksum=f"sha256:{evidence.artifact.checksum.value}",
            key_field="row_id",
            index_kind="EVIDENCE",
        )

    @staticmethod
    def _sample_spec(reference: dict[str, Any]) -> ArtifactIndexSpec:
        checksum = reference.get("sha256")
        if checksum is None and isinstance(reference.get("checksum"), dict):
            checksum = reference["checksum"].get("value")
        if not isinstance(checksum, str) or not checksum:
            raise ValueError("normalized artifact checksum is missing")
        if not checksum.startswith("sha256:"):
            checksum = f"sha256:{checksum}"
        return ArtifactIndexSpec(
            source_uri=reference["uri"],
            source_checksum=checksum,
            key_field="sample_id",
            index_kind="SAMPLE",
        )


class _VisibleText(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.hidden += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)

    def text(self) -> str:
        return "".join(self.parts)
