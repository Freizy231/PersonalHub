"""应用配置。

设计原则（AGENT.md §4.2、§8.6）：
- 配置从进程环境变量读取，秘密永远不写进源码或前端。
- 监听地址默认只绑定回环地址 127.0.0.1，绝不默认 0.0.0.0。
- CORS 使用白名单，不使用通配符。

当前 M1 阶段不需要数据库和模型密钥，所以这里只有最少的几项配置。
后续引入配置时优先迁移到 Pydantic Settings，并保持同样的默认安全值。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_str(name: str, default: str) -> str:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip()


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:  # 配置错误要尽早暴露，而不是静默回退
        raise ValueError(f"环境变量 {name} 必须是整数，当前值: {raw!r}") from exc


def _env_list(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return tuple(item.strip() for item in raw.split(",") if item.strip())


# Vite 开发服务器默认端口。生产形态下前端由后端同源提供，届时不需要 CORS。
DEFAULT_DEV_ORIGINS = (
    "http://127.0.0.1:5173",
    "http://localhost:5173",
)


@dataclass(frozen=True, slots=True)
class Settings:
    """不可变配置对象，创建后不可修改，避免运行期被意外改写。"""

    app_name: str = "Personal Hub"
    api_prefix: str = "/api/v1"
    host: str = "127.0.0.1"
    port: int = 8000
    allowed_origins: tuple[str, ...] = field(default=DEFAULT_DEV_ORIGINS)

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            host=_env_str("PH_HOST", "127.0.0.1"),
            port=_env_int("PH_PORT", 8000),
            allowed_origins=_env_list("PH_ALLOWED_ORIGINS", DEFAULT_DEV_ORIGINS),
        )


def get_settings() -> Settings:
    """每次调用重新读取环境变量。

    M1 不引入缓存，保证测试可以安全地修改环境变量。
    """
    return Settings.from_env()
