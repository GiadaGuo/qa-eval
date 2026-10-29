"""02080101 文本困惑程度（KenLM）Calculator 的单元测试。

使用 ``tests/fixtures/kenlm/test-zh-char.arpa``（由
``build_test_arpa.py`` 确定性生成的字符级 3-gram 中文小模型）。
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from qa_eval.calculators.protocol import CalculatorContext, CalculatorValidationError
from qa_eval.calculators.text_perplexity import (
    KenlmModelError,
    TextPerplexityCalculator,
    _load_model,
    score_text,
)
from qa_eval.config import Settings

FIXTURE_MODEL = (
    Path(__file__).parents[1] / "fixtures" / "kenlm" / "test-zh-char.arpa"
)

FLUENT = "我们坚持改革开放的政策"
DISORDERED = "政我建坚设化的现策开"
OOV_TEXT = "量子纠缠超级计算架构"


def _sample(
    version_id,
    sample_id: str,
    *,
    presence="PRESENT",
    text=FLUENT,
    role="article.body.plain",
):
    content = {
        "content_id": "body",
        "modality": "text",
        "role": role,
        "presence": presence,
        "attributes": {},
    }
    if presence == "PRESENT":
        content["payload"] = {"type": "inline_text", "text": text, "format": "plain"}
    return {
        "schema_version": "2.0",
        "dataset_version_id": str(version_id),
        "sample_id": sample_id,
        "contents": [content],
        "annotations": [],
        "metadata": {},
        "source": {
            "source_artifact_id": str(uuid4()),
            "source_record_id": sample_id,
            "source_row": 1,
            "locator": None,
            "attributes": {},
        },
        "lineage": {"input_fingerprint": f"sha256:{'a' * 64}", "transforms": []},
    }


def _context(tmp_path: Path, records, name: str = "ppl") -> CalculatorContext:
    path = tmp_path / f"{name}.jsonl"
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records),
        encoding="utf-8",
    )
    return CalculatorContext(
        run_id=uuid4(),
        stage_run_id=uuid4(),
        dataset_version_id=uuid4(),
        metric_key="02080101",
        metric_version="1.0.0",
        input_path=path,
        output_dir=tmp_path / f"out-{name}",
        input_schema_ref="2.0",
    )


def _no_model_settings(monkeypatch):
    monkeypatch.setattr(
        "qa_eval.calculators.text_perplexity.get_settings",
        lambda: Settings(kenlm_model_path=None),
    )


def test_score_formula_matches_kenlm_score_and_event_convention() -> None:
    model, _ = _load_model(FIXTURE_MODEL)
    tokens = list(FLUENT)
    perplexity, total_log10, events, oov = score_text(model, tokens)

    # KenLM score() 返回 log10 总概率；逐 token 求和必须一致
    direct = model.score(" ".join(tokens), bos=True, eos=True)
    assert total_log10 == pytest.approx(direct, abs=1e-4)
    # BOS 仅作条件、EOS 计入事件：N = tokens + 1（KenLM query 工具口径）
    assert events == len(tokens) + 1
    assert perplexity == pytest.approx(10 ** (-direct / events), rel=1e-4)
    assert oov == 0


def test_fluent_text_gets_lower_perplexity_than_disordered() -> None:
    model, _ = _load_model(FIXTURE_MODEL)
    fluent = score_text(model, list(FLUENT))
    disordered = score_text(model, list(DISORDERED))
    oov = score_text(model, list(OOV_TEXT))
    assert fluent[0] < disordered[0] < oov[0]
    assert oov[3] == len(OOV_TEXT)  # 全部未登录


def test_execute_aggregates_distribution_statistics(tmp_path: Path):
    version = uuid4()
    records = [
        _sample(version, "fluent", text=FLUENT),
        _sample(version, "fluent2", text="国家发展和现代化建设"),
        _sample(version, "disordered", text=DISORDERED),
        _sample(version, "empty", text="   \n"),
        _sample(version, "null", presence="NULL"),
        _sample(version, "single", text="改"),
    ]
    result = TextPerplexityCalculator().execute(
        _context(tmp_path, records),
        {"model_path": str(FIXTURE_MODEL), "high_perplexity_threshold": 10.0},
    )

    observed = {item["key"]: item["value"] for item in result.observations}
    assert observed["evaluable_record_count"] == 3
    model, _ = _load_model(FIXTURE_MODEL)
    expected = sorted(
        score_text(model, list(text))[0]
        for text in (FLUENT, "国家发展和现代化建设", DISORDERED)
    )
    assert observed["mean_perplexity"] == pytest.approx(
        sum(expected) / 3, rel=1e-6
    )
    # nearest-rank：n=3 时 median 取第 2 个次序统计量，p90/p95 取第 3 个
    assert observed["perplexity_median"] == pytest.approx(expected[1], rel=1e-6)
    assert observed["perplexity_p90"] == pytest.approx(expected[2], rel=1e-6)
    assert observed["perplexity_p95"] == pytest.approx(expected[2], rel=1e-6)
    assert observed["high_perplexity_record_ratio"] == pytest.approx(1 / 3)
    assert observed["oov_token_ratio"] == 0.0

    assert result.population == {
        "input_count": 6,
        "evaluated_count": 3,
        "excluded_count": 3,
        "exclusions": [
            {"reason": "TEXT_EMPTY", "count": 1},
            {"reason": "TEXT_NULL", "count": 1},
            {"reason": "TOO_FEW_TOKENS", "count": 1},
        ],
    }
    # 诊断包含模型指纹与口径说明
    info = result.diagnostics[-1]
    assert info["model_fingerprint"].startswith("sha256:")
    assert info["model_order"] == 3
    assert info["bos_eos_policy"].startswith("BOS conditioning-only")
    assert info["formula"] == "PPL = 10 ** (-log10_total / N)"


def test_execute_reports_oov_ratio_and_keeps_raw_perplexity_only(tmp_path: Path):
    version = uuid4()
    records = [
        _sample(version, "fluent", text=FLUENT),
        _sample(version, "oov", text=OOV_TEXT),
    ]
    result = TextPerplexityCalculator().execute(
        _context(tmp_path, records), {"model_path": str(FIXTURE_MODEL)}
    )
    observed = {item["key"]: item["value"] for item in result.observations}
    assert 0.0 < observed["oov_token_ratio"] < 1.0
    # PPL 是原始统计，不存在任何 0~1 质量分映射
    assert observed["mean_perplexity"] > 1.0
    coverage = result.semantic_coverage
    assert coverage["level"] == "FULL"
    assert any("0~1" in item or "质量分" in item for item in coverage["limitations"])


def test_execute_writes_per_record_evidence_without_content(tmp_path: Path):
    version = uuid4()
    records = [_sample(version, "fluent", text=FLUENT)]
    result = TextPerplexityCalculator().execute(
        _context(tmp_path, records), {"model_path": str(FIXTURE_MODEL)}
    )
    evidence = result.evidence[0]
    assert evidence.kind == "PERPLEXITY_SAMPLES"
    assert evidence.attributes["model_fingerprint"].startswith("sha256:")
    rows = [json.loads(line) for line in evidence.path.read_text().splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["sample_id"] == "fluent"
    assert row["status"] == "EVALUATED"
    assert row["token_count"] == len(FLUENT)
    assert row["event_count"] == len(FLUENT) + 1
    assert row["log10_probability"] == pytest.approx(model_score(), abs=1e-3)
    assert row["perplexity"] == pytest.approx(
        10 ** (-row["log10_probability"] / row["event_count"]), rel=1e-3
    )
    assert row["oov_token_count"] == 0
    # 证据不含正文内容
    assert FLUENT not in evidence.path.read_text()


def model_score() -> float:
    model, _ = _load_model(FIXTURE_MODEL)
    return model.score(" ".join(FLUENT), bos=True, eos=True)


def test_missing_model_refuses_to_execute(monkeypatch, tmp_path: Path):
    _no_model_settings(monkeypatch)
    calculator = TextPerplexityCalculator()
    context = _context(tmp_path, [], "no-model")
    with pytest.raises(KenlmModelError, match="refusing to fabricate"):
        calculator.validate(context, {})


def test_model_path_falls_back_to_settings(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        "qa_eval.calculators.text_perplexity.get_settings",
        lambda: Settings(kenlm_model_path=str(FIXTURE_MODEL)),
    )
    version = uuid4()
    records = [_sample(version, "fluent")]
    result = TextPerplexityCalculator().execute(_context(tmp_path, records), {})
    observed = {item["key"]: item["value"] for item in result.observations}
    assert observed["evaluable_record_count"] == 1
    assert observed["mean_perplexity"] > 0


def test_broken_model_path_raises_clear_error(monkeypatch, tmp_path: Path):
    _no_model_settings(monkeypatch)
    calculator = TextPerplexityCalculator()
    context = _context(tmp_path, [], "bad-model")
    calculator.validate(context, {"model_path": str(tmp_path / "missing.arpa")})
    with pytest.raises(KenlmModelError, match="missing"):
        calculator.execute(
            context, {"model_path": str(tmp_path / "missing.arpa")}
        )


def test_model_is_cached_per_process_and_reloaded_on_change(tmp_path: Path):
    import shutil

    copy = tmp_path / "copy.arpa"
    shutil.copy(FIXTURE_MODEL, copy)
    first, _ = _load_model(copy)
    second, _ = _load_model(copy)
    assert first is second
    # 替换模型文件（mtime/size 变化）后重新加载
    copy.write_bytes(
        copy.read_bytes().replace(b"1.000000", b"0.999999", 1) or copy.read_bytes()
    )
    third, _ = _load_model(copy)
    assert third is not first


def test_validate_rejects_bad_parameters(monkeypatch, tmp_path: Path):
    _no_model_settings(monkeypatch)
    calculator = TextPerplexityCalculator()
    context = _context(tmp_path, [], "validate")
    with pytest.raises(CalculatorValidationError, match="SampleRecord 2.0"):
        invalid = CalculatorContext(**{**context.__dict__, "input_schema_ref": "1.0"})
        calculator.validate(invalid, {"model_path": str(FIXTURE_MODEL)})
    with pytest.raises(CalculatorValidationError, match="tokenizer"):
        calculator.validate(
            context,
            {"model_path": str(FIXTURE_MODEL), "tokenizer": "jieba"},
        )
    with pytest.raises(CalculatorValidationError, match="min_token_count"):
        calculator.validate(
            context, {"model_path": str(FIXTURE_MODEL), "min_token_count": 0}
        )
    with pytest.raises(CalculatorValidationError, match="high_perplexity_threshold"):
        calculator.validate(
            context, {"model_path": str(FIXTURE_MODEL), "high_perplexity_threshold": 0}
        )


def test_whitespace_tokenizer_uses_space_separated_tokens(tmp_path: Path):
    version = uuid4()
    records = [_sample(version, "spaced", text="我们 坚持 改革 开放")]
    result = TextPerplexityCalculator().execute(
        _context(tmp_path, records),
        {"model_path": str(FIXTURE_MODEL), "tokenizer": "whitespace"},
    )
    rows = [json.loads(line) for line in result.evidence[0].path.read_text().splitlines()]
    assert rows[0]["token_count"] == 4
    assert rows[0]["event_count"] == 5
