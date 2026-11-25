"""用假 Provider 启动一个真实的 HTTP 服务，供浏览器走查应用创建流程。

**零外发**：Provider 在进程内返回固定脚本，不访问任何网络。
这是浏览器走查需要 "真实的提交 → 轮询 → 预览" 链路时唯一安全的做法——
用真实供应商会真的把用户描述发出去。

用法（另起前端指向它）：
    uv run python scripts/verify_app_flow.py --port 8030 --data-dir /tmp/ph-appflow
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from alembic.config import Config  # noqa: E402

from alembic import command  # noqa: E402
from app.ai import ChatMessage, StreamChunk  # noqa: E402
from app.core.config import Settings  # noqa: E402

# 生成两轮模型调用的固定输出：需求 JSON + 单文件 HTML。
REQUIREMENT_JSON = json.dumps(
    {
        "title": "喝水提醒清单",
        "purpose": "记录今天喝了几杯水",
        "elements": ["目标杯数", "打卡按钮", "当前进度"],
        "capability": "local_compute",
    },
    ensure_ascii=False,
)

APP_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>喝水提醒清单</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 24px; color: #222; }
    button { padding: 8px 16px; font-size: 1rem; }
    .count { font-size: 2rem; font-weight: 600; }
  </style>
</head>
<body>
  <h1>喝水提醒清单</h1>
  <p>目标：8 杯</p>
  <p class="count" id="count">0</p>
  <button id="drink" type="button">喝了一杯</button>
  <script>
    let cups = 0;
    document.getElementById('drink').addEventListener('click', () => {
      cups += 1;
      document.getElementById('count').textContent = String(cups);
    });
  </script>
</body>
</html>
"""

# 修改建议用的第二版（多一行计数说明）。
APP_HTML_V2 = APP_HTML.replace(
    '<p class="count" id="count">0</p>',
    '<p>今天已完成的比例会显示在下面。</p>\n  <p class="count" id="count">0</p>',
)


class ScriptedProvider:
    """按顺序返回预设文本的 Provider。不访问网络。

    脚本**循环使用**：走查要连续生成好几次（首次安装、升级、修改建议），
    一次性队列很容易在中途耗尽，表现为一个和被测代码无关的
    "没能理解你的需求描述"。循环让每次生成都拿到完整的一对输出。
    """

    name = "scripted"

    def __init__(self) -> None:
        self._scripts = [REQUIREMENT_JSON, APP_HTML]

    def use_variant(self, html: str) -> None:
        """切换生成用的 HTML（走查"修改建议"时需要另一份内容）。"""
        self._scripts = [REQUIREMENT_JSON, html]

    async def stream_chat(
        self,
        messages: list[ChatMessage],
        model: str,
        tools: object = None,
    ):  # type: ignore[no-untyped-def]
        # `tools` 是 Agent 循环会传的第三个位置参数（真实适配器同样接受）。
        # 脚本只产出纯文本，不发起工具调用，因此忽略它。
        del tools
        text = self._scripts.pop(0)
        self._scripts.append(text)  # 循环：这一轮用掉后放回队尾
        # 与真实适配器一致：逐块产出，前端才会看到 streaming 状态。
        for index in range(0, len(text), 200):
            yield StreamChunk(delta=text[index : index + 200])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8030)
    parser.add_argument("--data-dir", default="/tmp/ph-appflow")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    settings = Settings(data_dir=data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)

    cfg = Config(str(Path(__file__).resolve().parents[1] / "backend" / "alembic.ini"))
    cfg.cmd_opts = type("_Opts", (), {"x": [f"db_path={settings.db_path}"]})()
    command.upgrade(cfg, "head")

    provider = ScriptedProvider()

    from app.builder import jobs as builder_jobs
    from app.main import create_app

    builder_jobs.build_provider = lambda _settings: provider  # type: ignore[assignment]
    import app.api.chat as chat_api

    chat_api.build_provider = lambda _settings: provider  # type: ignore[assignment]

    app = create_app(settings)
    app.state.fake_provider = provider

    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
