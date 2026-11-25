"""系统设置（只读）——AGENT.md §2.5 第 6 项 Settings 页面。

    GET /api/v1/settings

**只读是刻意的**。配置项（模型、供应商、数据目录、密钥变量名）全部来自
环境变量与启动参数；运行期通过 HTTP 改写它们，意味着一个本机页面就能把
模型换成另一个地址、把数据目录指向别处——那会绕过"配置修改需重启"这条
既有约束（README 已写明），也会让"当前配置"与"进程实际状态"不一致。

用户在「设置」页看到的是**这份进程真正在用的配置**，改配置的方式写在页面上：
改环境变量后重启。将来若要做界面内修改，需要单独设计（写配置文件 + 校验 +
明确的重启提示），不在本期。

安全（§8.3、§8.6）：

- **绝不返回 API Key 本身**，只返回"是否已配置"与变量名。
- 数据目录只返回**路径**，不返回目录列表（目录里有什么不该由这个接口枚举）。
"""

from __future__ import annotations

import os
import shutil

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import func, select

from app import __version__
from app.ai import ProviderConfigurationError, read_api_key
from app.api.deps import SessionDep
from app.builder.staging import MAX_APPS, MAX_VERSIONS_PER_APP
from app.core.config import Settings
from app.models.conversation import Conversation
from app.models.memory import MemoryItem, MemoryStatus
from app.models.message import Message
from app.models.task import Task
from app.services.context import DEFAULT_BUDGET_CHARS

router = APIRouter(prefix="/settings", tags=["system"])


class ProviderOut(BaseModel):
    name: str
    model: str
    base_url: str
    # 密钥变量名（不是密钥）。用户需要知道该设哪个变量才能修好配置。
    api_key_env: str
    api_key_configured: bool
    # 配置是否完整可用。False 时 hint 给出可操作的说明。
    ready: bool
    hint: str = ""


class DataOut(BaseModel):
    data_dir: str
    db_path: str
    db_size_bytes: int
    free_disk_bytes: int


class MemoryPolicyOut(BaseModel):
    # 这些是**当前实现的行为**，不是可调参数。写出来是为了让用户知道
    # "什么会被记住、什么会被发给模型"——§2.5 要求 Settings 展示记忆策略。
    requires_approval: bool
    only_approved_injected: bool
    secret_never_injected: bool
    context_budget_chars: int
    note: str


class CountsOut(BaseModel):
    conversations: int
    messages: int
    memories_approved: int
    memories_proposed: int
    tasks: int
    apps: int


class RuntimeOut(BaseModel):
    version: str
    host: str
    port: int
    # 数量上限对用户有用：达到上限时他该知道为什么。
    max_apps: int
    max_versions_per_app: int


class SettingsOut(BaseModel):
    provider: ProviderOut
    data: DataOut
    memory_policy: MemoryPolicyOut
    counts: CountsOut
    runtime: RuntimeOut


def _provider_out(settings: Settings) -> ProviderOut:
    configured = bool(os.environ.get(settings.api_key_env, "").strip())
    ready = True
    hint = ""
    try:
        # read_api_key 是密钥的唯一读取点；这里只判断"读不读得到"，
        # **绝不**把返回值放进响应。
        read_api_key(settings.api_key_env)
    except ProviderConfigurationError as exc:
        ready = False
        # 文案来自 app/ai，是预先写死的固定文案，不含 Key。
        hint = str(exc)
    return ProviderOut(
        name=settings.provider,
        model=settings.model,
        base_url=settings.provider_base_url,
        api_key_env=settings.api_key_env,
        api_key_configured=configured,
        ready=ready,
        hint=hint,
    )


@router.get("", response_model=SettingsOut, summary="当前配置与数据概览")
async def get_settings_view(request: Request, session: SessionDep) -> SettingsOut:
    settings: Settings = request.app.state.settings
    store = getattr(request.app.state, "app_store", None)

    def count(model) -> int:  # type: ignore[no-untyped-def]
        return int(session.scalar(select(func.count()).select_from(model)) or 0)

    def count_memories(status: MemoryStatus) -> int:
        return int(
            session.scalar(
                select(func.count())
                .select_from(MemoryItem)
                .where(MemoryItem.status == status.value)
            )
            or 0
        )

    # ASGI 的 scope["server"] 是进程真实的监听地址。
    bound = request.scope.get("server") or (settings.host, settings.port)

    db_path = settings.db_path
    size = db_path.stat().st_size if db_path.exists() else 0
    try:
        free = shutil.disk_usage(settings.data_dir).free
    except OSError:
        # 数据目录还不存在（首次启动且未写入）：不把它变成错误。
        free = 0

    return SettingsOut(
        provider=_provider_out(settings),
        data=DataOut(
            data_dir=str(settings.data_dir),
            db_path=str(db_path),
            db_size_bytes=size,
            free_disk_bytes=free,
        ),
        memory_policy=MemoryPolicyOut(
            requires_approval=True,
            only_approved_injected=True,
            secret_never_injected=True,
            context_budget_chars=DEFAULT_BUDGET_CHARS,
            note=(
                "AI 提取的记忆一律是候选，必须经你批准才会被使用。"
                "对话时只注入与你当前问题相关的已批准记忆；标记为「秘密」的内容永不注入。"
            ),
        ),
        counts=CountsOut(
            conversations=count(Conversation),
            messages=count(Message),
            memories_approved=count_memories(MemoryStatus.APPROVED),
            memories_proposed=count_memories(MemoryStatus.PROPOSED),
            tasks=count(Task),
            apps=len(store.list_apps()) if store is not None else 0,
        ),
        runtime=RuntimeOut(
            version=__version__,
            # 用**实际监听地址**而不是配置默认值。
            # 实测：`uvicorn --port 8022` 启动时 Settings.port 仍是 8000，
            # 设置页会显示一个进程根本不在听的端口——而这一页的价值
            # 恰恰是"如实告诉你现在跑的是什么"。
            host=bound[0],
            port=bound[1],
            max_apps=MAX_APPS,
            max_versions_per_app=MAX_VERSIONS_PER_APP,
        ),
    )
