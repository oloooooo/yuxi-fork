"""受限网络部署：镜像站、Debian 源、PyPI 源与前端预构建各自只有一个拥有者。"""

import os
import subprocess
from pathlib import Path

import pytest

from test.unit.config._shared import load_compose, project_root


API_BUILD_SERVICES = ("api", "worker", "app-init", "sandbox-runner")


def _run_mirror_pull(tmp_path: Path, image: str, mirror: str | None) -> subprocess.CompletedProcess:
    """用假 docker 运行镜像站脚本，记录脚本实际拉取的镜像名。"""
    docker = tmp_path / "docker"
    docker.write_text(
        """#!/bin/bash
set -e
case "$1" in
    pull) echo "$2" >> "$IMAGE_STATE/pulls"; echo digest > "$IMAGE_STATE/mirror" ;;
    tag) cp "$IMAGE_STATE/mirror" "$IMAGE_STATE/original" ;;
    rmi) rm "$IMAGE_STATE/mirror" ;;
esac
"""
    )
    docker.chmod(0o700)
    env = {**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}", "IMAGE_STATE": str(tmp_path)}
    env.pop("YUXI_IMAGE_MIRROR", None)
    if mirror is not None:
        env["YUXI_IMAGE_MIRROR"] = mirror
    return subprocess.run(
        ["bash", str(project_root() / "scripts/pull_image_for_mirror.sh"), image],
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "image,mirror,pulled",
    [
        # 未配置或配置为空串都回落到公共加速站，与 pull_image.sh 的默认行为一致。
        ("python:3.13-slim", None, "m.daocloud.io/docker.io/library/python:3.13-slim"),
        ("python:3.13-slim", "", "m.daocloud.io/docker.io/library/python:3.13-slim"),
        # 内网代理基址带尾斜杠时不能拼出双斜杠。
        ("python:3.13-slim", "harbor.internal:5000/proxy/", "harbor.internal:5000/proxy/docker.io/library/python:3.13-slim"),
        ("milvusdb/milvus:v3.0.2", "harbor.internal", "harbor.internal/docker.io/milvusdb/milvus:v3.0.2"),
        ("quay.io/coreos/etcd:v3.5.25", "harbor.internal", "harbor.internal/quay.io/coreos/etcd:v3.5.25"),
    ],
)
def test_mirror_pull_script_uses_configured_host(tmp_path: Path, image, mirror, pulled):
    """YUXI_IMAGE_MIRROR 决定加速站前缀，拉取成功后仍以原始标签落库。"""
    result = _run_mirror_pull(tmp_path, image, mirror)

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "pulls").read_text().splitlines() == [pulled]
    assert (tmp_path / "original").read_text() == "digest\n"
    assert not (tmp_path / "mirror").exists()


def test_mirror_pull_script_falls_back_to_original_registry(tmp_path: Path):
    """加速站不通时回落原仓库，不留加速站标签。"""
    docker = tmp_path / "docker"
    docker.write_text(
        """#!/bin/bash
set -e
case "$1" in
    pull) [[ "$2" == m.daocloud.io/* ]] && exit 22; echo digest > "$IMAGE_STATE/original" ;;
esac
"""
    )
    docker.chmod(0o700)
    env = {**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}", "IMAGE_STATE": str(tmp_path)}
    env.pop("YUXI_IMAGE_MIRROR", None)

    result = subprocess.run(
        ["bash", str(project_root() / "scripts/pull_image_for_mirror.sh"), "redis:7.4.10-alpine"],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "original registry" in result.stderr


def test_mirror_pull_powershell_script_reads_same_variable():
    """Windows 版本必须读同一个环境变量，否则两平台行为分叉（本机无 pwsh，只校验接线）。"""
    script = (project_root() / "scripts/pull_image_for_mirror.ps1").read_text(encoding="utf-8")

    assert "$env:YUXI_IMAGE_MIRROR" in script
    assert '"m.daocloud.io"' in script


@pytest.mark.parametrize("compose_file", ["docker-compose.yml", "docker-compose.prod.yml"])
def test_compose_passes_build_source_args(compose_file):
    """apt/pypi 源只能经构建参数注入；未配置时传空串表示沿用镜像默认源。"""
    services = load_compose(compose_file)["services"]

    for name in API_BUILD_SERVICES:
        args = services[name]["build"]["args"]
        assert args["APT_MIRROR"] == "${APT_MIRROR:-}", name
        assert args["PYPI_INDEX_URL"] == "${PYPI_INDEX_URL:-}", name


def test_dev_compose_passes_build_source_args_to_sandbox_image():
    """沙盒镜像同样需要构建参数，否则内网环境拉不到 apt/pip 包。"""
    args = load_compose("docker-compose.yml")["services"]["sandbox-image"]["build"]["args"]

    assert args["APT_MIRROR"] == "${APT_MIRROR:-}"
    assert args["PYPI_INDEX_URL"] == "${PYPI_INDEX_URL:-}"


def test_prod_frontend_target_switches_to_prebuilt_image():
    """生产前端镜像默认仍走完整构建，受限网络可切到只打包 dist 的阶段。"""
    build = load_compose("docker-compose.prod.yml")["services"]["frontend"]["build"]

    assert build["target"] == "${YUXI_FRONTEND_BUILD_TARGET:-production}"


def test_api_dockerfile_keeps_current_sources_when_args_empty():
    """构建参数留空时 apt 与 pip 行为与改动前一致。"""
    dockerfile = (project_root() / "docker/api.Dockerfile").read_text(encoding="utf-8")

    assert 'APT_MIRROR="${APT_MIRROR:-mirrors.tuna.tsinghua.edu.cn}"' in dockerfile
    assert "uv sync --no-cache --group test --no-dev --frozen" in dockerfile
    assert 'if [ -n "$PYPI_INDEX_URL" ]; then UV_DEFAULT_INDEX="$PYPI_INDEX_URL" uv lock --no-cache; fi' in dockerfile


def test_sandbox_dockerfile_overrides_both_debian_source_sections():
    """沙盒镜像留空时用基础镜像自带的源；指定镜像源时 debian 与 security 两节都要改写。"""
    dockerfile = (project_root() / "docker/sandbox.Dockerfile").read_text(encoding="utf-8")

    assert 'if [ -n "$APT_MIRROR" ]; then sed -i "s|deb.debian.org|$APT_MIRROR|g;' in dockerfile
    assert "s|security.debian.org/debian-security|$APT_MIRROR/debian-security|g" in dockerfile
    assert 'export PIP_INDEX_URL="$PYPI_INDEX_URL"' in dockerfile


def test_frontend_prebuilt_stage_skips_package_manager():
    """prebuilt 阶段只打包宿主机构建的 dist，镜像内不执行 npm/pnpm。"""
    dockerfile = (project_root() / "docker/frontend.Dockerfile").read_text(encoding="utf-8")
    prebuilt = dockerfile.split("FROM nginx:alpine AS prebuilt", 1)[1]

    assert "COPY --chown=0:0 ./frontend/dist /usr/share/nginx/html" in prebuilt
    assert "npm" not in prebuilt
    assert "pnpm" not in prebuilt


def test_build_context_contains_prebuilt_dist():
    """frontend/dist 被 .dockerignore 忽略时 prebuilt 阶段拿不到静态文件。"""
    rules = {
        line.strip().rstrip("/")
        for line in (project_root() / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip()
    }

    # 带斜杠、不带斜杠、通配和整个 frontend 目录四种写法都会让 COPY 失败。
    for rule in ("frontend/dist", "frontend/dist/**", "frontend"):
        assert rule not in rules


def test_minio_mirror_layer_skips_package_manager():
    """受限网络叠层不装任何包、不联网下载，minio 健康检查也不再依赖 curl。"""
    dockerfile = (project_root() / "docker/minio/Dockerfile.mirror").read_text(encoding="utf-8")
    minio = load_compose("docker-compose.mirror.yml")["services"]["minio"]

    assert "apk add" not in dockerfile
    assert "COPY --from=minio-binary --chmod=0755 minio.linux-${TARGETARCH}.${MINIO_VERSION} /opt/bin/minio" in dockerfile
    assert minio["build"]["dockerfile"] == "Dockerfile.mirror"
    assert "minio-binary" in minio["build"]["additional_contexts"]
    assert "context" not in minio["build"]
    assert minio["healthcheck"]["test"][1:3] == ["wget", "-q"]
