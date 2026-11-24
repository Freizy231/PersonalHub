"""应用（Personal App）接口——M9 第 4 步，提案 §5.1 / §6。

    GET    /api/v1/apps                    列出已安装应用
    POST   /api/v1/apps/generate           描述 → 生成到 staging（不安装）
    GET    /api/v1/apps/staging/{id}       读取一次生成的预览内容
    POST   /api/v1/apps/staging/{id}/install  把 staging 内容安装为新版本
    DELETE /api/v1/apps/staging/{id}       丢弃一次生成
    GET    /api/v1/apps/{id}/content       已安装应用的 HTML（带安全响应头）
    POST   /api/v1/apps/{id}/rollback      回滚到上一版本
    DELETE /api/v1/apps/{id}               卸载（保留目录）

**安全边界（这是本模块最重要的部分）**

1. 返回应用内容时，响应头必须齐全：CSP `default-src 'none'`、
   `X-Content-Type-Options: nosniff`、`Cache-Control: no-store`。
   头在**这一个**返回 HTML 的地方统一加，不散落在各处。
2. 前端必须把内容放进**不透明来源**的 iframe（`sandbox="allow-scripts"`，
   不加 `allow-same-origin`）。后端无法强制这一点，因此两种用法都安全：
   即便被直接打开，CSP 也禁止一切网络，且应用不含同源凭据。
3. **不信任前端传来的内容**：安装只读 staging 里后端自己写下的东西。
   前端能传的只有 staging id。
4. 生成需要调用模型（外发），因此与对话一样记审计日志。

**为什么不提供"下载 HTML 文件"**：那会让应用脱离沙箱环境运行
（用户双击打开 = 任意本地 HTML），与"应用只能在沙箱里跑"的约束矛盾。
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from app.ai import ProviderConfigurationError
from app.api.chat import build_provider, provider_config_hint
from app.builder.generator import GeneratorError, generate_app
from app.builder.requirement import RequirementError, parse_request
from app.builder.staging import AppStore, BuilderError
from app.core.audit import EgressRecord, record_egress
from app.core.errors import PublicMessageError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/apps", tags=["apps"])

MAX_REQUEST_CHARS = 1_000

# 应用内容的响应头。**所有**返回 HTML 的地方都必须用它。
#
# CSP 逐条解释：
# - default-src 'none'      : 一切未显式允许的资源都不加载（含 fetch/图片/字体）
# - script-src 'unsafe-inline': 允许内联 <script>（应用是单文件自包含的）
# - style-src 'unsafe-inline' : 允许内联样式
# - img-src data:           : 只允许 data: URI 图片（实测远程图片被阻断）
# - connect-src 'none'      : 明确禁止 fetch/XHR/WebSocket（default-src 已覆盖，
#                             单独写出是为了让意图在代码里一眼可见）
APP_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; "
        "script-src 'unsafe-inline'; "
        "style-src 'unsafe-inline'; "
        "img-src data:; "
        "connect-src 'none'; "
        "base-uri 'none'; "
        "form-action 'none'; "
        # 只允许**本应用**的页面被同源的界面嵌入。开发时经 Vite 代理，
        # 生产时前端由后端同源提供，两者都满足；同时阻止本机其它网页
        # 把用户的应用嵌进去（例如钓鱼页里套一个"计算器"）。
        "frame-ancestors 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Cross-Origin-Resource-Policy": "same-origin",
    # 应用更新后不能读到旧版本：缓存会让用户以为改动没生效。
    "Cache-Control": "no-store",
    # 即使被直接打开（不经过 iframe），也不给站点任何跨源读取能力。
    "Cross-Origin-Opener-Policy": "same-origin",
}


class GenerateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: str = Field(min_length=1, max_length=MAX_REQUEST_CHARS)


class StagedAppOut(BaseModel):
    staging_id: str
    app_id: str
    title: str
    purpose: str
    elements: list[str]
    capability: str
    size_bytes: int
    generated_at: str


class PreviewOut(BaseModel):
    staging_id: str
    html: str
    title: str
    size_bytes: int


class AppOut(BaseModel):
    id: str
    name: str
    version: str | None
    versions: list[str]
    installed_at: str
    description: str
    ok: bool


class AppListOut(BaseModel):
    """与其他列表接口保持一致的结构（items + total）。"""

    items: list[AppOut]
    total: int


class InstallOut(BaseModel):
    ok: bool = True
    app: AppOut
    # 同标题再次生成会得到同一个 id，因此这里明确告诉用户"这是新版本"。
    message: str = ""


class OkOut(BaseModel):
    ok: bool = True
    message: str = ""


def _store(request: Request) -> AppStore:
    store = getattr(request.app.state, "app_store", None)
    if store is None:
        raise PublicMessageError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="APP_STORE_UNAVAILABLE",
            message="应用仓库尚未初始化。",
        )
    return store


def _to_out(app) -> AppOut:  # type: ignore[no-untyped-def]
    return AppOut(
        id=app.id,
        name=app.name,
        version=app.version,
        versions=list(app.versions),
        installed_at=app.installed_at,
        description=app.description,
        ok=app.ok,
    )


def _new_staging_id() -> str:
    """staging id 是随机值，不与 app id 相同。

    随机而不是由 app id 派生：两次生成同一应用时目录不会互相覆盖，
    用户可以在旧版本仍可用的前提下比较新结果。
    """
    return uuid.uuid4().hex


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@router.get("", response_model=AppListOut, summary="列出应用")
async def list_apps(request: Request) -> AppListOut:
    items = [_to_out(app) for app in _store(request).list_apps()]
    return AppListOut(items=items, total=len(items))


@router.post("/generate", response_model=StagedAppOut, summary="生成应用（不安装）")
async def generate(request: Request, payload: GenerateIn) -> StagedAppOut:
    """需求解析 → 生成 → 静态检查 → 写入 staging。

    任何一步失败都**不留下文件**：写 staging 是最后一步，
    且在内容通过检查之后。
    """
    settings = request.app.state.settings
    store = _store(request)

    try:
        provider = build_provider(settings)
    except ProviderConfigurationError as exc:
        raise PublicMessageError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="PROVIDER_NOT_CONFIGURED",
            message=provider_config_hint(settings, str(exc)),
        ) from exc

    started = _now_iso()
    try:
        requirement = await parse_request(provider, settings.model, payload.request)
    except RequirementError as exc:
        raise PublicMessageError(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code=exc.code,
            message=str(exc),
        ) from exc

    # 外发审计：生成会把用户描述发给模型，与对话同等对待。
    # 只记录元数据，不记录描述正文（同 D-004 的原则）。
    request_id = getattr(request.state, "request_id", None)
    egress = EgressRecord(
        provider=provider.name,
        model=settings.model,
        conversation_id="",
        message_count=2,
        char_count=len(payload.request),
    )

    try:
        generated = await generate_app(provider, settings.model, requirement)
    except GeneratorError as exc:
        record_egress(_with_outcome(egress, "failed", exc.code), request_id=request_id)
        # details 是静态检查的可读问题，不含模型原文，可以安全外露。
        message = str(exc)
        if exc.details:
            logger.info(
                "生成被拒绝",
                extra={"rules": list(exc.details), "app_id": requirement.app_id},
            )
            # 统一错误体只有 message 字段，因此把逐条问题拼进去——
            # 用户需要一次看到全部原因，而不是修一个报一个。
            message = f"{exc}\n" + "\n".join(f"· {item}" for item in exc.details)
        raise PublicMessageError(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code=exc.code,
            message=message,
        ) from exc

    record_egress(_with_outcome(egress, "completed"), request_id=request_id)

    staging_id = _new_staging_id()
    store.write_staging(staging_id, generated.html)
    store.write_staging_meta(
        staging_id,
        {
            "staging_id": staging_id,
            "app_id": generated.requirement.app_id,
            "title": generated.requirement.title,
            "purpose": generated.requirement.purpose,
            "elements": list(generated.requirement.elements),
            "capability": generated.requirement.capability.value,
            "source_request": generated.requirement.raw_request,
            "model": generated.model,
            "generated_at": generated.created_at,
            "started_at": started,
        },
    )

    logger.info(
        "应用已生成到 staging",
        extra={
            "staging_id": staging_id,
            "app_id": generated.requirement.app_id,
            "size_bytes": len(generated.html.encode("utf-8")),
        },
    )

    return StagedAppOut(
        staging_id=staging_id,
        app_id=generated.requirement.app_id,
        title=generated.requirement.title,
        purpose=generated.requirement.purpose,
        elements=list(generated.requirement.elements),
        capability=generated.requirement.capability.value,
        size_bytes=len(generated.html.encode("utf-8")),
        generated_at=generated.created_at,
    )


@router.get("/staging/{staging_id}", response_model=PreviewOut, summary="读取生成结果")
async def read_staging(request: Request, staging_id: str) -> PreviewOut:
    store = _store(request)
    meta = store.read_staging_meta(staging_id)
    html = store.read_staging(staging_id)
    if meta is None or html is None:
        raise PublicMessageError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="APP_STAGING_NOT_FOUND",
            message="这次生成已经结束或已被清理，请重新生成。",
        )
    return PreviewOut(
        staging_id=staging_id,
        html=html,
        title=str(meta.get("title", "")),
        size_bytes=len(html.encode("utf-8")),
    )


@router.get(
    "/staging/{staging_id}/content",
    response_class=HTMLResponse,
    summary="预染内容（沙箱 iframe 的 src）",
)
async def read_staging_content(request: Request, staging_id: str) -> HTMLResponse:
    """预览用：返回原始 HTML，响应头与已安装应用**完全相同**。

    预览与安装后运行使用同一套加载机制（提案 §3 推荐方案 A 的约束），
    因此不存在"预览时宽松、安装后收紧"的差异。
    """
    store = _store(request)
    html = store.read_staging(staging_id)
    if html is None:
        raise PublicMessageError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="APP_STAGING_NOT_FOUND",
            message="这次生成已经结束或已被清理，请重新生成。",
        )
    return HTMLResponse(
        content=html, media_type="text/html; charset=utf-8", headers=APP_SECURITY_HEADERS
    )


@router.post("/staging/{staging_id}/install", response_model=InstallOut, summary="安装")
async def install(request: Request, staging_id: str) -> InstallOut:
    """把 staging 内容安装为新版本。

    安装内容**只取自 staging**（后端自己写的），前端无法注入 HTML。
    同标题重复生成得到同一 app_id，因此第二次安装是"新版本"而不是"新应用"。
    """
    store = _store(request)
    meta = store.read_staging_meta(staging_id)
    html = store.read_staging(staging_id)
    if meta is None or html is None:
        raise PublicMessageError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="APP_STAGING_NOT_FOUND",
            message="这次生成已经结束或已被清理，请重新生成。",
        )

    app_id = str(meta.get("app_id", ""))
    title = str(meta.get("title", ""))

    # 版本号：已有应用则打补丁号 +1，否则 1.0.0。
    existing = store.get(app_id) if app_id else None
    version = _next_version(existing)

    try:
        installed = store.install(
            app_id=app_id,
            name=title,
            version=version,
            html=html,
            description=str(meta.get("purpose", "")),
            source_request=str(meta.get("source_request", "")),
        )
    except BuilderError as exc:
        raise PublicMessageError(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code=exc.code,
            message=str(exc),
        ) from exc
    except ValueError as exc:
        # sanitize_app_id 抛 ValueError（id 不合法）。文案由本模块写死。
        raise PublicMessageError(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="APP_INVALID_ID",
            message="应用标识不合法，无法安装。",
        ) from exc

    # 安装成功后丢弃 staging：内容已经在正式目录里，留着只会占空间。
    store.discard_staging(staging_id)

    logger.info("应用已安装", extra={"app_id": installed.id, "version": installed.version})
    return InstallOut(
        app=_to_out(installed),
        message=f"已安装为版本 {version}。",
    )


@router.delete("/staging/{staging_id}", response_model=OkOut, summary="丢弃生成结果")
async def discard_staging(request: Request, staging_id: str) -> OkOut:
    _store(request).discard_staging(staging_id)
    return OkOut(message="已丢弃这次生成。")


@router.get(
    "/{app_id}/content",
    response_class=HTMLResponse,
    summary="已安装应用的 HTML（沙箱 iframe 的 src）",
)
async def app_content(request: Request, app_id: str) -> HTMLResponse:
    store = _store(request)
    try:
        html = store.read_app(app_id)
    except ValueError as exc:
        raise PublicMessageError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="APP_NOT_FOUND",
            message="应用不存在。",
        ) from exc
    if html is None:
        raise PublicMessageError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="APP_NOT_FOUND",
            message="应用不存在或当前版本不可用。",
        )
    return HTMLResponse(
        content=html, media_type="text/html; charset=utf-8", headers=APP_SECURITY_HEADERS
    )


@router.post("/{app_id}/rollback", response_model=InstallOut, summary="回滚版本")
async def rollback(request: Request, app_id: str) -> InstallOut:
    store = _store(request)
    try:
        app = store.rollback(app_id)
    except BuilderError as exc:
        raise PublicMessageError(
            status_code=status.HTTP_409_CONFLICT, code=exc.code, message=str(exc)
        ) from exc
    except ValueError as exc:
        raise PublicMessageError(
            status_code=status.HTTP_404_NOT_FOUND, code="APP_NOT_FOUND", message="应用不存在。"
        ) from exc
    return InstallOut(app=_to_out(app), message=f"已回滚到版本 {app.version}。")


@router.delete("/{app_id}", response_model=OkOut, summary="卸载（保留目录）")
async def uninstall(request: Request, app_id: str) -> OkOut:
    store = _store(request)
    try:
        message = store.uninstall(app_id)
    except BuilderError as exc:
        raise PublicMessageError(
            status_code=status.HTTP_404_NOT_FOUND, code=exc.code, message=str(exc)
        ) from exc
    except ValueError as exc:
        raise PublicMessageError(
            status_code=status.HTTP_404_NOT_FOUND, code="APP_NOT_FOUND", message="应用不存在。"
        ) from exc
    return OkOut(message=message)


def _with_outcome(
    record: EgressRecord, outcome: str, error_code: str | None = None
) -> EgressRecord:
    return EgressRecord(
        provider=record.provider,
        model=record.model,
        conversation_id=record.conversation_id,
        message_count=record.message_count,
        char_count=record.char_count,
        outcome=outcome,
        error_code=error_code,
    )


def _next_version(existing) -> str:  # type: ignore[no-untyped-def]
    """下一版本号。

    没装过 → 1.0.0；已装过 → 当前版本补丁号 +1。
    刻意不做"自动语义化升版"（猜不出用户意图），
    版本号的语义留给用户：需要大改时卸载重装即可。
    """
    if existing is None or existing.version is None:
        return "1.0.0"
    parts = existing.version.split("-")[0].split(".")
    try:
        major, minor, patch = (int(item) for item in parts[:3])
    except (TypeError, ValueError):
        return "1.0.0"
    return f"{major}.{minor}.{patch + 1}"
