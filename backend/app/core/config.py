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
from pathlib import Path


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


def default_data_dir() -> Path:
    """按 AGENT.md §5.3 计算用户数据目录。

    Linux: $XDG_DATA_HOME/personal-hub，未设置时 ~/.local/share/personal-hub。
    数据库绝不能放在源码仓库内。
    """
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "personal-hub"


def _env_path(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return Path(raw).expanduser()


# Vite 开发服务器默认端口。生产形态下前端由后端同源提供，届时不需要 CORS。
DEFAULT_DEV_ORIGINS = (
    "http://127.0.0.1:5173",
    "http://localhost:5173",
)

# 官方 DeepSeek 接口地址。中转站/自建网关必须显式配置 PH_PROVIDER_BASE_URL，
# 不要把别人的地址当默认值——发错地址会让用户拿着有效 Key 得到 401 却无从判断。
DEEPSEEK_OFFICIAL_BASE_URL = "https://api.deepseek.com"


def _default_base_url() -> str:
    provider = _env_str("PH_PROVIDER", "deepseek")
    if provider == "deepseek":
        return DEEPSEEK_OFFICIAL_BASE_URL
    # 非官方供应商没有通用默认地址；留空会让用户在启动时就被提示去配置。
    return ""


@dataclass(frozen=True, slots=True)
class Settings:
    """不可变配置对象，创建后不可修改，避免运行期被意外改写。"""

    app_name: str = "Personal Hub"
    api_prefix: str = "/api/v1"
    host: str = "127.0.0.1"
    port: int = 8000
    allowed_origins: tuple[str, ...] = field(default=DEFAULT_DEV_ORIGINS)
    # 字段默认值同样读取 PH_DATA_DIR（经 from_env_path），
    # 避免 Settings() 与 Settings.from_env() 对同一环境变量给出不同结果。
    data_dir: Path = field(default_factory=lambda: _env_path("PH_DATA_DIR", default_data_dir()))

    # 模型配置。API Key 刻意不放在这里：Settings 会被打日志、序列化、跨进程传递，
    # 密钥一旦进入配置对象就很容易泄漏。Key 只在需要时从环境变量直接读取（见 app/ai）。
    provider: str = field(default_factory=lambda: _env_str("PH_PROVIDER", "deepseek"))
    model: str = field(default_factory=lambda: _env_str("PH_MODEL", "deepseek-chat"))
    # 未显式设置时按 provider 推导：deepseek 用官方地址，其余必须由用户提供。
    provider_base_url: str = field(default_factory=lambda: _default_base_url())
    # 存放密钥的环境变量名。中转站用户可能沿用不同变量名，允许覆盖。
    api_key_env: str = field(default_factory=lambda: _env_str("PH_API_KEY_ENV", "DEEPSEEK_API_KEY"))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "db" / "personal-hub.db"

    @property
    def skills_dir(self) -> Path:
        """用户技能目录（AGENT.md §5.3 数据目录约定）。

        每个技能一个子目录，里面放 SKILL.md。目录不存在时视为"没有技能"，
        不会自动创建——避免在用户不知情时往数据目录里塞东西。
        """
        return self.data_dir / "skills"

    @property
    def plugins_dir(self) -> Path:
        """插件目录（M8 第 6–7 项）。

        每个插件一个子目录；卸载时目录被重命名为 `.<id>.uninstalled`
        而**不删除**，保留用户可能的配置。
        """
        return self.data_dir / "plugins"

    @property
    def mcp_dir(self) -> Path:
        """MCP 配置与子进程工作目录（M8 第 4 项）。

        配置文件为 `servers.json`；每个 Server 获得一个同名子目录作为
        工作目录，子进程的默认工作路径因此不会落在主目录或源码仓库里。
        """
        return self.data_dir / "mcp"

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            host=_env_str("PH_HOST", "127.0.0.1"),
            port=_env_int("PH_PORT", 8000),
            allowed_origins=_env_list("PH_ALLOWED_ORIGINS", DEFAULT_DEV_ORIGINS),
            data_dir=_env_path("PH_DATA_DIR", default_data_dir()),
            provider=_env_str("PH_PROVIDER", "deepseek"),
            model=_env_str("PH_MODEL", "deepseek-chat"),
            provider_base_url=_env_str("PH_PROVIDER_BASE_URL", _default_base_url()),
            api_key_env=_env_str("PH_API_KEY_ENV", "DEEPSEEK_API_KEY"),
        )


def get_settings() -> Settings:
    """每次调用重新读取环境变量。

    M1 不引入缓存，保证测试可以安全地修改环境变量。
    """
    return Settings.from_env()
