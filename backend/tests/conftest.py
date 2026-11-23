"""测试夹具：每个测试用独立临时数据库。

原则（AGENT.md §9.2、§8.6）：
- 测试绝不触碰用户真实数据目录（~/.local/share/personal-hub）。
- 每个测试独立数据库文件，避免测试间相互污染。
- 建表走真实 Alembic 迁移，而不是 metadata.create_all()，
  这样测试同时验证迁移脚本本身。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from alembic import command
from app.core.config import Settings
from app.main import create_app

API = "/api/v1"
BACKEND_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _isolate_data_dir(monkeypatch, tmp_path_factory):  # type: ignore[no-untyped-def]
    """兜底护栏：把默认数据目录指向临时路径。

    防止任何测试（含未来新增的）在忘记注入 Settings(data_dir=...) 时
    写入 ~/.local/share/personal-hub。显式传入 data_dir 的测试不受影响。
    """
    safe_dir = tmp_path_factory.mktemp("ph-data") / "personal-hub"
    monkeypatch.setenv("PH_DATA_DIR", str(safe_dir))
    # 校验护栏确实生效：解析应用配置时必须得到临时路径。
    assert Settings().data_dir == safe_dir
    return safe_dir


def apply_migrations(db_path: Path) -> None:
    """在指定数据库上执行全部迁移（与生产同一套脚本）。"""
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.cmd_opts = type("_Opts", (), {"x": [f"db_path={db_path}"]})()
    command.upgrade(cfg, "head")


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "personal-hub-test"


@pytest.fixture()
def settings(data_dir: Path) -> Settings:
    return Settings(data_dir=data_dir)


@pytest.fixture()
def db_path(settings: Settings) -> Path:
    path = settings.db_path
    apply_migrations(path)
    return path


@pytest.fixture()
def client(settings: Settings, db_path: Path) -> Iterator[TestClient]:
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def session(settings: Settings, db_path: Path) -> Iterator[Session]:
    from app.storage.database import build_engine, build_session_factory

    engine = build_engine(settings.db_path)
    factory = build_session_factory(engine)
    with factory() as s:
        yield s
    engine.dispose()
