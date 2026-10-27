#!/usr/bin/env python3
"""Validate a dataset-document evaluation result and optionally render Markdown."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

METRICS = {
    "0101": ("基本信息完整性", ["数据集规模", "格式规范", "文件结构", "访问渠道", "技术支持方式"]),
    "0102": ("内容特征完整性", ["模态类型", "数据分布情况", "标签类别统计", "样本示例", "局限性说明"]),
    "0103": ("建设过程完整性", ["数据来源", "采集方法", "加工处理流程", "标注规范", "版本控制记录"]),
    "0104": ("应用说明完整性", ["使用许可", "目标应用场景", "评估方法", "基准测试结果", "典型应用案例"]),
}
ITEM_STATES = {"SATISFIED", "INSUFFICIENT", "MISSING", "UNVERIFIABLE", "ERROR"}
FIXABILITY = {"AUTO_FIXABLE", "DATA_DERIVABLE", "REQUIRES_HUMAN_INPUT"}
MODES = {"document-only", "evidence-assisted"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def validate(result: dict[str, Any], document_path: Path | None) -> list[str]:
    errors: list[str] = []
    require(result.get("schema_version") == "1.0", "schema_version must be '1.0'", errors)
    require(result.get("rubric_version") == "1.0", "rubric_version must be '1.0'", errors)

    run = result.get("run")
    require(isinstance(run, dict), "run must be an object", errors)
    if isinstance(run, dict):
        require(bool(run.get("run_id")), "run.run_id is required", errors)
        require(bool(run.get("evaluated_at")), "run.evaluated_at is required", errors)
        require(run.get("evaluation_mode") in MODES, "run.evaluation_mode is invalid", errors)

    document = result.get("document")
    require(isinstance(document, dict), "document must be an object", errors)
    lines: list[str] | None = None
    if isinstance(document, dict):
        require(bool(document.get("path")), "document.path is required", errors)
        require(bool(SHA256_RE.match(str(document.get("sha256", "")))), "document.sha256 is invalid", errors)
        require(isinstance(document.get("line_count"), int) and document["line_count"] > 0, "document.line_count must be positive", errors)
        if document_path:
            require(document_path.is_file(), f"document file not found: {document_path}", errors)
            if document_path.is_file():
                actual_hash = sha256_file(document_path)
                require(document.get("sha256") == actual_hash, "document SHA-256 does not match", errors)
                try:
                    lines = document_path.read_text(encoding="utf-8").splitlines()
                    require(document.get("line_count") == len(lines), "document line_count does not match", errors)
                except UnicodeDecodeError:
                    errors.append("document is not valid UTF-8")

    evidence_files = result.get("evidence_files")
    require(isinstance(evidence_files, list), "evidence_files must be an array", errors)
    evidence_index: dict[tuple[str, str], dict[str, Any]] = {}
    if isinstance(evidence_files, list):
        for index, evidence in enumerate(evidence_files):
            prefix = f"evidence_files[{index}]"
            require(isinstance(evidence, dict), f"{prefix} must be an object", errors)
            if not isinstance(evidence, dict):
                continue
            path = evidence.get("path")
            digest = evidence.get("sha256")
            require(bool(path), f"{prefix}.path is required", errors)
            require(bool(SHA256_RE.match(str(digest or ""))), f"{prefix}.sha256 is invalid", errors)
            require(bool(evidence.get("kind")), f"{prefix}.kind is required", errors)
            if path and digest:
                evidence_index[(path, digest)] = evidence
                local_path = Path(path)
                if local_path.is_file():
                    require(sha256_file(local_path) == digest, f"{prefix} SHA-256 does not match", errors)

    if isinstance(run, dict) and run.get("evaluation_mode") == "document-only":
        require(evidence_files == [], "document-only mode requires empty evidence_files", errors)

    metrics = result.get("metrics")
    require(isinstance(metrics, list) and len(metrics) == 4, "metrics must contain exactly four objects", errors)
    actual_scores: dict[str, Any] = {}
    satisfied_total = 0
    any_error = False
    if isinstance(metrics, list):
        codes = [metric.get("metric_code") for metric in metrics if isinstance(metric, dict)]
        require(codes == list(METRICS), "metrics must be ordered 0101 through 0104", errors)
        for metric_index, metric in enumerate(metrics):
            if not isinstance(metric, dict):
                errors.append(f"metrics[{metric_index}] must be an object")
                continue
            code = metric.get("metric_code")
            prefix = f"metric {code or metric_index}"
            if code not in METRICS:
                errors.append(f"{prefix}: invalid metric_code")
                continue
            expected_name, expected_items = METRICS[code]
            require(metric.get("metric_name") == expected_name, f"{prefix}: metric_name mismatch", errors)
            items = metric.get("items")
            require(isinstance(items, list) and len(items) == 5, f"{prefix}: items must contain five objects", errors)
            satisfied = 0
            item_error = False
            if isinstance(items, list):
                expected_codes = [f"{code}-{position:02d}" for position in range(1, 6)]
                item_codes = [item.get("item_code") for item in items if isinstance(item, dict)]
                require(item_codes == expected_codes, f"{prefix}: item codes/order mismatch", errors)
                for item_index, item in enumerate(items):
                    if not isinstance(item, dict):
                        errors.append(f"{prefix} item {item_index}: must be an object")
                        continue
                    item_prefix = f"item {item.get('item_code', item_index)}"
                    require(item.get("item_name") == expected_items[item_index], f"{item_prefix}: item_name mismatch", errors)
                    state = item.get("status")
                    require(state in ITEM_STATES, f"{item_prefix}: invalid status", errors)
                    satisfied += state == "SATISFIED"
                    item_error = item_error or state == "ERROR"
                    require(bool(str(item.get("inspected_scope", "")).strip()), f"{item_prefix}: inspected_scope is required", errors)
                    require(bool(str(item.get("reason", "")).strip()), f"{item_prefix}: reason is required", errors)
                    require(bool(str(item.get("suggestion", "")).strip()), f"{item_prefix}: suggestion is required", errors)
                    require(item.get("fixability") in FIXABILITY, f"{item_prefix}: invalid fixability", errors)
                    doc_evidence = item.get("document_evidence")
                    ext_evidence = item.get("external_evidence")
                    require(isinstance(doc_evidence, list), f"{item_prefix}: document_evidence must be an array", errors)
                    require(isinstance(ext_evidence, list), f"{item_prefix}: external_evidence must be an array", errors)
                    if state not in {"MISSING", "ERROR"}:
                        require(bool(doc_evidence), f"{item_prefix}: non-missing judgment needs document evidence", errors)
                    if isinstance(doc_evidence, list):
                        for evidence_index_number, evidence in enumerate(doc_evidence):
                            evidence_prefix = f"{item_prefix} document_evidence[{evidence_index_number}]"
                            require(isinstance(evidence, dict), f"{evidence_prefix} must be an object", errors)
                            if not isinstance(evidence, dict):
                                continue
                            start, end, excerpt = evidence.get("line_start"), evidence.get("line_end"), evidence.get("excerpt")
                            valid_range = isinstance(start, int) and isinstance(end, int) and 1 <= start <= end
                            if isinstance(document, dict) and isinstance(document.get("line_count"), int):
                                valid_range = valid_range and end <= document["line_count"]
                            require(valid_range, f"{evidence_prefix}: invalid line range", errors)
                            require(bool(str(excerpt or "").strip()), f"{evidence_prefix}: excerpt is required", errors)
                            if lines is not None and valid_range:
                                cited = "\n".join(lines[start - 1 : end])
                                require(str(excerpt) in cited, f"{evidence_prefix}: excerpt not found in cited lines", errors)
                    if isinstance(ext_evidence, list):
                        for ext_index, evidence in enumerate(ext_evidence):
                            evidence_prefix = f"{item_prefix} external_evidence[{ext_index}]"
                            require(isinstance(evidence, dict), f"{evidence_prefix} must be an object", errors)
                            if not isinstance(evidence, dict):
                                continue
                            key = (evidence.get("path"), evidence.get("sha256"))
                            require(key in evidence_index, f"{evidence_prefix}: path/hash not registered", errors)
                            require(bool(evidence.get("locator")), f"{evidence_prefix}: locator is required", errors)
                            require("excerpt" in evidence, f"{evidence_prefix}: excerpt is required", errors)

            metric_status = metric.get("status")
            expected_error = item_error
            require(metric_status == ("ERROR" if expected_error else "COMPLETED"), f"{prefix}: status inconsistent with items", errors)
            if expected_error:
                any_error = True
                for field in ("satisfied_count", "required_count", "score"):
                    require(metric.get(field) is None, f"{prefix}: {field} must be null on ERROR", errors)
                require(bool(str(metric.get("error", "")).strip()), f"{prefix}: error message is required", errors)
                actual_scores[code] = None
            else:
                expected_score = satisfied / 5 * 100
                require(metric.get("satisfied_count") == satisfied, f"{prefix}: satisfied_count mismatch", errors)
                require(metric.get("required_count") == 5, f"{prefix}: required_count must be 5", errors)
                require(metric.get("score") == expected_score, f"{prefix}: score mismatch", errors)
                require(metric.get("error") is None, f"{prefix}: error must be null when completed", errors)
                actual_scores[code] = expected_score
                satisfied_total += satisfied

    summary = result.get("summary")
    require(isinstance(summary, dict), "summary must be an object", errors)
    if isinstance(summary, dict):
        require(summary.get("scores") == actual_scores, "summary.scores must match metrics", errors)
        require(summary.get("satisfied_items") == satisfied_total, "summary.satisfied_items mismatch", errors)
        expected_total = None if any_error else 20
        require(summary.get("total_items") == expected_total, "summary.total_items mismatch", errors)
        forbidden = {"overall_score", "weighted_score", "verdict"} & set(summary)
        require(not forbidden, f"summary contains forbidden aggregate fields: {sorted(forbidden)}", errors)

    require(isinstance(result.get("limitations"), list), "limitations must be an array", errors)
    return errors


def esc(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_markdown(result: dict[str, Any]) -> str:
    run, document = result["run"], result["document"]
    output = [
        "# 数据集说明文档完整性评测报告",
        "",
        f"- 文档：`{document['path']}`",
        f"- SHA-256：`{document['sha256']}`",
        f"- 评测时间：{run['evaluated_at']}",
        f"- 评测模式：`{run['evaluation_mode']}`",
        f"- Rubric 版本：`{result['rubric_version']}`",
        "",
        "> 本报告评测说明文档完整性，不等同于数据事实准确性、数据质量或质量契约结论。",
        "",
        "## 指标结果",
        "",
        "| 指标 | 名称 | 满足项 | 应满足项 | 分数 | 状态 |",
        "| --- | --- | ---: | ---: | ---: | --- |",
    ]
    for metric in result["metrics"]:
        score = "—" if metric["score"] is None else f"{metric['score']:g}"
        satisfied = "—" if metric["satisfied_count"] is None else metric["satisfied_count"]
        required = "—" if metric["required_count"] is None else metric["required_count"]
        output.append(f"| {metric['metric_code']} | {metric['metric_name']} | {satisfied} | {required} | {score} | {metric['status']} |")

    output.extend(["", "## 检查项明细", ""])
    for metric in result["metrics"]:
        output.extend([f"### {metric['metric_code']} {metric['metric_name']}", "", "| 检查项 | 状态 | 正文证据 | 判定原因 | 修改建议 | 可修复性 |", "| --- | --- | --- | --- | --- | --- |"])
        for item in metric["items"]:
            evidence_parts = []
            for evidence in item["document_evidence"]:
                evidence_parts.append(f"L{evidence['line_start']}-L{evidence['line_end']}: {evidence['excerpt']}")
            if not evidence_parts:
                evidence_parts.append(f"未找到；检查范围：{item['inspected_scope']}")
            output.append(
                f"| {item['item_code']} {esc(item['item_name'])} | {item['status']} | {esc('; '.join(evidence_parts))} | {esc(item['reason'])} | {esc(item['suggestion'])} | {item['fixability']} |"
            )
        output.append("")

    output.extend(["## 修改建议分类", ""])
    for fixability in ("AUTO_FIXABLE", "DATA_DERIVABLE", "REQUIRES_HUMAN_INPUT"):
        output.append(f"### {fixability}")
        output.append("")
        findings = [item for metric in result["metrics"] for item in metric["items"] if item["status"] != "SATISFIED" and item["fixability"] == fixability]
        if findings:
            output.extend([f"- {item['item_code']} {item['item_name']}：{item['suggestion']}" for item in findings])
        else:
            output.append("- 无。")
        output.append("")

    output.extend(["## 外部证据", ""])
    if result["evidence_files"]:
        for evidence in result["evidence_files"]:
            output.append(f"- `{evidence['path']}`（{evidence['kind']}，SHA-256 `{evidence['sha256']}`）")
    else:
        output.append("- 未使用外部证据。")
    output.extend(["", "## 覆盖限制", ""])
    if result["limitations"]:
        output.extend([f"- {limitation}" for limitation in result["limitations"]])
    else:
        output.append("- 无额外限制。")
    output.append("")
    return "\n".join(output)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result", type=Path, help="Evaluation JSON result")
    parser.add_argument("--document", type=Path, help="Source Markdown for hash and evidence validation")
    parser.add_argument("--render-markdown", type=Path, help="Write deterministic Markdown report")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = json.loads(args.result.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"error: cannot read result: {exc}", file=sys.stderr)
        return 2
    if not isinstance(result, dict):
        print("error: result root must be an object", file=sys.stderr)
        return 2

    errors = validate(result, args.document)
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1

    if args.render_markdown:
        args.render_markdown.parent.mkdir(parents=True, exist_ok=True)
        args.render_markdown.write_text(render_markdown(result), encoding="utf-8")
        print(f"valid; rendered {args.render_markdown}")
    else:
        print("valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
