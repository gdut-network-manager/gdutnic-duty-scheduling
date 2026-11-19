#!/usr/bin/env sh
# Container entrypoint: prepare data/, wait for MySQL, then run the application.
# The development start.sh is deliberately not used here: it creates a virtualenv
# and installs dependencies, both of which already happened at image build time.
set -eu

cd /app

# The admin About page renders description/description.md at request time. The app
# degrades to an empty card when it is missing, so make a bad image obvious here.
if [ ! -f description/description.md ]; then
    echo "[entrypoint] 警告：镜像中缺少 description/description.md，关于页的功能说明将不可用。" >&2
fi

python -m server.init_data

ATTEMPTS="${DB_WAIT_ATTEMPTS:-60}"
INTERVAL="${DB_WAIT_INTERVAL:-5}"
attempt=1

while ! python -m server.check_database; do
    if [ "$attempt" -ge "$ATTEMPTS" ]; then
        echo "[entrypoint] 数据库在 ${ATTEMPTS} 次尝试后仍不可用，退出。" >&2
        exit 1
    fi
    echo "[entrypoint] 数据库尚未就绪（第 ${attempt}/${ATTEMPTS} 次），${INTERVAL}s 后重试…" >&2
    attempt=$((attempt + 1))
    sleep "$INTERVAL"
done

# Allow `docker run <image> python -m ...` for maintenance and tests.
if [ "$#" -gt 0 ]; then
    exec "$@"
fi

exec python -m uvicorn server.app:app \
    --host 0.0.0.0 \
    --port "${PORT:-50025}" \
    --no-proxy-headers
