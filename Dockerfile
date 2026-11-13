# syntax=docker/dockerfile:1

# Base images are pulled through the campus mirror. Override PYTHON_IMAGE when the
# mirror path differs (for example an air-gapped Harbor copy).
ARG PYTHON_IMAGE=registry.gdut.edu.cn/docker/library/python:3.12-slim

FROM ${PYTHON_IMAGE} AS builder

ARG PIP_INDEX_URL=https://mirrors.gdut.edu.cn/pypi/web/simple
ARG PIP_TRUSTED_HOST=mirrors.gdut.edu.cn

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

# Dependencies are installed into a separate prefix so the runtime stage stays small.
COPY requirements.txt .

RUN python -m pip install \
        --prefix=/install \
        --no-warn-script-location \
        --index-url "${PIP_INDEX_URL}" \
        --trusted-host "${PIP_TRUSTED_HOST}" \
        -r requirements.txt


FROM ${PYTHON_IMAGE}

ARG APT_MIRROR=mirrors.gdut.edu.cn

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Shanghai \
    PORT=50025

# tzdata is required by zoneinfo's ZoneInfo('Asia/Shanghai') on slim images.
RUN set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i \
            -e "s|http://deb.debian.org/debian|https://${APT_MIRROR}/debian|g" \
            -e "s|http://deb.debian.org/debian-security|https://${APT_MIRROR}/debian-security|g" \
            /etc/apt/sources.list.d/debian.sources; \
    fi; \
    if [ -f /etc/apt/sources.list ]; then \
        sed -i \
            -e "s|http://deb.debian.org/debian|https://${APT_MIRROR}/debian|g" \
            -e "s|http://deb.debian.org/debian-security|https://${APT_MIRROR}/debian-security|g" \
            /etc/apt/sources.list; \
    fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates tzdata; \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

WORKDIR /app

RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid app --no-create-home --home-dir /app --shell /usr/sbin/nologin app \
 && mkdir -p /app/data \
 && chown -R 10001:10001 /app

COPY --chown=10001:10001 server ./server
COPY --chown=10001:10001 web ./web
COPY --chown=10001:10001 icon ./icon
COPY --chown=10001:10001 sql ./sql
# Feature guide rendered on the admin About page (server/wiki.py reads it at request time).
COPY --chown=10001:10001 description ./description
COPY --chown=10001:10001 requirements.txt .env.example ./
COPY --chown=10001:10001 docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN chmod 0755 /usr/local/bin/docker-entrypoint.sh

USER 10001:10001

EXPOSE 50025

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
