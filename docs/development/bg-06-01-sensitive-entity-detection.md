# BG-06-01 sensitive entity detection Calculator 1.0.0

`bg_06_01_sensitive_entity_detection@1.0.0` is a DIRECT, Dataset-scoped implementation bound only to `0202@2.0.0`. It does not replace or change `0202@1.0.0` or `content_safety_compliance@1.0.0`. `BG-06-01` remains requirement metadata rather than a Metric key.

The Calculator streams SampleRecord 2.0 JSONL, scans `article.body.html` in preference to `article.body.plain`, and scans `article.title` separately. HTML is converted to visible text; its evidence offsets are explicitly `VISIBLE_TEXT_UNICODE_CODE_POINT`, not raw HTML offsets. Metadata, source and lineage are not scanned.

Two channels run for every eligible text: validated PRC resident-ID/mobile-phone rules and UIE-mini CPU inference. UIE runs once per Stage in an isolated `ner-eval` child process, loads only the configured local model, and has network-offline environment flags. Long text uses overlapping Unicode character windows. Exact category/span duplicates are merged and retain both channels.

Measurements are neutral observations: total hits, fixed-category counts, affected/evaluated records and scanned/eligible character coverage. Hits do not mean violations. Contracts should normally use `OBSERVE_ONLY`.

Evidence is written incrementally, one row per merged detected fact, and is not capped by the legacy `evidence_limit` parameter. The parameter remains accepted only for historical Contract compatibility; preview limits belong to query/UI APIs. Evidence contains identifiers, category, coordinate space, channels, offsets and masked values only. Internal instructions never include their original text. The MVP does not emit a stable sensitive-value fingerprint because no deployment HMAC secret provider is currently available.

Required deployment parameters are the isolated Python executable, model directory, UIE vendor source directory and immutable model SHA-256. The model checksum is verified before worker startup. `max_records` marks the intended sampled scope in diagnostics; it does not claim full-dataset coverage.

The isolated worker is registered immediately after process creation and is reaped on initialization failure as well as normal Stage completion. Cleanup is idempotent and escalates from stdin close to terminate and kill. API lifespan shutdown closes all active UIE providers before shutting down the single-worker EvaluationRun executor, preventing reload or service shutdown from leaving model processes behind.

## MVP verification (2026-11-06)

A persistent isolated platform run is stored under `experiments/bg06_ner/platform_mvp/`. It used a PUBLISHED DatasetVersion containing ten synthetic policy records and an `OBSERVE_ONLY` Contract.

- Run `01a1b010-3f9b-7bec-8a07-94f1a153f79c`: `SUCCEEDED`
- StageRun `01a1b010-3f9f-7d5b-848a-094140c87e8a`: `SUCCEEDED`
- MetricResult `01a1b010-4db2-7345-9832-2a2f937730cb`: `OK`
- QualityReport `01a1b010-4dbb-70dd-930d-ac8f5a3291ca`: execution `SUCCEEDED`, gate `PASS`
- Seven fused hits: three involved persons, one internal instruction, two mobile phones and one PRC resident ID; five records affected, ten evaluated, selected-text character coverage 1.0.

The evidence artifact contains both real UIE and deterministic-rule hits, including fused dual-channel hits, and its leak assertions reject complete fixture IDs and phone numbers. The run summary contains the Contract and Logical/Physical Plan IDs.

Focused verification passed 30 Registry/Calculator/Contract/Planner tests, five execution regression tests, and the two comprehensive recommendation regressions. The broad unit/contracts run passed 365 tests with one unrelated pre-existing failure: the database compatibility test expects Alembic head `c9f4a21d8e73`, while the repository head is already `f6c3b47a12de` (`artifact cleanup outbox`).
