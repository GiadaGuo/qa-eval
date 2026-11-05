---
name: qa-eval-calculator-implementer
description: >
  Implement, extend, or repair executable Calculators in the qa-eval platform,
  including protocol-compatible code, Registry descriptors and bindings,
  streaming evidence, code digests, tests, and development notes. Use when a
  metric design must become a registered Calculator or an existing Calculator
  implementation is being changed; do not use for metric-definition research,
  running a dataset quality audit, or changing shared platform contracts alone.
---

# qa-eval Calculator Implementer

Turn an agreed metric or detection rule into the smallest complete qa-eval
Calculator slice that the existing planner and execution service can load,
execute, persist, and test.

## Establish the real extension points

Before designing code, locate and read the current implementations rather than
relying on remembered paths or interfaces. At minimum inspect:

- `src/qa_eval/calculators/protocol.py` and `loader.py`;
- the closest existing Calculator and its rule/helper modules;
- canonical input contracts under `src/qa_eval/contracts/`;
- Calculator descriptors, metric definitions, and bindings under
  `configs/registry/`;
- parameter validation, applicability, logical planning, execution, Metric
  Result construction, and evidence persistence in application services;
- representative unit and end-to-end Calculator tests.

Use `rg`/`rg --files` to confirm paths because the repository may evolve. Treat
the platform contracts and services as canonical. Do not create a parallel
runner, result model, registry, or artifact lifecycle inside a Calculator.

Record these design decisions before implementation:

- Calculator key, version, entrypoint, and supported metric;
- exact eligible input population and selector semantics;
- parameters, defaults, validation, and `additionalProperties` policy;
- observations, numerator/denominator, population exclusions, and N/A/error
  behavior;
- violation codes, evidence coordinate system, redaction, and limits;
- aggregation and deduplication at record/content/event levels;
- semantic coverage and claims that the available input cannot support;
- shared-rule reuse and compatibility impact on existing Calculators.

## Preserve platform invariants

- Keep calculation separate from Quality Contract thresholds, rule verdicts,
  scores, and reports.
- Emit only measurements declared by the target Metric Definition. Extend the
  definition deliberately if a genuinely new public measurement is required.
- A result with no eligible population must use the platform's non-computable
  status and must not fabricate a perfect or zero ratio. Check canonical result
  validators: non-OK statuses may prohibit observations, and population counts
  and exclusion counts must close exactly.
- Distinguish invalid input, execution failure, not applicable, partial
  semantic coverage, and detected quality violations. A violation is not a
  Calculator runtime error.
- Evidence offsets must identify their coordinate space and indexing semantics.
  Never present Unicode character indices as source-file byte offsets or map
  transformed text back to raw content without a reliable mapping.
- Limit excerpts and avoid logging full sensitive content. Declare the actual
  redaction policy and raw-content behavior.
- Preserve behavior of existing Calculators when extracting shared rules. A
  shared detector may feed multiple metrics while each Calculator retains its
  own eligibility, thresholds, aggregation, and interpretation.

## Implement for production-sized artifacts

Follow the existing Calculator Protocol and execution context. Prefer one-pass
JSONL processing and write evidence incrementally. Keep aggregate memory
bounded by configuration or distinct low-cardinality counters; do not retain
all samples or violations merely to serialize them later.

Make long-text behavior explicit. Either scan the full text or expose and
report a configured truncation policy; never silently skip a suffix while
claiming full coverage. Compile reusable patterns once and reuse parsers when
safe. Evidence caps must not silently cap aggregate violation counts.

Use canonical SampleRecord models, but account for the detector's target. If a
lower-level JSON decoder rejects the exact anomaly being detected (for example
an escaped isolated surrogate), parse through a safe path that preserves the
anomaly and then validate the resulting object against the canonical model.
Document why this exception is necessary.

For HTML or other transformed text, separate representation-level checks from
natural-language checks. Scan raw content when offsets remain raw; scan visible
text when markup, CSS, scripts, URLs, or entities would cause false positives.
Mark visible-text offsets as such rather than inventing raw offsets.

## Register and keep identities consistent

Add or update the canonical Calculator descriptor and Metric binding. Multiple
Calculators may bind to one metric when the registry and contract select an
implementation explicitly; do not overwrite a broader Calculator merely to add
a focused detection operator.

Keep descriptor defaults and JSON Schema validation aligned with runtime
validation. After all source edits, calculate the digest with
`TrustedCalculatorLoader.code_digest()` and update every descriptor whose
`source_paths()` includes a changed file. Recompute digests again after
formatting or any final source edit. Confirm the trusted Loader can load each
affected descriptor.

## Verify the complete slice

Test meaningful behavior, not only helper functions:

- clean positive cases and likely false-positive cases;
- each violation/error category and exact evidence offsets;
- selector and parameter validation;
- missing, null, empty, wrong-modality, and no-eligible-input behavior;
- record/content/event aggregation and derived-content duplication;
- evidence caps versus uncapped aggregate counts;
- streaming behavior with many records;
- Registry sync, descriptor digest, Loader, and planner compatibility;
- `DatasetVersion -> Calculator execution -> MetricResult -> Evidence Artifact`;
- regression tests for any Calculator that shares changed code.

Run focused tests first, then the relevant unit suite and static checks. Use the
project-declared environment command (currently `uv run ...` when applicable).
If the default tool cache is outside the writable sandbox, point only that
tool's cache to a temporary writable directory rather than changing project
configuration.

## Deliver a traceable handoff

Add a focused note under `docs/development/` when the implementation introduces
a new Calculator or non-obvious semantics. Include inputs, outputs, parameters,
rules, counting and deduplication, evidence coordinates, relationships to
existing Calculators, invocation, performance behavior, semantic coverage, and
verified test results.

In the final handoff list changed files, test commands and outcomes, coverage
boundaries, and remaining limitations. Do not claim source-byte, physical-file,
semantic, or full-metric coverage that the Calculator did not actually compute.

