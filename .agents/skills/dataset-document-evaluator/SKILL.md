---
name: dataset-document-evaluator
description: Evaluate local Markdown dataset documentation against completeness metrics 0101–0104, with item-level evidence, scores, and actionable findings. Use for dataset-card or README completeness audits; do not use to generate or silently rewrite documentation.
---

# Dataset Document Evaluator

Evaluate an existing local Markdown dataset document. Produce a canonical JSON result and a Markdown view without modifying the source document or dataset.

## Required references

Before judging content, read [references/evaluation-rubric.md](references/evaluation-rubric.md) completely. Before creating result artifacts, read [references/output-schema.md](references/output-schema.md) completely.

## Inputs

Require `document_path`. Accept optional `evidence_paths`, `evaluation_mode`, and `output_directory`.

- MVP supports local Markdown only.
- Use `evidence-assisted` when the user supplies or clearly binds readable manifests, snapshots, profiles, or version records.
- Otherwise use `document-only` and state that completeness does not establish factual accuracy.
- Read only evidence the user supplied or placed in scope. Do not connect to a database or fetch remote evidence implicitly.

## Workflow

1. Run `scripts/inspect_markdown.py DOCUMENT [--evidence PATH ...]` and use its numbered lines, hashes, headings, and evidence metadata.
2. Apply the rubric to all 20 items. A heading or keyword alone is not sufficient. Distinguish an absent fact from a fact omitted by the document; use external evidence when available and never guess.
3. Record one of `SATISFIED`, `INSUFFICIENT`, `MISSING`, or `UNVERIFIABLE` for every item. Use `ERROR` only when that item cannot be evaluated because execution failed.
4. Attach concise 1-based document line evidence to every non-missing judgment. For `MISSING`, record the inspected scope and a suggested insertion section. Cite external evidence with file path, SHA-256, and locator.
5. Calculate each metric independently: `score = SATISFIED item count / 5 * 100`. Do not produce an overall score or a PASS/FAIL quality-contract verdict.
6. Write the JSON result first. Then run `scripts/validate_result.py RESULT --document DOCUMENT --render-markdown REPORT` so arithmetic, identifiers, evidence lines, excerpts, and the derived report are checked deterministically.
7. Report output paths, per-metric scores, unresolved facts, errors, and coverage limitations.

## Non-negotiable boundaries

- Do not count `INSUFFICIENT`, `MISSING`, `UNVERIFIABLE`, or `ERROR` as satisfied.
- If any item is `ERROR`, mark its metric `ERROR` and set that metric's counts and score to `null`; do not turn execution failure into a zero quality score.
- A claim contradicted by supplied evidence cannot be `SATISFIED`.
- “No labels” can satisfy label statistics only when the absence is supported and quantified. “No benchmark,” “support unavailable,” and vague “internal use” wording do not satisfy their respective requirements.
- Never invent licenses, authorization scope, support contacts, business purposes, benchmark results, or dataset facts.
- Classify remediation as `AUTO_FIXABLE`, `DATA_DERIVABLE`, or `REQUIRES_HUMAN_INPUT`. Suggestions do not authorize edits.
- Do not overwrite the source document. Generate a revised copy only if the user explicitly requests a separate rewrite task.

## Artifacts

When an output directory is requested, use stable names:

- `evaluation_result.json` — canonical result.
- `evaluation_report.md` — rendered view of that exact JSON.

Do not recompute or alter judgments while rendering Markdown.
