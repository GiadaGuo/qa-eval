---
name: dataset-document-generator
description: Generate or revise local Markdown dataset documentation from manifests, JSONL snapshots, profiles, and explicit user facts, with traceable provenance and unresolved-item tracking. Use when a dataset card or README must be created; do not invent licenses, contacts, business intent, or benchmark results.
---

# Dataset Document Generator

Generate a new, traceable Markdown dataset document. Preserve every input and existing document; never overwrite them.

## Required references

Before starting, read [references/input-schema.md](references/input-schema.md) and [references/output-schema.md](references/output-schema.md) completely. Before writing the document, read [references/document-template.md](references/document-template.md) completely.

## Modes

- `draft` (default): generate a document while clearly marking unavailable human facts and listing them in `unresolved_items.json`.
- `strict`: technical support and usage-license facts are mandatory. Benchmark facts also become mandatory when the stated purpose includes model development, model selection, performance comparison, or public performance claims. If blocking inputs are missing, produce only the profile and blocking records; do not label any document final.

## Workflow

1. Validate the user-scoped input paths and create a new UTC `YYYYMMDDTHHMMSSZ` output directory. Stop on collision; never reuse or overwrite it.
2. Run `scripts/profile_jsonl.py` when a JSONL snapshot is supplied and no suitable verified profile exists. Use field names from the manifest or explicit user configuration; do not guess silently.
3. Build a fact inventory. Mark every key fact as `EVIDENCE_VERIFIED`, `DERIVED`, `USER_PROVIDED`, or `UNRESOLVED`. AI wording is not a source.
4. Resolve conflicts conservatively. Do not use a conflicting fact as established; record the conflict as unresolved or stop if it affects deterministic identity, count, or fingerprint checks.
5. Apply strict-mode blocking rules, then write the Markdown using the template. In draft mode, retain sections for missing human facts and use precise “current inputs do not provide...” wording.
6. Write `generation_manifest.json` and `unresolved_items.json`. Each documented key fact must have a reproducible source path/locator or an explicit unresolved record.
7. Run `scripts/validate_generation.py` to validate hashes, output isolation, fact provenance, unresolved links, and mode/status consistency.
8. If the user asks for generation plus evaluation, invoke `dataset-document-evaluator` only after generation validation. Keep its JSON and Markdown artifacts under the run directory's `evaluation/` subdirectory. Do not revise repeatedly to chase a score.

## Truth and safety boundaries

- Missing evidence does not prove non-existence. “No license evidence supplied” is allowed; “the dataset has no license” is not.
- A genuinely unlabeled snapshot may be documented as such only after deterministic schema/profile evidence supports it.
- Target uses are facts only when explicitly supplied by the user, configuration, or project documentation. Never infer them from filenames or content.
- Never invent authorization, ownership, departments, contacts, service commitments, annotation governance, production cases, or benchmark results.
- Do not read `.env`, credentials, or unrelated files. Do not connect to databases or fetch remote evidence without separate authorization.
- Stream large JSONL files. Do not copy full long records into examples; truncate samples and avoid unnecessary sensitive content.
- Do not perform language detection, topic classification, near-duplicate detection, or sensitive-content model inference in MVP.

## Stable artifacts

Each run directory contains:

- `dataset_document.generated.md` when the mode allows document output;
- `dataset_profile.json` when profiling is performed;
- `generation_manifest.json`;
- `unresolved_items.json`;
- optional `evaluation/` results created by the independent evaluator.
