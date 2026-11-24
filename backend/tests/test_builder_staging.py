"""生成应用的静态检查与版本管理测试（M9 第 1 步）。

本步**不调用模型、不执行生成的代码**，因此测试完全确定。

重点：
1. 每个禁项都被拦住（网络、外链、iframe、cookie、storage、parent、eval…）；
2. 一次列出全部问题，而不是修一个报一个；
3. 安装失败不破坏已有版本；回滚、卸载语义正确；
4. 路径逃逸（非法 id、非法版本号）被拒绝。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.builder.staging import (
    MAX_VERSIONS_PER_APP,
    AppStore,
    BuilderError,
)
from app.builder.validate import (
    MAX_APP_BYTES,
    sanitize_app_id,
    validate_app,
)

VALID_APP = """<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>待办清单模板</title>
<style>body { font-family: system-ui; } .done { text-decoration: line-through; }</style>
</head>
<body>
  <h1>我的待办</h1>
  <ul id="list"></ul>
  <script>
    const items = ["读书", "跑步", "写周报"];
    const list = document.getElementById("list");
    for (const item of items) {
      const li = document.createElement("li");
      li.textContent = item;
      list.appendChild(li);
    }
  </script>
</body>
</html>
"""


class TestValidationAccepts:
    def test_valid_app_passes(self) -> None:
        result = validate_app(VALID_APP)

        assert result.ok, result.messages

    def test_empty_body_is_valid_html(self) -> None:
        assert validate_app("<html><body></body></html>").ok

    def test_data_uri_image_allowed(self) -> None:
        html = '<html><body><img src="data:image/png;base64,AAAA"></body></html>'

        assert validate_app(html).ok

    def test_anchor_links_allowed(self) -> None:
        assert validate_app('<html><body><a href="#top">top</a></body></html>').ok


class TestValidationRejects:
    def test_empty_content(self) -> None:
        assert not validate_app("   ").ok

    def test_oversized_app(self) -> None:
        html = "<html><body>" + "x" * (MAX_APP_BYTES + 10) + "</body></html>"

        result = validate_app(html)

        assert not result.ok
        assert any("超过上限" in m for m in result.messages)

    @pytest.mark.parametrize(
        "snippet,rule",
        [
            ("fetch('http://example.com')", "network_fetch"),
            ("new XMLHttpRequest()", "network_xhr"),
            ("new WebSocket('ws://x')", "network_websocket"),
            ("new EventSource('/sse')", "network_eventsource"),
            ("import('./x.js')", "dynamic_import"),
            ("parent.document.body", "parent_access"),
            ("top.location = 'x'", "parent_access"),
            ("document.cookie", "cookie_access"),
            ("localStorage.setItem('a','b')", "storage_access"),
            ("sessionStorage.getItem('a')", "storage_access"),
            ("indexedDB.open('x')", "storage_access"),
            ("eval('1+1')", "code_eval"),
            ("new Function('return 1')", "function_constructor"),
        ],
    )
    def test_forbidden_javascript(self, snippet: str, rule: str) -> None:
        html = f"<html><body><script>{snippet}</script></body></html>"

        result = validate_app(html)

        assert not result.ok
        assert any(issue.rule == rule for issue in result.issues), result.messages

    @pytest.mark.parametrize("tag", ["iframe", "object", "embed", "base", "form"])
    def test_forbidden_tags(self, tag: str) -> None:
        html = f"<html><body><{tag}></{tag}></body></html>"

        result = validate_app(html)

        assert not result.ok
        assert any(issue.rule == "forbidden_tag" for issue in result.issues)

    def test_external_script_blocked(self) -> None:
        html = '<html><body><script src="https://cdn.example.com/x.js"></script></body></html>'

        result = validate_app(html)

        assert not result.ok
        assert any(issue.rule == "external_script" for issue in result.issues)

    def test_external_stylesheet_blocked(self) -> None:
        html = '<html><head><link rel="stylesheet" href="https://x.com/a.css"></head></html>'

        result = validate_app(html)

        assert not result.ok
        assert any(issue.rule == "external_resource" for issue in result.issues)

    def test_external_image_blocked(self) -> None:
        html = '<html><body><img src="https://tracker.example.com/p.gif"></body></html>'

        result = validate_app(html)

        assert not result.ok
        assert any(issue.rule == "external_resource" for issue in result.issues)

    def test_reports_all_issues_at_once(self) -> None:
        """一次列出全部问题，用户一次就知道要改哪里。"""
        html = """<html><body>
        <iframe></iframe>
        <script src="https://x.com/a.js"></script>
        <script>fetch('/x'); localStorage.setItem('a','b');</script>
        </body></html>"""

        result = validate_app(html)

        rules = {issue.rule for issue in result.issues}
        assert {"forbidden_tag", "external_script", "network_fetch", "storage_access"} <= rules

    def test_comments_are_also_checked(self) -> None:
        """保守处理：注释里出现禁用调用也拒绝，避免拼接绕过。"""
        html = "<html><body><script>/* fetch('x') */</script></body></html>"

        assert not validate_app(html).ok

    def test_case_insensitive(self) -> None:
        html = "<html><body><script>FETCH('/x')</script></body></html>"

        assert not validate_app(html).ok


class TestAppIdSanitizing:
    @pytest.mark.parametrize("bad", ["", "A", "bad id", "../x", "a/b", "-lead", "x" * 70, "中文"])
    def test_invalid_ids_rejected(self, bad: str) -> None:
        with pytest.raises(ValueError):
            sanitize_app_id(bad)

    def test_valid_id(self) -> None:
        assert sanitize_app_id("todo-list") == "todo-list"


class TestStaging:
    def _store(self, tmp_path: Path) -> AppStore:
        return AppStore(root=tmp_path / "data")

    def test_write_and_read_staging(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)

        store.write_staging("abcdefgh1234", VALID_APP)

        assert store.read_staging("abcdefgh1234") == VALID_APP

    def test_invalid_staging_id_rejected(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)

        with pytest.raises(BuilderError, match="不合法"):
            store.write_staging("../escape", VALID_APP)

    def test_discard_staging(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        store.write_staging("abcdefgh1234", VALID_APP)

        store.discard_staging("abcdefgh1234")

        assert store.read_staging("abcdefgh1234") is None

    def test_cleanup_stale_keeps_active(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        store.write_staging("aaaaaaaa1111", VALID_APP)
        store.write_staging("bbbbbbbb2222", VALID_APP)

        removed = store.cleanup_stale_staging(keep_ids={"aaaaaaaa1111"})

        assert removed == 1
        assert store.read_staging("aaaaaaaa1111") is not None
        assert store.read_staging("bbbbbbbb2222") is None


class TestInstall:
    def _store(self, tmp_path: Path) -> AppStore:
        return AppStore(root=tmp_path / "data")

    def test_install_creates_version_and_pointer(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)

        app = store.install(app_id="todo-list", name="待办清单", version="1.0.0", html=VALID_APP)

        assert app.id == "todo-list"
        assert app.version == "1.0.0"
        assert store.read_app("todo-list") == VALID_APP

    def test_second_version_switches_pointer(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        store.install(app_id="todo-list", name="待办", version="1.0.0", html="<html>A</html>")

        app = store.install(app_id="todo-list", name="待办", version="1.1.0", html="<html>B</html>")

        assert app.version == "1.1.0"
        assert app.versions == ("1.0.0", "1.1.0")
        assert store.read_app("todo-list") == "<html>B</html>"

    def test_duplicate_version_rejected(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        store.install(app_id="todo-list", name="待办", version="1.0.0", html=VALID_APP)

        with pytest.raises(BuilderError, match="已存在"):
            store.install(app_id="todo-list", name="待办", version="1.0.0", html=VALID_APP)

    def test_invalid_version_rejected(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)

        with pytest.raises(BuilderError, match="版本号"):
            store.install(app_id="todo-list", name="待办", version="../1.0.0", html=VALID_APP)

    def test_invalid_app_id_rejected(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)

        with pytest.raises(ValueError):
            store.install(app_id="../escape", name="x", version="1.0.0", html=VALID_APP)

    def test_failed_write_does_not_break_existing_version(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """安装失败时旧版本必须继续可用（指针不动）。"""
        store = self._store(tmp_path)
        store.install(app_id="todo-list", name="待办", version="1.0.0", html="<html>OLD</html>")

        original = Path.write_text

        def explode(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            if self.name == "index.html" and "1.1.0" in str(self):
                raise OSError("模拟写入失败")
            return original(self, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", explode)

        with pytest.raises(BuilderError, match="写入应用文件失败"):
            store.install(app_id="todo-list", name="待办", version="1.1.0", html="<html>NEW</html>")

        monkeypatch.undo()
        # 旧版本仍在，指针未变。
        assert store.read_app("todo-list") == "<html>OLD</html>"
        assert store.get("todo-list").version == "1.0.0"  # type: ignore[union-attr]

    def test_versions_sorted_numerically(self, tmp_path: Path) -> None:
        """1.10.0 必须排在 1.9.0 之后（字符串比较会排反）。"""
        store = self._store(tmp_path)
        for version in ("1.9.0", "1.10.0"):
            store.install(app_id="a1", name="x", version=version, html="<html>x</html>")

        assert store.get("a1").versions == ("1.9.0", "1.10.0")  # type: ignore[union-attr]
        assert store.get("a1").version == "1.10.0"  # type: ignore[union-attr]

    def test_too_many_versions(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        for index in range(MAX_VERSIONS_PER_APP):
            store.install(app_id="a1", name="x", version=f"1.0.{index}", html="<html>x</html>")

        with pytest.raises(BuilderError, match="版本数已达上限"):
            store.install(app_id="a1", name="x", version="2.0.0", html="<html>x</html>")


class TestRollback:
    def _store(self, tmp_path: Path) -> AppStore:
        return AppStore(root=tmp_path / "data")

    def test_rollback_to_previous_version(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        store.install(app_id="a1", name="x", version="1.0.0", html="<html>V1</html>")
        store.install(app_id="a1", name="x", version="1.1.0", html="<html>V2</html>")

        app = store.rollback("a1")

        assert app.version == "1.0.0"
        assert store.read_app("a1") == "<html>V1</html>"

    def test_rollback_with_single_version_fails(self, tmp_path: Path) -> None:
        store = self._store(tmp_path)
        store.install(app_id="a1", name="x", version="1.0.0", html="<html>x</html>")

        with pytest.raises(BuilderError, match="没有更早的版本"):
            store.rollback("a1")

    def test_rollback_does_not_delete_newer(self, tmp_path: Path) -> None:
        """回滚只是切指针，新版本仍在磁盘上（可以再切回来）。"""
        store = self._store(tmp_path)
        store.install(app_id="a1", name="x", version="1.0.0", html="<html>V1</html>")
        store.install(app_id="a1", name="x", version="1.1.0", html="<html>V2</html>")

        store.rollback("a1")

        assert store.get("a1").versions == ("1.0.0", "1.1.0")  # type: ignore[union-attr]


class TestUninstall:
    def test_uninstall_keeps_directory(self, tmp_path: Path) -> None:
        root = tmp_path / "data"
        store = AppStore(root=root)
        store.install(app_id="a1", name="x", version="1.0.0", html="<html>x</html>")

        message = store.uninstall("a1")

        assert "保留" in message
        assert (root / "apps" / ".a1.uninstalled").is_dir()
        assert store.list_apps() == []

    def test_uninstall_missing_app(self, tmp_path: Path) -> None:
        store = AppStore(root=tmp_path / "data")

        with pytest.raises(BuilderError, match="不存在"):
            store.uninstall("nope")


class TestCorruptionTolerance:
    def test_broken_pointer_marks_app_unavailable(self, tmp_path: Path) -> None:
        root = tmp_path / "data"
        store = AppStore(root=root)
        store.install(app_id="a1", name="x", version="1.0.0", html="<html>x</html>")
        (root / "apps" / "a1" / "current").write_text("../../etc/passwd", encoding="utf-8")

        app = store.get("a1")

        assert app is not None
        assert app.version is None
        assert not app.ok
        assert store.read_app("a1") is None

    def test_broken_meta_falls_back_to_id(self, tmp_path: Path) -> None:
        root = tmp_path / "data"
        store = AppStore(root=root)
        store.install(app_id="a1", name="真实名称", version="1.0.0", html="<html>x</html>")
        (root / "apps" / "a1" / "meta.json").write_text("not json", encoding="utf-8")

        app = store.get("a1")

        assert app is not None
        assert app.name == "a1"
        assert app.ok  # 指针仍有效，应用可用

    def test_dotted_directories_ignored(self, tmp_path: Path) -> None:
        """卸载保留的目录（.x.uninstalled）不应被当作应用。"""
        root = tmp_path / "data"
        store = AppStore(root=root)
        store.install(app_id="a1", name="x", version="1.0.0", html="<html>x</html>")
        store.uninstall("a1")

        assert store.list_apps() == []

    def test_meta_round_trip(self, tmp_path: Path) -> None:
        store = AppStore(root=tmp_path / "data")
        store.install(
            app_id="a1",
            name="待办清单",
            version="1.0.0",
            html="<html>x</html>",
            description="一个简单的待办模板",
            source_request="我想要一个待办清单",
        )

        meta = json.loads(
            (tmp_path / "data" / "apps" / "a1" / "meta.json").read_text(encoding="utf-8")
        )
        assert meta["source_request"] == "我想要一个待办清单"
        assert store.get("a1").description == "一个简单的待办模板"  # type: ignore[union-attr]


class TestKnownBypasses:
    """记录**已知会被绕过**的写法，并用测试固定这一事实。

    这些用例的断言是"静态检查放行"——不是缺陷，而是如实记录：
    纯文本匹配无法识别字符串拼接与动态属性访问。
    真正的拦截发生在浏览器（iframe sandbox + CSP），
    已在 2026-11-24 用 Edge 无头浏览器实测验证（见 validate.py 模块文档）。

    把这件事写成测试的用意：将来若有人想"只靠静态检查保证安全"，
    这组测试会立刻提醒他这个假设不成立。
    """

    @pytest.mark.parametrize(
        "snippet",
        [
            "window['fe'+'tch']('http://evil.example')",
            "window['fetch']('http://evil.example')",
            "const n = String.fromCharCode(102,101,116,99,104); window[n]('x')",
        ],
    )
    def test_these_are_not_caught_statically(self, snippet: str) -> None:
        html = f"<html><body><script>{snippet}</script></body></html>"

        result = validate_app(html)

        # 如实记录：静态检查放行。安全由浏览器的 CSP 与 sandbox 保证。
        assert result.ok, (
            "如果这条开始失败，说明静态检查变强了——请更新 validate.py 模块文档里的绕过手法表格"
        )


class TestInlineHandlers:
    """内联事件处理器同样是代码（第 3 步发现的缺口）。"""

    @pytest.mark.parametrize(
        "snippet,rule",
        [
            ("<li onclick=\"fetch('/x')\">读书</li>", "network_fetch"),
            ("<img src='data:,' onerror=\"new XMLHttpRequest()\">", "network_xhr"),
            ("<body onload=\"localStorage.setItem('a','b')\">", "storage_access"),
            ("<a href='#' onclick=\"eval('1')\">x</a>", "code_eval"),
        ],
    )
    def test_handler_bodies_are_checked(self, snippet: str, rule: str) -> None:
        html = f"<html><body><ul>{snippet}</ul></body></html>"

        result = validate_app(html)

        assert not result.ok
        assert any(issue.rule == rule for issue in result.issues), result.messages

    def test_harmless_handler_allowed(self) -> None:
        html = (
            "<html><body><button onclick=\"this.textContent='已点击'\">按我</button></body></html>"
        )

        assert validate_app(html).ok
