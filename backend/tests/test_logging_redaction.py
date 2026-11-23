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
