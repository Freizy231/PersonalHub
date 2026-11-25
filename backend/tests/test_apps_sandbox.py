"""沙箱边界的真实浏览器验证（M9 第 6 步）。

单元测试能证明"我们下发了正确的响应头"，但证明不了
"应用在浏览器里真的读不到父页面"——那必须让浏览器跑一遍。

本测试：
1. 用真实后端（TestClient 不行，必须是监听端口的真实服务）提供一个应用；
2. 启动 `scripts/verify-app-sandbox.mjs` 以无头浏览器打开它；
3. 断言五项越界尝试全部被拦。

环境不满足时**跳过而不是失败**：没有 Node 或没有浏览器时，
这组测试无法给出有意义的结论。跳过是诚实的；失败会误导成"边界破了"。
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn

from app.ai import ChatMessage, StreamChunk
from app.builder import jobs as builder_jobs
from app.core.config import Settings
from app.main import create_app

NODE = shutil.which("node")
BROWSER_CANDIDATES = (
    os.environ.get("PH_BROWSER"),
    "/usr/bin/microsoft-edge",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
)
BROWSER = next((path for path in BROWSER_CANDIDATES if path and Path(path).exists()), None)

requires_browser = pytest.mark.skipif(
    NODE is None or BROWSER is None,
    reason="需要 Node ≥ 22 与 Edge/Chrome/Chromium 才能验证浏览器沙箱",
)

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
SCRIPT = SCRIPTS / "verify-app-sandbox.mjs"
PROBE_APP = SCRIPTS / "sandbox_probe_app.html"

# 探针放在 scripts/ 而不是 app/：它是**故意越界**的内容，
# 不该出现在可发布的包体里，也不该被误当成产品代码。
STUB_APP_HTML = PROBE_APP.read_text(encoding="utf-8")


class _StubProvider:
    name = "stub"

    async def stream_chat(self, messages: list[ChatMessage], model: str, tools=None):  # type: ignore[no-untyped-def]
        yield StreamChunk(delta=STUB_APP_HTML)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture()
def live_server(settings: Settings, db_path: Path, monkeypatch) -> Iterator[str]:
    """在随机回环端口上跑真实服务。"""
    monkeypatch.setattr(builder_jobs, "build_provider", lambda s: _StubProvider(), raising=False)
    app = create_app(settings)
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="on")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 20
    while time.time() < deadline and not server.started:
        time.sleep(0.05)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5)
        pytest.skip("本地服务未能启动")

    # 直接经 AppStore 安装，跳过模型调用——本测试关注的是浏览器层。
    app.state.app_store.install(
        app_id="app-sandbox", name="沙箱探针", version="1.0.0", html=STUB_APP_HTML
    )

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


@requires_browser
class TestBrowserSandbox:
    def test_boundaries_hold(self, live_server: str) -> None:
        """唯一正确的结果是五项全部被拦。任何一项"成功"都是发布阻断项。"""
        result = subprocess.run(
            [
                NODE,
                str(SCRIPT),
                "--origin",
                live_server,
                "--embed",
                "--page",
                "/api/v1/health",
                "--app-id",
                "app-sandbox",
                "--port",
                str(_free_port()),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )

        output = result.stdout + result.stderr
        assert result.returncode == 0, output
        assert "全部通过" in output
        for label in ("不透明来源", "父页面不可读", "localStorage", "cookie", "网络请求"):
            assert f"✅ {label}" in output, output
