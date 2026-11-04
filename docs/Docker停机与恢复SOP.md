# Docker 停机与恢复 SOP

> 结论：`stop` 容器不会删 volume，数据全部保留。危险的是 `down -v` / `volume prune`。

统一入口（`.env` 在项目根目录，compose 文件在 `deploy/`，必须带 `--env-file`）：

```bash
cd /Users/daphne/Dev/project-intern/qa-eval
export DC="docker compose --env-file .env -f deploy/docker-compose.yml"
```

## 〇、日常操作（推荐：alias 一次配置）

```bash
# 加到 ~/.zshrc，之后任意目录可用
alias qa-up='docker compose --env-file ~/Dev/project-intern/qa-eval/.env -f ~/Dev/project-intern/qa-eval/deploy/docker-compose.yml up -d'
alias qa-stop='docker compose --env-file ~/Dev/project-intern/qa-eval/.env -f ~/Dev/project-intern/qa-eval/deploy/docker-compose.yml stop'
alias qa-ps='docker compose --env-file ~/Dev/project-intern/qa-eval/.env -f ~/Dev/project-intern/qa-eval/deploy/docker-compose.yml ps'
```

- 启动项目：`qa-up` → 等 postgres `(healthy)` → 再起 API（uvicorn）和前端
- 关闭项目：先停 API → `qa-stop`
- 只需要数据库时：把结尾换成 `up -d postgres`
- `qa-eval-mysql` 不在 compose 里，单独 `docker start/stop qa-eval-mysql`

## 一、停机前检查（可选但建议）

```bash
# 确认没有应用还连着库（只应看到 psql 自己）
docker exec qa-eval-postgres-1 sh -c \
  'psql -U "$POSTGRES_USER" -d qa_eval -tAc "SELECT pid, application_name FROM pg_stat_activity WHERE datname IS NOT NULL;"'
```

## 二、停机（保留数据）

```bash
$DC stop          # 只停容器；volume 原样保留
```

- postgres 正常 stop 会做 checkpoint，数据安全。
- `restart: unless-stopped`：stop 之后即使重启 Docker Desktop 也不会被自动拉起。
- 想彻底不占资源可退出 Docker Desktop（Quit），volume 存在磁盘镜像里，安全。

## 三、确认数据还在

```bash
docker volume ls | grep -E 'qa-eval|mysql'
# 应看到 4 个卷：
#   qa-eval_postgres_data   (qa_eval 93MB + qa_eval_test 11MB)
#   qa-eval_redis_data
#   qa-eval_minio_data
#   mysql_mysql_data        (qa-eval-mysql 容器用，该容器单独创建、已停)
```

## 四、恢复

```bash
$DC up -d
$DC ps            # 等 postgres 显示 (healthy)
```

## 五、（可选）停机前导出一份逻辑备份

```bash
mkdir -p backups
docker exec qa-eval-postgres-1 sh -c 'pg_dump -U "$POSTGRES_USER" qa_eval' \
  > backups/qa_eval_$(date +%Y%m%d).sql
```

## 🚫 红线（任何一条都会丢数据）

| 命令                                        | 后果                                           |
| ------------------------------------------- | ---------------------------------------------- |
| `docker compose down -v`                  | `-v` 会删除所有 named volume = 删库          |
| `docker volume prune`                     | 清掉"未使用"卷；已停容器的卷也可能被判定未使用 |
| `docker volume rm <卷名>`                 | 直接删卷                                       |
| Docker Desktop → Reset to factory defaults | 全部数据清空                                   |

（`docker compose down` 不带 `-v`、Quit Docker Desktop 都是安全的。）

## 备注

- `qa-eval-mysql` 是单独 `docker run` 创建的（不在 compose 项目里），已停 6 天，无需操作；它的数据在 `mysql_mysql_data` 卷，同样不要 prune。
- redis 已开 AOF（`--appendonly yes`），重启后数据自动恢复。
