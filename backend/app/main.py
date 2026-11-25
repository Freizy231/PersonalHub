"""FastAPI 应用装配。

工厂模式 create_app() 的好处：
- 测试可以创建独立实例，不共享全局状态。
- 测试可注入临时数据目录，绝不触碰真实用户数据。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect

from app.api import (
    approvals,
    apps,
    capabilities,
    chat,
    conversations,
    health,
    jobs,
    mcp,
    memory,
    messages,
    plugins,
    user_tasks,
)
from app.api import (
    settings as settings_api,
)
from app.capabilities import ToolRegistry
from app.capabilities.registry import (
    CapabilityRegistry,
    manifest_from_skill,
    manifest_from_tool,
)
from app.capabilities.skills import SkillLibrary, load_skills
from app.capabilities.tools import register_builtin_tools
from app.core.config import Settings, get_settings
from app.core.errors import install_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestIDMiddleware
from app.storage.database import build_engine, build_session_factory
from app.workers.worker import Worker, builtin_handlers

logger = logging.getLogger(__name__)

# 启动时必须存在的表。缺失说明数据库没跑过 Alembic 迁移。
REQUIRED_TABLES = frozenset(
    {
        "conversations",
        "messages",
        "memory_items",
        "memory_sources",
        "tool_invocations",
        "tasks",
        "approvals",
        "job_queue",
        "task_runs",
        "task_events",
    }
)


def check_schema(engine) -> list[str]:  # type: ignore[no-untyped-def]
    """返回缺失的表名。

    实测教训：新数据目录下启动服务，用户只会看到"服务器内部错误"的 500，
    完全不知道需要执行迁移。这里在启动时就检查并明确提示。
    """
    existing = set(inspect(engine).get_table_names())
    return sorted(REQUIRED_TABLES - existing)


async def _bootstrap_mcp(app: FastAPI) -> None:
    """读取 MCP 配置、发现工具并登记。

    失败路径全部收敛：配置错误、Server 连不上、名称冲突，
    都只记日志并把原因留在 app.state.mcp_status 里供界面展示，
    绝不阻止本地环境启动。
    """
    from app.mcp.bridge import discover_and_register, manifest_for_mcp_tool
    from app.mcp.client import McpClient
    from app.mcp.config import load_settings

    settings: Settings = app.state.settings
    config_path = settings.mcp_dir / "servers.json"
    mcp_settings = load_settings(config_path)

    for error in mcp_settings.errors:
        logger.warning("MCP 配置有问题", extra={"error": error})

    app.state.mcp_settings = mcp_settings
    app.state.mcp_status = {"errors": list(mcp_settings.errors), "servers": {}}

    servers = mcp_settings.enabled_servers
    if not servers:
        return

    client = McpClient(workdir_root=settings.mcp_dir)
    app.state.mcp_client = client

    result, discovered = await discover_and_register(
        client=client,
        servers=servers,
        registry=app.state.tool_registry,
    )

    for server, tools in discovered:
        app.state.mcp_status["servers"][server.name] = {
            "tool_count": len(tools),
            "tools": [tool.name for tool in tools],
            "status": "ready",
        }
        # 能力页展示用：与内建能力放在同一个注册表里。
        for tool in tools:
            app.state.capability_registry.register(manifest_for_mcp_tool(tool, server))

    # 连接失败 / 重名的 Server 在状态里标明原因，界面才能解释"为什么没有它的工具"。
    for name, reason in result.skipped:
        server_name = name.split("__")[1] if name.startswith("mcp__") else name
        if server_name in app.state.mcp_status["servers"]:
            continue
        app.state.mcp_status["servers"][server_name] = {
            "tool_count": 0,
            "tools": [],
            "status": "unavailable",
            "reason": reason,
        }

    if result.registered:
        logger.info(
            "MCP 工具已登记",
            extra={"servers": len(servers), "tools": len(result.registered)},
        )


def _bootstrap_plugins(app: FastAPI) -> None:
    """读取已安装插件，把**已启用**的插件工具登记进注册表。

    同步完成：声明式插件只有 JSON，读取很快，不涉及任何外部进程或网络。

    安全约束（§8.5、提案 §4）：

    - 默认不启用：安装时 enabled=False，用户必须显式开启；
    - 清单不合法 → 永不启用（registry 已保证）；
    - 组合工具的风险 = 所组合工具的最高风险，内部调用仍各自经 Broker 审批。
    """
    from app.plugins.declarative import (
        manifest_for_plugin_tool,
        register_plugin_tools,
        register_process_plugin,
    )
    from app.plugins.manifest import PluginType
    from app.plugins.registry import PluginRegistry

    settings: Settings = app.state.settings
    registry = PluginRegistry(root=settings.plugins_dir)
    registry.load()
    app.state.plugin_registry = registry

    registered: list[str] = []
    skipped: list[tuple[str, str]] = []

    for installed in registry.list():
        if not installed.ok or not installed.enabled:
            continue
        manifest = installed.manifest
        assert manifest is not None

        # 必须按 manifest.type 分派。实测缺陷（审计 P0-1）：
        # 原先对所有插件都调用 register_plugin_tools，进程插件重启后
        # 会被当成声明式注册——没有 composes，于是 risk=READ，
        # 一个"执行外部代码"的工具以只读身份出现在模型工具清单里，
        # 不触发审批。执行时虽然会因缺少 handler 失败，但那是偶然兜底，
        # 不是安全保证。
        #
        # 两条路径的 Broker 都由请求期 contextvar 提供
        # （plugins/declarative.py 的 set_current_broker），这里不绑定通道。
        if manifest.type is PluginType.PROCESS:
            result = register_process_plugin(
                manifest,
                plugin_dir=registry.root / installed.directory,
                registry=app.state.tool_registry,
            )
        else:
            result = register_plugin_tools(manifest, registry=app.state.tool_registry)
        registered.extend(result.registered)
        skipped.extend(result.skipped)

        for spec in manifest.tools:
            name = f"plugin__{spec.name}"
            if name in result.registered:
                app.state.capability_registry.register(manifest_for_plugin_tool(manifest, spec))

    app.state.plugin_status = {
        "registered": registered,
        "skipped": skipped,
    }
    if registered:
        logger.info("插件工具已登记", extra={"count": len(registered)})


def create_app(settings: Settings | None = None) -> FastAPI:
    configure_logging()
    settings = settings or get_settings()

    engine = build_engine(settings.db_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # 启动时不做任何建表：Schema 只由 Alembic 迁移建立（AGENT.md §9.4）。
        missing = check_schema(engine)
        if missing:
            logger.error(
                "数据库缺少数据表，请先执行迁移",
                extra={
                    "db_path": str(settings.db_path),
                    "missing_tables": missing,
                    "remedy": "cd backend && uv run alembic upgrade head",
                },
            )
        # MCP Servers（M8 第 4–5 项）：启动时发现一次工具并登记。
        # 为什么在启动时做而不是"首次调用时懒发现"：工具清单要在
        # 第一次对话前就进入模型的工具声明里；若首次调用才发现，
        # 模型第一轮根本不知道有这些工具。
        # 代价是启动会等外部 Server 握手，因此每个 Server 都有超时，
        # 且失败只记日志、不影响启动（外部依赖不能拖垮本地环境）。
        await _bootstrap_mcp(app)
        _bootstrap_plugins(app)

        # M9：清理上次异常退出留下的 staging 目录。
        # 只删超过 1 小时的：用户刚生成、还开着预览页的产物不能删。
        # 清理失败不影响启动——它只是回收空间。
        removed = app.state.app_store.cleanup_stale_staging(max_age_seconds=3600)
        if removed:
            logger.info("已清理遗留的生成临时目录", extra={"count": removed})

        # 后台 Worker：在应用生命周期内运行，关掉浏览器不影响它。
        # 数据库缺表时 Worker 会持续报错，因此只在表齐全时启动。
        #
        # 异步作业（M9 生成）：领取仍由 Worker 做，执行交给 AsyncJobRunner，
        # 这样一次几十秒的生成不会挡住其它作业。
        worker: Worker | None = None
        if not missing:
            from app.api.chat import build_provider
            from app.builder.jobs import GENERATE_APP_JOB, build_generate_app_handler
            from app.workers.async_runner import AsyncJobRunner

            runner = AsyncJobRunner(app.state.session_factory)
            app.state.async_runner = runner
            worker = Worker(
                app.state.session_factory,
                app.state.handler_registry,
                async_handlers={
                    GENERATE_APP_JOB: build_generate_app_handler(
                        settings=settings,
                        store=app.state.app_store,
                        build_provider=build_provider,
                    )
                },
                async_runner=runner,
            )
            app.state.worker = worker
            await worker.start()
        else:
            app.state.worker = None

        yield

        if worker is not None:
            await worker.stop()
        engine.dispose()

    app = FastAPI(
        title=settings.app_name,
        version="0.0.1",
        # 文档只挂在带版本前缀的路径下，且仅在回环地址可达的环境中提供。
        docs_url=f"{settings.api_prefix}/docs",
        openapi_url=f"{settings.api_prefix}/openapi.json",
        redoc_url=None,
        lifespan=lifespan,
    )

    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = build_session_factory(engine)

    # 工具注册表：显式登记 M5 的只读工具。不存在自动发现，
    # 避免某个模块被导入就悄悄获得能力（AGENT.md §4.9）。
    # 用户技能（M8 第 3 项）：启动时加载一次，运行期只读。
    # 刻意不做热重载：对话中途清单与正文不一致会让模型看到"清单里有、
    # 加载时没了"的错位。改技能后重启服务即可（README 有说明）。
    skill_results = load_skills(settings.skills_dir)
    skill_library = SkillLibrary(skill_results)
    app.state.skill_library = skill_library
    for failure in skill_library.failures:
        logger.warning(
            "技能加载失败，已跳过",
            extra={"directory": failure.directory, "reason": failure.error},
        )

    # 生成的应用（M9）：staging 与已安装版本都由它管理。
    # 数据目录延迟创建——构造它不碰文件系统，首次写入时才建目录。
    # 必须在工具注册之前创建：应用工具（list_apps 等）需要它。
    from app.builder.staging import AppStore

    app.state.app_store = AppStore(root=settings.builder_dir)

    tool_registry = ToolRegistry()
    register_builtin_tools(tool_registry, app.state.session_factory, skills=skill_library)
    # 应用工具（M9 / D-027）：让对话里的 Agent 能读取并提议修改已安装应用。
    # READ 工具无需审批；propose_app_change 是 WRITE，每次都弹审批卡片。
    from app.capabilities.app_tools import register_app_tools

    register_app_tools(tool_registry, app.state.app_store)
    app.state.tool_registry = tool_registry

    # 能力注册表（M8）：存放可检索、可展示的 Manifest 描述。
    # 与 tool_registry 分开：前者是**描述**（可导出、可给用户看），
    # 后者是**运行时对象**（含 handler，绝不出现在 API 响应里）。
    capability_registry = CapabilityRegistry()
    for definition in tool_registry.all():
        capability_registry.register(manifest_from_tool(definition))
    for skill in skill_library.skills:
        capability_registry.register(manifest_from_skill(skill))
    app.state.capability_registry = capability_registry

    # 已安装应用也投影进能力注册表：这样「生成」前能检索已有应用、
    # 优先复用（AGENT.md M9 交付项）。
    from app.capabilities.registry import manifest_from_app

    for installed_app in app.state.app_store.list_apps():
        if not installed_app.ok or installed_app.version is None:
            continue
        try:
            capability_registry.register(
                manifest_from_app(
                    app_id=f"app.{installed_app.id}",
                    name=installed_app.name,
                    version=installed_app.version,
                    description=installed_app.description,
                ),
                replace=True,
            )
        except Exception:  # noqa: BLE001 - 单个应用损坏不影响启动
            logger.warning("应用能力登记失败", extra={"app_id": installed_app.id})

    # 会话级授权（"本次会话内不再询问"）。刻意放在内存：
    # 刷新页面/重启服务即失效，避免一次勾选变成永久放行（D-010）。
    from app.policy.grants import SessionGrants

    app.state.session_grants = SessionGrants()

    # 作业处理器：显式登记，不做自动发现（同工具注册表的原则）。
    app.state.handler_registry = builtin_handlers()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_credentials=False,  # 无跨源 Cookie；开启前必须先有 CSRF 方案
        # 只放行实际使用的方法，不用通配符扩大攻击面。
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(RequestIDMiddleware)

    install_exception_handlers(app)

    app.include_router(health.router, prefix=settings.api_prefix)
    app.include_router(conversations.router, prefix=settings.api_prefix)
    app.include_router(messages.router, prefix=settings.api_prefix)
    app.include_router(memory.router, prefix=settings.api_prefix)
    app.include_router(approvals.router, prefix=settings.api_prefix)
    app.include_router(jobs.router, prefix=settings.api_prefix)
    app.include_router(user_tasks.router, prefix=settings.api_prefix)
    app.include_router(capabilities.router, prefix=settings.api_prefix)
    app.include_router(mcp.router, prefix=settings.api_prefix)
    app.include_router(plugins.router, prefix=settings.api_prefix)
    app.include_router(settings_api.router, prefix=settings.api_prefix)
    app.include_router(apps.router, prefix=settings.api_prefix)
    app.include_router(chat.router, prefix=settings.api_prefix)

    return app


# 刻意不在模块级实例化应用：create_app() 会打开数据库并创建数据目录，
# 若在导入时执行，任何 `import app.main`（包括测试收集）都会触碰真实用户目录。
# 启动时使用：uvicorn app.main:create_app --factory
