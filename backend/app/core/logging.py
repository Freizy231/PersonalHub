"""结构化日志（AGENT.md §7.4）。

要求：日志为 JSON 行，包含时间、级别、logger、消息和 request_id（若可用）。
禁止记录 API Key、密码、Token、完整敏感文档。

注意：异常消息本身可能携带敏感内容（例如数据库 DSN 回显密码）。因此本模块
提供 redact() 对异常正文做脱敏，未处理异常的堆栈在写出前必须经过它。
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime

# 常见秘密模式的兜底脱敏。这不是万能的，真正的防线仍是"不要把秘密放进异常消息"，
# 但可以防止 DSN、Authorization 头一类最常见的泄漏。
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # scheme://user:password@host  ->  保留用户名，抹掉密码
    (re.compile(r"(://[^:/@\s]+:)[^@\s/]+(@)"), r"\1***\2"),
    # key=value / key: value 形式的秘密字段。
    # token 允许带 -/_ 前缀（refresh_token、access-token），也允许裸 token。
    (
        re.compile(
            r"(?i)\b((?:api[_-]?key|(?:access[_-]?|refresh[_-]?)?token|secret|password|passwd|pwd))"
            r"(\s*[=:]\s*)([^\s,;\"'}\]]+)"
        ),
        r"\1\2***",
    ),
    # Bearer / Basic 凭证
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 ***"),
)


def redact(text: str) -> str:
    """对可能包含秘密的文本做尽力脱敏。"""
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class JsonFormatter(logging.Formatter):
    """把 LogRecord 序列化为单行 JSON。"""

    # 不进入额外字段的标准属性，避免输出噪声。
    _RESERVED = frozenset(
        {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
            "taskName",
            "message",
            "asctime",
        }
    )

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
        }

        request_id = getattr(record, "request_id", None)
        if request_id:
            payload["request_id"] = request_id

        for key, value in record.__dict__.items():
            if key not in self._RESERVED and not key.startswith("_"):
                payload[key] = redact(value) if isinstance(value, str) else value

        if record.exc_info:
            # 堆栈只写入服务端日志，不返回给前端；写出前统一脱敏。
            payload["exception"] = redact(self.formatException(record.exc_info))

        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    """配置根 logger 输出 JSON 到 stderr。

    可重复调用：已存在我们的 handler 时只更新级别，避免测试中重复添加。
    """
    root = logging.getLogger()
    handler = next(
        (h for h in root.handlers if getattr(h, "_personal_hub", False)),
        None,
    )
    if handler is None:
        handler = logging.StreamHandler(sys.stderr)
        handler._personal_hub = True  # type: ignore[attr-defined]
        handler.setFormatter(JsonFormatter())
        root.addHandler(handler)

    root.setLevel(level)
