# Output schemas 1.0

## Run directory

Use `<output_root>/<UTC YYYYMMDDTHHMMSSZ>/`. A collision is an error. Do not overwrite or merge with an existing run.

## dataset_profile.json

Required top-level fields:

- `schema_version`: `"1.0"`.
- `generated_at`: RFC 3339 timestamp.
- `source`: `path`, `sha256`, `size_bytes`, and `format` (`jsonl`).
- `parameters`: `id_field`, `text_field`, `sample_limit`, `sample_text_max_chars`, and length bins.
- `records`: `total_lines`, `valid_json`, `invalid_json`, and `object_records`.
- `fields`: occurrence and observed JSON type counts for every top-level field.
- `id`: configured field, missing count, null count, unique count, duplicate extra count.
- `text`: configured field, missing/null/empty/blank counts, character totals and distribution.
- `exact_text_duplicates`: group count and duplicate-extra count.
- `samples`: deterministic first valid records, with long string values truncated.
- `errors`: bounded list of parse errors with line numbers.

The profiler exits nonzero if any line is invalid JSON, if a valid JSON value is not an object, or if the configured ID/text field never occurs. It may still write a diagnostic profile only when explicitly designed to do so; MVP should fail without publishing a generation document.

## generation_manifest.json

Required fields:

- `schema_version`: `"1.0"`.
- `run`: `run_id`, RFC 3339 `generated_at`, and `mode`.
- `dataset`: `dataset_id` and `dataset_version`.
- `inputs`: array of `{path, sha256, kind}` for every file read as evidence.
- `outputs`: object containing registered output path/hash pairs. It must include the generation manifest's sibling document/profile/unresolved paths as applicable, but must not attempt to self-hash the manifest.
- `facts`: array of fact records.
- `unresolved_item_ids`: exact IDs present in `unresolved_items.json`.
- `status`: `GENERATED`, `GENERATED_WITH_UNRESOLVED_ITEMS`, `BLOCKED_BY_MISSING_INPUT`, or `ERROR`.
- `errors`: array of technical error strings.

Fact record fields:

- `fact_id`: unique stable identifier.
- `value`: JSON value or null.
- `status`: `EVIDENCE_VERIFIED`, `DERIVED`, `USER_PROVIDED`, or `UNRESOLVED`.
- `source_path`: required for all non-unresolved facts.
- `source_sha256`: required for all non-unresolved facts and must match a registered input or output profile.
- `locator`: reproducible JSON locator or calculation name; required for all non-unresolved facts.
- `used_in_sections`: non-empty array when the fact appears in the document.

For `UNRESOLVED`, value, source path, source hash, and locator must be null, and a corresponding unresolved item must exist.

## unresolved_items.json

An object containing `schema_version`, `run_id`, and `items`.

Every item contains:

- `item_id`: unique ID matching an unresolved fact or conflict.
- `metric_item`: relevant evaluator item code such as `0104-01`.
- `section`: target document section.
- `reason`: why the fact is unavailable or conflicting.
- `requested_from`: responsible role, not an invented person.
- `question`: actionable question.
- `blocks_strict_output`: boolean.

Default strict blockers are technical support (`0101-05`) and usage license (`0104-01`). Benchmark (`0104-04`) blocks strict output only when model-development or performance claims are explicitly supplied.

## Status consistency

- `GENERATED`: document exists and unresolved item list is empty.
- `GENERATED_WITH_UNRESOLVED_ITEMS`: draft document exists and unresolved list is non-empty.
- `BLOCKED_BY_MISSING_INPUT`: mode is strict, blocking items exist, and no output may be represented as final. A draft may exist only when its filename and document banner explicitly say draft.
- `ERROR`: errors is non-empty and no successful generated document may be represented.
