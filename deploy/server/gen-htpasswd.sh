#!/usr/bin/env bash
# ============================================================================
# 生成 / 更新 deploy/server/htpasswd（nginx Basic Auth 凭据）。
#
#   ./gen-htpasswd.sh <用户名> [密码]     # 省略密码则交互式输入（不回显）
#
# - 同名用户重复执行会覆盖旧条目；多个用户 = 多次执行
# - 文件权限 0640；已被 .gitignore / .dockerignore 排除，不会入库或进镜像
# - 服务器上必须有 openssl（系统自带）
# ============================================================================
set -euo pipefail

dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
file="${dir}/htpasswd"

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <username> [password]" >&2
  exit 1
fi
user="$1"
if [[ $# -ge 2 ]]; then
  pass="$2"
else
  read -rsp "Password for '${user}': " pass
  echo
fi

hash="$(openssl passwd -apr1 "${pass}")"

touch "${file}"
# 覆盖同名用户，保留其他用户
grep -v "^${user}:" "${file}" > "${file}.tmp" || true
mv "${file}.tmp" "${file}"
printf '%s:%s\n' "${user}" "${hash}" >> "${file}"
chmod 0640 "${file}"

echo "updated ${file}"
echo "current users: $(cut -d: -f1 "${file}" | paste -sd, -)"
