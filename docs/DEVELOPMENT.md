# 开发说明

## 数据库与迁移

数据库文件位置（`AGENT.md` §5.3）：

- Linux：`$XDG_DATA_HOME/personal-hub/db/personal-hub.db`，未设置 `XDG_DATA_HOME` 时为
  `~/.local/share/personal-hub/db/personal-hub.db`
- 可用 `PH_DATA_DIR` 覆盖整个数据根目录

**数据库绝不在源码仓库内**，`.gitignore` 已排除 `data/`、`*.db`、`*.sqlite*`。

### 表结构只由迁移建立

代码中**没有** `create_all()`。表结构唯一来源是 `backend/alembic/versions/`。

**所有 alembic / uv 命令都必须在 `backend/` 目录下执行。** 仓库根目录不是 uv 项目，
在根目录运行会得到 `Failed to spawn: alembic`（含义是"当前目录不是 uv 项目"，
而不是"alembic 未安装"）。

```bash
cd backend

# 首次或升级后：建立/更新表结构
uv run alembic upgrade head

# 在临时副本上演练某个迁移（不碰真实数据）
uv run alembic -x db_path=/tmp/rehearsal.db upgrade head

# 回滚一步
uv run alembic downgrade -1

# 查看当前版本
uv run alembic current
```

### 新增迁移的流程

1. 修改 `app/models/` 下的 ORM 模型。
2. 生成迁移：`uv run alembic revision --autogenerate -m "说明"`
   - **autogenerate 也会创建数据库文件**，如果只是想生成脚本，注意事后确认没有在真实目录留下空库。
3. **人工审阅生成的脚本**（`ruff` 已排除该目录，不会替你发现问题）：
   - 检查 `ondelete`、索引、约束是否齐全
   - 检查 `downgrade()` 是否可执行
4. 在临时副本上演练 upgrade 与 downgrade。
5. 涉及删除字段/表、数据转换时，必须先备份并提供恢复方案（`AGENT.md` §9.4）。

### 已知陷阱

- **不要**在 `alembic/env.py` 的 `engine.connect()` 之后执行任何 SQL（例如 `PRAGMA`）。
  SQLAlchemy 2.0 会因此隐式开启事务，Alembic 的 `begin_transaction()` 变成空操作，
  最终**版本号不会提交**，下次升级会重复建表而失败。PRAGMA 已统一由
  `app/storage/database.py` 的连接事件设置。
- `fileConfig()` 必须传 `disable_existing_loggers=False`，否则执行迁移会禁用应用的全部 logger。
- SQLite 不保存时区。应用内统一存 UTC，API 层用 `ConversationOut` 的序列化器补 `Z` 后缀。

## 运行

```bash
./scripts/dev.sh                      # 前后端一起启动
uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

必须带 `--factory`：`create_app()` 会打开数据库并创建数据目录，不能在模块导入时执行。

## 测试

```bash
cd backend && uv run pytest -q        # 全部后端测试
cd backend && uv run ruff check . && uv run ruff format --check .
cd frontend && npm run build && npm run lint
```

测试安全约定：

- 每个测试使用独立的临时数据库，并通过 `apply_migrations()` 走真实迁移脚本。
- `conftest.py` 的 autouse 夹具把 `PH_DATA_DIR` 指向临时目录，防止任何测试写入
  `~/.local/share/personal-hub`。
- 测试**绝不**触碰用户真实数据。
