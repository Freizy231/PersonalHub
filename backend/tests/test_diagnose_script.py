"""配置诊断工具测试。

重点是：工具绝不能把密钥正文打印出来。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "diagnose_model.py"


def _load():
    spec = importlib.util.spec_from_file_location("diagnose_model", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestMask:
    def test_hides_middle(self) -> None:
        m = _load()
        assert m.mask("sk-abcdefghijklmnop") == "sk-a...mnop (长度 19)"
        assert "abcdefghijkl" not in m.mask("sk-abcdefghijklmnop")

    def test_short_value_fully_masked(self) -> None:
        assert _load().mask("short") == "*****"


class TestKeyFormat:
    @pytest.mark.parametrize(
        ("value", "expect"),
        [
            ("sk-validkey1234", "ok"),
            (" sk-validkey1234", "whitespace"),
            ("sk-validkey1234 ", "whitespace"),
            ('"sk-validkey1234"', "quote"),
            ("Bearer sk-validkey1234", "bearer"),
            ("sk-valid key", "space"),
            ("invalidkey", "prefix"),
            ("sk-valid\nkey", "whitespace"),
        ],
    )
    def test_detects_problems(self, value: str, expect: str) -> None:
        m = _load()
        problems = " ".join(m.check_key_format(value)).lower()
        if expect == "ok":
            assert m.check_key_format(value) == []
        else:
            assert problems, f"{value!r} 应被判定为有问题"
            keyword = {
                "whitespace": "空白",
                "quote": "引号",
                "bearer": "bearer",
                "space": "空白",
                "prefix": "sk-",
            }[expect]
            assert keyword in problems


class TestNoLeak:
    def test_report_never_contains_full_key(self, monkeypatch, capsys) -> None:
        m = _load()
        secret = "sk-SUPERSECRETVALUE-1234567890"
        monkeypatch.setenv("DEEPSEEK_API_KEY", secret)
        # 阻止真实网络请求
        monkeypatch.setattr(
            m.httpx, "Client", lambda **kw: (_ for _ in ()).throw(m.httpx.ConnectError("blocked"))
        )

        m.main()
        out = capsys.readouterr().out

        assert secret not in out, "诊断输出不得包含完整密钥"
        assert "SUPERSECRET" not in out
