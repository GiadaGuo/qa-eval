from datetime import datetime
from typing import Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import String, case, cast, func, or_, select, true
from sqlalchemy.orm import aliased

from qa_eval.application.evaluation.unified_report import QualityReportV2
from qa_eval.application.services.evaluation_run_service import (
    HISTORICAL_SINGLE_METRIC_RUN_ID,
)
from qa_eval.application.services.quality_result_service import QualityResultService
from qa_eval.application.services.run_metadata import run_timing
from qa_eval.contracts.quality import QualityReportV1_1
from qa_eval.infrastructure.db.models.dataset import DatasetModel
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.db.models.document_version import DocumentVersionModel
from qa_eval.infrastructure.db.models.evaluation_run import (
    EvaluationRunModel as Run,
    PhysicalEvaluationPlanModel as Plan,
    StageRunModel as Stage,
)
from qa_eval.infrastructure.db.models.quality_contract import QualityContractVersionModel as Contract
from qa_eval.infrastructure.db.models.quality_result import QualityReportModel as Report

ACTIVE = ("PENDING", "RUNNING", "CANCEL_REQUESTED")
COMPLETED = ("SUCCEEDED", "PARTIAL_SUCCEEDED")
Row = TypeVar("Row")


class Target(BaseModel):
    dataset_name: str
    dataset_version: str | None
    document_version: str | None


class TaskRow(BaseModel):
    run_id: UUID
    name: str
    category: str
    target: Target
    status: str
    settled_stages: int | None
    planned_stages: int | None
    duration_seconds: float | None
    elapsed_seconds: float | None
    elapsed_as_of: datetime | None
    finished_at: datetime | None
    report_id: UUID | None
    actions: list[str]


class Measurement(BaseModel):
    label: str
    value: str | float | int | bool
    unit: str | None = None


class ReportRow(BaseModel):
    report_id: UUID
    run_id: UUID
    name: str
    category: str
    target: Target
    measurements: list[Measurement]
    verdict: str
    generated_at: datetime


class CatalogPage(BaseModel, Generic[Row]):
    items: list[Row]
    total: int
    counts: dict[str, int]


def _run_category():
    return func.coalesce(Run.document["task_type"].as_string(), case(
        (Run.run_id == HISTORICAL_SINGLE_METRIC_RUN_ID, "SINGLE_METRIC"),
        (Run.document_version_id.is_not(None), "DOCUMENT"),
        else_="HISTORICAL_UNCLASSIFIED",
    ))


def _target_query(query, *, report_catalog=False):
    source_document = aliased(Report)
    doc_id = Run.document_version_id
    if report_catalog:
        query = query.outerjoin(source_document, source_document.report_id == Report.source_document_report_id)
        doc_id = func.coalesce(Report.document_version_id, source_document.document_version_id)
    return (query.join(Contract, Contract.contract_version_id == Run.contract_version_id)
            .outerjoin(DatasetVersionModel, DatasetVersionModel.id == Run.dataset_version_id)
            .outerjoin(DocumentVersionModel, DocumentVersionModel.id == doc_id)
            .join(DatasetModel, DatasetModel.id == func.coalesce(
                DatasetVersionModel.dataset_id, DocumentVersionModel.dataset_id)))


def _scope(category, status, category_expr, search, status_expr, *search_columns):
    filters = []
    if category:
        filters.append(category_expr == category)
    if status:
        filters.append(status_expr == status)
    if search and (term := search.strip()):
        columns = (Contract.name, DatasetModel.name, DatasetVersionModel.version,
                   DocumentVersionModel.version, *search_columns)
        filters.append(or_(*(column.icontains(term, autoescape=True) for column in columns)))
    return filters


def _counts(session, stmt, filters):
    return dict(session.execute(stmt.where(*filters)).all())


def list_tasks(service, *, search, category, status, limit, offset, duration_order):
    session = service.session
    task_category = _run_category()
    filters = _scope(category, status, task_category, search, Run.status, cast(Run.run_id, String))
    counts = _target_query(select(
        func.count().label("total"),
        func.count().filter(Run.status == "RUNNING").label("active"),
        func.count().filter(Run.status.in_(COMPLETED)).label("completed"),
        func.count().filter(Run.status == "FAILED").label("failed"),
    ).select_from(Run)).where(*filters).subquery()
    report = aliased(Report)
    query = _target_query(select(
        Run, Contract.name.label("contract_name"), DatasetModel.name.label("dataset_name"),
        DatasetVersionModel.version.label("dataset_version"),
        DocumentVersionModel.version.label("document_version"), task_category.label("category"),
        report.report_id, Plan.document["stages"].label("stages"),
    ).select_from(Run)).join(Plan, Plan.physical_plan_id == Run.physical_plan_id).outerjoin(
        report, (report.run_id == Run.run_id) & report.source_report_id.is_(None),
    )

    def order(columns):
        sort = [columns.created_at.desc(), columns.run_id.desc()]
        if duration_order:
            seconds = columns.duration_sort
            sort.insert(0, (seconds.asc() if duration_order == "asc" else seconds.desc()).nulls_last())
        return sort

    if duration_order:
        end = case(
            (Run.finished_at.is_not(None), Run.finished_at),
            (Run.status.in_(("RUNNING", "CANCEL_REQUESTED")), func.current_timestamp()),
        )
        difference = end - Run.started_at
        if session.get_bind().dialect.name == "sqlite":
            difference = func.julianday(end) - func.julianday(Run.started_at)
        query = query.add_columns(case((end >= Run.started_at, difference)).label("duration_sort"))
    page = query.where(*filters).order_by(*order(query.selected_columns)).limit(limit).offset(offset).subquery()

    # Keep counts and rows in one database snapshot, including when the page is empty.
    results = session.execute(select(
        aliased(Run, page), page.c.contract_name, page.c.dataset_name, page.c.dataset_version,
        page.c.document_version, page.c.category, page.c.report_id, page.c.stages,
        *counts.c,
    ).select_from(counts.outerjoin(page, true())).order_by(*order(page.c))
      .execution_options(populate_existing=True)).all()
    total, active, completed, failed = results[0][-4:]
    rows = [result[:8] for result in results if result[0] is not None]
    stage_counts = {}
    if rows:
        latest = (select(Stage.run_id, Stage.stage_id, func.max(Stage.attempt).label("attempt"))
                  .where(Stage.run_id.in_([row[0].run_id for row in rows]))
                  .group_by(Stage.run_id, Stage.stage_id).subquery())
        stage_counts = dict(session.execute(
            select(Stage.run_id, func.sum(case(
                (Stage.status.in_(("SUCCEEDED", "FAILED")), 1), else_=0,
            ))).join(latest, (Stage.run_id == latest.c.run_id)
                & (Stage.stage_id == latest.c.stage_id) & (Stage.attempt == latest.c.attempt))
            .group_by(Stage.run_id)
        ).all())
    items = []
    for row, name, dataset, version, document, kind, report_id, stages in rows:
        run = service._read_run_row(row)
        actions = ["DETAIL"]
        if run.status == "PENDING":
            actions.append("CANCEL")
        if run.status in ("FAILED", "CANCELED", "PARTIAL_SUCCEEDED"):
            actions.append("RETRY")
        if report_id:
            actions.append("REPORT")
        if run.status not in ACTIVE:
            actions.append("DELETE")
        planned = len(stages) if isinstance(stages, list) and stages else None
        items.append(TaskRow(
            run_id=run.run_id, name=name, category=kind,
            target=Target(dataset_name=dataset, dataset_version=version, document_version=document),
            status=run.status, settled_stages=stage_counts.get(run.run_id, 0 if run.status == "PENDING" else None) if planned else None,
            planned_stages=planned, **run_timing(run),
            finished_at=run.finished_at, report_id=report_id, actions=actions,
        ))
    return CatalogPage[TaskRow](items=items, total=total, counts={
        "all": total, "active": active, "completed": completed, "failed": failed,
    })


def _measurements(report, kind):
    if isinstance(report, QualityReportV2):
        return [Measurement(label=item.key, value=item.score, unit="points")
                for item in report.dimensions if item.score is not None]
    if isinstance(report, QualityReportV1_1) and kind != "SINGLE_METRIC":
        scores = [Measurement(label=item.dimension, value=item.score, unit="points")
                  for item in report.dimension_scores if item.score is not None]
        if scores:
            return scores
    if isinstance(report, QualityReportV1_1) and report.native_evaluations:
        return [Measurement(label=evaluation.statistic_ref.statistics_key,
                            value=evaluation.metric_value, unit=evaluation.statistic.unit)
                for item in report.native_evaluations
                if (evaluation := item.evaluation).metric_value is not None]
    return [Measurement(label=item.measurement.key, value=item.measurement.value, unit=item.measurement.unit)
            for item in report.metrics if item.measurement is not None
            and isinstance(item.measurement.value, (str, int, float, bool))]


def list_results(service, *, search, category, status, limit, offset):
    session = service.session
    category_expr = case(
        (Report.source_report_id.is_not(None), "COMBINED"),
        (Report.document_version_id.is_not(None), "DOCUMENT"),
        else_=func.coalesce(Report.document["task_type"].as_string(), _run_category()),
    )
    verdict = case((Report.gate_status.in_(("PASS", "FAIL")), Report.gate_status), else_="INDETERMINATE")
    filters = _scope(category, status, category_expr, search, verdict, cast(Report.report_id, String),
                     cast(Run.run_id, String), Report.document["metrics"][0]["metric"]["name"].as_string())

    def query(*columns):
        return _target_query(select(*columns).select_from(Report).join(Run, Run.run_id == Report.run_id),
                             report_catalog=True).where(*filters)

    totals = _counts(session, query(verdict, func.count()).group_by(verdict), [])
    rows = session.execute(query(
        Report, Contract.name, DatasetModel.name, DatasetVersionModel.version,
        DocumentVersionModel.version, category_expr, verdict,
    ).order_by(Report.generated_at.desc(), Report.report_id.desc()).limit(limit).offset(offset)).all()
    items = []
    for row, name, dataset, version, document, kind, conclusion in rows:
        report = QualityResultService._parse_report(row.document)
        if kind == "SINGLE_METRIC" and not isinstance(report, QualityReportV2) and report.metrics:
            name = report.metrics[0].metric.name
        items.append(ReportRow(
            report_id=row.report_id, run_id=row.run_id, name=name, category=kind,
            target=Target(dataset_name=dataset, dataset_version=version, document_version=document),
            measurements=_measurements(report, kind)[:4], verdict=conclusion,
            generated_at=(report.finalized_at if isinstance(report, QualityReportV2) else report.generated_at),
        ))
    total = sum(totals.values())
    return CatalogPage[ReportRow](items=items, total=total, counts={
        "all": total, "passed": totals.get("PASS", 0), "failed": totals.get("FAIL", 0),
        "indeterminate": totals.get("INDETERMINATE", 0),
    })
