# Output schema 1.0

The JSON result is canonical. The Markdown report is a deterministic view rendered from it.

## Top-level object

Required keys:

- `schema_version`: exactly `"1.0"`.
- `rubric_version`: exactly `"1.0"` for this skill version.
- `run`: object with `run_id`, RFC 3339 `evaluated_at`, and `evaluation_mode` (`document-only` or `evidence-assisted`).
- `document`: object with `path`, hexadecimal SHA-256 `sha256`, and positive integer `line_count`.
- `evidence_files`: array of `{path, sha256, kind}`. Use an empty array in document-only mode.
- `metrics`: exactly four metric objects, ordered 0101 through 0104.
- `summary`: object with `scores`, `satisfied_items`, and `total_items`.
- `limitations`: array of strings describing coverage limits. It may be empty.

## Metric object

Required keys:

- `metric_code`: one of `0101`, `0102`, `0103`, `0104`.
- `metric_name`: the rubric name.
- `status`: `COMPLETED` or `ERROR`.
- `satisfied_count`, `required_count`, `score`.
- `items`: exactly the five item objects belonging to this metric.
- `error`: `null` for completed metrics; a non-empty string for errors.

For `COMPLETED`, `required_count` is 5, `satisfied_count` equals the number of `SATISFIED` items, and `score` equals `satisfied_count / 5 * 100`.

If any item is `ERROR`, the metric must be `ERROR`; `satisfied_count`, `required_count`, and `score` must all be `null`. Summary score for that metric must also be `null`.

## Item object

Required keys:

- `item_code`: one of `0101-01` through `0104-05`, in rubric order.
- `item_name`: rubric item name.
- `status`: `SATISFIED`, `INSUFFICIENT`, `MISSING`, `UNVERIFIABLE`, or `ERROR`.
- `document_evidence`: array of document evidence objects.
- `external_evidence`: array of external evidence objects.
- `inspected_scope`: non-empty string describing where the evaluator looked. Required especially for `MISSING`.
- `reason`: concise explanation of why the state applies.
- `suggestion`: actionable improvement; it may state that no change is needed for a satisfied item.
- `fixability`: `AUTO_FIXABLE`, `DATA_DERIVABLE`, or `REQUIRES_HUMAN_INPUT`.

Document evidence object:

```json
{
  "line_start": 10,
  "line_end": 12,
  "excerpt": "Exact text copied from the cited line range"
}
```

Line numbers are 1-based. `line_start <= line_end <= document.line_count`. The excerpt must occur in the joined cited lines after normalizing line endings only.

External evidence object:

```json
{
  "path": "path/to/manifest.json",
  "sha256": "64 lowercase hexadecimal characters",
  "locator": "$.row_count",
  "excerpt": "14960"
}
```

Use a JSONPath-like field locator, record ID, line number, or another reproducible locator. Every external path and hash must match an entry in `evidence_files`.

## Summary rules

- `scores` has exactly keys `0101`–`0104`, each copied from its metric score.
- `satisfied_items` is the total number of `SATISFIED` items across successfully evaluated metrics.
- `total_items` is 20 only when all metrics completed; set it to `null` if any metric is `ERROR`.
- Do not include an overall score, weighted score, PASS/FAIL verdict, or quality-contract decision.

## Markdown rendering

The rendered report includes metadata, metric scores, all 20 item judgments, evidence, recommendations grouped by fixability, conflicts, limitations, and errors. Rendering must not reinterpret or recompute semantic judgments.
