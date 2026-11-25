"""MCP 配置校验测试（M8 第 4 项，提案第 1 步）。

重点关注"用户手写配置出错"的场景：错误必须可定位，
且单个 Server 写错不能拖垮其他 Server。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.mcp.config import (
    MAX_SERVERS,
    McpConfigError,
    McpServerConfig,
    Transport,
    load_settings,
    parse_settings,
)


def _stdio(**overrides):  # type: ignore[no-untyped-def]
    data = {"name": "fs", "transport": "stdio", "command": "npx", "args": ["-y", "pkg"]}
    data.update(overrides)
    return data


class TestServerValidation:
    def test_valid_stdio_server(self) -> None:
        server = McpServerConfig(**_stdio())

        assert server.transport is Transport.STDIO
        assert server.enabled is False, "默认必须是关闭的"

    def test_valid_http_server(self) -> None:
        server = McpServerConfig(name="remote", transport="http", url="https://example.com/mcp")

        assert server.transport is Transport.HTTP

    @pytest.mark.parametrize("bad_name", ["", "Upper", "with space", "-lead", "x" * 33, "中文"])
    def test_invalid_server_name(self, bad_name: str) -> None:
        with pytest.raises(McpConfigError, match="Server 名"):
            McpServerConfig(name=bad_name, transport="stdio", command="npx")

    def test_stdio_requires_command(self) -> None:
        with pytest.raises(McpConfigError, match="必须提供 command"):
            McpServerConfig(name="fs", transport="stdio")

    def test_http_requires_url(self) -> None:
        with pytest.raises(McpConfigError, match="必须提供 url"):
            McpServerConfig(name="r", transport="http")

    def test_http_rejects_command(self) -> None:
        with pytest.raises(McpConfigError, match="不应提供 command"):
            McpServerConfig(name="r", transport="http", url="https://a.b/c", command="npx")

    def test_stdio_rejects_url(self) -> None:
        with pytest.raises(McpConfigError, match="不应提供 url"):
            McpServerConfig(name="fs", transport="stdio", command="npx", url="https://a.b")

    def test_invalid_transport(self) -> None:
        with pytest.raises(McpConfigError, match="transport"):
            McpServerConfig(name="fs", transport="carrier-pigeon")  # type: ignore[arg-type]

    def test_url_scheme_restricted(self) -> None:
        for bad in ("file:///etc/passwd", "ftp://x.y", "javascript:alert(1)"):
            with pytest.raises(McpConfigError, match="http 或 https"):
                McpServerConfig(name="r", transport="http", url=bad)

    def test_timeout_bounds(self) -> None:
        with pytest.raises(McpConfigError, match="timeout_seconds"):
            McpServerConfig(name="fs", transport="stdio", command="npx", timeout_seconds=0)
        with pytest.raises(McpConfigError, match="timeout_seconds"):
            McpServerConfig(name="fs", transport="stdio", command="npx", timeout_seconds=9999)

    def test_null_byte_in_args(self) -> None:
        with pytest.raises(McpConfigError, match="空字节"):
            McpServerConfig(name="fs", transport="stdio", command="npx", args=["a\x00b"])

    def test_too_many_args(self) -> None:
        with pytest.raises(McpConfigError, match="args 不能超过"):
            McpServerConfig(
                name="fs", transport="stdio", command="npx", args=[str(i) for i in range(40)]
            )


class TestCommandValidation:
    """command 是最危险的字段：它会被执行。"""

    def test_whitelisted_command_accepted(self) -> None:
        for command in ("npx", "uvx", "node", "python3", "python"):
            server = McpServerConfig(name="fs", transport="stdio", command=command)
            assert server.command == command

    @pytest.mark.parametrize(
        "bad", ["rm -rf /", "sh -c 'curl evil'", "bash", "npx; rm -rf /", "./script"]
    )
    def test_non_whitelisted_relative_command_rejected(self, bad: str) -> None:
        """配置里的特殊字符被 sh 解释时，一个分号就变成另一条命令。"""
        with pytest.raises(McpConfigError):
            McpServerConfig(name="fs", transport="stdio", command=bad)

    def test_absolute_system_binary_is_allowed(self) -> None:
        """绝对路径是用户明确写出的完整可执行文件，只要存在就接受。

        这条与"白名单"并不矛盾：白名单约束的是**相对名**（会去 PATH 里找），
        而绝对路径本身就是精确指定，且经过存在性校验。
        真正的隔离由第 6 项的独立低权限进程负责。
        """
        if not Path("/bin/sh").exists():  # pragma: no cover - 平台差异
            pytest.skip("/bin/sh 不存在")

        server = McpServerConfig(name="fs", transport="stdio", command="/bin/sh")

        assert server.command == "/bin/sh"

    def test_absolute_path_must_exist(self, tmp_path: Path) -> None:
        missing = tmp_path / "nope" / "server"

        with pytest.raises(McpConfigError, match="不存在"):
            McpServerConfig(name="fs", transport="stdio", command=str(missing))

    def test_absolute_path_to_directory_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(McpConfigError, match="是目录"):
            McpServerConfig(name="fs", transport="stdio", command=str(tmp_path))

    def test_absolute_path_to_file_accepted(self, tmp_path: Path) -> None:
        binary = tmp_path / "my-server"
        binary.write_text("#!/bin/sh\n", encoding="utf-8")

        server = McpServerConfig(name="fs", transport="stdio", command=str(binary))

        assert server.command == str(binary)

    def test_null_byte_in_command(self) -> None:
        with pytest.raises(McpConfigError, match="空字节"):
            McpServerConfig(name="fs", transport="stdio", command="npx\x00")


class TestParseSettings:
    def test_multiple_valid_servers(self) -> None:
        settings = parse_settings(
            {
                "servers": [
                    _stdio(name="fs"),
                    {"name": "remote", "transport": "http", "url": "https://a.b/mcp"},
                ]
            }
        )

        assert [s.name for s in settings.servers] == ["fs", "remote"]
        assert settings.errors == ()

    def test_empty_config(self) -> None:
        settings = parse_settings({})

        assert settings.servers == ()
        assert settings.errors == ()

    def test_unknown_top_level_field(self) -> None:
        with pytest.raises(McpConfigError, match="未知字段"):
            parse_settings({"servers": [], "extra": 1})

    def test_servers_must_be_array(self) -> None:
        with pytest.raises(McpConfigError, match="servers 必须是数组"):
            parse_settings({"servers": {}})

    def test_too_many_servers(self) -> None:
        with pytest.raises(McpConfigError, match="不能超过"):
            parse_settings({"servers": [_stdio(name=f"s{i}") for i in range(MAX_SERVERS + 1)]})

    def test_one_bad_server_does_not_block_others(self) -> None:
        """用户手写配置很容易写错一处；整份失效会让人不知道错在哪。"""
        settings = parse_settings(
            {"servers": [_stdio(name="good"), {"name": "bad", "transport": "stdio"}]}
        )

        assert [s.name for s in settings.servers] == ["good"]
        assert len(settings.errors) == 1
        assert "bad" in settings.errors[0]

    def test_duplicate_name_rejected(self) -> None:
        settings = parse_settings({"servers": [_stdio(), _stdio()]})

        assert len(settings.servers) == 1
        assert any("重复" in e for e in settings.errors)

    def test_unknown_server_field(self) -> None:
        settings = parse_settings({"servers": [_stdio(extra="x")]})

        assert settings.servers == ()
        assert any("未知字段" in e for e in settings.errors)

    def test_risk_field_is_explicitly_rejected(self) -> None:
        """MCP 工具一律 WRITE；不允许配置把它们降级成只读（提案问题 3）。"""
        settings = parse_settings({"servers": [_stdio(risk="READ")]})

        assert settings.servers == ()
        assert any("一律按 WRITE" in e for e in settings.errors)

    def test_enabled_must_be_boolean(self) -> None:
        settings = parse_settings({"servers": [_stdio(enabled="yes")]})

        assert settings.servers == ()
        assert any("enabled" in e for e in settings.errors)

    def test_enabled_servers_filter(self) -> None:
        settings = parse_settings(
            {"servers": [_stdio(name="off"), _stdio(name="on", enabled=True)]}
        )

        assert [s.name for s in settings.enabled_servers] == ["on"]

    def test_get_by_name(self) -> None:
        settings = parse_settings({"servers": [_stdio()]})

        assert settings.get("fs") is not None
        assert settings.get("nope") is None


class TestLoadSettings:
    def test_missing_file_means_no_servers(self, tmp_path: Path) -> None:
        """没有配置文件是正常状态，不是错误。"""
        settings = load_settings(tmp_path / "servers.json")

        assert settings.servers == ()
        assert settings.errors == ()

    def test_reads_valid_file(self, tmp_path: Path) -> None:
        path = tmp_path / "servers.json"
        path.write_text(json.dumps({"servers": [_stdio()]}), encoding="utf-8")

        settings = load_settings(path)

        assert [s.name for s in settings.servers] == ["fs"]

    def test_invalid_json_reports_line_and_column(self, tmp_path: Path) -> None:
        path = tmp_path / "servers.json"
        path.write_text('{"servers": [}', encoding="utf-8")

        settings = load_settings(path)

        assert settings.servers == ()
        assert any("第 1 行" in e for e in settings.errors)

    def test_non_utf8_file_reported(self, tmp_path: Path) -> None:
        path = tmp_path / "servers.json"
        path.write_bytes(b'{"servers": "\xff\xfe"}')

        settings = load_settings(path)

        assert settings.servers == ()
        assert settings.errors

    def test_broken_file_does_not_raise(self, tmp_path: Path) -> None:
        """外部能力的配置问题不应让本地 AI 环境起不来。"""
        path = tmp_path / "servers.json"
        path.write_text("not json at all", encoding="utf-8")

        settings = load_settings(path)

        assert settings.servers == ()
        assert len(settings.errors) == 1
