# qa-eval 服务器部署（Ubuntu 共享 GPU 服务器）

面向 `/data/slx/projects/qa-eval-deploy` 的 Docker Compose 部署。日常更新从 Mac
一键完成（`deploy/server/deploy.sh`）；本文档覆盖首次配置、迁移与运维。

访问地址：`http://100.100.20.106:5173`（整站免密，内网部署约定）。

## 架构

```
用户浏览器 ──HTTP──▶ :5173 web 容器 (nginx)
                      ├─ 静态前端（React 生产构建，SPA 回退，免密）
                      └─ /api/ ─▶ api 容器 :8000（仅 compose 内部网络，不发布端口）
                                    ├─ uvicorn × 1（评测在进程内线程池串行执行）
                                    ├─ SQLite ▶ 挂载 $DATA_DIR/runtime（WAL）
                                    ├─ Artifact ▶ 挂载 $DATA_DIR/artifacts
                                    └─ UIE 子进程 ▶ /opt/ner-venv（CPU torch，本地 wheel 构建）
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

## 〇、首次使用前的必要配置（服务器上，一次性）

> 已完成过首次部署的服务器跳过本节。

```bash
cd /data/slx/projects/qa-eval-deploy/deploy/server

# 1. 环境配置（服务器本地文件，git/rsync 均不同步、不覆盖）
cp .env.example .env
vim .env    # QA_EVAL_DATA_DIR / QA_EVAL_UID / QA_EVAL_GID（见文件内注释）

# 2. 数据目录（uid 与 .env 一致；目录必须在本机磁盘，不能是 NFS——SQLite WAL 要求）
DATA_DIR=$(sed -n 's/^QA_EVAL_DATA_DIR=//p' .env | tail -n 1)
sudo mkdir -p "$DATA_DIR"/{runtime,artifacts,models/uie-mini,vendor/uie_pytorch_src}
sudo chown -R "$(id -u):$(id -g)" "$DATA_DIR"/runtime "$DATA_DIR"/artifacts

# 3. 本地 PyTorch CPU wheel（api 镜像构建必需，服务器外网不可达 pytorch.org）
#    下载地址（Mac 或服务器能访问时）:
#      https://download.pytorch.org/whl/cpu/torch-2.12.1%2Bcpu-cp312-cp312-linux_x86_64.whl
#    放到受保护目录（deploy.sh 不会同步/覆盖/删除其中内容）：
mkdir -p wheels
cp /路径/torch-2.12.1+cpu-cp312-cp312-manylinux_2_28_x86_64.whl wheels/
#    早期版本放在 deploy/server/ 根下的 wheel 会被 deploy.sh 自动迁移进 wheels/。

# 4. docker 权限（deploy.sh 需要非交互执行 docker；二选一）
#    a) 把 slx 加入 docker 组：  sudo usermod -aG docker slx（重新登录生效）
#    b) 或配置免密 sudo：        sudo visudo 加一行
#         slx ALL=(ALL) NOPASSWD: /usr/bin/docker, /usr/bin/docker-compose, /usr/bin/docker-compose*

# 5. 首次构建启动
sudo docker compose --env-file .env up -d --build
sudo docker compose --env-file .env ps     # 等 api/web 都 (healthy)
curl -fsS http://127.0.0.1:5173/healthz   # ok
curl -fsS http://127.0.0.1:5173/api/v1/health
```

首次启动时：数据库文件不存在 → 自动 `alembic upgrade head` 建表 → 自动
Registry 同步（BG-06-01 默认参数已在镜像内改写为容器路径）。

Nginx 整站**免密访问**（`nginx.conf` 构建时打进镜像，无 htpasswd 依赖）。若以后要
重新启用 Basic Auth：恢复 `auth_basic` 两行 + `/etc/nginx/htpasswd` 挂载（见 git
历史 `c817bc9`），并重建 web 镜像。

## 一、日常一键部署（Mac 上执行）

```bash
bash deploy/server/deploy.sh web        # 只更新 Web（同步代码 → 构建 → 更新 → 健康检查）
bash deploy/server/deploy.sh api        # 只更新 API
bash deploy/server/deploy.sh all        # 两者都更新（先 api 后 web）
bash deploy/server/deploy.sh web --dry-run   # 只看将同步哪些文件、将执行哪些远程命令
bash deploy/server/deploy.sh --help
```

前置：Mac 与服务器 SSH 免密（`~/.ssh/config` 别名 `zju`）、当前网络可达服务器。

工作原理（脚本自动完成，无需手工干预）：

1. **预检**：SSH 连通性；服务器 docker/compose 可用性（自动探测直连或
   `sudo -n`）；`.env` 与 `QA_EVAL_DATA_DIR` 存在；api 构建时 `wheels/` 下有
   torch wheel（缺失会在任何改动之前明确报错）。
2. **增量同步**：rsync 白名单 —— `pyproject.toml uv.lock alembic.ini README.md
   .dockerignore` + `src/ apps/ configs/ migrations/ schemas/ deploy/server/`。
   代码目录内 `--delete` 保持与仓库一致；`deploy/server/` 只增不删，且排除
   `.env`、`htpasswd`、`wheels/`、`*.whl`。其余服务器文件（数据目录、模型、
   其他项目）一律不碰。
3. **构建**：`DOCKER_BUILDKIT=0 docker build --network=host`（服务器已验证方式；
   新 docker 报 legacy builder 错误时改用 `QA_DEPLOY_BUILDKIT=1 bash
   deploy/server/deploy.sh ...`）。pip/uv 默认走清华镜像，可用环境变量
   `PIP_INDEX_URL` 覆盖。构建前自动给现有镜像打 `:previous` 标签作为回滚锚点。
   构建失败即停止，**不会**动正在运行的容器。
4. **更新**：`docker compose --env-file .env up -d --no-deps <服务>` 只重建指定
   服务，其他服务不受影响；不执行任何 `down -v` / `prune` / 数据操作。
5. **健康检查**（总超时 180s，可 `QA_DEPLOY_HEALTH_TIMEOUT` 覆盖）：
   `/` HTTP 200、`/healthz` HTTP 200；api 另查 `/api/v1/health` JSON `status=ok`
   （同时验证 nginx 反代）。超时则打印对应容器最近 100 行日志并给出回滚命令。

## 二、历史数据迁移（Mac → 服务器）

Mac 本地待迁：SQLite 历史库、约 21GB `data/artifacts`、历史评测结果、32 万条
政策数据（已在库中）。**UIE 模型与 vendor 源码在 gitignore 的 `experiments/`
下，部署同步不包含它们，必须单独拷贝。**

### 1. 服务器停机窗口

迁移期间旧数据不会再变化（先停 Mac 侧使用），但要求服务器侧**尚未启动过
API**（或先 `sudo docker compose --env-file .env down`），避免 SQLite 在拷贝中
被写入。

### 2. 从 Mac 传输（rsync 断点续传）

```bash
# Mac 上执行。DB 先做一致性快照（WAL 合并进主文件）
cd /Users/daphne/Dev/project-intern/qa-eval
sqlite3 data/runtime/qa-eval.sqlite3 "VACUUM INTO '/tmp/qa-eval-migrate.sqlite3'"

# 数据库（几百 MB 级）
rsync -av --inplace /tmp/qa-eval-migrate.sqlite3 \
  zju:/data/slx/projects/qa-eval-data/runtime/qa-eval.sqlite3

# Artifact Store（约 21GB，可分批、可续传）
rsync -av data/artifacts/ zju:/data/slx/projects/qa-eval-data/artifacts/

# UIE 模型（权重 + tokenizer 配置，含校验用的 pytorch_model.bin）
rsync -av experiments/bg06_ner/models/uie-mini/ \
  zju:/data/slx/projects/qa-eval-data/models/uie-mini/

# UIE vendor 源码（uie_pytorch 包本体）
rsync -av experiments/bg06_ner/vendor/uie_pytorch/src/ \
  zju:/data/slx/projects/qa-eval-data/vendor/uie_pytorch_src/
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
sudo docker compose --env-file .env ps              # 双 (healthy)
curl -fsS http://127.0.0.1:5173/healthz            # ok
curl -fsS http://127.0.0.1:5173/api/v1/health      # {"status":"ok",...}
# UI：数据集列表非空（政策数据在库）；随便开一个历史评测 Run 看结果可读；
#     下载一个 document-version 原始文件（验证 Artifact 挂载）；
#     对任一数据集执行 BG-06-01 评测（验证 UIE 子进程 + 模型挂载）。
```

## 三、日常运维

```bash
alias qas='sudo docker compose --env-file /data/slx/projects/qa-eval-deploy/deploy/server/.env -f /data/slx/projects/qa-eval-deploy/deploy/server/docker-compose.yml'
qas ps                  # 状态（healthy 与否）
qas logs -f api         # API 日志（评测执行进度也在其中）
qas restart api         # 重启 API（web 不动）
qas stop                # 停止（数据保留）
qas up -d               # 恢复
```

- **代码更新**：直接用 Mac 一键脚本（第一节）；服务器上不再需要 `git pull`。
- **备份**：
  ```bash
  sqlite3 $DATA_DIR/runtime/qa-eval.sqlite3 "VACUUM INTO '/备份路径/qa-eval-$(date +%F).sqlite3'"
  tar -C $DATA_DIR -czf /备份路径/artifacts-$(date +%F).tgz artifacts   # 21GB，按需
  ```
- **危险操作清单**（共享服务器红线）：不要 `down -v`；不要 `docker system prune`；
  不要动其他用户的容器、端口、GPU 进程。

## 四、回滚

每次部署构建前，脚本会把当前镜像打上 `:previous` 标签（如
`qa-eval-server/api:previous`）。更新后发现问题：

```bash
# 服务器上执行：回滚到上一次部署的镜像（当前镜像不受影响，可再切回）
cd /data/slx/projects/qa-eval-deploy/deploy/server
sudo docker tag qa-eval-server/api:previous qa-eval-server/api:latest
sudo docker compose --env-file .env up -d --no-deps api
curl -fsS http://127.0.0.1:5173/api/v1/health
```

代码级回滚（跨多个版本的回滚）：Mac 上 `git checkout <旧提交>` 后重新
`bash deploy/server/deploy.sh <服务>` 即可（构建会复用缓存层，很快）。

数据回滚：SQLite 与 Artifact 全程不被部署触碰；用「日常运维 → 备份」的副本恢复。

## 五、数据持久化说明

| 数据 | 位置（宿主机） | 部署是否会动 |
| --- | --- | --- |
| SQLite 数据库（WAL） | `$QA_EVAL_DATA_DIR/runtime/` | 永不（同步白名单之外；迁移策略默认 if-missing，不自动改历史库） |
| Artifact Store（原始/归一化数据、证据、报告） | `$QA_EVAL_DATA_DIR/artifacts/` | 永不 |
| UIE 模型权重 | `$QA_EVAL_DATA_DIR/models/uie-mini/`（只读挂载） | 永不 |
| UIE vendor 源码 | `$QA_EVAL_DATA_DIR/vendor/uie_pytorch_src/`（只读挂载） | 永不 |
| 服务器 `.env` / `htpasswd` | `deploy/server/` | 永不（rsync 显式排除，且该目录不做删除同步） |
| PyTorch wheel | `deploy/server/wheels/` | 永不（同上；缺失时构建明确报错，不静默下载） |

数据库迁移由 api 容器 entrypoint 按 `QA_EVAL_RUN_MIGRATIONS` 策略执行，默认
只对全新库自动建表；历史库要升级必须先手动备份、再把策略临时改为 `always`。

## 六、故障排查

| 现象 | 排查 |
| --- | --- |
| 预检报 `docker 不可用` | slx 不在 docker 组且 sudo 需要密码：按「〇.4」配置后重试 |
| 预检/构建报 `未找到本地 PyTorch wheel` | 把 `torch-2.12.1+cpu-*.whl` 放入服务器 `deploy/server/wheels/`（脚本不会替你下载） |
| 构建报 `legacy builder` 相关错误 | 服务器 Docker 较新：`QA_DEPLOY_BUILDKIT=1 bash deploy/server/deploy.sh ...` |
| 同步阶段卡住/报权限 | 检查 `zju` 免密登录；deploy.sh 使用 BatchMode，不会交互式询问密码 |
| 健康检查超时 | 看脚本末尾自动打印的容器日志；常见：`.env` 的 UID/GID 与数据目录属主不符、`models/uie-mini` 未迁移 |
| api 起不来，日志 `permission denied` | `$DATA_DIR/runtime|artifacts` 属主与 `.env` 的 UID/GID 不一致 → `chown` |
| health 显示 `database writable: false` | 同上；或 data 目录误挂 NFS（SQLite WAL 要求本机磁盘） |
| BG-06-01 评测报 `ner_python is unavailable` | `models/uie-mini` 或 `vendor/uie_pytorch_src` 没迁移/挂载；或合约未重写路径（见迁移第 4 步） |
| BG-06-01 报 `model checksum mismatch` | 权重与 Mac 不一致，重新 rsync 并核对 sha256 |
| 说明文档评测一直无结果 | `.env` 里 LLM 网关未配置或服务器不可达；文档评测为可选功能 |
| 8000 端口冲突 | 本部署不发布 8000，检查是否有别的 compose 项目占用（勿动他人容器） |
| Mac 无法访问但服务器本机 curl 正常 | Mac 到服务器的网络/VPN 问题，与部署本身无关 |

## 七、安全边界

- 唯一入口 5173，整站免密（内网部署约定）；`/healthz` 只返回存活标记，无数据；
  API 健康检查走容器内部网络 + nginx 反代。
- `.env`、SQLite、Artifact、模型权重均不进镜像（`.dockerignore` 强制排除）。
- API 容器以非 root、指定 uid 运行；模型/vendor 目录只读挂载。
- 部署脚本白名单同步 + 分目录 `--delete`，不会触碰服务器上的配置、数据、
  wheel 及其他项目；构建失败不会停止在跑的服务。
