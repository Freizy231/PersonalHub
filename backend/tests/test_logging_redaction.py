"""日志脱敏测试。

发现的真实风险：异常消息可能携带数据库 DSN 或凭证，直接写进日志等于把秘密落到磁盘。
redact() 提供兜底，测试必须保证常见秘密形态被抹掉，同时不误伤正常内容。
"""

from __future__ import annotations

import pytest

from app.core.logging import redact


class TestRedact:
    @pytest.mark.parametrize(
        ("raw", "must_not_appear"),
        [
            ("connect to postgresql://admin:s3cr3t@db.local/app failed", "s3cr3t"),
            ("mysql://root:hunter2@127.0.0.1:3306/x", "hunter2"),
            ("api_key=sk-live-abcdef123456", "sk-live-abcdef123456"),
            ("API-KEY: sk-live-abcdef123456", "sk-live-abcdef123456"),
            ("password=hunter2", "hunter2"),
            ("passwd: hunter2", "hunter2"),
            ("secret = value123", "value123"),
            ("refresh_token=abc.def.ghi", "abc.def.ghi"),
            ("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig", "eyJhbGciOiJIUzI1NiJ9"),
        ],
    )
    def test_known_secret_shapes_are_masked(self, raw: str, must_not_appear: str) -> None:
        assert must_not_appear not in redact(raw)

    @pytest.mark.parametrize(
        "text",
        [
            "GET /api/v1/health returned 200",
            "用户偏好项目驱动的学习方式",
            "未处理异常",
            "connect to postgresql://db.local/app failed",  # 无凭证的 DSN 不应被破坏
        ],
    )
    def test_normal_text_is_preserved(self, text: str) -> None:
        assert redact(text) == text

    def test_keeps_userinfo_but_masks_password(self) -> None:
        result = redact("redis://cache-user:pw123@redis.local:6379/0")

        assert "cache-user" in result
        assert "pw123" not in result
        assert result == "redis://cache-user:***@redis.local:6379/0"

    def test_multiple_secrets_in_one_line(self) -> None:
        raw = "dsn=mysql://u:pw1@h/db password=pw2 token=sometoken12345"

        result = redact(raw)

        for secret in ("pw1", "pw2", "sometoken12345"):
            assert secret not in result

    def test_is_idempotent(self) -> None:
        raw = "postgresql://user:pass@host/db password=pass"

        once = redact(raw)

        assert redact(once) == once


class TestNoReservedLogKeys:
    """`extra=` 不能使用 LogRecord 的内置属性名。

    实测踩过：`extra={"created": True}` 让 logging 在执行**日志语句时**
    抛 KeyError("Attempt to overwrite 'created' in LogRecord")——请求直接 500，
    而堆栈指向的是日志调用，很容易被误认为是别的问题。
    这类错误在代码审查里几乎看不出来，所以用测试扫一遍所有 logger 调用。
    """

    # 摘自 Python logging 的 LogRecord.__init__ 与 makeRecord 的保留字段。
    RESERVED = {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "taskName", "message", "asctime",
    }  # fmt: skip

    def test_no_logger_call_uses_reserved_extra_key(self) -> None:
        import ast
        from pathlib import Path

        offenders: list[str] = []
        root = Path(__file__).resolve().parents[1] / "app"
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                # logger.info(...) / logger.warning(...) 等
                if not (
                    isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id in {"logger", "self"}
                    and func.attr in {"debug", "info", "warning", "error", "exception", "critical"}
                ):
                    continue
                for keyword in node.keywords:
                    if keyword.arg != "extra" or not isinstance(keyword.value, ast.Dict):
                        continue
                    for key in keyword.value.keys:
                        if isinstance(key, ast.Constant) and key.value in self.RESERVED:
                            offenders.append(
                                f"{path.name}:{node.lineno} 使用了保留字段 {key.value!r}"
                            )

        assert offenders == [], "\n".join(offenders)
