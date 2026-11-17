#!/bin/bash

# 带端口的私有仓库（Nexus）拉取脚本：先试 Nexus 的几种路径形态，再回落代理式镜像源，最后直连原仓库。
# NEXUS_REGISTRY 形如 nexus.example.internal:8443，可用 YUXI_NEXUS_REGISTRY 覆盖。
# 代理式镜像源默认 m.daocloud.io，可用 YUXI_IMAGE_MIRROR 覆盖。

if [ $# -ne 1 ]; then
    echo "Usage: $0 <image:tag>"
    exit 1
fi

set -e

IMAGE_TAG=$1
NEXUS_REGISTRY="${YUXI_NEXUS_REGISTRY:-nexus.devops.abcd.com:8443}"
MIRROR_HOST="${YUXI_IMAGE_MIRROR:-m.daocloud.io}"
MIRROR_HOST="${MIRROR_HOST%/}"

# 斜杠数量决定镜像格式
SLASH_COUNT=$(echo "$IMAGE_TAG" | tr -cd '/' | wc -c)

# Nexus 仓库是否带 library/ 前缀取决于仓库配置，官方镜像两个候选都试；
# 多级路径先试去掉上游 host 的形态，保留完整路径兜底。
if [ "$SLASH_COUNT" -eq 0 ]; then
    NEXUS_REFS=("$NEXUS_REGISTRY/library/$IMAGE_TAG" "$NEXUS_REGISTRY/$IMAGE_TAG")
elif [ "$SLASH_COUNT" -eq 1 ]; then
    NEXUS_REFS=("$NEXUS_REGISTRY/$IMAGE_TAG")
else
    NEXUS_REFS=("$NEXUS_REGISTRY/${IMAGE_TAG#*/}" "$NEXUS_REGISTRY/$IMAGE_TAG")
fi

if [ "$SLASH_COUNT" -eq 0 ]; then
    MIRROR_URL="$MIRROR_HOST/docker.io/library"
elif [ "$SLASH_COUNT" -eq 1 ]; then
    MIRROR_URL="$MIRROR_HOST/docker.io"
else
    MIRROR_URL="$MIRROR_HOST"
fi

# 从指定源拉取并改回原始 tag；仅在 pull 成功后发布原始 tag
pull_and_retag() {
    local mirror_ref="$1"
    if docker pull "$mirror_ref"; then
        docker tag "$mirror_ref" "$IMAGE_TAG"
        docker rmi "$mirror_ref"
        return 0
    fi
    return 1
}

for nexus_ref in "${NEXUS_REFS[@]}"; do
    echo "Pulling image from Nexus: $nexus_ref"
    if pull_and_retag "$nexus_ref"; then
        echo "Process completed successfully!"
        exit 0
    fi
done

echo "Pulling image from mirror: $MIRROR_URL/$IMAGE_TAG"
if pull_and_retag "$MIRROR_URL/$IMAGE_TAG"; then
    echo "Process completed successfully!"
    exit 0
fi

echo "Mirror unavailable; pulling from the original registry: $IMAGE_TAG" >&2
docker pull "$IMAGE_TAG"
