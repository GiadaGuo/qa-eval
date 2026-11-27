"""Personal work checkpoints backed by the browser account database."""

import json
from datetime import UTC, datetime
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from apps.api.routes.web_session import connect, current_user
from qa_eval.infrastructure.db.models.agent import AgentSessionModel as Agent
from qa_eval.infrastructure.db.models.dataset import DatasetModel as Dataset
from qa_eval.infrastructure.db.models.dataset_version import (
    DatasetVersionModel as Version,
)
from qa_eval.infrastructure.db.models.document_quality_report import (
    DocumentQualityReportModel as DocumentReport,
)
from qa_eval.infrastructure.db.models.document_version import (
    DocumentVersionModel as Document,
)
from qa_eval.infrastructure.db.models.evaluation_run import EvaluationRunModel as Run
from qa_eval.infrastructure.db.models.quality_contract import (
    QualityContractVersionModel as Contract,
)
from qa_eval.infrastructure.db.models.quality_result import QualityIssueModel as Issue
from qa_eval.infrastructure.db.models.quality_result import QualityReportModel as Report
from qa_eval.infrastructure.db.session import get_db

router = APIRouter(prefix="/api/web/workspace", tags=["Personal workspace"])
Kind = Literal[
    "dataset",
    "version",
    "mapping",
    "agent",
    "manual",
    "single",
    "document",
    "run",
    "report",
    "document-report",
    "issue",
    "contract",
    "ingestion",
    "operator",
]


class Checkpoint(BaseModel):
    kind: Kind
    object_id: UUID


def metadata(session, kind, ids):
    """Read only display columns, using one query for a batch of each kind."""
    if kind == "dataset":
        stmt = select(Dataset.id, Dataset.name).where(Dataset.id.in_(ids))
        return {
            str(key): {"name": name, "dataset_id": key}
            for key, name in session.execute(stmt)
        }
    if kind in {"document", "document-report"}:
        if kind == "document":
            stmt = select(Document.id, Dataset.name, Document.version, Dataset.id).join(
                Dataset, Dataset.id == Document.dataset_id
            )
            return {
                str(key): {"name": name, "version": version, "dataset_id": dataset}
                for key, name, version, dataset in session.execute(
                    stmt.where(Document.id.in_(ids))
                )
            }
        stmt = select(
            DocumentReport.report_id,
            Dataset.name,
            Document.version,
            DocumentReport.execution_status,
        )
        stmt = stmt.join(
            Document, Document.id == DocumentReport.document_version_id
        ).join(Dataset, Dataset.id == Document.dataset_id)
        return {
            str(key): {"name": name, "version": version, "status": status}
            for key, name, version, status in session.execute(
                stmt.where(DocumentReport.report_id.in_(ids))
            )
        }
    if kind == "contract":
        last_run = (
            select(Run.run_id)
            .where(Run.contract_version_id == Contract.contract_version_id)
            .order_by(Run.created_at.desc(), Run.run_id.desc())
            .limit(1)
            .correlate(Contract)
            .scalar_subquery()
        )
        stmt = select(
            Contract.contract_version_id,
            Contract.name,
            Version.version,
            Version.id,
            Dataset.id,
            Contract.status,
            last_run,
        )
        stmt = stmt.join(
            Version, Version.id == Contract.target_dataset_version_id
        ).join(Dataset, Dataset.id == Version.dataset_id)
        return {
            str(key): {
                "name": name,
                "version": version,
                "version_id": vid,
                "dataset_id": did,
                "status": status,
                "run_id": run,
            }
            for key, name, version, vid, did, status, run in session.execute(
                stmt.where(
                    Contract.contract_version_id.in_(ids), Dataset.enabled.is_(True)
                )
            )
        }
    if kind in {"run", "report", "issue"}:
        key = {"run": Run.run_id, "report": Report.report_id, "issue": Issue.issue_id}[
            kind
        ]
        data_id = (
            Run.dataset_version_id if kind != "report" else Report.dataset_version_id
        )
        source_report = aliased(Report)
        doc_id = (
            Run.document_version_id
            if kind != "report"
            else func.coalesce(
                Report.document_version_id, source_report.document_version_id
            )
        )
        base_report_id = (
            select(Report.report_id)
            .where(Report.run_id == Run.run_id, Report.source_report_id.is_(None))
            .correlate(Run)
            .scalar_subquery()
        )
        stmt = select(
            key,
            Dataset.name,
            func.coalesce(Version.version, Document.version),
            Run.status,
            Run.run_id,
            Version.id,
            Run.contract_version_id,
            Run.document["task_type"].as_string(),
            Dataset.id,
            Document.id,
            Report.report_id if kind == "report" else base_report_id,
        )
        if kind == "report":
            stmt = stmt.select_from(Report).join(Run, Run.run_id == Report.run_id)
            stmt = stmt.outerjoin(
                source_report,
                source_report.report_id == Report.source_document_report_id,
            )
        elif kind == "issue":
            stmt = (
                stmt.select_from(Issue)
                .join(Run, Run.run_id == Issue.run_id)
                .where(base_report_id.is_not(None))
            )
        stmt = stmt.outerjoin(Version, Version.id == data_id).outerjoin(
            Document, Document.id == doc_id
        )
        stmt = stmt.join(
            Dataset,
            Dataset.id == func.coalesce(Version.dataset_id, Document.dataset_id),
        )
        return {
            str(item): {
                "name": name,
                "version": version,
                "status": status,
                "run_id": run,
                "version_id": vid,
                "contract_id": cid,
                "category": category,
                "dataset_id": dataset_id,
                "document_id": document_id,
                "report_id": rid,
            }
            for item, name, version, status, run, vid, cid, category, dataset_id, document_id, rid in session.execute(
                stmt.where(key.in_(ids))
            )
        }
    key = Agent.session_id if kind == "agent" else Version.id
    status = Agent.status if kind == "agent" else Version.status
    stmt = select(key, Dataset.name, Version.version, status, Version.id, Dataset.id)
    if kind == "agent":
        stmt = stmt.select_from(Agent).join(
            Version, Version.id == Agent.dataset_version_id
        )
    stmt = stmt.join(Dataset, Dataset.id == Version.dataset_id)
    if kind in {"manual", "agent", "single"}:
        stmt = stmt.where(Dataset.enabled.is_(True), Version.status == "PUBLISHED")
    return {
        str(item): {
            "name": name,
            "version": version,
            "status": state,
            "version_id": vid,
            "dataset_id": did,
        }
        for item, name, version, state, vid, did in session.execute(
            stmt.where(key.in_(ids))
        )
    }


def resolve_metadata(session, db, username, kind, ids):
    if kind != "operator":
        return metadata(session, kind, ids)
    placeholders = ",".join("?" for _ in ids)
    references = {
        row["draft_id"]: UUID(row["version_id"])
        for row in db.execute(
            f"SELECT draft_id, version_id FROM operator_work_drafts WHERE user_id = ? AND draft_id IN ({placeholders})",
            (username, *map(str, ids)),
        )
    }
    versions = metadata(session, "single", list(references.values()))
    return {
        key: versions[str(version)]
        for key, version in references.items()
        if str(version) in versions
    }


class RegistryReference(BaseModel):
    key: str = Field(min_length=1, max_length=255)
    version: str = Field(min_length=1, max_length=64)


class OperatorDraft(BaseModel):
    version_id: UUID
    metric: RegistryReference
    calculator: RegistryReference
    operators: list[RegistryReference] = Field(default_factory=list, max_length=100)
    values: dict
    mapping_digest: str | None = Field(default=None, max_length=71)
    slot_choices: dict[str, str] = Field(default_factory=dict)
    contract_id: UUID | None = None


@router.get("/operator-drafts/{draft_id}")
def read_operator_draft(
    draft_id: UUID,
    user=Depends(current_user),
    db=Depends(connect),
    session=Depends(get_db),
):
    if not resolve_metadata(session, db, user["username"], "operator", [draft_id]):
        raise HTTPException(404, "单算子配置已失效或不可访问")
    row = db.execute(
        "SELECT payload FROM operator_work_drafts WHERE user_id = ? AND draft_id = ?",
        (user["username"], str(draft_id)),
    ).fetchone()
    value = json.loads(row["payload"])
    if value.get("contract_id"):
        contract = metadata(session, "contract", [UUID(value["contract_id"])]).get(
            value["contract_id"]
        )
        if contract:
            value["run_id"] = contract["run_id"]
        else:
            value["contract_id"] = None
    return value


@router.put("/operator-drafts")
def write_operator_draft(
    draft: OperatorDraft,
    user=Depends(current_user),
    db=Depends(connect),
    session=Depends(get_db),
):
    if not metadata(session, "single", [draft.version_id]):
        raise HTTPException(404, "数据集版本已失效")
    if draft.contract_id:
        contract = metadata(session, "contract", [draft.contract_id]).get(
            str(draft.contract_id)
        )
        if not contract or contract["version_id"] != draft.version_id:
            raise HTTPException(404, "质量契约已失效")
    payload = draft.model_dump_json()
    if len(payload.encode()) > 262144:
        raise HTTPException(413, "草稿超过大小限制")
    identity = json.dumps(
        [
            str(draft.version_id),
            draft.metric.key,
            draft.metric.version,
            draft.calculator.key,
            draft.calculator.version,
        ]
    )
    draft_id = uuid5(NAMESPACE_URL, "qa-eval/operator-draft/" + identity)
    with db:
        db.execute(
            """INSERT INTO operator_work_drafts VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id, draft_id) DO UPDATE SET payload = excluded.payload""",
            (user["username"], str(draft_id), str(draft.version_id), payload),
        )
        touch(db, user["username"], "operator", draft_id)
    return {"draft_id": draft_id}


class ManualDraft(BaseModel):
    name: str = Field(max_length=255)
    selected: dict[str, list[dict]]
    excluded: dict[str, str]


class SingleDraft(BaseModel):
    revision: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")
    minChars: int = Field(ge=1)
    threshold: float = Field(ge=0, le=1)


class DocumentDraft(BaseModel):
    metrics: dict[str, dict]


DraftKind = Literal["manual", "single", "document"]
draft_models = {"manual": ManualDraft, "single": SingleDraft, "document": DocumentDraft}


@router.get("/drafts/{kind}/{object_id}")
def read_draft(
    kind: DraftKind,
    object_id: UUID,
    user=Depends(current_user),
    db=Depends(connect),
    session=Depends(get_db),
):
    if not metadata(session, kind, [object_id]):
        raise HTTPException(404, "工作对象已失效")
    row = db.execute(
        "SELECT payload FROM work_drafts WHERE user_id = ? AND kind = ? AND object_id = ?",
        (user["username"], kind, str(object_id)),
    ).fetchone()
    return json.loads(row["payload"]) if row else None


@router.put("/drafts/{kind}/{object_id}")
def write_draft(
    kind: DraftKind,
    object_id: UUID,
    draft: dict,
    user=Depends(current_user),
    db=Depends(connect),
    session=Depends(get_db),
):
    try:
        payload = draft_models[kind].model_validate(draft).model_dump_json()
    except ValidationError as failure:
        raise HTTPException(422, "草稿格式不正确") from failure
    if len(payload.encode()) > 262144:
        raise HTTPException(413, "草稿超过大小限制")
    if not metadata(session, kind, [object_id]):
        raise HTTPException(404, "工作对象已失效")
    with db:
        db.execute(
            """INSERT INTO work_drafts VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id, kind, object_id) DO UPDATE SET payload = excluded.payload""",
            (user["username"], kind, str(object_id), payload),
        )
        touch(db, user["username"], kind, object_id)
    return {"ok": True}


def touch(db, username, kind, object_id):
    db.execute(
        """INSERT INTO work_checkpoints VALUES (?, ?, ?, ?, 0)
           ON CONFLICT(user_id, kind, object_id) DO UPDATE SET
             touched_at = excluded.touched_at, hidden = 0""",
        (username, kind, str(object_id), datetime.now(UTC).isoformat()),
    )


@router.put("/checkpoints")
def save(
    body: Checkpoint,
    user=Depends(current_user),
    db=Depends(connect),
    session=Depends(get_db),
):
    if not resolve_metadata(session, db, user["username"], body.kind, [body.object_id]):
        raise HTTPException(404, "工作对象已失效")
    with db:
        touch(db, user["username"], body.kind, body.object_id)
    return {"ok": True}


@router.delete("/checkpoints/{kind}/{object_id}")
def hide(kind: Kind, object_id: UUID, user=Depends(current_user), db=Depends(connect)):
    with db:
        db.execute(
            "UPDATE work_checkpoints SET hidden = 1 WHERE user_id = ? AND kind = ? AND object_id = ?",
            (user["username"], kind, str(object_id)),
        )
    return {"ok": True}


@router.get("/checkpoints/{kind}/{object_id}")
def restore(
    kind: Kind,
    object_id: UUID,
    user=Depends(current_user),
    db=Depends(connect),
    session=Depends(get_db),
):
    row = db.execute(
        "SELECT 1 FROM work_checkpoints WHERE user_id = ? AND kind = ? AND object_id = ? AND hidden = 0",
        (user["username"], kind, str(object_id)),
    ).fetchone()
    item = (
        resolve_metadata(session, db, user["username"], kind, [object_id]).get(
            str(object_id)
        )
        if row
        else None
    )
    if item is None:
        raise HTTPException(404, "工作对象已失效或不可访问")
    return item


@router.get("")
def overview(user=Depends(current_user), db=Depends(connect), session=Depends(get_db)):
    rows = [
        dict(row)
        for row in db.execute(
            """SELECT kind, object_id, touched_at FROM work_checkpoints
           WHERE user_id = ? AND hidden = 0 ORDER BY touched_at DESC""",
            (user["username"],),
        )
    ]
    groups = {}
    for row in rows:
        groups.setdefault(row["kind"], []).append(UUID(row["object_id"]))
    resolved = {}
    for kind, ids in groups.items():
        resolved[kind] = {}
        for start in range(0, len(ids), 500):
            resolved[kind].update(
                resolve_metadata(
                    session, db, user["username"], kind, ids[start : start + 500]
                )
            )
    run_counts = dict(
        session.execute(select(Run.status, func.count()).group_by(Run.status)).all()
    )
    datasets, reports, document_reports = session.execute(
        select(
            select(func.count()).select_from(Dataset).scalar_subquery(),
            select(func.count()).select_from(Report).scalar_subquery(),
            select(func.count())
            .select_from(DocumentReport)
            .where(
                ~select(Report.report_id)
                .where(Report.document_quality_report_id == DocumentReport.report_id)
                .exists()
            )
            .scalar_subquery(),
        )
    ).one()
    return {
        "counts": {
            "datasets": datasets,
            "pending": run_counts.get("PENDING", 0),
            "running": run_counts.get("RUNNING", 0),
            "reports": reports + document_reports,
        },
        "items": [
            {
                **row,
                **resolved[row["kind"]].get(row["object_id"], {}),
                "valid": row["object_id"] in resolved[row["kind"]],
            }
            for row in rows
        ],
    }
