"""Resolve HTML/plain body slots for the original within-body rules and recall."""

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

from qa_eval.calculators.content_consistency.calculator import (
    BodyContentConsistencyCalculator,
    _count,
    evaluate,
)
from qa_eval.calculators.loader import TrustedCalculatorLoader
from qa_eval.calculators.protocol import CalculatorValidationError
from qa_eval.contracts.datasets import ContentItem, InlineTextPayload, SampleRecordV2


class LogicalBodyContentConsistencyCalculator(BodyContentConsistencyCalculator):
    version = "2.1.0"

    @staticmethod
    def source_paths():
        return (*BodyContentConsistencyCalculator.source_paths(), Path(__file__))

    @staticmethod
    def _stream(
        context,
        parameters,
        destination,
        totals,
        distributions,
        encoder,
        checkpoint_path,
    ):
        if context.input_schema_ref != "3.0" or context.resolved_records is None:
            raise CalculatorValidationError("body consistency requires a v3 Resolver")
        if checkpoint_path != ":memory:":
            digest = TrustedCalculatorLoader.code_digest(
                LogicalBodyContentConsistencyCalculator()
            ).split(":")[1]
            checkpoint_path = checkpoint_path.with_suffix(f".{digest}.sqlite3")
        resumed = 0
        persistent = checkpoint_path != ":memory:"
        with (
            closing(sqlite3.connect(checkpoint_path, timeout=30)) as checkpoint,
            destination.open("w", encoding="utf-8") as output,
        ):
            checkpoint.execute(
                "CREATE TABLE IF NOT EXISTS decisions (line INTEGER PRIMARY KEY, fingerprint TEXT, row TEXT)"
            )
            pending = []
            for line, resolved in enumerate(context.resolved_records(), 1):
                fingerprint = None
                if persistent:
                    payload = [
                        str(resolved.sample_id),
                        {key: asdict(value) for key, value in resolved.values.items()},
                    ]
                    fingerprint = hashlib.sha256(
                        json.dumps(payload, sort_keys=True).encode()
                    ).hexdigest()
                cached = (
                    checkpoint.execute(
                        "SELECT row FROM decisions WHERE line=? AND fingerprint=?",
                        (line, fingerprint),
                    ).fetchone()
                    if persistent
                    else None
                )
                if cached:
                    [serialized] = cached
                    row = json.loads(serialized)
                    resumed += 1
                else:
                    contents = []
                    origins = {}
                    for key, fmt in (("html", "html"), ("plain", "plain")):
                        value = resolved.values[key]
                        if value.status in {"UNBOUND", "MISSING", "NULL"}:
                            continue
                        if value.status not in {"PRESENT", "EMPTY"}:
                            raise CalculatorValidationError(
                                "Unable to resolve body", reason=value.status
                            )
                        origins[key] = asdict(value.origin)
                        contents.append(
                            ContentItem(
                                content_id=key,
                                role=f"article.body.{key}",
                                modality="text",
                                presence="PRESENT",
                                payload=InlineTextPayload(
                                    type="inline_text", text=value.value, format=fmt
                                ),
                            )
                        )
                    record = SampleRecordV2.model_construct(
                        sample_id=str(resolved.sample_id),
                        contents=contents,
                    )
                    row = evaluate(record, parameters, encoder)
                    row["attributes"]["source_origins"] = origins
                    serialized = json.dumps(
                        row, ensure_ascii=False, separators=(",", ":")
                    )
                    if persistent:
                        pending.append((line, fingerprint, serialized))
                _count(totals, distributions, row)
                output.write(serialized + "\n")
                if len(pending) == 64:
                    BodyContentConsistencyCalculator._commit(checkpoint, pending)
                if context.progress and totals["input_count"] % 100 == 0:
                    context.progress(
                        totals["input_count"],
                        {"evaluable": totals["evaluable_record_count"]},
                    )
            BodyContentConsistencyCalculator._commit(checkpoint, pending)
        return resumed
