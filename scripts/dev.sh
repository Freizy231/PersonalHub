#!/usr/bin/env bash
# 启动本地开发环境：后端 127.0.0.1:8000 + 前端 127.0.0.1:5173。
#
# 安全约束（AGENT.md §4.2）：两者都只绑定回环地址，绝不使用 0.0.0.0。
# 用法：
#   ./scripts/dev.sh          # 前台启动，Ctrl+C 同时停止
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="127.0.0.1"
BACKEND_PORT="${PH_PORT:-8000}"
FRONTEND_PORT=5173
STATE_DIR="$(mktemp -d)"

if ! command -v setsid >/dev/null; then
  echo "错误：需要 util-linux 的 setsid 来可靠地停止子进程。" >&2
  exit 1
fi

# 每个子服务放进独立会话/进程组。停止时按组清理，避免 npm 派生的 vite、
# uvicorn reload 派生的工作进程变成孤儿继续占用端口。
#
# 注意：不能用 $(start_service ...) 取 PID —— 命令替换会等待管道 EOF，
# 而服务进程一直占着 stdout，脚本会永久阻塞。因此改用文件传递进程组号。
start_service() {
  local name="$1"
  local dir="$2"
  local pgfile="$STATE_DIR/$name.pgid"
  shift 2
  (
    cd "$dir"
    # 在内层 shell 里记录 $$：setsid 让该 shell 成为新会话首进程，
    # 因此 $$ 就是新的进程组号（pgid）。
    exec setsid bash -c 'echo $$ > "$1"; shift; exec "$@"' _ "$pgfile" "$@"
  ) &
  # 等待子进程把 pgid 写入文件，超时则报错而不是静默继续。
  for _ in $(seq 1 40); do
    [ -s "$pgfile" ] && return 0
    sleep 0.05
  done
  echo "错误：$name 进程组启动超时。" >&2
  return 1
}

cleanup() {
  local code=$?
  trap - EXIT INT TERM
  local pg
  for pgfile in "$STATE_DIR"/*.pgid; do
    [ -s "$pgfile" ] || continue
    pg="$(cat "$pgfile")"
    kill -TERM -- "-$pg" 2>/dev/null || true
  done
  sleep 0.5
  for pgfile in "$STATE_DIR"/*.pgid; do
    [ -s "$pgfile" ] || continue
    pg="$(cat "$pgfile")"
    kill -KILL -- "-$pg" 2>/dev/null || true
  done
  rm -rf "$STATE_DIR"
  wait 2>/dev/null || true
  exit "$code"
}
trap cleanup EXIT INT TERM

echo "→ 检查依赖…"
if [ ! -d "$ROOT/backend/.venv" ]; then
  echo "  后端虚拟环境不存在，执行 uv sync。"
  ( cd "$ROOT/backend" && uv sync --all-groups )
fi
if [ ! -d "$ROOT/frontend/node_modules" ]; then
  echo "  前端依赖不存在，执行 npm ci。"
  ( cd "$ROOT/frontend" && npm ci )
fi

# 数据库结构必须由 Alembic 迁移建立。缺少时直接在这里补齐，
# 避免用户启动后才遇到"数据库还没有初始化表结构"。
# 注意：uv/alembic 必须在 backend 目录下运行 —— 根目录没有 pyproject.toml，
# 否则会报 "Failed to spawn: alembic / No such file or directory"。
echo "→ 检查数据库结构…"
# 用 Alembic 版本与迁移脚本头部比较，而不是硬编码表名清单。
# 实测教训：早先只检查 conversations/memory_items，M6 新增 tasks/approvals 时
# 脚本误判"已就绪"，用户启动后才遇到 "no such table: tasks"。
SCHEMA_STATE="$( cd "$ROOT/backend" && .venv/bin/python -c '
import sqlite3
from pathlib import Path
from alembic.config import Config
from alembic.script import ScriptDirectory
from app.core.config import get_settings

# 迁移脚本里的最新版本
cfg = Config("alembic.ini")
latest = ScriptDirectory.from_config(cfg).get_current_head()

# 数据库里记录的版本
db = get_settings().db_path
if not Path(db).exists():
    print("missing"); raise SystemExit
try:
    con = sqlite3.connect(db)
    rows = con.execute("select version_num from alembic_version").fetchall()
    current = rows[0][0] if rows else None
except Exception:
    current = None

if current is None:
    print("missing")
elif current != latest:
    print(f"outdated {current} -> {latest}")
else:
    print("ready")
' 2>/dev/null || echo "missing" )"

case "$SCHEMA_STATE" in
  ready)
    echo "  表结构已是最新。"
    ;;
  missing)
    echo "  尚未初始化，执行 uv run alembic upgrade head（在 backend 目录下）。"
    ( cd "$ROOT/backend" && uv run alembic upgrade head )
    ;;
  outdated*)
    echo "  数据库版本落后（${SCHEMA_STATE#outdated }），正在升级。"
    ( cd "$ROOT/backend" && uv run alembic upgrade head )
    ;;
  *)
    echo "  无法判断数据库状态，执行 uv run alembic upgrade head。"
    ( cd "$ROOT/backend" && uv run alembic upgrade head )
    ;;
esac

echo "→ 启动后端 http://$HOST:$BACKEND_PORT"
start_service backend "$ROOT/backend" \
  .venv/bin/uvicorn app.main:create_app --factory --host "$HOST" --port "$BACKEND_PORT" --reload

# 等待后端就绪，避免前端首屏显示"未连接"造成误解。
for _ in $(seq 1 60); do
  curl -fsS "http://$HOST:$BACKEND_PORT/api/v1/health" >/dev/null 2>&1 && break
  sleep 0.25
done

echo "→ 启动前端 http://$HOST:$FRONTEND_PORT"
start_service frontend "$ROOT/frontend" npm run dev

echo
echo "打开 http://$HOST:$FRONTEND_PORT ，Ctrl+C 停止。"
wait
