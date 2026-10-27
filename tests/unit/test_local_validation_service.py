# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-27
# @Description: 闭环 1：LOCAL_VALIDATION 只读服务测试。
# ====================================================

from qa_eval.application.services.local_validation_service import (
    LOCAL_VALIDATION_AUTHORITY,
    LocalValidationService,
)


def test_service_loads_default_artifacts():
    service = LocalValidationService()
    artifacts = service.load()

    assert artifacts.authority == LOCAL_VALIDATION_AUTHORITY
    assert artifacts.not_a_platform_run is True
    assert artifacts.manifest.schema_version == "2.0"
    assert artifacts.manifest.name == "policy.jhsjk_article"
    assert artifacts.metric_result.metric.key == "02080103"
    assert artifacts.metric_result.status == "OK"
    assert artifacts.contract.status == "PUBLISHED"
    assert len(artifacts.rule_evaluations) == 1
    assert len(artifacts.issues) == 7562
    assert artifacts.issue_index.summary.confirmed_count == 45
    assert artifacts.issue_index.summary.candidate_count == 7517
    assert artifacts.report.gate_status == "PASS"
    assert artifacts.report.execution_status == "SUCCEEDED"


def test_service_filters_issues_by_determination():
    service = LocalValidationService()
    artifacts = service.load()

    confirmed = [
        item for item in artifacts.issues if item.determination == "CONFIRMED"
    ]
    candidate = [
        item for item in artifacts.issues if item.determination == "CANDIDATE"
    ]

    assert len(confirmed) == 45
    assert len(candidate) == 7517
    assert all(item.scope == "SAMPLE" for item in confirmed)
    assert all(item.code == "EXACT_DUPLICATE_REDUNDANT" for item in confirmed)
    assert all(item.code == "NEAR_DUPLICATE_CANDIDATE" for item in candidate)
