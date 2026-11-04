#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required. Install it from https://docs.astral.sh/uv/ and retry." >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example"
fi

# The local launcher intentionally overrides legacy PostgreSQL/S3 values that
# may still exist in .env. Use QA_EVAL_LOCAL_* to choose another local path.
export QA_EVAL_DATABASE_URL="${QA_EVAL_LOCAL_DATABASE_URL:-sqlite:///data/runtime/qa-eval.sqlite3}"
export QA_EVAL_ARTIFACT_STORE_TYPE="local"
export QA_EVAL_ARTIFACT_STORE_LOCAL_ROOT="${QA_EVAL_LOCAL_ARTIFACT_ROOT:-data/artifacts}"

mkdir -p data/runtime data/artifacts/.staging

if [[ "${QA_EVAL_SKIP_SYNC:-0}" != "1" ]]; then
  uv sync --all-groups
  alembic_command=(uv run alembic)
  uvicorn_command=(uv run uvicorn)
else
  if [[ ! -x .venv/bin/alembic || ! -x .venv/bin/uvicorn ]]; then
    echo "QA_EVAL_SKIP_SYNC=1 requires an existing .venv; run uv sync first." >&2
    exit 1
  fi
  alembic_command=(.venv/bin/alembic)
  uvicorn_command=(.venv/bin/uvicorn)
fi

"${alembic_command[@]}" upgrade head

if [[ "${QA_EVAL_INIT_ONLY:-0}" == "1" ]]; then
  echo "Local runtime initialized: data/runtime/qa-eval.sqlite3"
  exit 0
fi

echo "QA Eval API: http://127.0.0.1:8000"
if [[ "${QA_EVAL_RELOAD:-0}" == "1" ]]; then
  exec "${uvicorn_command[@]}" apps.api.main:app --host 127.0.0.1 --port 8000 --reload
fi
exec "${uvicorn_command[@]}" apps.api.main:app --host 127.0.0.1 --port 8000 --workers 1
