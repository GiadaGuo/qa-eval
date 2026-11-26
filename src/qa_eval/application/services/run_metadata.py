from datetime import UTC, datetime

from qa_eval.contracts.execution import EvaluationRunV1, StageRunV1
from qa_eval.infrastructure.db.models.dataset import DatasetModel
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.db.models.document_version import DocumentVersionModel


def duration_seconds(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None or end < start:
        return None
    return (end - start).total_seconds()


def run_timing(run: EvaluationRunV1) -> dict:
    observed = datetime.now(UTC) if (
        run.status in {"RUNNING", "CANCEL_REQUESTED"} and run.finished_at is None
    ) else None
    elapsed = duration_seconds(run.started_at, observed)
    return {
        "duration_seconds": duration_seconds(run.started_at, run.finished_at),
        "elapsed_seconds": elapsed,
        "elapsed_as_of": observed if elapsed is not None else None,
    }


class EvaluationRunView(EvaluationRunV1):
    duration_seconds: float | None = None
    queue_duration_seconds: float | None = None
    elapsed_seconds: float | None = None
    elapsed_as_of: datetime | None = None
    dataset_name: str | None = None
    dataset_version: str | None = None
    document_version: str | None = None


class StageRunView(StageRunV1):
    duration_seconds: float | None = None


def run_view(run: EvaluationRunV1, session, version_cache=None) -> EvaluationRunView:
    if version_cache is None:
        version_cache = {}

    def version_for(model, version_id):
        if version_id is None:
            return None
        key = (model, version_id)
        if key not in version_cache:
            version_cache[key] = session.get(model, version_id)
        return version_cache[key]

    data = version_for(DatasetVersionModel, run.dataset_version_id)
    document = version_for(DocumentVersionModel, run.document_version_id)
    target = data or document
    dataset = version_for(DatasetModel, target.dataset_id) if target else None
    return EvaluationRunView(
        **run.model_dump(),
        **run_timing(run),
        queue_duration_seconds=duration_seconds(run.created_at, run.started_at),
        dataset_name=dataset.name if dataset else None,
        dataset_version=data.version if data else None,
        document_version=document.version if document else None,
    )
