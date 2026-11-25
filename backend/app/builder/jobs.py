"""生成作业（M9：生成改为后台作业）。

用户提交描述后只做两件快事：入队、返回作业 id。真正的生成在后台跑，
用户可以去别的页面，回来后看「活动」或「应用」页的结果。

## 为什么失败要分类

- **配置问题**（没设 Key）：重试一百次也一样，不可重试，直接告诉用户去改配置。
- **需求超范围 / 静态检查不通过**：同一描述重跑多半还是一样的结果，
  自动重试只是浪费模型调用；用户改描述后手动重试才是对的。
- **供应商暂时性错误**（限流、网络）：这是真的"过一会儿再试"，可重试。

错误文案一律来自本模块或适配器里**预先写死**的内容（§8.4）：
JobFailure 的 message 会写进数据库并在活动中展示，绝不含模型原文或异常正文。

## 进度怎么表达

作业事件（TaskEvent）只记阶段名：parsed / generating / validated。
**不记描述正文**，也不记生成的 HTML——活动中心是元数据视图。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.ai import ProviderConfigurationError
from app.api.chat import provider_config_hint
from app.builder.generator import GeneratorError, generate_app
from app.builder.requirement import RequirementError, parse_request
from app.builder.staging import AppStore, BuilderError
from app.core.audit import EgressRecord, record_egress
from app.core.config import Settings
from app.models.job import Job
from app.models.task_run import TaskRun
from app.workers.async_runner import JobFailure

logger = logging.getLogger(__name__)

GENERATE_APP_JOB = "generate_app"

# 阶段事件文本。界面据此显示"进行到哪一步"，而不是笼统的"正在生成"。
# 刻意是常量而不是自由文本：它们会落库并展示，任何拼接都可能带进用户内容。
PROGRESS_GENERATING = "正在生成页面"
PROGRESS_CHECKED = "已通过安全检查"

# 自动重试有上限：模型限流时连续重试 3 次足够，再多是在打扰供应商。
MAX_ATTEMPTS = 3


def build_generate_app_handler(*, settings: Settings, store: AppStore, build_provider):  # type: ignore[no-untyped-def]
    """构造作业处理器。

    用工厂而不是注册一个带状态的类：Worker 只认 `(session, job, run, report)`
    这个签名，状态（settings / store / 供应商工厂）在闭包里，签名保持干净。
    """

    handler = GenerateAppJob(settings=settings, store=store, build_provider=build_provider)
    return handler


class GenerateAppJob:
    """`generate_app` 作业的处理器。

    持有 provider 工厂与 AppStore：二者都是进程级的，随应用创建一次。
    """

    def __init__(self, *, settings: Settings, store: AppStore, build_provider) -> None:  # type: ignore[no-untyped-def]
        self._settings = settings
        self._store = store
        self._build_provider = build_provider

    async def __call__(
        self,
        session: Session,
        job: Job,
        run: TaskRun,
        report,  # type: ignore[no-untyped-def]
    ) -> dict[str, Any]:
        import json

        payload = json.loads(job.payload or "{}")
        request_text = str(payload.get("request", ""))
        staging_id = str(payload.get("staging_id", ""))
        if not staging_id:
            raise JobFailure("生成作业缺少临时标识。", code="JOB_PAYLOAD_INVALID")

        try:
            provider = self._build_provider(self._settings)
        except ProviderConfigurationError as exc:
            # 文案由 chat.provider_config_hint 预先写死，含配置项名但不含 Key。
            raise JobFailure(
                provider_config_hint(self._settings, str(exc)),
                code="PROVIDER_NOT_CONFIGURED",
            ) from exc

        try:
            requirement = await parse_request(provider, self._settings.model, request_text)
        except RequirementError as exc:
            raise JobFailure(str(exc), code=exc.code) from exc

        # 阶段事件只带**预先写死**的文案，不含需求原文、模型输出或文件路径：
        # 它们会写进数据库并在活动/应用页展示（§7.4「用户可理解的执行摘要」）。
        report("progress", PROGRESS_GENERATING)

        egress = EgressRecord(
            provider=provider.name,
            model=self._settings.model,
            conversation_id="",
            message_count=2,
            char_count=len(request_text),
        )

        try:
            generated = await generate_app(provider, self._settings.model, requirement)
        except GeneratorError as exc:
            record_egress(_with_outcome(egress, "failed", exc.code), request_id=None)
            # details 是静态检查的可读问题，不含模型原文。拼进去让用户一次看全。
            message = str(exc)
            if exc.details:
                logger.info(
                    "生成被拒绝",
                    extra={"job_id": job.id, "rules": list(exc.details)},
                )
                message = f"{exc} 原因：" + "；".join(exc.details)
            raise JobFailure(message, code=exc.code) from exc

        record_egress(_with_outcome(egress, "completed"), request_id=None)

        # 写 staging：与同步版本相同，是最后一步，失败不留下半成品。
        try:
            self._store.write_staging(staging_id, generated.html)
            self._store.write_staging_meta(
                staging_id,
                {
                    "staging_id": staging_id,
                    "job_id": job.id,
                    "app_id": generated.requirement.app_id,
                    "title": generated.requirement.title,
                    "purpose": generated.requirement.purpose,
                    "elements": list(generated.requirement.elements),
                    "capability": generated.requirement.capability.value,
                    "source_request": generated.requirement.raw_request,
                    "model": generated.model,
                    "generated_at": generated.created_at,
                },
            )
        except (BuilderError, OSError) as exc:
            # 磁盘问题属于环境错误，重试可能有用（例如空间被清理）。
            raise JobFailure(
                "生成的内容无法保存到磁盘，请检查磁盘空间后重试。",
                code="APP_WRITE_FAILED",
                retryable=True,
            ) from exc

        report("progress", PROGRESS_CHECKED)

        logger.info(
            "应用已生成到 staging",
            extra={
                "job_id": job.id,
                "staging_id": staging_id,
                "app_id": generated.requirement.app_id,
                "size_bytes": len(generated.html.encode("utf-8")),
            },
        )

        return {
            "message": f"已生成「{generated.requirement.title}」",
            "staging_id": staging_id,
            "app_id": generated.requirement.app_id,
            "title": generated.requirement.title,
        }


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
