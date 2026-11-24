import copy
import json
from pathlib import Path
from uuid import UUID

import pytest

from qa_eval.calculators.field_relation_consistency.configuration import settings
from qa_eval.calculators.field_relation_consistency.engine import FieldView, evaluate
from qa_eval.calculators.field_relation_consistency.legacy import LegacyAdapter
from qa_eval.calculators.field_relation_consistency.local_matching import (
    RuleClassifier,
    semantic_match,
    type_match,
)
from qa_eval.calculators.field_relation_consistency.structured import (
    exact_match,
    structured_match,
)
from qa_eval.calculators.protocol import CalculatorValidationError
from qa_eval.contracts.datasets import SampleRecordV2

MATCHERS = {
    "exact_match": exact_match,
    "structured_match": structured_match,
    "semantic_match": semantic_match,
    "type_match": type_match,
}
VERSION = UUID(int=123)


def config(name):
    return json.loads(Path(f"configs/field_relation_consistency/{name}.json").read_bytes())


def content(role, content_id, text, source=None):
    return {
        "role": role,
        "content_id": content_id,
        "modality": "text",
        "presence": "PRESENT",
        "payload": {"type": "inline_text", "text": text, "format": "plain"},
        "attributes": {"source_paths": [source]} if source else {},
    }


def record(contents, metadata, sample_id="r1"):
    return SampleRecordV2.model_validate(
        {
            "schema_version": "2.0",
            "dataset_version_id": VERSION,
            "sample_id": sample_id,
            "contents": contents,
            "metadata": metadata,
            "source": {"source_artifact_id": UUID(int=321), "source_row": 3},
            "lineage": {"input_fingerprint": "sha256:" + "a" * 64},
        }
    )


def view(name, value, tag=None, presence="PRESENT"):
    return FieldView(name, value, "string", presence, {}, frozenset({name}), tag)


class TestPhaseOne:
    def test_selectors_and_presence(self):
        row = record(
            [content("name", "a", "one", "$.original"), content("name", "b", "two")],
            {"a/b~c": [None, "", "-", 3]},
        )
        options = {
            "ambiguity": {
                "selector": "content_role",
                "role": "name",
                "value_type": "text",
            },
            "qualified": {
                "selector": "content_role",
                "role": "name",
                "content_id": "a",
                "value_type": "text",
            },
            "alias": {
                "selector": "record_pointer",
                "path": "/contents/0/payload/text",
                "value_type": "text",
            },
        }
        options.update(
            {
                f"p{i}": {
                    "selector": "record_pointer",
                    "path": f"/metadata/a~1b~0c/{i}",
                    "value_type": "text",
                    "missing_tokens": ["-"],
                }
                for i in range(5)
            }
        )
        values = LegacyAdapter(options).extract(row)
        assert [values[f"p{i}"].presence for i in range(5)] == [
            "NULL",
            "EMPTY",
            "PLACEHOLDER",
            "INVALID",
            "MISSING",
        ]
        assert values["ambiguity"].presence == "AMBIGUOUS"
        assert values["qualified"].value == values["alias"].value == "one"
        assert "$.original" in values["qualified"].origins & values["alias"].origins
        assert values["alias"].source_ref["source_row"] == 3

    @pytest.mark.parametrize(
        "fault", ["edge", "source", "type", "weight", "pointer", "version"]
    )
    def test_graph_validation(self, fault):
        options = config("non-government")
        edge = options["relations"][0]
        if fault == "edge":
            options["relations"].append(
                edge | {"id": "reversed", "left": edge["right"], "right": edge["left"]}
            )
        elif fault == "source":
            for endpoint in ("display_name", "supplier_name"):
                options["fields"][endpoint]["source_paths"] = ["$.same"]
        elif fault == "type":
            options["fields"]["supplier_name"]["value_type"] = "object"
        elif fault == "weight":
            edge["weight"] = float("nan")
        elif fault == "pointer":
            options["fields"]["supplier_name"]["path"] = "/metadata/~3invalid"
        elif fault == "version":
            options["relations"][-1]["parameters"]["dictionary"]["version"] = "floating"
        with pytest.raises(CalculatorValidationError):
            settings(options)

    def test_non_government_and_veto(self):
        options = settings(config("non-government"))
        row = record(
            [
                content("inventory.product_name", "name", "Ｐｅｎ"),
                content("inventory.description", "desc", "书写用"),
            ],
            {
                "vendor": {"name": "pen"},
                "listed": {"weight": "1 kg"},
                "measured": {"weight": "995 g"},
                "category": "文具",
            },
        )
        fields = LegacyAdapter(options["fields"]).extract(row)
        assert evaluate(fields, options, MATCHERS)["label"] == "1"
        row.metadata["vendor"]["name"] = None
        row.metadata["measured"]["weight"] = "994.9 g"
        decision = evaluate(
            LegacyAdapter(options["fields"]).extract(row), options, MATCHERS
        )
        assert decision["label"] == "0"
        assert decision["reason"] == "CONFIRMED_CONFLICT_VETO"
        assert decision["relations"][1]["score"] == 0

    def test_dates_times_and_events(self):
        field = view("a", "2022-07-27T16:00:00+00:00", "publication")
        other = view("b", "2022-07-28T00:00:00", "publication")
        parameters = {"attribute": "date", "timezone": "Asia/Shanghai"}
        assert structured_match(field, other, parameters).status == "UNKNOWN"
        parameters["right_naive_timezone"] = "Asia/Shanghai"
        assert structured_match(field, other, parameters).status == "PASS"
        assert (
            structured_match(field, view("b", other.value, "signed"), parameters).status
            == "UNKNOWN"
        )
        assert (
            structured_match(
                field, view("b", "2099-05-05", "publication"), parameters
            ).reason
            == "DATE_SENTINEL_OR_OUTSIDE_DOMAIN"
        )
        assert (
            structured_match(
                field,
                view("b", "2024-11-03T01:30:00", "publication"),
                parameters | {"right_naive_timezone": "America/New_York"},
            ).status
            == "UNKNOWN"
        )

    def test_document_number_requires_own_document(self):
        a = view("left", "政发〔2024〕1号", "own")
        parameters = {
            "attribute": "document_number",
            "right_extractor": "outer_document_number_v1",
        }
        for body in (
            "根据政发〔2024〕2号执行。",
            "附件\n发文字号：政发〔2024〕2号",
            "发文字号：政发〔2024]2号",
        ):
            assert (
                structured_match(a, view("right", body, "own"), parameters).status
                == "UNKNOWN"
            )
        body = "测试通知\n发文字号：政发[2024]2号\n其他正文"
        match = structured_match(a, view("right", body, "own"), parameters)
        assert match.confirmed_conflict
        start, end = match.details["right_span"]
        assert body[start:end] == "政发[2024]2号"

    def test_coverage_score_thresholds_and_same_source(self):
        fields = {
            name: view(name, value, "tag", presence)
            for name, value, presence in (
                ("a", "a", "PRESENT"),
                ("b", "a", "PRESENT"),
                ("c", "b", "PRESENT"),
                ("d", None, "NULL"),
            )
        }
        options = {
            "relations": [
                {
                    "id": name,
                    "left": "a",
                    "right": name,
                    "method": "exact_match",
                    "weight": weight,
                    "required": False,
                    "parameters": {},
                }
                for name, weight in (("b", 4), ("c", 1), ("d", 3))
            ],
            "aggregation": {"min_coverage": 0.625, "pass_threshold": 0.8},
        }
        decision = evaluate(fields, options, MATCHERS)
        assert (decision["label"], decision["score"], decision["coverage"]) == (
            "1",
            0.8,
            0.625,
        )
        options["aggregation"]["min_coverage"] = 0.626
        assert evaluate(fields, options, MATCHERS)["reason"] == "COVERAGE_BELOW_MINIMUM"
        options["relations"][2]["required"] = True
        assert (
            evaluate(fields, options, MATCHERS)["reason"] == "REQUIRED_RELATION_UNKNOWN"
        )
        fields["b"] = FieldView(
            "b", "a", "text", "PRESENT", {}, fields["a"].origins, "tag"
        )
        assert (
            evaluate(fields, options, MATCHERS)["relations"][0]["reason"]
            == "SAME_SOURCE"
        )
        options["relations"][2]["parameters"]["applies_when"] = {
            "field": "a",
            "equals": "other",
        }
        assert (
            evaluate(fields, options, MATCHERS)["relations"][2]["status"]
            == "NOT_APPLICABLE"
        )


class TestPhaseTwo:
    def test_tfidf_uses_suffix_and_does_not_take_peak(self):
        params = settings(config("policy"))["relations"][0]["parameters"]
        title = view("title", "企业人才补贴申请")
        relevant = "企业人才补贴申请。" * 70
        irrelevant = "防洪排涝桥梁工程设计。" * 740
        absent = semantic_match(title, view("body", irrelevant), params)
        matched = semantic_match(title, view("body", irrelevant + relevant), params)
        only = semantic_match(title, view("body", relevant), params)
        assert absent.raw_score < matched.raw_score < only.raw_score
        assert matched.details["processed_characters"] == len(irrelevant + relevant)
        assert matched.details["block_count"] > 1
        assert matched.details["keyword_coverage"] == 1

    def test_policy_category_rules_are_independent_and_can_abstain(self):
        params = settings(config("policy"))["relations"][1]["parameters"]
        body = view("body", "就有关问题说明\n问：什么是申请条件？")
        tag = "policy.source_section"
        assert type_match(view("declared", "解读", tag), body, params).status == "PASS"
        contradicts = type_match(view("declared", "政策", tag), body, params)
        assert contradicts.status == "FAIL"
        assert not contradicts.confirmed_conflict
        for label in ("13", "通知", "未核实"):
            assert (
                type_match(view("declared", label, tag), body, params).reason
                == "CATEGORY_MAPPING_UNVERIFIED"
            )
        prediction = RuleClassifier().predict(
            "第一条 任职条件\n问：如何办理？", params["dictionary"]
        )
        assert prediction.label is None and prediction.reason == "CATEGORY_AMBIGUOUS"

    def test_abstention_and_versioned_support(self):
        options = config("policy")
        parameters = options["relations"][0]["parameters"]
        parameters.update(fail_below=0.0, pass_at=1.0)
        title = view("title", "人才补贴")
        body = view("body", "防洪排涝桥梁工程设计。" * 20)
        p = settings(options)["relations"][0]["parameters"]
        assert semantic_match(title, body, p).reason == "ABSTENTION_INTERVAL"
        parameters.update(
            fail_below=0.1,
            pass_at=0.9,
            calibration={
                "version": "trial-map-v1",
                "points": [[0, 0.05], [0.5, 0.5], [1, 0.95]],
            },
        )
        p = settings(options)["relations"][0]["parameters"]
        match = semantic_match(title, body, p)
        assert match.raw_score == 0.0 and match.score == 0.05
        assert match.details["calibration_digest"].startswith("sha256:")
        options["relations"].append(
            copy.deepcopy(options["relations"][0])
            | {"id": "other", "right": "declared_number"}
        )
        options["relations"][-1]["parameters"]["tfidf"]["default_idf"] = 1
        with pytest.raises(CalculatorValidationError, match="defined multiple ways"):
            settings(options)


def test_native_zero_evaluable_and_duplicate_input(tmp_path):
    from qa_eval.calculators.field_relation_consistency.calculator import (
        FieldRelationConsistencyCalculator,
    )
    from qa_eval.calculators.protocol import CalculatorContext

    row = record([content("unrelated", "unused", "text")], {})
    source = tmp_path / "input.jsonl"
    serialized = row.model_dump_json() + "\n"
    source.write_text(serialized)
    calc = FieldRelationConsistencyCalculator()
    context = CalculatorContext(
        UUID(int=1),
        UUID(int=2),
        VERSION,
        "0207",
        "2.0.0",
        source,
        tmp_path / "output",
        "2.0",
    )
    result = calc.execute(context, config("non-government"))
    assert result.status == "MISSING"
    assert result.statistics["consistent_among_evaluable"] is None
    assert result.statistics["evaluable_record_ratio"] == 0
    assert result.statistics["not_evaluated_record_count"] == 1
    assert json.loads(result.decisions.path.read_bytes())["status"] == "NOT_EVALUATED"
    source.write_text(serialized * 2)
    with pytest.raises(
        CalculatorValidationError, match="duplicate normalized sample ID"
    ):
        calc.execute(context, config("non-government"))
