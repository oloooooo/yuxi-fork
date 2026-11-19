# Yuxi 轻量沙盒镜像：单租户、非 root、按 Run 用完即弃。
FROM node:24-bullseye-slim AS node-runtime

FROM python:3.13-slim

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=Asia/Shanghai \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# 受限网络的构建参数：APT_MIRROR 需同时提供 /debian 与 /debian-security 路径。
# 留空时保留基础镜像自带的 Debian 官方源，PYPI_INDEX_URL 留空时 pip 使用官方索引。
ARG APT_MIRROR=
ARG PYPI_INDEX_URL=

RUN set -eux \
    # 仅在传入镜像源时改写；留空保留基础镜像自带的 Debian 官方源
    && if [ -n "$APT_MIRROR" ]; then sed -i "s|deb.debian.org|$APT_MIRROR|g; s|security.debian.org/debian-security|$APT_MIRROR/debian-security|g" /etc/apt/sources.list.d/debian.sources; fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        curl \
        file \
        git \
        iproute2 \
        jq \
        libcap2-bin \
        procps \
        ripgrep \
        unzip \
        util-linux \
    && rm -rf /var/lib/apt/lists/*

# 固定普通用户与现有 Workspace 文件身份一致；/usr/bin/python3 供 runner 的 python 模式使用。
RUN groupadd --gid 1000 user \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash user \
    && ln -sf /usr/local/bin/python3 /usr/bin/python3 \
    && ln -sf /usr/local/bin/pip3 /usr/bin/pip3 \
    && mkdir -p /home/user/workspace /home/user/skills \
    && chown -R 1000:1000 /home/user

COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && node --version && npm --version

RUN if [ -n "$PYPI_INDEX_URL" ]; then export PIP_INDEX_URL="$PYPI_INDEX_URL"; fi \
    && pip install --no-cache-dir \
        beautifulsoup4 \
        httpx \
        openpyxl \
        pandas \
        pillow \
        pyarrow \
        pymysql \
        requests

COPY --from=ghcr.io/astral-sh/uv:0.12.6 /uv /uvx /usr/local/bin/

USER 1000:1000
WORKDIR /home/user
CMD ["sleep", "infinity"]
