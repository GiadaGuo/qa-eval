"""Run the three-metric Shared LLM MVP against a published DatasetVersion.

This is an explicit, state-changing validation command: it syncs Registry metadata,
publishes a Quality Contract, creates an EvaluationRun, and executes it synchronously.
It never prints provider credentials or raw sample content.
"""

from __future__ import annotations

import argparse
import json
from uuid import UUID

from apps.api.dependencies import get_artifact_store
from qa_eval.application.services.evaluation_run_service import EvaluationRunService
from qa_eval.application.services.metric_registry_service import MetricRegistryService
from qa_eval.application.services.quality_contract_service import (
    QualityContractMultiPublishRequest,
    QualityContractService,
)
from qa_eval.config import get_settings
from qa_eval.infrastructure.db.models.dataset_version import DatasetVersionModel
from qa_eval.infrastructure.db.session import SessionLocal


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset_version_id", type=UUID)
    parser.add_argument("--sample-limit", type=int, default=3)
    args = parser.parse_args()
    if args.sample_limit < 1 or args.sample_limit > 20:
        parser.error("--sample-limit must be between 1 and 20")

    settings = get_settings()
    if not (settings.llm_base_url and settings.llm_api_key and settings.llm_model):
        raise SystemExit("LLM provider is not fully configured")
    with SessionLocal() as session:
        version = session.get(DatasetVersionModel, args.dataset_version_id)
        if version is None or version.status != "PUBLISHED":
            raise SystemExit("DatasetVersion is missing or not PUBLISHED")
        MetricRegistryService(session).sync()
        sharing = {
            "allow_shared_execution": True,
            "provider": "openai-compatible",
            "model": settings.llm_model,
            "sampling_fingerprint": f"first-{args.sample_limit}",
            "prompt_family": "text-quality-semantic-v1",
            "preprocessing_fingerprint": "canonical-sample-record-v2",
            "context_policy": "per-field-head-12000",
            "privacy_policy": "normalized-only",
            "input_selector": "article.body.plain",
            "independent_outputs": True,
        }
        definitions = [
            ("density", "02080102", "mean_effective_knowledge_score", "knowledge_information_density", "GTE", 0.5),
            ("missing", "02080105", "key_element_missing_ratio", "information_missing_degree", "LTE", 0.5),
            ("coherence", "02080107", "coherent_text_ratio", "text_coherence", "GTE", 0.5),
        ]
        items = [
            {
                "key": key,
                "metric_key": metric,
                "metric_version": "1.0.0",
                "measurement": measurement,
                "calculator_key": calculator,
                "calculator_version": "1.0.0",
                "parameters": {
                    "content_role": "article.body.plain",
                    "sample_limit": args.sample_limit,
                    "max_input_chars": 12000,
                    "static_context": {"validation_scope": "jhsjk_article_mvp"},
                    "sharing": sharing,
                },
                "rule": {
                    "operator": operator,
                    "threshold": threshold,
                    "severity": "ERROR",
                    "on_missing": "UNKNOWN",
                },
            }
            for key, metric, measurement, calculator, operator, threshold in definitions
        ]
        published = QualityContractService(session).publish_multi(
            QualityContractMultiPublishRequest.model_validate(
                {
                    "name": "jhsjk_article shared semantic MVP",
                    "version_label": "mvp-1.1",
                    "dataset_version_id": args.dataset_version_id,
                    "items": items,
                }
            )
        )
        session.commit()
        service = EvaluationRunService(session, get_artifact_store())
        run = service.create(published.contract.contract_version_id)
        session.commit()
        completed = service.execute(run.run_id)
        results = service.list_results(run.run_id)
        stages = service.list_stage_runs(run.run_id)
        print(
            json.dumps(
                {
                    "run_id": str(run.run_id),
                    "status": completed.status,
                    "stage_count": len(stages),
                    "stage_stats": [stage.stats for stage in stages],
                    "results": [
                        {
                            "metric_key": result.metric.key,
                            "status": result.status,
                            "status_detail": result.status_detail,
                            "observations": [
                                {"key": item.key, "value": item.value}
                                for item in result.observations
                            ],
                        }
                        for result in results
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
