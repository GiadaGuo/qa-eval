# qa-eval 服务器部署（Ubuntu 共享 GPU 服务器）

面向 `/data/slx/projects/qa-eval-deploy`（git 同步的仓库目录）的 Docker Compose
部署。目标：授权用户通过 `http://服务器IP:5173` 访问完整平台。

## 架构

```
用户浏览器 ──HTTP──▶ :5173 web 容器 (nginx)
                      ├─ Basic Auth（整站，静态 + /api 全覆盖）
                      ├─ 静态前端（React 生产构建，SPA 回退）
                      └─ /api/ ─▶ api 容器 :8000（仅 compose 内部网络，不发布端口）
                                    ├─ uvicorn × 1（评测在进程内线程池串行执行）
                                    ├─ SQLite ▶ 挂载 $DATA_DIR/runtime（WAL）
                                    ├─ Artifact ▶ 挂载 $DATA_DIR/artifacts
                                    └─ UIE 子进程 ▶ /opt/ner-venv（CPU torch）
                                         + 只读挂载 $DATA_DIR/models/uie-mini
                                         + 只读挂载 $DATA_DIR/vendor/uie_pytorch_src
```

- 只发布 TCP 5173；API、数据库、Artifact 均不出容器网络。
- 不依赖 PostgreSQL / Redis / MinIO（本地 SQLite + 本地 Artifact Store）。
- 不使用 `privileged`、`network_mode: host`；不申请 GPU（见下）。
- 与本地开发配置完全隔离：不动 `deploy/docker-compose.yml`，compose 项目名
  `qa-eval-server`。

## GPU 说明（重要，先读）

**当前代码没有 GPU 推理。** 全仓库唯一的模型调用链是 BG-06-01 敏感要素识别：
`IsolatedUIEProvider` 以子进程方式拉起独立解释器运行 `ner_worker.py`，其中
`device="cpu"` 为硬编码（`src/qa_eval/calculators/sensitive_entity_detection/ner_worker.py`），
全代码库没有任何 CUDA/GPU 调用。因此本部署：

- **不创建 GPU Worker**（子进程 stdin/stdout 协议已存在，凭空加 HTTP worker 只会
  造出不可用的空壳）；
- **不向容器申请 GPU 资源**——共享服务器上抢显存只会影响他人任务；
- API 镜像内置 CPU 版 torch 的 UIE 推理环境 `/opt/ner-venv`（torch==2.12.1+cpu，
  transformers==5.13.0，与本机 conda env `ner-eval` 同规格），模型权重不进镜像。

未来若要真正用上 GPU，最小改动路径（本次不做，仅供规划）：

1. 镜像里为 `/opt/ner-venv` 换装 CUDA 版 torch（改 `Dockerfile.api` 的安装源）；
2. 给 `ner_worker.py` 的 `UIEExtractor(device=...)` 加参数化（一行代码 + Registry
   描述符加 `device` 参数 + `code_digest` 随 sync 自动更新）；
3. 给 api 服务加 `gpus` / `deploy.resources.reservations.devices`，并先在服务器验证
   `sudo docker run --rm --gpus all nvidia/cuda:12.6.1-base-ubuntu22.04 nvidia-smi`。

前置验证（服务器上执行，二进制能出 GPU 表格才算 GPU 容器可用）：

```bash
sudo nvidia-container-cli --no-kmod info   # 只验证 CLI 本身
sudo docker run --rm --gpus all nvidia/cuda:12.6.1-base-ubuntu22.04 nvidia-smi
```

## 〇、首次部署（无历史数据）

```bash
cd /data/slx/projects/qa-eval-deploy/deploy/server

# 1. 环境配置
cp .env.example .env
vim .env    # QA_EVAL_DATA_DIR / QA_EVAL_UID / QA_EVAL_GID（见文件内注释）

# 2. 准备数据目录（uid 与 .env 一致）
DATA_DIR=$(grep -E '^QA_EVAL_DATA_DIR=' .env | cut -d= -f2)
sudo mkdir -p "$DATA_DIR"/{runtime,artifacts,models/uie-mini,vendor/uie_pytorch_src}
sudo chown -R "$(grep '^QA_EVAL_UID=' .env | cut -d= -f2):$(grep '^QA_EVAL_GID=' .env | cut -d= -f2)" \
  "$DATA_DIR"/runtime "$DATA_DIR"/artifacts

# 3. 生成访问认证（必须；容器会拒绝在无凭据时启动，杜绝裸奔）
chmod +x gen-htpasswd.sh
./gen-htpasswd.sh slx            # 交互输入密码；多个授权用户 = 多次执行

# 4. 构建并启动
sudo docker compose --env-file .env up -d --build
sudo docker compose --env-file .env ps     # 等 api/web 都 (healthy)

# 5. 验证
curl -u slx:密码 http://127.0.0.1:5173/api/v1/health   # status: ok
# 浏览器访问 http://服务器IP:5173，输入 Basic Auth 凭据
```

首次启动时：数据库文件不存在 → 自动 `alembic upgrade head` 建表 → 自动
Registry 同步（BG-06-01 默认参数已在镜像内改写为容器路径）。

## 一、历史数据迁移（Mac → 服务器）

Mac 本地待迁：SQLite 历史库、约 21GB `data/artifacts`、历史评测结果、32 万条
政策数据（已在库中）。**UIE 模型与 vendor 源码在 gitignore 的 `experiments/`
下，git 同步不包含它们，必须单独拷贝。**

### 1. 服务器停机窗口

迁移期间旧数据不会再变化（先停 Mac 侧使用），但要求服务器侧**尚未启动过
API**（或先 `sudo docker compose --env-file .env down`），避免 SQLite 在拷贝中
被写入。

### 2. 从 Mac 传输（示例，rsync 断点续传）

```bash
# Mac 上执行。DB 先做一致性快照（WAL 合并进主文件）
cd /Users/daphne/Dev/project-intern/qa-eval
sqlite3 data/runtime/qa-eval.sqlite3 "VACUUM INTO '/tmp/qa-eval-migrate.sqlite3'"

# 数据库（几百 MB 级）
rsync -av --inplace /tmp/qa-eval-migrate.sqlite3 \
  slx@服务器:/data/slx/projects/qa-eval-data/runtime/qa-eval.sqlite3

# Artifact Store（约 21GB，可分批、可续传）
rsync -av data/artifacts/ slx@服务器:/data/slx/projects/qa-eval-data/artifacts/

# UIE 模型（权重 + tokenizer 配置，含校验用的 pytorch_model.bin）
rsync -av experiments/bg06_ner/models/uie-mini/ \
  slx@服务器:/data/slx/projects/qa-eval-data/models/uie-mini/

# UIE vendor 源码（uie_pytorch 包本体）
rsync -av experiments/bg06_ner/vendor/uie_pytorch/src/ \
  slx@服务器:/data/slx/projects/qa-eval-data/vendor/uie_pytorch_src/
```

> 校验：`sha256sum` 两端对比 `models/uie-mini/pytorch_model.bin`，必须等于
> Registry 描述符中的 `model_checksum`
> （`sha256:84dcf731…787c0a6`）。不一致时 BG-06-01 会拒绝执行——这是设计行为。

### 3. 权限与启动

```bash
DATA_DIR=/data/slx/projects/qa-eval-data
sudo chown -R <uid>:<gid> "$DATA_DIR"/runtime "$DATA_DIR"/artifacts
sudo docker compose --env-file .env up -d --build
```

启动行为（对历史库安全）：

- 检测到已存在的 `qa-eval.sqlite3` → **不会自动迁移**（默认
  `QA_EVAL_RUN_MIGRATIONS=if-missing`），只打印当前 Alembic 版本。历史库与当前
  代码同版本（head 一致），无需升级；若未来版本落后，先
  `cp qa-eval.sqlite3 qa-eval.sqlite3.bak.$(date +%F)` 备份，再临时把
  `QA_EVAL_RUN_MIGRATIONS` 改为 `always` 升级一次。
- Registry 同步会把镜像内已改写路径的 BG-06-01 描述符 upsert 进库。

### 4. 重写历史合约里的 UIE 路径（一次性，必做）

合约创建时会把当时的默认路径（Mac 绝对路径）固化进合约文档，必须重写为容器
路径，否则老合约跑 BG-06-01 时找不到解释器/模型：

```bash
# 先预览会改什么
sudo docker compose --env-file .env exec api \
  python /opt/scripts/rewrite-uie-paths.py --dry-run
# 确认后执行（幂等，重复执行无副作用）
sudo docker compose --env-file .env exec api \
  python /opt/scripts/rewrite-uie-paths.py
```

只改 `quality_contract_versions` 中的三个路径参数并同步重算 fingerprint；
历史评测 Run 记录保持原样（它们是对当时运行情况的记录，不会被重放）。

### 5. 验收清单

```bash
sudo docker compose --env-file .env ps                          # 双 (healthy)
curl -u 用户:密码 http://127.0.0.1:5173/api/v1/health           # status ok
# UI：数据集列表非空（政策数据在库）；随便开一个历史评测 Run 看结果可读；
#     下载一个 document-version 原始文件（验证 Artifact 挂载）；
#     对任一数据集执行 BG-06-01 评测（验证 UIE 子进程 + 模型挂载）。
```

## 二、日常运维

```bash
alias qas='sudo docker compose --env-file /data/slx/projects/qa-eval-deploy/deploy/server/.env -f /data/slx/projects/qa-eval-deploy/deploy/server/docker-compose.yml'
qas ps                  # 状态（healthy 与否）
qas logs -f api         # API 日志（评测执行进度也在其中）
qas restart api         # 重启 API（web 不动）
qas stop                # 停止（数据保留）
qas up -d               # 恢复
```

- **代码更新**：`git pull` 后 `qas up -d --build`（Registry sync 每次启动自动执行）。
- **新增访问用户**：`./gen-htpasswd.sh 新用户名` 后 `qas restart web`（htpasswd
  是挂载文件，重启即生效；改密 = 对同名用户重跑一次）。
- **撤销访问**：编辑 `htpasswd` 删掉对应行后 `qas restart web`；如需立即踢掉
  已认证会话，改 `nginx.conf` 的 realm 字符串（`auth_basic "QA Eval v2"`）再重启。
- **备份**：
  ```bash
  sqlite3 $DATA_DIR/runtime/qa-eval.sqlite3 "VACUUM INTO '/备份路径/qa-eval-$(date +%F).sqlite3'"
  tar -C $DATA_DIR -czf /备份路径/artifacts-$(date +%F).tgz artifacts   # 21GB，按需
  ```
- **危险操作清单**（共享服务器红线）：不要 `down -v`（本部署无 named volume，
  数据在 bind mount，但习惯性带上 -v 有风险）；不要 `docker system prune`；
  不要动其他用户的容器、端口、GPU 进程。

## 三、故障排查

| 现象 | 排查 |
| --- | --- |
| web 反复重启，日志含 `htpasswd 缺失` | 没生成凭据：`./gen-htpasswd.sh <用户>` 后 `qas up -d` |
| api 起不来，日志 `permission denied` | `$DATA_DIR/runtime|artifacts` 属主与 `.env` 的 UID/GID 不一致 → `chown` |
| health 显示 `database writable: false` | 同上；或 data 目录误挂 NFS（SQLite WAL 要求本机磁盘） |
| BG-06-01 评测报 `ner_python is unavailable` | `models/uie-mini` 或 `vendor/uie_pytorch_src` 没迁移/挂载；或合约未重写路径（见迁移第 4 步） |
| BG-06-01 报 `model checksum mismatch` | 权重与 Mac 不一致，重新 rsync 并核对 sha256 |
| 说明文档评测一直无结果 | `.env` 里 LLM 网关未配置或服务器不可达；文档评测为可选功能 |
| 8000 端口冲突 | 本部署不发布 8000，检查是否有别的 compose 项目占用（勿动他人容器） |

## 四、安全边界

- 唯一入口 5173，整站 Basic Auth（含 `/api/*`），凭据文件不在镜像、不在 git。
- `/healthz` 免认证但只返回存活标记，无数据；API 健康检查走容器内部网络。
- `.env`、SQLite、Artifact、模型权重均不进镜像（`.dockerignore` 强制排除）。
- API 容器以非 root、指定 uid 运行；模型/vendor 目录只读挂载。
