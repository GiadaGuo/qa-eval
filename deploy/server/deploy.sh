#!/usr/bin/env bash
# ============================================================================
# qa-eval Mac → Linux 服务器 一键增量部署
#
#   bash deploy/server/deploy.sh web [--dry-run]   # 只同步代码 + 构建 + 更新 Web
#   bash deploy/server/deploy.sh api [--dry-run]   # 只同步代码 + 构建 + 更新 API
#   bash deploy/server/deploy.sh all [--dry-run]   # 两者都更新
#   bash deploy/server/deploy.sh --help
#
# 流程：本地白名单增量同步（rsync）→ 远程 docker build（仅指定服务，
# 复用服务器已验证的构建方式：DOCKER_BUILDKIT=0 + --network=host + 国内
# PyPI 镜像 + 本地 PyTorch CPU wheel）→ compose up -d --no-deps（只重建
# 指定服务，构建失败不碰在跑的服务）→ 健康检查（超时失败则打印容器日志）。
#
# 同步安全：只同步下方 SYNC_FILES / CODE_DIRS / ADDITIVE_DIRS 白名单；
# 代码目录内 --delete 仅清理已验证的代码树；deploy/server 为增量同步、
# 永不删除；.env / htpasswd / wheels / 数据目录 / 其他 Docker 项目一律不碰。
#
# 兼容 macOS 自带 bash 3.2：不使用关联数组 / 空数组展开等 4.x 特性。
# ============================================================================
set -euo pipefail

# ---------------------------------------------------------------- 配置（可用环境变量覆盖）
SSH_HOST="${QA_DEPLOY_SSH:-zju}"                                   # SSH 别名
REMOTE_ROOT="${QA_DEPLOY_REMOTE_ROOT:-/data/slx/projects/qa-eval-deploy}"  # 服务器仓库目录
WEB_PORT="${QA_DEPLOY_WEB_PORT:-5173}"                             # Web 发布端口（服务器本机回环探测）
EXTERNAL_URL="${QA_DEPLOY_EXTERNAL_URL:-http://100.100.20.106:5173}"       # 外部访问地址（仅最佳 effort 验证）
DOCKER_BUILDKIT="${QA_DEPLOY_BUILDKIT:-0}"                         # 服务器已验证的构建方式；新 docker 可设 =1
HEALTH_TIMEOUT="${QA_DEPLOY_HEALTH_TIMEOUT:-180}"                  # 健康检查总超时（秒）
PIP_INDEX_URL_OVERRIDE="${PIP_INDEX_URL:-}"                        # 可选：api 构建时覆盖国内 PyPI 镜像

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"                          # 本地仓库根

# ---- 同步白名单：只传构建必需的路径，其余一律不同步 ----
SYNC_FILES=(pyproject.toml uv.lock alembic.ini README.md .dockerignore)  # 根目录构建元数据（不删除远端多余文件）
CODE_DIRS=(src apps configs migrations schemas)                    # 纯代码目录：允许 --delete 保持两边一致
ADDITIVE_DIRS=(deploy/server)                                      # 含服务器本地文件：只增量更新，绝不删除

RSYNC_EXCLUDE_COMMON=(--exclude=.DS_Store --exclude=__pycache__/ --exclude='*.pyc')
RSYNC_EXCLUDE_APPS=(--exclude=web/node_modules/ --exclude=web/dist/ --exclude=web/coverage/)
# deploy/server 排除：服务器本地文件绝不覆盖/删除；注意 .env.example 必须放行
RSYNC_EXCLUDE_DEPLOY=(--include=.env.example --exclude=.env --exclude='.env.*' \
  --exclude=htpasswd --exclude='htpasswd.*' --exclude=wheels/ --exclude='*.whl' \
  --exclude='*.sqlite3*' --exclude='*.log')

# ---------------------------------------------------------------- 小工具（bash 3.2 兼容，不用关联数组）
c_info()  { printf '\033[1m==>\033[0m %s\n' "$*"; }
c_ok()    { printf '\033[32m  ok\033[0m %s\n' "$*"; }
c_warn()  { printf '\033[33m  warn\033[0m %s\n' "$*" >&2; }
die()     { printf '\033[31mFATAL:\033[0m %s\n' "$*" >&2; exit 1; }
step()    { printf '\033[1m---- %s ----\033[0m\n' "$*"; }

img_name()       { case "$1" in web) echo "qa-eval-server/web:latest" ;; api) echo "qa-eval-server/api:latest" ;; *) return 1 ;; esac; }
dockerfile_for() { case "$1" in web) echo "deploy/server/Dockerfile.web" ;; api) echo "deploy/server/Dockerfile.api" ;; *) return 1 ;; esac; }

DRY_RUN=0
TARGET=""
SERVICES=()

usage() {
  cat <<'EOF'
qa-eval 一键增量部署（仅从 Mac 执行）

用法:
  bash deploy/server/deploy.sh <web|api|all> [--dry-run]
  bash deploy/server/deploy.sh --help

选项:
  --dry-run   只展示将执行的 rsync 变更（需要 SSH 可达）与远程构建/更新命令，
              不产生任何修改；SSH 不可达时仅打印命令模板
  --help      显示本帮助

环境变量覆盖（一般不需要）:
  QA_DEPLOY_SSH            SSH 别名（默认 zju）
  QA_DEPLOY_REMOTE_ROOT    服务器仓库目录（默认 /data/slx/projects/qa-eval-deploy）
  QA_DEPLOY_BUILDKIT       0=legacy builder（服务器已验证，默认）；1=BuildKit
  QA_DEPLOY_HEALTH_TIMEOUT 健康检查总超时秒数（默认 180）
  PIP_INDEX_URL            api 构建时的 PyPI 镜像（默认用 Dockerfile 内的阿里云镜像）

同步范围（白名单，其余一律不同步）:
  文件: pyproject.toml uv.lock alembic.ini README.md .dockerignore
  目录: src/ apps/ configs/ migrations/ schemas/（与服务器保持一致，
        --delete 仅作用于这些代码目录内部）
  增量: deploy/server/（不删除；.env / htpasswd / wheels/ 等服务器本地文件被排除）

数据与配置安全:
  - 不同步/覆盖/删除: 服务器 .env、htpasswd、PyTorch wheel、SQLite、Artifact、
    模型权重、Docker volumes、其他项目目录
  - api 构建复用 deploy/server/wheels/ 里的本地 torch wheel，缺失会明确报错
  - 构建失败不会停止当前运行中的服务；构建前自动给现有镜像打 :previous 标签便于回滚
EOF
}

parse_args() {
  while (($#)); do
    case "$1" in
      web|api|all)
        [[ -z "${TARGET}" ]] || die "只能指定一个部署目标: web | api | all"
        TARGET="$1" ;;
      --dry-run) DRY_RUN=1 ;;
      --help|-h) usage; exit 0 ;;
      *)         usage >&2; die "未知参数: $1" ;;
    esac
    shift
  done
  [[ -n "${TARGET}" ]] || { usage >&2; die "必须指定部署目标: web | api | all"; }
  case "${TARGET}" in
    all) SERVICES=(api web) ;;    # 先 api 后 web：先保证后端健康，再切前端
    *)   SERVICES=("${TARGET}") ;;
  esac
}

SSH_OPTS="-o BatchMode=yes -o ConnectTimeout=10"
rsh() { ssh ${SSH_OPTS} "${SSH_HOST}" "$@"; }

# 远程执行：非 dry-run 通过 ssh 跑脚本；dry-run 打印脚本内容（不产生修改）
# 用法: remote_run "描述" <<'EOF' ... EOF
remote_run() {
  local desc="$1" script; script="$(cat)"
  if ((DRY_RUN)); then
    printf '\033[36m[dry-run] 远程: %s\033[0m\n' "$desc"
    printf '%s\n' "$script" | sed 's/^/[dry-run]   | /'
    return 0
  fi
  local env_prefix
  env_prefix="$(printf 'QA_SUDO=%q QA_BUILDKIT=%q QA_PIP_INDEX=%q QA_REMOTE_ROOT=%q QA_COMPOSE_DIR=%q' \
    "${SUDO_PREFIX:-}" "${DOCKER_BUILDKIT}" "${PIP_INDEX_URL_OVERRIDE}" "${REMOTE_ROOT}" "${REMOTE_ROOT}/deploy/server")"
  printf '%s\n' "$script" | ssh ${SSH_OPTS} "${SSH_HOST}" "${env_prefix} bash -se"
}

# ---------------------------------------------------------------- 预检
preflight_local() {
  step "本地预检"
  [[ "$(uname -s)" == "Darwin" ]] || die "本脚本只能从 Mac 本地执行（检测到 $(uname -s)）"
  [[ "${REMOTE_ROOT}" == /* && "${REMOTE_ROOT}" != "/" ]] || die "REMOTE_ROOT 异常: '${REMOTE_ROOT}'"
  command -v rsync >/dev/null || die "缺少 rsync"
  command -v ssh  >/dev/null || die "缺少 ssh"
  local p
  for p in "${SYNC_FILES[@]}";   do [[ -f "${ROOT}/${p}" ]] || die "本地缺少 ${p}"; done
  for p in "${CODE_DIRS[@]}" "${ADDITIVE_DIRS[@]}"; do [[ -d "${ROOT}/${p}" ]] || die "本地缺少目录 ${p}/"; done
  c_ok "本地仓库: ${ROOT}"
}

preflight_ssh() {
  step "SSH 连通性"
  if ! rsh true 2>/dev/null; then
    if ((DRY_RUN)); then c_warn "SSH 别名 ${SSH_HOST} 不可达，dry-run 只打印命令模板"; return 0; fi
    die "无法 SSH 到 ${SSH_HOST}（BatchMode）。请检查网络/VPN 与 ~/.ssh/config"
  fi
  c_ok "SSH ${SSH_HOST} 连通"
}

preflight_remote() {
  step "服务器预检（docker / compose / 配置 / 构建依赖）"
  local out
  if ! out="$(rsh "QA_REMOTE_ROOT=$(printf '%q' "${REMOTE_ROOT}") bash -se" <<'EOF'
set -e
R="$QA_REMOTE_ROOT"
[[ -d "$R" ]]                                || { echo "ERR 服务器仓库目录不存在: $R"; exit 1; }
[[ -f "$R/deploy/server/docker-compose.yml" ]] || { echo "ERR 缺少 deploy/server/docker-compose.yml"; exit 1; }
[[ -f "$R/deploy/server/.env" ]]             || { echo "ERR 缺少 deploy/server/.env（服务器本地文件，需手动创建，见 README）"; exit 1; }
grep -q '^QA_EVAL_DATA_DIR=[^[:space:]]' "$R/deploy/server/.env" || { echo "ERR .env 未配置 QA_EVAL_DATA_DIR"; exit 1; }
# docker（必要时允许免密 sudo）
SUDO=""
if docker info >/dev/null 2>&1; then SUDO="";
elif sudo -n docker info >/dev/null 2>&1; then SUDO="sudo -n";
else echo "ERR docker 不可用（用户不在 docker 组且 sudo -n 需要密码）"; exit 1; fi
# compose（v2 插件优先，退回 v1 独立二进制）
if $SUDO docker compose version >/dev/null 2>&1; then COMPOSE="$SUDO docker compose";
elif command -v docker-compose >/dev/null 2>&1 && $SUDO docker-compose version >/dev/null 2>&1; then COMPOSE="$SUDO docker-compose";
else echo "ERR docker compose 不可用"; exit 1; fi
DATA_DIR="$(sed -n 's/^QA_EVAL_DATA_DIR=//p' "$R/deploy/server/.env" | tail -n 1)"
DATA_DIR="${DATA_DIR%\"}"; DATA_DIR="${DATA_DIR#\"}"
[[ -d "$DATA_DIR/runtime" && -d "$DATA_DIR/artifacts" ]] || { echo "ERR 数据目录缺失 runtime/artifacts: $DATA_DIR"; exit 1; }
WHEEL="$(ls "$R"/deploy/server/wheels/torch-*.whl "$R"/deploy/server/torch-*.whl 2>/dev/null | head -n 1)"
printf 'SUDO=%q\nCOMPOSE=%q\nDATA_DIR=%q\nWHEEL=%q\n' "$SUDO" "$COMPOSE" "$DATA_DIR" "$WHEEL"
EOF
)"; then
    die "服务器预检执行失败: ${out:-<无输出，检查 SSH/stderr>}"
  fi
  case "${out}" in
    ERR*) die "服务器预检失败: ${out}" ;;
  esac
  eval "${out}"   # 导出 SUDO / COMPOSE / DATA_DIR / WHEEL（来自可信服务器，%q 转义）
  SUDO_PREFIX="${SUDO}"
  c_ok "docker: ${SUDO:-<direct>}    compose: ${COMPOSE}"
  c_ok "数据目录: ${DATA_DIR}"
  local svc
  for svc in "${SERVICES[@]}"; do
    if [[ "${svc}" == "api" ]]; then
      [[ -n "${WHEEL}" ]] || die "api 构建缺少本地 PyTorch wheel: 请将 torch-2.12.1+cpu-*.whl 放入
       服务器 ${REMOTE_ROOT}/deploy/server/wheels/（该目录受同步保护，不会被覆盖/删除）"
      c_ok "PyTorch wheel: ${WHEEL}"
    fi
  done
}

# ---------------------------------------------------------------- 同步
rsync_cmd() { # rsync_cmd <本地路径> <远端目标> <额外rsync参数...>
  local src="$1" dst="$2"; shift 2
  if ((DRY_RUN)); then
    printf '\033[36m[dry-run] rsync %s → %s:%s\033[0m\n' "${src#"$ROOT"/}" "${SSH_HOST}" "${dst}"
    if rsh true 2>/dev/null; then
      rsync -azn -v -e "ssh ${SSH_OPTS}" "$@" "${src}" "${SSH_HOST}:${dst}" | sed 's/^/  ~ /' || true
    else
      c_warn "SSH 不可达，跳过实际差异预览"
    fi
  else
    rsync -az -e "ssh ${SSH_OPTS}" "$@" "${src}" "${SSH_HOST}:${dst}"
  fi
}

sync_code() {
  step "增量同步代码（白名单）"
  local p
  for p in "${SYNC_FILES[@]}"; do
    rsync_cmd "${ROOT}/${p}" "${REMOTE_ROOT}/" || die "同步失败: ${p}"
  done
  for p in "${CODE_DIRS[@]}"; do
    if [[ "${p}" == "apps" ]]; then
      rsync_cmd "${ROOT}/${p}/" "${REMOTE_ROOT}/${p}/" --delete \
        "${RSYNC_EXCLUDE_COMMON[@]}" "${RSYNC_EXCLUDE_APPS[@]}" || die "同步失败: ${p}/"
    else
      rsync_cmd "${ROOT}/${p}/" "${REMOTE_ROOT}/${p}/" --delete \
        "${RSYNC_EXCLUDE_COMMON[@]}" || die "同步失败: ${p}/"
    fi
  done
  for p in "${ADDITIVE_DIRS[@]}"; do
    rsync_cmd "${ROOT}/${p}/" "${REMOTE_ROOT}/${p}/" \
      "${RSYNC_EXCLUDE_COMMON[@]}" "${RSYNC_EXCLUDE_DEPLOY[@]}" || die "同步失败: ${p}/"
  done
  c_ok "同步完成（白名单之外的服务器文件一律未触碰）"
}

# ---------------------------------------------------------------- 构建 + 更新 + 健康检查
# 一次性迁移：服务器早期把 wheel 直接放在 deploy/server/ 根下，
# 统一移入受保护的 deploy/server/wheels/（同盘 mv，不复制、不删除数据）。
# 仅在 api 部署且 wheels/ 里还没有 wheel 时执行；dry-run 只打印。
ensure_wheel_layout() {
  local svc
  for svc in "${SERVICES[@]}"; do
    [[ "${svc}" == "api" ]] || continue
    step "确认 PyTorch wheel 位置（deploy/server/wheels/）"
    remote_run "迁移 wheel 到 wheels/（如需要）" <<'EOF' || die "wheel 位置确认/迁移失败（见上方远程输出）"
set -e
cd "$QA_REMOTE_ROOT/deploy/server"
if [ -z "$(ls wheels/torch-*.whl 2>/dev/null | head -n 1)" ]; then
  old="$(ls torch-*.whl 2>/dev/null | head -n 1 || true)"
  if [ -n "$old" ]; then
    mkdir -p wheels
    mv "$old" wheels/
    echo "migrated: $old -> wheels/"
  fi
fi
ls wheels/torch-*.whl >/dev/null 2>&1 || { echo "ERR wheels/ 中没有 torch-*.whl"; exit 1; }
EOF
    c_ok "wheels/ 下已有 torch wheel"
  done
}

build_service() {
  local svc="$1" img prev
  img="$(img_name "${svc}")"; prev="${img/:latest/:previous}"
  step "构建 ${svc} 镜像 ${img}（服务器已验证方式: BUILDKIT=${DOCKER_BUILDKIT} --network=host，全量缓存复用）"
  remote_run "docker build ${svc}" <<EOF || die "${svc} 镜像构建失败（当前运行中的服务未受影响，可修复后重试或回滚）"
set -e
cd "\$QA_REMOTE_ROOT"
# 构建前把当前镜像标记为 :previous，作为回滚锚点（若存在）
if \${QA_SUDO} docker image inspect "${img}" >/dev/null 2>&1; then
  \${QA_SUDO} docker tag "${img}" "${prev}" && echo "tagged ${prev}"
fi
if [ -n "\$QA_PIP_INDEX" ]; then PIP_ARG="--build-arg PIP_INDEX_URL=\$QA_PIP_INDEX"; else PIP_ARG=""; fi
\${QA_SUDO} env DOCKER_BUILDKIT="\$QA_BUILDKIT" docker build --network=host \$PIP_ARG \
  -f "$(dockerfile_for "${svc}")" -t "${img}" .
EOF
  c_ok "${svc} 镜像构建成功: ${img}"
}

update_service() {
  local svc="$1"
  step "更新 ${svc} 容器（compose up -d --no-deps，只动 ${svc}，其他服务不受影响）"
  remote_run "compose up ${svc}" <<EOF || die "${svc} 容器更新失败（构建产物仍在，可手动在服务器上排查后重试）"
set -e
cd "\$QA_COMPOSE_DIR"
\$QA_COMPOSE --env-file .env up -d --no-deps "${svc}"
EOF
  c_ok "${svc} 容器已更新"
}

# 在服务器回环上探测 HTTP 200
remote_http_ok() {
  rsh "curl -fsS -o /dev/null --max-time 8 -w '%{http_code}' 'http://127.0.0.1:${WEB_PORT}$1'" 2>/dev/null | grep -q '^200$'
}

wait_health() {
  local svc="$1" deadline ok
  if ((DRY_RUN)); then
    step "健康检查 ${svc}（dry-run 不实际探测）"
    printf '  将每 5s 探测（共 %ss，服务器回环 127.0.0.1:%s）: GET / → 200; GET /healthz → 200' \
      "${HEALTH_TIMEOUT}" "${WEB_PORT}"
    [[ "${svc}" == "api" ]] && printf '; GET /api/v1/health → {"status":"ok"}'
    printf '；超时则打印容器日志并给出回滚命令\n'
    return 0
  fi
  deadline=$((SECONDS + HEALTH_TIMEOUT))
  step "健康检查 ${svc}（总超时 ${HEALTH_TIMEOUT}s）"
  while ((SECONDS < deadline)); do
    ok=1
    remote_http_ok /        || ok=0
    remote_http_ok /healthz || ok=0
    if [[ "${svc}" == "api" ]]; then
      rsh "curl -fsS --max-time 8 'http://127.0.0.1:${WEB_PORT}/api/v1/health'" 2>/dev/null \
        | grep -q '"status"[[:space:]]*:[[:space:]]*"ok"' || ok=0
    fi
    if ((ok)); then c_ok "${svc} 健康检查通过（/、/healthz$( [[ "${svc}" == "api" ]] && echo '、/api/v1/health' ) 均正常）"; return 0; fi
    sleep 5
  done
  c_warn "健康检查超时，最近容器日志:"
  remote_run "compose logs ${svc}" <<EOF
set -e
cd "\$QA_COMPOSE_DIR"
\$QA_COMPOSE --env-file .env logs --tail=100 "${svc}" || true
EOF
  die "${svc} 健康检查未通过。回滚: ssh ${SSH_HOST} '${SUDO_PREFIX:-sudo -n} docker tag $(img_name "${svc}" | sed 's/:latest/:previous/') $(img_name "${svc}") && cd ${REMOTE_ROOT}/deploy/server && ${COMPOSE:-sudo -n docker compose} --env-file .env up -d --no-deps ${svc}'"
}

external_check() {
  step "外部访问抽查（仅提示，不判定失败）"
  if ((DRY_RUN)); then
    printf '  将探测: GET %s/healthz（失败仅警告，不影响部署结果）\n' "${EXTERNAL_URL}"
    return 0
  fi
  if curl -fsS -o /dev/null --max-time 6 "${EXTERNAL_URL}/healthz" 2>/dev/null; then
    c_ok "外部可访问: ${EXTERNAL_URL}/healthz"
  else
    c_warn "Mac 当前不可达 ${EXTERNAL_URL}（VPN/网络问题不影响本次部署结果，请在服务器内网验证）"
  fi
}

# ---------------------------------------------------------------- 主流程
main() {
  parse_args "$@"
  preflight_local
  preflight_ssh
  if ((DRY_RUN)); then
    step "服务器预检（dry-run 跳过）"
    SUDO_PREFIX="sudo -n"; COMPOSE="sudo -n docker compose"
  else
    preflight_remote
  fi
  sync_code
  ensure_wheel_layout
  local svc
  for svc in "${SERVICES[@]}"; do build_service "${svc}"; done
  for svc in "${SERVICES[@]}"; do update_service "${svc}"; wait_health "${svc}"; done
  external_check
  step "部署完成: ${SERVICES[*]}"
  c_ok "状态查看: ssh ${SSH_HOST} 'cd ${REMOTE_ROOT}/deploy/server && ${COMPOSE:-sudo -n docker compose} --env-file .env ps'"
}

main "$@"
