import json
import os
import resource
import sys
from itertools import islice
from pathlib import Path
from time import perf_counter

import pytest
from sqlalchemy.orm import Session
from test_perplexity_purity_run import _prepare

from qa_eval.application.services.evaluation_run_service import EvaluationRunService
from qa_eval.application.services.evidence_query_service import EvidenceQueryService
from qa_eval.application.services.metric_registry_service import MetricRegistryService
from qa_eval.application.services.quality_contract_service import (
    QualityContractMultiPublishRequest,
    QualityContractService,
)
from qa_eval.application.services.quality_result_service import QualityResultService

ROLE = "article.body.plain"
TEXT = "A complete repeated sentence."


def item(key, measurement, **parameters):
    return {
        "key": key,
        "metric_key": "0208",
        "metric_version": "2.0.0",
        "measurement": measurement,
        "calculator_key": key,
        "calculator_version": "1.0.0",
        "parameters": {
            "fields": [{"selector": "content_role", "role": ROLE}],
            **parameters,
        },
        "rule": {
            "strategy": "OBSERVE_ONLY",
            "severity": "WARNING",
            "on_missing": "UNKNOWN",
        },
    }


def execute(
    tmp_path,
    texts,
    *,
    prove_duplicates=False,
    include_failure=False,
    cross_parameters=None,
    intra_parameters=None,
):
    engine, store, version_id = _prepare(tmp_path, texts)
    with Session(engine) as session:
        registry = MetricRegistryService(session)
        bindings = [
            b
            for b in registry.list_bindings("0208")
            if b.metric_version == "2.0.0"
            and not (
                b.calculator_key
                in {"cross_record_duplicate", "intra_record_repetition", "text_perplexity"}
                and b.calculator_version == "1.1.0"
            )
        ]
        assert len({b.compatibility["operator_slot"] for b in bindings}) == 8
        assert {ref for b in bindings for ref in b.compatibility["standard_refs"]} == {
            f"020801{i:02}" for i in range(1, 8)
        }
        cross = item(
            "cross_record_duplicate",
            "duplicate_redundant_record_ratio",
            **(
                {"strategies": ["record_exact", "field_exact", "field_near"]}
                | (cross_parameters or {})
            ),
        )
        intra = item(
            "intra_record_repetition",
            "intra_record_repetition_score",
            **(
                {"repetition_threshold": 0.5, "exclude_patterns": ["固定表头"]}
                | (intra_parameters or {})
            ),
        )
        intra["operator_slot"] = "intra_record_repetition"
        for field in ("calculator_key", "calculator_version", "measurement"):
            intra.pop(field)
        items = [cross, intra]
        if include_failure:
            items.append(
                item(
                    "text_perplexity",
                    "mean_perplexity",
                    model_path=str(tmp_path / "missing.arpa"),
                )
            )
            items[-1]["parameters"].pop("fields")
        request = {
            "name": "isolated 0208",
            "version_label": "v1",
            "dataset_version_id": str(version_id),
            "items": items,
        }
        contracts = QualityContractService(session)
        if prove_duplicates:
            with pytest.raises(ValueError, match="per operator slot"):
                contracts.publish_multi(
                    QualityContractMultiPublishRequest.model_validate(
                        request | {"items": [cross, cross | {"key": "duplicate-slot"}]}
                    )
                )
        contract = contracts.publish_multi(
            QualityContractMultiPublishRequest.model_validate(request)
        )
        service = EvaluationRunService(session, store)
        run = service.create(contract.contract.contract_version_id)
        session.commit()
        executed = service.execute(run.run_id)
        stages = service.list_stage_runs(run.run_id)
        assert len(stages) == len(items)
        statuses = {stage.calculator["key"]: stage.status for stage in stages}
        assert (
            statuses["cross_record_duplicate"]
            == statuses["intra_record_repetition"]
            == "SUCCEEDED"
        ), executed.error
        if include_failure:
            assert statuses["text_perplexity"] == "FAILED"
        else:
            assert executed.status == "SUCCEEDED", executed.error
        results = service.list_results(run.run_id)
        values = {
            obs.key: obs.value for result in results for obs in result.observations
        }
        evidence = {
            artifact.kind: store.read(artifact.artifact.uri)
            for result in results
            for artifact in result.evidence
        }
        report = QualityResultService(session, store).get_report(run.run_id)
        assert report is not None, json.dumps(executed.error)
        assert len(report.metrics) == len(items)
        assert report.scoring.overall_score is None
        if prove_duplicates:
            assert report.issue_summary.confirmed_count > 0
            sections = {section.operator_slot: section for section in report.metrics}
            assert (
                sections["cross_record_duplicate"].issue_summary.by_scope["GROUP"] >= 1
            )
            assert (
                sections["intra_record_repetition"].issue_summary.by_scope["SAMPLE"]
                == 1
            )
            assert sections["cross_record_duplicate"].operator_score == 0.75
            assert sections["text_perplexity"].operator_score is None
            query = EvidenceQueryService(session, store)
            for result in results:
                for artifact in result.evidence:
                    assert (
                        query.build_evidence_index(artifact.evidence_id)["status"]
                        == "READY"
                    )
                    rows, _ = query.list_evidence_rows(artifact.evidence_id, limit=1)
                    assert rows[0]["row_id"]
        return values, evidence


def test_two_slots_and_failure_isolation(tmp_path):
    values, evidence = execute(
        tmp_path,
        {
            "a": TEXT,
            "b": TEXT,
            "c": f"{TEXT}\n\n{TEXT}",
            "d": "固定表头\n固定表头\nAn unrelated valid sentence.",
            "missing": None,
        },
        prove_duplicates=True,
        include_failure=True,
    )
    assert values["duplicate_redundant_record_ratio"] == 1 / 4
    assert values["duplicate_candidate_record_ratio"] >= 2 / 4
    assert values["duplicate_coverage_ratio"] == 4 / 5
    assert values["duplicate_group_count"] == 1
    rows = {
        row["sample_id"]: row
        for row in map(json.loads, evidence["INTRA_RECORD_REPETITION"].splitlines())
    }
    assert set(rows) == {"a", "b", "c", "d"}
    assert rows["c"]["score"] == 0.5
    assert rows["c"]["verdict"] == 1
    assert rows["c"]["locations"][0]["ranges"] == [[len(TEXT) + 2, 2 * len(TEXT) + 2]]
    assert rows["d"]["score"] == 1


@pytest.mark.skipif(
    not os.environ.get("QA_EVAL_0208_POLICY_SAMPLE"),
    reason="requires a local policy snapshot",
)
def test_local_policy_sample(tmp_path):
    source = Path(os.environ["QA_EVAL_0208_POLICY_SAMPLE"])
    with source.open(encoding="utf-8") as stream:
        sample = [json.loads(line) for line in islice(stream, 64)]
    texts = {}
    for record in sample:
        body = next(
            (
                content.get("payload")
                for content in record["contents"]
                if content["role"] == ROLE
            ),
            None,
        )
        texts[record["sample_id"]] = (body or {}).get("text")
    started = perf_counter()
    values, evidence = execute(tmp_path, texts)
    seconds = perf_counter() - started
    peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    rss_mib = peak_rss / (1024**2 if sys.platform == "darwin" else 1024)
    print(
        f"seconds={seconds:.4f} records_per_second={len(texts) / seconds:.1f} peak_rss_mib={rss_mib:.1f}"
    )
    assert "intra_record_repetition_score" in values
    assert "duplicate_redundant_record_ratio" in values
    print(
        f"policy_records={len(texts)} evidence_bytes={sum(map(len, evidence.values()))} observations={values}"
    )


def test_near_pairs_do_not_form_transitive_groups(tmp_path, monkeypatch):
    # Force all pairs through the candidate filter so Jaccard confirmation and
    # non-transitive grouping are independently testable.
    monkeypatch.setattr(
        "qa_eval.calculators.repetition.minhash_signature",
        lambda shingles, count: (0,) * count,
    )
    shared = "abcdefghijklmnop"
    values, evidence = execute(
        tmp_path,
        {
            "a": shared + "xyzxyz",
            "b": "123456" + shared + "xyzxyz",
            "c": "123456" + shared,
        },
        cross_parameters={"strategies": ["field_near"], "near_threshold": 0.7},
    )
    rows = [
        json.loads(line) for line in evidence["CROSS_RECORD_DUPLICATE"].splitlines()
    ]
    assert {
        (row["representative_sample_id"], row["sample_id"]): row["determination"]
        for row in rows
    } == {("a", "b"): "CONFIRMED", ("b", "c"): "CONFIRMED", ("a", "c"): "CANDIDATE"}
    assert values["duplicate_group_count"] == 2
    assert values["duplicate_redundant_record_ratio"] == 1 / 3


def test_record_pointer_and_repeated_fragment(tmp_path):
    pointer = {"selector": "record_pointer", "path": "/contents/0/payload/text"}
    text = " prefix abcdef middle abcdef suffix "
    values, evidence = execute(
        tmp_path,
        {"fragment": text, "too_short": "Hi."},
        cross_parameters={
            "fields": [{"selector": "content_role", "role": ROLE}, pointer],
            "strategies": ["record_exact"],
        },
        intra_parameters={"fields": [pointer], "min_length": 6, "local_ngram_size": 6},
    )
    [row] = map(json.loads, evidence["INTRA_RECORD_REPETITION"].splitlines())
    assert row["sample_id"] == "fragment"
    assert row["redundant_chars"] == 6
    assert row["locations"][0]["ranges"] == [
        [text.rfind("abcdef"), text.rfind("abcdef") + 6]
    ]
    assert values["duplicate_coverage_ratio"] == 1
