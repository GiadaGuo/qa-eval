"""在本地 jhsjk_article 快照上离线验证 02080101 / 02080106 Calculator。

用途：
- 02080106 文本纯净程度：对真实快照子集给出可解释的规则命中统计；
- 02080101 文本困惑度：仅验证链路可用性（fixtures 下的字符级玩具模型，
  词表只覆盖 8 句政策新闻语料，真实文章 PPL 数值无业务含义）；
- 追加一组**明确标记**的合成异常样本（synthetic= 前缀），验证规则在
  真实长度文本上的召回，其结果不得与真实样本混计。

用法：
    .venv/bin/python scripts/validate_ppl_purity_on_snapshot.py \
        [--snapshot PATH] [--limit 2000] [--ppl-limit 500]

MySQL 实源连通时可直接替换 --snapshot 指向新导出的 raw_snapshot.jsonl。
"""

from __future__ import annotations

import argparse
import json
import tempfile
from uuid import uuid4
from pathlib import Path

from qa_eval.calculators.protocol import CalculatorContext
from qa_eval.calculators.text_perplexity import TextPerplexityCalculator
from qa_eval.calculators.text_purity import TextPurityCalculator

REPO = Path(__file__).resolve().parents[1]
DEFAULT_SNAPSHOT = (
    REPO
    / "data/artifacts/datasets/88488e69-e358-4256-a6f1-3fccf8131575/"
    / "01a181b8-17b2-7bb7-92c3-bfdcf16bba09/raw_snapshot.jsonl"
)
FIXTURE_MODEL = REPO / "tests/fixtures/kenlm/test-zh-char.arpa"

# 合成异常样本：仅用于验证规则召回，sample_id 以 synthetic= 前缀显式标记
SYNTHETIC_ANOMALIES = [
    "锟斤拷锟斤拷锟斤拷本文为测试乱码样本锟斤拷锟斤拷，用于验证规则召回。",
    "！！！@@@@######## ！！！！巨大优惠狂欢来袭，点击此处立即购买！@@@@######## ！！！",
    "<div class='wrap'><p>正文段落</p><span>残留标签</span>&nbsp;&amp;</div><br/>",
    "这篇报道正文内容第一段。\n- 12 -\n第 2 页 共 10 页\n版权所有 人民网 保留所有权利",
    "扫描二维码关注公众号领取资料，添加微信号 jhsjk_kefu咨询更多，限时特价！",
    "正文出现了明显的排版残留。\n……\n下一页　返回列表\n……\n正文继续。",
    "政务通报：锟斤拷数据损坏提示烫烫烫烫烫烫屯屯屯，请忽略本合成样本。",
    "重磅！！！！！超值优惠券免费领取，订购热线：400-800-8888，手慢无！！！",
]


def _record(version_id: uuid.UUID, sample_id: str, text: str | None) -> dict:
    content = {
        "content_id": "body",
        "modality": "text",
        "role": "article.body.plain",
        "presence": "PRESENT" if text is not None else "NULL",
        "attributes": {},
    }
    if text is not None:
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
        "lineage": {"input_fingerprint": f"sha256:{'0' * 64}", "transforms": []},
    }


def _run(calculator, records: list[dict], output_dir: Path, parameters: dict):
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "input.jsonl"
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records),
        encoding="utf-8",
    )
    context = CalculatorContext(
        run_id=uuid4(),
        stage_run_id=uuid4(),
        dataset_version_id=uuid4(),
        metric_key="02080106",
        metric_version="1.0.0",
        input_path=path,
        output_dir=output_dir,
        input_schema_ref="2.0",
    )
    return calculator.execute(context, parameters)


def _load_real(snapshot: Path, limit: int) -> list[dict]:
    version_id = uuid4()
    records = []
    with snapshot.open(encoding="utf-8") as handle:
        for line in handle:
            if len(records) >= limit:
                break
            row = json.loads(line)
            text = row.get("plain_text_content")
            if not isinstance(text, str):
                text = text.strip() if isinstance(text, str) else None
            records.append(_record(version_id, row["id"], text or None))
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--limit", type=int, default=2000)
    parser.add_argument("--ppl-limit", type=int, default=500)
    args = parser.parse_args()

    real = _load_real(args.snapshot, args.limit)
    synthetic = [
        _record(uuid4(), f"synthetic={index}", text)
        for index, text in enumerate(SYNTHETIC_ANOMALIES, start=1)
    ]
    print(
        f"snapshot={args.snapshot}\nreal_records={len(real)} "
        f"synthetic_anomalies={len(synthetic)} (sample_id 前缀 synthetic= 显式标记)"
    )

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        # ---- 02080106 真实子集 ----
        purity_real = _run(
            TextPurityCalculator(), real, tmp_path / "purity-real", {}
        )
        print("\n== 02080106 文本纯净程度 · 真实快照子集 ==")
        print(json.dumps(purity_real.population, ensure_ascii=False))
        for item in purity_real.observations:
            print(f"  {item['key']} = {item['value']}")
        stats = next(
            (
                item
                for item in purity_real.diagnostics
                if "rule_stats" in item
            ),
            {},
        ).get("rule_stats", {})
        print("  rule_stats:", json.dumps(stats, ensure_ascii=False))

        # ---- 02080106 真实子集 + 合成异常（分离统计）----
        purity_mixed = _run(
            TextPurityCalculator(),
            real + synthetic,
            tmp_path / "purity-mixed",
            {},
        )
        mixed_rows = [
            json.loads(line)
            for line in (tmp_path / "purity-mixed" / "purity_violations.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        synthetic_hits = [r for r in mixed_rows if r["sample_id"].startswith("synthetic=")]
        real_hits = [r for r in mixed_rows if not r["sample_id"].startswith("synthetic=")]
        print("\n== 02080106 · 真实 + 合成（分开统计）==")
        print(f"  受影响记录（真实）={len(real_hits)}  受影响记录（合成）={len(synthetic_hits)}/{len(synthetic)}")
        for row in synthetic_hits:
            rules = ",".join(hit["rule_id"] for hit in row["hits"])
            print(f"    {row['sample_id']}: {rules}")

        # ---- 02080101 链路验证（玩具模型，无数值结论）----
        ppl = _run(
            TextPerplexityCalculator(),
            real[: args.ppl_limit],
            tmp_path / "ppl",
            {"model_path": str(FIXTURE_MODEL)},
        )
        print(
            "\n== 02080101 文本困惑度 · 链路验证（字符级玩具模型，"
            "PPL 数值无业务含义）=="
        )
        print(json.dumps(ppl.population, ensure_ascii=False))
        for item in ppl.observations:
            print(f"  {item['key']} = {item['value']}")


if __name__ == "__main__":
    main()
