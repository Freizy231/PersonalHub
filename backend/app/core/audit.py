"""外发审计日志（AGENT.md §4.12、§7.4）。

用户已决定不实现 Data Egress 检查层（决策见 docs/DECISIONS.md），
但仍要求记录外发行为。本模块只记录**元数据**：

    记录：供应商、模型、消息条数、字符数、耗时、结果
    不记录：对话正文、API Key、完整 Prompt

理由：把正文抄进日志等于把个人数据多存一份，且日志通常权限更宽、
保留更久。审计需要回答"什么时候把多少数据发给了谁"，不需要正文。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger("app.audit.egress")


@dataclass(frozen=True, slots=True)
class EgressRecord:
    provider: str
    model: str
    conversation_id: str
    message_count: int
    char_count: int
    # 由调用方在各阶段填充
    outcome: str = "started"
    error_code: str | None = None


def record_egress(record: EgressRecord, *, request_id: str | None = None) -> None:
    """写一条外发审计记录。

    outcome 取值：started / completed / failed / aborted。
    不记录正文；char_count 足以判断外发数据量。
    """
    logger.info(
        "模型外发",
        extra={
            "event": "model.egress",
            "provider": record.provider,
            "model": record.model,
            "conversation_id": record.conversation_id,
            "message_count": record.message_count,
            "char_count": record.char_count,
            "outcome": record.outcome,
            **({"error_code": record.error_code} if record.error_code else {}),
            **({"request_id": request_id} if request_id else {}),
        },
    )
