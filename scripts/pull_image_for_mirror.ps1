param(
    [Parameter(Mandatory=$true)]
    [string]$ImageTag
)

# 当命令失败时，立即退出脚本
$ErrorActionPreference = "Stop"

Write-Host "Pulling image: $ImageTag" -ForegroundColor Green

# 镜像站基址：受限网络用 YUXI_IMAGE_MIRROR 指向内网代理，默认与 pull_image.ps1 一致
$mirrorHost = $env:YUXI_IMAGE_MIRROR
if ([string]::IsNullOrEmpty($mirrorHost)) {
    $mirrorHost = "m.daocloud.io"
}
$mirrorHost = $mirrorHost.TrimEnd("/")

# 计算斜杠数量来确定镜像格式
$slashCount = ($ImageTag -split '/' | Measure-Object).Count - 1

# 根据镜像格式设置镜像 URL
switch ($slashCount) {
    0 {
        # 无前缀 (例如: python:3.13-slim)
        $mirrorUrl = "$mirrorHost/docker.io/library"
        Write-Host "Image format: Official image (no prefix)" -ForegroundColor Cyan
    }
    1 {
        # 一个前缀 (例如: milvusdb/milvus:latest)
        $mirrorUrl = "$mirrorHost/docker.io"
        Write-Host "Image format: Hub repository (one prefix)" -ForegroundColor Cyan
    }
    default {
        # 两个或更多前缀 (例如: quay.io/coreos/etcd:v3.5.5)
        $mirrorUrl = "$mirrorHost"
        Write-Host "Image format: Third-party registry (multiple prefixes)" -ForegroundColor Cyan
    }
}

$fullMirrorUrl = "$mirrorUrl/$ImageTag"
Write-Host "Mirror URL: $fullMirrorUrl" -ForegroundColor Yellow

try {
    # 从镜像加速器拉取镜像
    Write-Host "Step 1: Pulling image from mirror..." -ForegroundColor Blue
    docker pull $fullMirrorUrl

    if ($LASTEXITCODE -ne 0) {
        # 与 pull_image_for_mirror.sh 一致：镜像站缺少该镜像时回落原仓库
        Write-Host "Mirror unavailable; pulling from the original registry..." -ForegroundColor Yellow
        docker pull $ImageTag
    } else {
        # 重新标记为原始名称，再删除镜像加速器标签
        Write-Host "Step 2: Tagging image with original name..." -ForegroundColor Blue
        docker tag $fullMirrorUrl $ImageTag
        Write-Host "Step 3: Removing mirror tag..." -ForegroundColor Blue
        docker rmi $fullMirrorUrl
    }

    if ($LASTEXITCODE -ne 0) {
        exit 1
    }

    Write-Host "`nProcess completed successfully!" -ForegroundColor Green
    Write-Host "`nCurrent Docker images:" -ForegroundColor Yellow
    docker images
} catch {
    Write-Host "`nError occurred: $_" -ForegroundColor Red
    exit 1
}
