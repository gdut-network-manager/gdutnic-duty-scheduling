#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")"

if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "需要 Python 3.10+（建议 3.12）。" >&2
  exit 1
fi

if ! "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "需要 Python 3.10+，当前: $($PY -V 2>&1)" >&2
  exit 1
fi

# 使用项目内虚拟环境，避免 Linux 上 PEP 668「externally-managed-environment」
if [ ! -x ".venv/bin/python" ]; then
  echo "[start] 创建虚拟环境 .venv …"
  "$PY" -m venv .venv
fi
PY="$(cd .venv && pwd)/bin/python"
PIP=("$PY" -m pip)

echo "[start] 升级 pip …"
"${PIP[@]}" install --upgrade pip >/dev/null 2>&1 || true

pip_install() {
  "${PIP[@]}" install -r requirements.txt "$@"
}

echo "[start] 安装依赖（默认 PyPI）…"
if ! pip_install; then
  echo "[start] 默认源失败，改用国内镜像…"
  ok=0
  while IFS='|' read -r index host; do
    [ -n "$index" ] || continue
    echo "[start] 尝试镜像: $index"
    if pip_install -i "$index" --trusted-host "$host"; then
      ok=1
      break
    fi
  done <<'EOF'
https://pypi.tuna.tsinghua.edu.cn/simple|pypi.tuna.tsinghua.edu.cn
https://mirrors.aliyun.com/pypi/simple|mirrors.aliyun.com
https://pypi.mirrors.ustc.edu.cn/simple|pypi.mirrors.ustc.edu.cn
https://mirror.baidu.com/pypi/simple|mirror.baidu.com
EOF
  if [ "$ok" -ne 1 ]; then
    echo "[start] 依赖安装失败：默认源与国内镜像均不可用。" >&2
    exit 1
  fi
fi

"$PY" -m server.init_data || exit 1
"$PY" -m server.check_database || exit 1

exec "$PY" -m uvicorn server.app:app --host 0.0.0.0 --port 50025 --no-proxy-headers
