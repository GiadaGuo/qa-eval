"""Platform BGE adapter for bounded full-field comparison."""

import math
import os
from functools import cache, lru_cache
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from threading import BoundedSemaphore

from qa_eval.calculators.content_consistency import recall
from qa_eval.calculators.field_relation_consistency.engine import unknown
from qa_eval.calculators.field_relation_consistency.local_matching import (
    threshold_decision,
)
from qa_eval.calculators.protocol import CalculatorValidationError

MODEL_ID = "platform-bge"
BATCH_SIZE = 32
MODEL_ACCESS = BoundedSemaphore(1)


@lru_cache(maxsize=2)
def _checkpoint_digest(path, files):
    return recall._model_fingerprint(Path(path))


def model_configuration():
    location = os.getenv("QA_EVAL_0206_BGE_MODEL_DIR")
    if not location:
        raise CalculatorValidationError("平台尚未配置本地 BGE 模型")
    path = Path(location).resolve()
    if not all(
        (path / filename).is_file()
        for filename in ("modules.json", "config.json", "tokenizer_config.json")
    ) or not any(path.glob("*.safetensors")):
        raise CalculatorValidationError("平台 BGE 本地检查点不完整")
    try:
        packages = ":".join(
            version(name) for name in ("sentence-transformers", "transformers", "torch")
        )
        files = tuple(
            (
                str(file.relative_to(path)),
                stat.st_size,
                stat.st_mtime_ns,
                stat.st_ctime_ns,
            )
            for file in sorted(path.rglob("*"))
            if file.is_file() and (stat := file.stat())
        )
        identity = f"bge:{_checkpoint_digest(str(path), files)}:{packages}"
    except PackageNotFoundError as exc:
        raise CalculatorValidationError("平台缺少本地 BGE 运行依赖") from exc
    except OSError as exc:
        raise CalculatorValidationError("平台 BGE 模型文件不可读取") from exc
    return path, identity


class Encoder(recall.Encoder):
    """Reuse the offline provider and prevent tokenizer-side truncation."""

    def fits(self, text):
        prompt = getattr(self.model, "prompts", {}).get(
            getattr(self.model, "default_prompt_name", None), ""
        )
        return (
            len(self.model.tokenizer(prompt + text, truncation=False)["input_ids"])
            <= self.model.max_seq_length
        )


def split_field(text, parameters, encoder):
    """Return nonblank slices of the full resolved value or reject the entire field."""
    chunk_chars = parameters["chunk_chars"]
    max_chunks = parameters["max_chunks"]
    if len(text) > chunk_chars * max_chunks:
        return None
    chunks = []
    for start in range(0, len(text), chunk_chars):
        pending = [(start, min(start + chunk_chars, len(text)))]
        while pending:
            lo, hi = pending.pop()
            value = text[lo:hi]
            if not value.strip():
                continue
            if encoder.fits(value):
                chunks.append((lo, hi, value))
                if len(chunks) > max_chunks:
                    return None
            elif hi - lo > 1:
                middle = (lo + hi) // 2
                pending.extend(((middle, hi), (lo, middle)))
            else:
                return None
    return chunks


def encode_window(encoder, texts):
    vectors = {}
    items = list(dict.fromkeys(texts))
    for offset in range(0, len(items), BATCH_SIZE):
        batch = items[offset : offset + BATCH_SIZE]
        try:
            encoded = list(encoder.encode(batch))
            for text, vector in zip(batch, encoded, strict=True):
                if vector and all(math.isfinite(value) for value in vector):
                    norm = math.sqrt(math.fsum(value * value for value in vector))
                    if norm and math.isfinite(norm):
                        vectors[text] = tuple(value / norm for value in vector)
        except Exception:  # noqa: BLE001 - isolate the offline provider
            # Keep completed batches. Missing vectors cause the affected comparisons to abstain.
            for text in batch:
                vectors.pop(text, None)
    return vectors


@cache
def _numpy():
    try:
        import numpy
    except ImportError:
        return None
    return numpy


def cosine_rows(left, right, vectors):
    a = [vectors[chunk[2]] for chunk in left]
    b = [vectors[chunk[2]] for chunk in right]
    if np := _numpy():
        # BGE's runtime already includes NumPy. Vectorize the bounded chunk matrix.
        return (np.asarray(a) @ np.asarray(b).T).tolist()
    return [
        [
            math.fsum(x * y for x, y in zip(query, candidate, strict=True))
            for candidate in b
        ]
        for query in a
    ]


def match(parameters, left, right, vectors):
    details = {
        "algorithm": "embedding-cosine-v1",
        "model_identity": parameters["model_identity"],
        "aggregation": parameters["aggregation"],
        "chunk_chars": parameters["chunk_chars"],
        "max_chunks": parameters["max_chunks"],
        "threshold_version": parameters["threshold_version"],
        "fail_below": parameters["fail_below"],
        "pass_at": parameters["pass_at"],
    }
    if left is None or right is None:
        return unknown("EMBEDDING_CHUNK_LIMIT", **details)
    if not left or not right:
        return unknown("INSUFFICIENT_EMBEDDING_TEXT", **details)
    details.update(left_chunk_count=len(left), right_chunk_count=len(right))
    all_chunks = (*left, *right)
    if any(chunk[2] not in vectors for chunk in all_chunks):
        return unknown("EMBEDDING_MODEL_FAILED", **details)
    if len({len(vectors[chunk[2]]) for chunk in all_chunks}) != 1:
        return unknown("EMBEDDING_VECTOR_DIMENSION_MISMATCH", **details)
    right_best = [-1.0] * len(right)
    left_best = []
    peak = -2.0
    hit = (0, 0)
    for i, row in enumerate(cosine_rows(left, right, vectors)):
        best = -1.0
        for j, dot in enumerate(row):
            cosine = min(1.0, max(-1.0, dot))
            best = max(best, cosine)
            right_best[j] = max(right_best[j], cosine)
            if cosine > peak:
                peak, hit = cosine, (i, j)
        left_best.append(best)
    cosine = peak
    if parameters["aggregation"] == "bidirectional_mean":
        cosine = (
            math.fsum(left_best) / len(left) + math.fsum(right_best) / len(right)
        ) / 2

    def evidence(chunk):
        start, end, value = chunk
        excerpt = value[:160]
        return {
            "chunk_span": [start, end],
            "excerpt_span": [start, start + len(excerpt)],
            "excerpt": excerpt,
        }

    details.update(
        cosine=cosine,
        peak_cosine=peak,
        left_fragment=evidence(left[hit[0]]),
        right_fragment=evidence(right[hit[1]]),
        coordinate_space="resolved-value-unicode-codepoints",
    )
    return threshold_decision(max(0.0, cosine), parameters, details)
