"""Benchmark policy_structure_compliance throughput on a sample of records."""

import json
import tempfile
import time
from pathlib import Path

from qa_eval.calculators.policy_structure_compliance import (
    PolicyStructureComplianceCalculator,
)
from qa_eval.calculators.protocol import CalculatorContext


def main():
    source_path = Path(
        "data/artifacts/datasets/9ac80218-f44b-4edc-8abd-db1a39c8fdee/"
        "01a1a9a4-c688-7a18-a70b-927c73ab24e0/sample_records.jsonl"
    )
    sample_sizes = [10, 50, 100, 500, 1000, 5000]
    parameters = {
        "html_role": "article.body.html",
        "plain_role": "article.body.plain",
        "title_role": "article.title",
        "max_html_length_chars": 300000,
        "long_text_policy": "REJECT",
        "evidence_sample_limit": 0,
    }
    calculator = PolicyStructureComplianceCalculator()
    for n in sample_sizes:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "sample.jsonl"
            output_dir = root / "output"
            with open(source_path, "r", encoding="utf-8") as src, open(
                input_path, "w", encoding="utf-8"
            ) as dst:
                for i, line in enumerate(src):
                    if i >= n:
                        break
                    dst.write(line)
            context = CalculatorContext(
                run_id="benchmark-run",
                stage_run_id="benchmark-stage",
                dataset_version_id="benchmark-version",
                metric_key="0204",
                metric_version="2.0.0",
                input_path=input_path,
                output_dir=output_dir,
                input_schema_ref="2.0",
            )
            start = time.perf_counter()
            output = calculator.execute(context, parameters)
            elapsed = time.perf_counter() - start
            print(
                f"n={n:>4} elapsed={elapsed:>7.2f}s "
                f"per_record={elapsed/n*1000:>7.2f}ms "
                f"status={output.status} "
                f"population={output.population}"
            )


if __name__ == "__main__":
    main()
