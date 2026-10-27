# qa-eval JSON Schemas

This directory contains the checked-in, language-neutral wire contracts of the
qa-eval platform. Pydantic models in `src/qa_eval/contracts/` are the executable
source used to generate these files; the frozen architecture proposal and ADRs
remain the semantic source of truth.

Generate schemas:

```bash
uv run python scripts/contracts/generate_json_schemas.py
```

Check that committed schemas are current:

```bash
uv run python scripts/contracts/generate_json_schemas.py --check
```

Incompatible contract changes require a new schema major version and an ADR.
Do not edit generated `*.schema.json` files by hand.
