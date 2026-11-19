"""步骤 5：把本地文件上传到工作区目录（默认为第一个项目的 workdir，否则工作区根目录）。

用法：
  python3 scripts/demo/05_upload_to_workdir.py --file report.pdf
  python3 scripts/demo/05_upload_to_workdir.py --file a.csv --file b.csv --parent /uploads
"""

import argparse

import yuxi_api


def main():
    parser = argparse.ArgumentParser(description="上传文件到工作区目录")
    parser.add_argument("--file", action="append", required=True, help="本地文件路径，可重复指定")
    parser.add_argument("--parent", help="目标目录的虚拟路径，默认第一个项目的 workdir_path，否则 /")
    parser.add_argument("--base-url", help="API 地址，默认取 YUXI_BASE_URL")
    parser.add_argument("--token", help="access token，默认取 YUXI_ACCESS_TOKEN")
    args = parser.parse_args()

    client, base, token = yuxi_api.session(args)
    parent = args.parent
    if not parent:
        projects = client.call("GET", "/projects") or []
        parent = next((p.get("workdir_path") for p in projects if p.get("workdir_path")), "/")

    data = yuxi_api.post_multipart(
        base,
        token,
        "/workspace/upload",
        {"parent_path": parent},
        [("files", path) for path in args.file],
    )
    print("已上传到 %s：" % parent)
    for entry in data.get("entries") or []:
        print("  %s（%s 字节，沙盒路径 %s）" % (entry.get("path"), entry.get("size"), entry.get("virtual_path")))
    return 0


if __name__ == "__main__":
    raise SystemExit(yuxi_api.run(main))
