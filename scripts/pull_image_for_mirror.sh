#!/bin/bash
# 受限网络用：镜像站基址由 YUXI_IMAGE_MIRROR 指定，接口与 pull_image.sh 一致。
# 用法: YUXI_IMAGE_MIRROR=registry.example.com ./scripts/pull_image_for_mirror.sh python:3.13-slim

if [ $# -ne 1 ]; then
    echo "Usage: YUXI_IMAGE_MIRROR=<mirror-host> $0 <image:tag>"
    exit 1
fi

set -e  # 当命令失败时，立即退出脚本

IMAGE_TAG=$1
MIRROR_HOST="${YUXI_IMAGE_MIRROR:-m.daocloud.io}"
MIRROR_HOST="${MIRROR_HOST%/}"

# Count the number of slashes to determine the image format
SLASH_COUNT=$(echo $IMAGE_TAG | tr -cd '/' | wc -c)

# Set mirror URL based on image format
if [ $SLASH_COUNT -eq 0 ]; then
    # No prefix (e.g., python:3.13-slim)
    MIRROR_URL="$MIRROR_HOST/docker.io/library"
elif [ $SLASH_COUNT -eq 1 ]; then
    # One prefix (e.g., milvusdb/milvus:latest)
    MIRROR_URL="$MIRROR_HOST/docker.io"
else
    # Two or more prefixes (e.g., quay.io/coreos/etcd:v3.5.5)
    MIRROR_URL="$MIRROR_HOST"
fi

# Pull image from mirror
echo "Pulling image from mirror: $MIRROR_URL/$IMAGE_TAG"
if ! docker pull "$MIRROR_URL/$IMAGE_TAG"; then
    echo "Mirror unavailable; pulling from the original registry: $IMAGE_TAG" >&2
    docker pull "$IMAGE_TAG"
    exit $?
fi

# Tag image with original name
docker tag "$MIRROR_URL/$IMAGE_TAG" "$IMAGE_TAG"

# Remove mirror image
docker rmi "$MIRROR_URL/$IMAGE_TAG"

echo "Process completed successfully!"
