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
