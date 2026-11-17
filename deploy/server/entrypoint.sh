#!/bin/sh
# ============================================================================
# qa-eval API 容器入口：
#   1. 按策略执行 Alembic 迁移（默认只在数据库文件不存在时执行，
#      绝不自动改动已存在的历史数据库 —— 那种库必须先备份再显式指定
#      QA_EVAL_RUN_MIGRATIONS=always）
#   2. 同步 Registry（幂等 upsert；镜像构建时已把 BG-06-01 的 UIE 路径
#      默认值改写为容器内路径，此步骤可把迁移来的旧库描述符一并修正）
#   3. exec uvicorn（单 worker：评测在进程内 ThreadPoolExecutor 串行执行，
#      SQLite 单写者，多 worker 只会增加锁竞争）
# ============================================================================
set -eu

DB_PATH="/app/data/runtime/qa-eval.sqlite3"
MIGRATIONS="${QA_EVAL_RUN_MIGRATIONS:-if-missing}"

echo "[entrypoint] migration policy: ${MIGRATIONS}"
case "${MIGRATIONS}" in
  always)
    echo "[entrypoint] running 'alembic upgrade head' (forced)"
    alembic upgrade head
    ;;
  never)
    echo "[entrypoint] migrations skipped (QA_EVAL_RUN_MIGRATIONS=never)"
    ;;
  if-missing)
    if [ ! -f "${DB_PATH}" ]; then
      echo "[entrypoint] fresh database, running 'alembic upgrade head'"
      alembic upgrade head
    else
      echo "[entrypoint] existing database found at ${DB_PATH}; NOT migrating automatically."
      echo "[entrypoint]   若确需升级：先备份该文件，然后在 .env 中设置 QA_EVAL_RUN_MIGRATIONS=always 并重启。"
      alembic current || true
    fi
    ;;
  *)
    echo "[entrypoint] FATAL: unknown QA_EVAL_RUN_MIGRATIONS='${MIGRATIONS}' (expect if-missing|always|never)" >&2
    exit 1
    ;;
esac

# Registry 同步：best-effort，失败不阻塞启动（可通过 UI 的「同步」按钮重试）
if python - <<'PY'
from qa_eval.application.services.metric_registry_service import MetricRegistryService
from qa_eval.infrastructure.db.session import SessionLocal

with SessionLocal() as session:
    result = MetricRegistryService(session).sync()
    session.commit()
print("[entrypoint] registry sync:", result)
PY
then
  :
else
  echo "[entrypoint] WARNING: registry sync failed; continue booting (retry via UI 同步按钮)" >&2
fi

echo "[entrypoint] starting uvicorn on :8000 (container-internal)"
exec uvicorn apps.api.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --workers 1 \
  --proxy-headers \
  --forwarded-allow-ips='*'
