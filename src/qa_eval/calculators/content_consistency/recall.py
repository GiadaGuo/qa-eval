"""Bounded lexical retrieval and optional offline vector retrieval."""

import hashlib
import json
import math
import re
import sqlite3
from collections import deque
from contextlib import contextmanager
from importlib.metadata import version
from pathlib import Path

from qa_eval.calculators.content_consistency.protocol_support import (
    implementation_fingerprint,
)
from qa_eval.calculators.protocol import CalculatorValidationError

CONCEPTS = (
    ("补贴", "补助", "资助", "津贴"),
    ("申请", "申报", "提出申请"),
    ("报送", "提交", "递交", "上传"),
    ("费用", "收费", "金额", "缴费"),
    ("期限", "时限", "截止", "办理时间"),
    ("企业", "公司", "经营主体"),
    ("居民", "个人", "群众"),
    ("公开", "公布", "发布", "公示"),
    ("名额", "数量", "人数"),
    ("实施", "施行", "生效"),
    ("比例", "比率", "占比", "费率"),
    ("申请人", "申报人", "申请者"),
    ("材料", "文件", "附件"),
    ("允许", "许可", "准许"),
)
CONCEPT_PATTERNS = tuple(re.compile("|".join(words)) for words in CONCEPTS)


def _normalize(values):
    norm = math.sqrt(sum(value * value for value in values))
    return tuple(value / norm for value in values) if norm else tuple(values)


def _concept_vector(text):
    vector = [float(bool(pattern.search(text))) for pattern in CONCEPT_PATTERNS]
    # Signed character bigrams retain topic and entity information.
    vector.extend([0.0] * 48)
    for index in range(len(text) - 1):
        digest = hashlib.blake2s(
            text[index : index + 2].encode(), digest_size=2
        ).digest()
        vector[len(CONCEPTS) + digest[0] % 48] += 0.24 if digest[1] % 2 else -0.24
    return _normalize(vector)


def _model_fingerprint(path):
    digest = hashlib.sha256()
    for file in sorted(item for item in path.rglob("*") if item.is_file()):
        digest.update(file.relative_to(path).as_posix().encode() + b"\0")
        with file.open("rb") as stream:
            digest.update(hashlib.file_digest(stream, "sha256").digest())
    return digest.hexdigest()


class Encoder:
    def __init__(self, backend, model_path, connection, batch_size):
        self.model = None
        self.connection = connection
        self.batch_size = batch_size
        self.identity = f"concept:{implementation_fingerprint()}"
        if backend == "bge":
            if not model_path or not Path(model_path).is_dir():
                raise CalculatorValidationError("BGE requires a local model directory")
            path = Path(model_path).resolve()
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:
                raise CalculatorValidationError(
                    "BGE requires sentence-transformers"
                ) from exc
            # Restrict supported checkpoints to the standard local Transformer+pooling layout.
            try:
                modules = json.loads((path / "modules.json").read_text())
                kinds = [module["type"] for module in modules]
                if (
                    kinds
                    not in (
                        [
                            "sentence_transformers.models.Transformer",
                            "sentence_transformers.models.Pooling",
                        ],
                        [
                            "sentence_transformers.models.Transformer",
                            "sentence_transformers.models.Pooling",
                            "sentence_transformers.models.Normalize",
                        ],
                    )
                    or modules[0]["path"]
                    or any(
                        not (path / module["path"]).resolve().is_relative_to(path)
                        for module in modules
                    )
                ):
                    raise ValueError("unsupported sentence-transformers layout")
                self.model = SentenceTransformer(
                    str(path),
                    device="cpu",
                    local_files_only=True,
                    trust_remote_code=False,
                    model_kwargs={"use_safetensors": True},
                )
            except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
                raise CalculatorValidationError(
                    "BGE checkpoint cannot be loaded locally"
                ) from exc
            self.identity = f"bge:{_model_fingerprint(path)}:{version('sentence-transformers')}:{version('transformers')}:{version('torch')}"
        self.misses = 0
        self.hits = 0

    def encode(self, texts):
        for offset in range(0, len(texts), self.batch_size):
            batch = texts[offset : offset + self.batch_size]
            keys = [hashlib.sha256(text.encode()).hexdigest() for text in batch]
            cached = {}
            if self.connection:
                placeholders = ",".join("?" for _ in keys)
                for key, vector in self.connection.execute(
                    f"SELECT key, vector FROM vectors WHERE encoder = ? AND key IN ({placeholders})",
                    (self.identity, *keys),
                ):
                    cached[key] = json.loads(vector)
            missing = {
                key: value
                for key, value in zip(keys, batch, strict=True)
                if key not in cached
            }
            self.misses += len(missing)
            self.hits += len(batch) - len(missing)
            if missing:
                if self.model is None:
                    vectors = map(_concept_vector, missing.values())
                else:
                    vectors = self.model.encode(
                        list(missing.values()),
                        batch_size=self.batch_size,
                        normalize_embeddings=True,
                        show_progress_bar=False,
                    ).tolist()
                fresh = dict(zip(missing, vectors, strict=True))
                if self.connection:
                    with self.connection:
                        self.connection.executemany(
                            "INSERT OR IGNORE INTO vectors VALUES (?, ?, ?)",
                            (
                                (self.identity, key, json.dumps(vector))
                                for key, vector in fresh.items()
                            ),
                        )
                cached.update(fresh)
            yield from (cached[key] for key in keys)


@contextmanager
def open_cache(path):
    if not path:
        yield None
        return
    connection = sqlite3.connect(path, timeout=30)
    try:
        with connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS vectors (encoder TEXT, key TEXT, vector TEXT, PRIMARY KEY (encoder, key)) WITHOUT ROWID"
            )
        yield connection
    finally:
        connection.close()


class CandidateIndex:
    def __init__(self, top_k, posting_limit):
        self.top_k = top_k
        self.posting_limit = posting_limit
        self.postings = {}
        self.truncated_queries = 0

    def query(self, keys):
        possible = set()
        truncated = False
        for key in keys:
            if key in self.postings:
                first, recent, size = self.postings[key]
                possible.update(recent)
                possible.add(first)
                truncated |= size > len(recent) + (first not in recent)
        return possible, truncated

    def insert(self, keys, index):
        for key in keys:
            first, recent, size = self.postings.get(key, (index, None, 0))
            if recent is None:
                recent = deque(maxlen=self.posting_limit - 1)
            if size:
                recent.append(index)
            self.postings[key] = (first, recent, size + 1)

    def select(self, matches, posting_truncated, rank):
        self.truncated_queries += posting_truncated or len(matches) > self.top_k
        return sorted(matches, key=rank)[: self.top_k]


def _lexical_keys(fact):
    subject = fact.subject
    middle = len(subject) // 2
    return (
        ("exact", fact.key),
        ("subject", subject),
        ("prefix", fact.attribute, subject[:3]),
        ("suffix", fact.attribute, subject[-3:]),
        ("middle", fact.kind, subject[middle : middle + 2]),
    )


def _vector_keys(vector):
    # Multi-table sign LSH. Buckets are searched globally within the article.
    for start in range(0, min(len(vector), 60), 10):
        bits = sum(
            1 << index
            for index, value in enumerate(vector[start : start + 10])
            if value > 0
        )
        yield start, bits


def candidates(facts, encoder, top_k, posting_limit):
    lexical = CandidateIndex(top_k, posting_limit)
    semantic = CandidateIndex(top_k, posting_limit)
    vectors = []
    embeddings = (
        encoder.encode([f.subject + " " + f.action + " " + f.excerpt for f in facts])
        if encoder
        else None
    )
    for index, fact in enumerate(facts):
        keys = _lexical_keys(fact)
        matches, truncated = lexical.query(keys)
        matches = {
            old
            for old in matches
            if not fact.excluded_context or not facts[old].excluded_context
        }
        rule = lexical.select(
            matches,
            truncated,
            lambda old, fact=fact: (
                facts[old].key != fact.key,
                (facts[old].condition, facts[old].time_scope, facts[old].unit)
                != (fact.condition, fact.time_scope, fact.unit),
                facts[old].signature == fact.signature,
                old,
            ),
        )
        lexical.insert(keys, index)
        vector = []
        if embeddings is not None:
            current = next(embeddings)
            vector_keys = tuple(_vector_keys(current))
            matches, truncated = semantic.query(vector_keys)
            vector = semantic.select(
                matches,
                truncated,
                lambda old, current=current: (
                    -sum(a * b for a, b in zip(current, vectors[old], strict=True)),
                    old,
                ),
            )
            semantic.insert(vector_keys, index)
            vectors.append(current)
        yield index, rule, vector, lexical.truncated_queries, semantic.truncated_queries
