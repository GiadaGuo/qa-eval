"""Reproducible radar chart for jhsjk_article V0.1 quality report.

Reads the fixed quality-report.json and renders an SVG radar chart.
N/A dimensions are rendered as a dashed ring/label instead of being plotted
as 0, to avoid confusing "not evaluated" with "actual zero score".
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

REPORT_PATH = Path(__file__).with_name("quality-report.json")
OUTPUT_PATH = Path(__file__).with_suffix(".svg")

DIMENSIONS = [f"020{i}" for i in range(1, 9)]
LABELS = {
    "0201": "格式规范",
    "0202": "安全规范",
    "0203": "标注规范",
    "0204": "结构完整",
    "0205": "内容真实",
    "0206": "内容一致",
    "0207": "类型一致",
    "0208": "内容干净",
}


def polar(angle: float, radius: float, cx: float, cy: float) -> tuple[float, float]:
    return cx + radius * math.cos(angle), cy + radius * math.sin(angle)


def render(report: dict) -> str:
    raw_radar = report.get("radar", {})
    radar: dict[str, dict[str, object]] = {}
    if isinstance(raw_radar, dict):
        for dim, score in raw_radar.items():
            radar[dim] = {"score": score}
    else:
        radar = {item["dimension"]: item for item in raw_radar}

    width = 640
    height = 520
    cx, cy = width / 2, height / 2 + 10
    max_radius = 180
    padding = 46

    lines: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '  <rect width="100%" height="100%" fill="#fafafa"/>',
        '  <text x="50%" y="28" text-anchor="middle" font-family="sans-serif" font-size="18" font-weight="bold" fill="#333">jhsjk_article 综合质量雷达图（V0.1）</text>',
    ]

    # Grid rings at 25, 50, 75, 100
    for pct in (25, 50, 75, 100):
        r = max_radius * pct / 100
        lines.append(
            f'  <circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="none" stroke="#ddd" stroke-width="1"/>'
        )
        lines.append(
            f'  <text x="{cx + 4:.1f}" y="{cy - r + 12:.1f}" font-family="sans-serif" font-size="10" fill="#999">{pct}</text>'
        )

    n = len(DIMENSIONS)
    angles = [math.pi / 2 - 2 * math.pi * i / n for i in range(n)]

    # Axis lines and labels
    for dim, angle in zip(DIMENSIONS, angles):
        x, y = polar(angle, max_radius, cx, cy)
        lines.append(
            f'  <line x1="{cx:.1f}" y1="{cy:.1f}" x2="{x:.1f}" y2="{y:.1f}" stroke="#ddd" stroke-width="1"/>'
        )
        lx, ly = polar(angle, max_radius + padding, cx, cy)
        anchor = "middle"
        dx = 0
        if abs(math.cos(angle)) > 0.7:
            anchor = "start" if math.cos(angle) > 0 else "end"
        lines.append(
            f'  <text x="{lx + dx:.1f}" y="{ly + 4:.1f}" text-anchor="{anchor}" font-family="sans-serif" font-size="12" fill="#444">{dim} {LABELS[dim]}</text>'
        )

    # Build polygon points for evaluated dimensions only
    polygon_points: list[tuple[float, float]] = []
    na_annotations: list[tuple[str, float, float]] = []
    for dim, angle in zip(DIMENSIONS, angles):
        item = radar.get(dim, {})
        score = item.get("score")
        if score is None:
            # Draw a small N/A tick near the axis end
            tx, ty = polar(angle, max_radius + 14, cx, cy)
            na_annotations.append((dim, tx, ty))
            continue
        r = max(0.0, min(100.0, float(score))) / 100 * max_radius
        polygon_points.append(polar(angle, r, cx, cy))

    if polygon_points:
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in polygon_points)
        lines.append(
            f'  <polygon points="{pts}" fill="rgba(59,130,246,0.18)" stroke="#2563eb" stroke-width="2"/>'
        )
        for x, y in polygon_points:
            lines.append(
                f'  <circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="#2563eb"/>'
            )

    for dim, tx, ty in na_annotations:
        lines.append(
            f'  <text x="{tx:.1f}" y="{ty:.1f}" text-anchor="middle" font-family="sans-serif" font-size="9" fill="#9ca3af" font-style="italic">N/A</text>'
        )

    # Legend / summary
    score = report.get("scoring", {}).get("evaluated_score")
    coverage = report.get("scoring", {}).get("effective_coverage")
    lines.append(
        f'  <text x="{width - 20}" y="{height - 50}" text-anchor="end" font-family="sans-serif" font-size="12" fill="#555">已评测得分: {score:.5f}</text>'
    )
    lines.append(
        f'  <text x="{width - 20}" y="{height - 30}" text-anchor="end" font-family="sans-serif" font-size="12" fill="#555">有效覆盖率: {coverage * 100:.4f}%</text>'
    )
    lines.append(
        f'  <text x="{width - 20}" y="{height - 10}" text-anchor="end" font-family="sans-serif" font-size="10" fill="#888">固定 Run: {report.get("run_id", "")}</text>'
    )

    lines.append("</svg>")
    return "\n".join(lines) + "\n"


def main() -> int:
    if not REPORT_PATH.exists():
        print(f"error: {REPORT_PATH} not found", file=sys.stderr)
        return 1
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    svg = render(report)
    OUTPUT_PATH.write_text(svg, encoding="utf-8")
    print(f"wrote {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
