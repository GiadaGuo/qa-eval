# Input schema 1.0

## Generation request

The request is a JSON object with these fields:

- `mode`: optional `draft` or `strict`; default `draft`.
- `dataset_id`: required non-empty stable identifier.
- `dataset_version`: required non-empty version identifier.
- `snapshot_path`: optional local JSONL path.
- `manifest_path`: optional local JSON path.
- `profile_path`: optional verified profile compatible with output schema 1.0.
- `existing_document_path`: optional local Markdown path used only when the user requests a new revision.
- `human_inputs_path`: optional local JSON path following the structure below.
- `output_root`: required local directory. Create a fresh UTC `YYYYMMDDTHHMMSSZ` child directory.
- `evaluate_after_generation`: optional boolean; default `false` unless the user explicitly asks for evaluation too.
- `fields`: optional `{id, text}` overrides. Prefer manifest mappings, then explicit overrides. Stop if neither resolves required fields.

At least one of snapshot, manifest, or profile must exist. A profile used without its source snapshot must still contain source path and SHA-256.

Paths must be explicit, readable, and within user scope. The run directory, generated document, profile, and manifests must not resolve to an input path. Stop rather than overwrite.

## Human-input object

All sections are optional. Empty or null means unknown, not false and not non-existent.

```json
{
  "technical_support": {
    "owner_department": null,
    "contact_name": null,
    "email": null,
    "issue_tracker": null,
    "service_scope": null
  },
  "license": {
    "name": null,
    "authorization_basis": null,
    "allowed_departments": [],
    "allowed_uses": [],
    "redistribution": null,
    "restrictions": []
  },
  "application": {
    "target_users": [],
    "target_tasks": [],
    "prohibited_uses": [],
    "confirmed_cases": [],
    "claims_model_development": false,
    "claims_performance_comparison": false,
    "claims_public_performance": false
  },
  "annotation": {
    "has_annotations": null,
    "guideline_path": null,
    "quality_control": null
  },
  "benchmark": {
    "performed": null,
    "task": null,
    "split": null,
    "method_or_model": null,
    "metric": null,
    "result": null,
    "environment": null,
    "report_path": null
  }
}
```

Treat these values as `USER_PROVIDED`; record the human-input file hash and a JSON locator. Do not elevate an incomplete license or contact block into a complete fact.

## Field resolution

For the known snapshot-manifest format, use:

- `field_mapping.sample_id` for ID.
- `field_mapping.text` for text.

An explicit `fields` override may be used only when the user supplies it. Record the selected fields in profile parameters. If a configured field is missing from any record, count it as missing; if it is missing from every valid record, stop with an error.

## Conflicts

Identity and integrity conflicts are fatal for the run: snapshot hash mismatch, manifest row count mismatch, dataset/version disagreement, or an output path equal to an input path.

Non-integrity business conflicts are unresolved facts. Record both sources and ask for human resolution; do not choose the more convenient claim.
