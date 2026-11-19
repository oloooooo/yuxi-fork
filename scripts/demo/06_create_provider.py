"""步骤 6：创建自定义模型供应商（需要超级管理员），并打印可用的模型 spec。

用法：
  python3 scripts/demo/06_create_provider.py \
      --provider-id demo-provider --display-name "自定义供应商" \
      --provider-url https://api.siliconflow.cn/v1 --api-key-env SILICONFLOW_API_KEY \
      --model deepseek-ai/DeepSeek-V4.1-Flash

随后在客户端选择该模型回答：
  SPEC_MODEL=demo-provider:deepseek-ai/DeepSeek-V4.1-Flash python3 scripts/demo/yuxi_client.py
"""

import argparse
import sys

import yuxi_api


def main():
    parser = argparse.ArgumentParser(description="创建自定义模型供应商")
    parser.add_argument("--provider-id", required=True, help="稳定标识，仅字母/数字/_/-，长度 2-100")
    parser.add_argument("--display-name", help="展示名称，默认同 provider-id")
    parser.add_argument("--provider-url", required=True, help="OpenAI 兼容 API 基础 URL，如 https://api.deepseek.com/v1")
    parser.add_argument(
        "--provider-type", default="openai", choices=["openai", "anthropic", "gemini", "openrouter"]
    )
    parser.add_argument("--api-key-env", help="密钥所在环境变量名（推荐），如 SILICONFLOW_API_KEY")
    parser.add_argument("--api-key", help="直接写入密钥，会存进数据库；优先用 --api-key-env")
    parser.add_argument("--model", action="append", required=True, help="启用的 chat 模型 id，可重复")
    parser.add_argument("--base-url", help="Yuxi API 地址，默认取 YUXI_BASE_URL")
    parser.add_argument("--token", help="access token，默认取 YUXI_ACCESS_TOKEN")
    args = parser.parse_args()

    if bool(args.api_key) == bool(args.api_key_env):
        print("需要且只能指定 --api-key-env 或 --api-key 之一", file=sys.stderr)
        return 2

    client, _base, _token = yuxi_api.session(args)
    payload = {
        "provider_id": args.provider_id,
        "display_name": args.display_name or args.provider_id,
        "provider_type": args.provider_type,
        "base_url": args.provider_url,
        "capabilities": ["chat"],
        "enabled_models": [
            {"id": model, "type": "chat", "display_name": model, "source": "manual"} for model in args.model
        ],
        "is_enabled": True,
        ("api_key_env" if args.api_key_env else "api_key"): args.api_key_env or args.api_key,
    }
    provider = (client.call("POST", "/system/model-providers", payload) or {}).get("data") or {}
    print(
        "已创建供应商 %s（类型 %s，已启用模型 %d 个）"
        % (provider.get("provider_id"), provider.get("provider_type"), len(provider.get("enabled_models") or []))
    )
    if args.api_key_env:
        print("密钥取自环境变量 %s，需确保 api/worker 进程能读到它" % args.api_key_env, file=sys.stderr)
    print("可用模型 spec：")
    for model in args.model:
        print("  %s:%s" % (args.provider_id, model))
    print(
        "客户端使用：SPEC_MODEL=%s:%s python3 scripts/demo/yuxi_client.py" % (args.provider_id, args.model[0]),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(yuxi_api.run(main))
