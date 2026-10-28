# ====================================================
# @Author: Daphne.Guo
# @Date: 2026-10-28
# @Description:
# 冻结 rubric 的加载与完整性校验：路径相对 cwd 解析，
# 回退到仓库根；返回 (rubric, sha256) 供任务快照与报告引用。
# ====================================================

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]


def load_rubric(path_setting: str) -> tuple[dict[str, Any], str]:
    """加载冻结的评测标准 JSON；文件缺失或结构不完整即抛异常。"""

    path = Path(path_setting)
    if not path.is_absolute():
        candidates = [Path.cwd() / path, REPO_ROOT / path]
        path = next(
            (candidate for candidate in candidates if candidate.exists()),
            path,
        )
    if not path.exists():
        raise FileNotFoundError(f"评测标准文件不存在：{path_setting}")

    content = path.read_bytes()
    rubric = json.loads(content)
    _check_rubric(rubric)
    return rubric, hashlib.sha256(content).hexdigest()


def _check_rubric(rubric: dict[str, Any]) -> None:
    metrics = rubric.get("metrics") or []
    if not metrics:
        raise ValueError("评测标准缺少 metrics")
    required = rubric.get("scoring", {}).get("required_count_per_metric")
    for metric in metrics:
        items = metric.get("items") or []
        if not items:
            raise ValueError(f"指标 {metric.get('metric_code')} 缺少检查项")
        if required is not None and len(items) != required:
            raise ValueError(
                f"指标 {metric.get('metric_code')} 检查项数 "
                f"{len(items)} 与冻结要求 {required} 不一致"
            )
