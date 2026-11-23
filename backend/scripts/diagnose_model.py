#!/usr/bin/env python3
"""模型配置诊断工具。

用途：当对话出现"模型服务鉴权失败"时，定位是配置问题还是密钥问题。
安全：**绝不打印密钥正文**，只输出长度、前缀、结构特征和脱敏后的错误信息。

用法：
    cd backend && uv run python scripts/diagnose_model.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from app.ai.deepseek import _resolve_proxy  # noqa: E402
from app.core.config import get_settings  # noqa: E402

ENV_NAME = "DEEPSEEK_API_KEY"


def mask(value: str) -> str:
    """只显示首尾各 4 位，中间打码。"""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]} (长度 {len(value)})"


def check_key_format(raw: str) -> list[str]:
    """检查常见配置错误。返回问题列表（空表示未发现问题）。"""
    problems: list[str] = []

    if raw != raw.strip():
        problems.append("值的前后存在空白字符（开始或结尾有空格/换行）")
    if any(c in raw for c in ('"', "'")):
        problems.append("值里含有引号（很可能把引号也一起写进了环境变量）")
    if raw.lower().startswith("bearer "):
        problems.append("值以 'Bearer ' 开头——只填密钥本身，不要带认证前缀")
    if any(c.isspace() for c in raw):
        problems.append("值中间含有空白字符")
    if not raw.startswith("sk-"):
        problems.append("值不以 'sk-' 开头（DeepSeek 密钥通常以 sk- 开头）")
    if "\n" in raw:
        problems.append("值含有换行符（可能复制了多行内容）")

    return problems


def main() -> int:
    settings = get_settings()
    print("=" * 60)
    print("模型配置诊断")
    print("=" * 60)

    print(f"\n供应商     : {settings.provider}")
    print(f"模型       : {settings.model}")
    print(f"接口地址   : {settings.provider_base_url}")

    raw = os.environ.get(ENV_NAME, "")
    if not raw:
        print(f"\n❌ 环境变量 {ENV_NAME} 未设置。")
        print("\n设置方式（在**启动后端的那一个终端**里执行）：")
        print(f'    export {ENV_NAME}="sk-你的密钥"')
        print("    ./scripts/dev.sh")
        print("\n注意：在一个终端 export，只影响那个终端。")
        return 1

    print(f"\n✅ 已读取 {ENV_NAME}: {mask(raw)}")

    problems = check_key_format(raw)
    if problems:
        print("\n⚠️  密钥格式存在问题：")
        for p in problems:
            print(f"   - {p}")
        print("\n请用以下方式重新设置（注意引号的位置）：")
        print(f"    export {ENV_NAME}=sk-实际密钥内容     # 引号不包进值里也可以")
    else:
        print("✅ 密钥格式检查通过（无空白、无引号、sk- 前缀）")

    key = raw.strip()
    proxy = _resolve_proxy(settings.provider_base_url)
    print(f"\n代理       : {proxy or '直连（不使用代理）'}")

    print("\n正在向供应商发送一个最小测试请求（不包含你的任何对话内容）…")

    body = {
        "model": settings.model,
        "messages": [{"role": "user", "content": "ping"}],
        "stream": False,
        "max_tokens": 1,
    }
    try:
        with httpx.Client(timeout=30, proxy=proxy, trust_env=False) as client:
            response = client.post(
                f"{settings.provider_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=body,
            )
    except httpx.HTTPError as exc:
        print(f"\n❌ 网络请求失败：{type(exc).__name__}")
        print("   请检查网络，或代理设置（参考上面的“代理”一行）。")
        return 1

    print(f"\nHTTP 状态  : {response.status_code}")
    print(f"响应        : {response.text[:400]}")

    if response.status_code == 200:
        print("\n✅ 密钥有效，模型可用。")
        return 0

    if response.status_code == 401:
        print("\n❌ 密钥被供应商拒绝（401）。可能原因：")
        print("   1. 密钥输入错误或已失效（可到 DeepSeek 控制台重新生成）")
        print("   2. 密钥已被删除/重置")
        print("   3. 复制时漏字符或多字符（对比响应里提到的末 4 位）")
        print("\n   → 供应商在响应中回显了它收到的密钥末 4 位，")
        print("     请与你在控制台看到的密钥末 4 位核对。")
        return 2

    if response.status_code == 402:
        print("\n❌ 账户余额不足（402）。")
        return 3

    print("\n❌ 其他错误，请参考上面的响应内容。")
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
