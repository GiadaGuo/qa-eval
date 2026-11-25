"""Record and dataset repetition detectors for 0208."""

import json
import re
import sqlite3
from contextlib import closing
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from qa_eval.calculators.field_relation_consistency.legacy import LegacyAdapter
from qa_eval.calculators.protocol import (
    CalculatorOutput,
    CalculatorValidationError,
    EvidenceOutput,
)
from qa_eval.calculators.text_duplicate_algorithm import (
    PARAGRAPH_SPLIT_PATTERN,
    minhash_signature,
    normalize_text,
    sha256_text,
)
from qa_eval.contracts.datasets import SampleRecordV2

DEFAULT_EXCLUSIONS = (
    r"(?m)^[ \t]*>[^\r\n]*",
    r"(?m)^[ \t]*\|[^\r\n]*\|[ \t]*\r?\n[ \t]*\|[ :|\-]+\|[ \t]*(?=\r?$)",
)
SENTENCE_DELIMITERS = "。！？!?；;."
SENTENCE_SPANS = re.compile(r"[^。！？!?；;.\r\n]+[。！？!?；;.]?")


def paragraph_spans(text):
    start = 0
    for separator in PARAGRAPH_SPLIT_PATTERN.finditer(text):
        yield start, separator.start()
        start = separator.end()
    yield start, len(text)


def ratio(key, numerator, denominator):
    return {
        "key": key,
        "value": numerator / denominator,
        "value_type": "NUMBER",
        "unit": "ratio",
        "numerator": numerator,
        "denominator": denominator,
    }


def write_row(stream, row):
    stream.write(
        json.dumps({"row_id": str(stream.tell()), **row}, ensure_ascii=True) + "\n"
    )


class RepetitionCalculator:
    version = "1.0.0"

    def source_paths(self):
        root = Path(__file__).parent
        return (
            Path(__file__),
            root / "text_duplicate_algorithm.py",
            root / "field_relation_consistency/legacy.py",
            root / "field_relation_consistency/engine.py",
        )

    def validate(self, context, parameters):
        if context.input_schema_ref != "2.0":
            raise CalculatorValidationError("Expected SampleRecord 2.0")
        try:
            self.adapter(parameters)
        except ValueError as exc:
            raise CalculatorValidationError(str(exc)) from exc
        return []

    @staticmethod
    def adapter(parameters):
        return LegacyAdapter(
            {
                str(index): {"value_type": "text"} | selector
                for index, selector in enumerate(parameters["fields"])
            }
        )

    def rows(self, context, parameters):
        adapter = self.adapter(parameters)
        with context.input_path.open(encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    sample = SampleRecordV2.model_validate_json(line)
                    views = adapter.extract(sample)
                    yield (
                        sample.sample_id,
                        [
                            (field.value, field.source_ref)
                            if field.presence == "PRESENT"
                            else (None, field.source_ref)
                            for field in views.values()
                        ],
                    )

    def output(self, path, total, evaluated, observations, evidence_count, diagnostics):
        excluded = total - evaluated
        return CalculatorOutput(
            status="OK" if evaluated else "MISSING",
            status_detail={} if evaluated else {"reason": "INSUFFICIENT_TEXT"},
            observations=observations if evaluated else [],
            population={
                "input_count": total,
                "evaluated_count": evaluated,
                "excluded_count": excluded,
                "exclusions": [{"reason": "INSUFFICIENT_TEXT", "count": excluded}]
                if excluded
                else [],
            },
            semantic_coverage={
                "level": "PARTIAL",
                "covered": ["configured lexical repetition"],
                "excluded": ["semantic repetition"],
                "limitations": [],
            },
            evidence=[
                EvidenceOutput(
                    path=path,
                    kind=self.calculator_key.upper(),
                    schema_ref=f"qa-eval://schemas/evidence/{self.calculator_key}/1.0.0",
                    subject_scope="DATASET"
                    if self.calculator_key == "cross_record_duplicate"
                    else "SAMPLE",
                    record_count=evidence_count,
                )
            ],
            diagnostics=[diagnostics],
        )


class CrossRecordDuplicateCalculator(RepetitionCalculator):
    calculator_key = "cross_record_duplicate"

    def execute(self, context, parameters):
        self.validate(context, parameters)
        context.output_dir.mkdir(parents=True, exist_ok=True)
        path = context.output_dir / "cross_record_duplicate.jsonl"
        with TemporaryDirectory(dir=context.output_dir) as temp:
            index = Path(temp) / "index.sqlite"
            with (
                closing(sqlite3.connect(index)) as db,
                path.open("w", encoding="utf-8") as stream,
            ):
                result = self.scan(context, parameters, db, stream)
        return self.output(path, *result)

    def scan(self, context, parameters, db, stream):
        db.executescript("""
            CREATE TABLE confirmed_groups (id TEXT PRIMARY KEY) WITHOUT ROWID;
            CREATE TABLE matches (later INTEGER, earlier INTEGER,
                PRIMARY KEY(later,earlier)) WITHOUT ROWID;
            CREATE TABLE records (id INTEGER PRIMARY KEY, sample TEXT, refs TEXT);
            CREATE TABLE exact (key TEXT, id INTEGER, PRIMARY KEY(key,id)) WITHOUT ROWID;
            CREATE INDEX exact_by_record ON exact(id);
            CREATE TABLE exact_keepers (key TEXT PRIMARY KEY) WITHOUT ROWID;
            CREATE TABLE shingles (field INTEGER, id INTEGER, value TEXT,
                PRIMARY KEY(field,id)) WITHOUT ROWID;
            CREATE TABLE buckets (field INTEGER, band TEXT, id INTEGER,
                PRIMARY KEY(field,band,id)) WITHOUT ROWID;
            CREATE TABLE flags (id INTEGER PRIMARY KEY, affected INTEGER DEFAULT 0,
                redundant INTEGER DEFAULT 0, candidate INTEGER DEFAULT 0);
        """)
        total = eligible = rows = saturated = near_eligible = omitted = 0
        strategies = set(parameters.get("strategies", ["record_exact"]))
        cap = parameters.get("bucket_limit", 32)
        threshold = parameters.get("near_threshold")
        for total, (sample, fields) in enumerate(self.rows(context, parameters), 1):
            text_values = [
                normalize_text(value) if isinstance(value, str) else ""
                for value, _ in fields
            ]
            values = [
                json.dumps(
                    text_values[index] if isinstance(value, str) else value,
                    ensure_ascii=True,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                if value is not None
                and (not isinstance(value, str) or text_values[index])
                else ""
                for index, (value, _) in enumerate(fields)
            ]
            keys = []
            if "record_exact" in strategies and all(values):
                keys.append(
                    "record:" + sha256_text(json.dumps(values, ensure_ascii=True))
                )
            if "field_exact" in strategies:
                keys.extend(
                    f"field:{i}:" + sha256_text(value)
                    for i, value in enumerate(values)
                    if value
                )
            near = {
                i: {value[j : j + 5] for j in range(len(value) - 4)}
                for i, value in enumerate(text_values)
                if "field_near" in strategies
                and 5 <= len(value) <= parameters.get("near_max_chars", 20000)
            }
            near_eligible += bool(near)
            if not keys and not near:
                continue
            eligible += 1
            refs = [ref for _, ref in fields]
            db.execute(
                "INSERT INTO records VALUES (?,?,?)", (total, sample, json.dumps(refs))
            )
            db.execute("INSERT INTO flags(id) VALUES (?)", (total,))
            db.executemany(
                "INSERT INTO exact VALUES (?,?)", [(key, total) for key in keys]
            )
            for field, shingles in near.items():
                signature = minhash_signature(shingles, 32)
                candidates = set()
                for band in range(0, 32, 4):
                    key = str((band, signature[band : band + 4]))
                    bucket = db.execute(
                        "SELECT id FROM buckets WHERE field=? AND band=? ORDER BY id LIMIT ?",
                        (field, key, cap),
                    ).fetchall()
                    candidates.update(row[0] for row in bucket)
                    if len(bucket) < cap:
                        db.execute(
                            "INSERT INTO buckets VALUES (?,?,?)", (field, key, total)
                        )
                    else:
                        saturated += 1
                if candidates:
                    db.executemany(
                        "UPDATE flags SET candidate=1 WHERE id=?",
                        [(item,) for item in sorted(candidates | {total})],
                    )
                for other in sorted(candidates):
                    previous = set(
                        json.loads(
                            db.execute(
                                "SELECT value FROM shingles WHERE field=? AND id=?",
                                (field, other),
                            ).fetchone()[0]
                        )
                    )
                    similarity = len(previous & shingles) / len(previous | shingles)
                    confirmed = threshold is not None and similarity >= threshold
                    group_id = sha256_text(f"{other},{total},")
                    if confirmed:
                        db.execute(
                            "INSERT OR IGNORE INTO confirmed_groups VALUES (?)",
                            (group_id,),
                        )
                        db.execute(
                            "UPDATE flags SET affected=1 WHERE id IN (?,?)",
                            (other, total),
                        )
                        db.execute(
                            "INSERT OR IGNORE INTO matches VALUES (?,?)", (total, other)
                        )
                    if rows >= parameters.get("near_evidence_limit", 10000):
                        omitted += 1
                        continue
                    representative, old_refs = db.execute(
                        "SELECT sample, refs FROM records WHERE id=?", (other,)
                    ).fetchone()
                    write_row(
                        stream,
                        {
                            "group_id": ("duplicate:" if confirmed else "candidate:")
                            + group_id,
                            "strategy": "field_near",
                            "sample_id": sample,
                            "representative_sample_id": representative,
                            "field": refs[field],
                            "representative_field": json.loads(old_refs)[field],
                            "determination": "CONFIRMED" if confirmed else "CANDIDATE",
                            "similarity": similarity,
                            "basis": "character-5-shingle Jaccard",
                        },
                    )
                    rows += 1
                db.execute(
                    "INSERT INTO shingles VALUES (?,?,?)",
                    (field, total, json.dumps(sorted(shingles))),
                )
        for key, representative, size in db.execute(
            "SELECT key,min(id),count(*) FROM exact GROUP BY key HAVING count(*)>1"
        ):
            member_digest = sha256()
            for (position,) in db.execute(
                "SELECT id FROM exact WHERE key=? ORDER BY id", (key,)
            ):
                member_digest.update(f"{position},".encode("ascii"))
            group_digest = member_digest.hexdigest()
            db.execute(
                "INSERT OR IGNORE INTO confirmed_groups VALUES (?)", (group_digest,)
            )
            representative_sample = db.execute(
                "SELECT sample FROM records WHERE id=?", (representative,)
            ).fetchone()[0]
            for position, sample, refs in db.execute(
                "SELECT id,sample,refs FROM exact JOIN records USING(id) WHERE key=? ORDER BY id",
                (key,),
            ):
                db.execute("UPDATE flags SET affected=1 WHERE id=?", (position,))
                source = json.loads(refs)
                write_row(
                    stream,
                    {
                        "group_id": "duplicate:" + group_digest,
                        "strategy": "record_exact"
                        if key.startswith("record:")
                        else "field_exact",
                        "sample_id": sample,
                        "member_count": size,
                        "representative_sample_id": representative_sample,
                        "fields": source
                        if key.startswith("record:")
                        else [source[int(key.split(":")[1])]],
                        "determination": "CONFIRMED",
                        "similarity": 1,
                        "basis": "normalized SHA-256",
                    },
                )
                rows += 1
        # A record is redundant only when it directly matches an earlier keeper.
        # In input order, a keeper can never become redundant later.
        for (position,) in db.execute("SELECT id FROM flags ORDER BY id"):
            (has_keeper,) = db.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM exact JOIN exact_keepers USING(key) WHERE exact.id=?
                    UNION ALL
                    SELECT 1 FROM matches JOIN flags ON flags.id=matches.earlier
                    WHERE matches.later=? AND flags.redundant=0
                )
            """,
                (position, position),
            ).fetchone()
            if has_keeper:
                db.execute("UPDATE flags SET redundant=1 WHERE id=?", (position,))
            else:
                db.execute(
                    "INSERT OR IGNORE INTO exact_keepers SELECT key FROM exact WHERE id=?",
                    (position,),
                )
        affected, redundant, candidates = db.execute(
            "SELECT sum(affected),sum(redundant),sum(candidate) FROM flags"
        ).fetchone()
        (groups,) = db.execute("SELECT count(*) FROM confirmed_groups").fetchone()
        observations = [
            {
                "key": "duplicate_group_count",
                "value": groups,
                "value_type": "NUMBER",
                "unit": "group",
            }
        ]
        if eligible:
            observations.extend(
                [
                    ratio("duplicate_redundant_record_ratio", redundant, eligible),
                    ratio("duplicate_affected_record_ratio", affected, eligible),
                    ratio("duplicate_candidate_record_ratio", candidates, eligible),
                    ratio("duplicate_coverage_ratio", eligible, total),
                ]
            )
        return (
            total,
            eligible,
            observations,
            rows,
            {
                "saturated_bucket_probes": saturated,
                "near_eligible_records": near_eligible,
                "omitted_near_evidence_rows": omitted,
            },
        )


class IntraRecordRepetitionCalculator(RepetitionCalculator):
    calculator_key = "intra_record_repetition"

    def execute(self, context, parameters):
        self.validate(context, parameters)
        context.output_dir.mkdir(parents=True, exist_ok=True)
        path = context.output_dir / "intra_record_repetition.jsonl"
        total = eligible = chars = redundant = failed = 0
        minimum = parameters.get("min_length", 12)
        threshold = parameters.get("repetition_threshold")
        try:
            filters = [
                re.compile(pattern)
                for pattern in (
                    *DEFAULT_EXCLUSIONS,
                    *parameters.get("exclude_patterns", []),
                )
            ]
        except re.error as exc:
            raise CalculatorValidationError(
                "Invalid repetition exclusion pattern"
            ) from exc
        local_size = parameters.get("local_ngram_size")
        with path.open("w", encoding="utf-8") as stream:
            for total, (sample, fields) in enumerate(self.rows(context, parameters), 1):
                count = duplicate = 0
                has_eligible_span = False
                evidence = []
                selected_paths = set()
                for value, ref in fields:
                    if not isinstance(value, str) or not value:
                        continue
                    path_key = ref.get("path")
                    if path_key in selected_paths:
                        continue
                    selected_paths.add(path_key)
                    masked = bytearray(len(value))
                    for pattern in filters:
                        for match in pattern.finditer(value):
                            masked[match.start() : match.end()] = b"\x01" * len(
                                match[0]
                            )
                    count += sum(
                        not masked[i] and not ch.isspace() for i, ch in enumerate(value)
                    )
                    intervals = []
                    for spans in (
                        paragraph_spans(value),
                        (match.span() for match in SENTENCE_SPANS.finditer(value)),
                    ):
                        seen = set()
                        for start, end in spans:
                            normalized = normalize_text(value[start:end]).rstrip(
                                SENTENCE_DELIMITERS
                            )
                            if len(normalized) < minimum or any(masked[start:end]):
                                continue
                            has_eligible_span = True
                            digest = sha256_text(normalized)
                            if digest in seen:
                                intervals.append((start, end))
                            seen.add(digest)
                    if local_size is not None:
                        # Keep raw coordinates across whitespace and stop at excluded spans.
                        first_end = {}
                        for start in range(len(value) - local_size + 1):
                            end = start + local_size
                            if any(masked[start:end]):
                                continue
                            token = normalize_text(value[start:end])
                            if len(token) < minimum:
                                continue
                            has_eligible_span = True
                            initial_end = first_end.setdefault(token, end)
                            if initial_end <= start:
                                intervals.append((start, end))
                    merged = []
                    for start, end in sorted(intervals):
                        if merged and start <= merged[-1][1]:
                            merged[-1][1] = max(end, merged[-1][1])
                        else:
                            merged.append([start, end])
                    duplicate += sum(
                        not ch.isspace()
                        for start, end in merged
                        for ch in value[start:end]
                    )
                    if merged:
                        evidence.append({"field": ref, "ranges": merged})
                if count < minimum or not has_eligible_span:
                    continue
                eligible += 1
                chars += count
                redundant += duplicate
                record_ratio = duplicate / count
                verdict = None if threshold is None else int(record_ratio <= threshold)
                failed += verdict == 0
                write_row(
                    stream,
                    {
                        "sample_id": sample,
                        "locations": evidence,
                        "redundant_chars": duplicate,
                        "effective_chars": count,
                        "score": 1 - record_ratio,
                        "verdict": "NOT_EVALUATED" if verdict is None else verdict,
                        "coordinates": "selected-text Unicode codepoints, half-open",
                    },
                )
        observations = []
        if chars:
            observations = [
                ratio("intra_record_repetition_score", chars - redundant, chars),
                ratio("intra_record_repetition_ratio", redundant, chars),
            ]
            if threshold is not None:
                observations.append(
                    ratio("intra_record_failure_ratio", failed, eligible)
                )
        return self.output(
            path,
            total,
            eligible,
            observations,
            eligible,
            {"aggregation": "character-weighted"},
        )
