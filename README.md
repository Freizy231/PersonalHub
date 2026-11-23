# Personal Hub

Local-first、用户拥有和控制的个人 AI 环境。数据默认留在本机，服务只监听回环地址。

## 当前状态

项目处于 **M1 — Frontend/Backend Skeleton**（后端骨架已完成）。已确认技术选型：后端 Python + FastAPI，
前端 **React + TypeScript + Vite**。

> 历史版本 v0.14.2 与本项目无关联，不作为参考实现，其源码与数据不导入本项目。

### 已完成（M1 后端）

- `GET /api/v1/health` 健康检查，返回服务名、版本与服务器时间
- 统一错误结构：`{"error": {"code", "message", "request_id"}}`，不泄漏堆栈、路径或秘密
- `X-Request-ID` 请求追踪（含 500 路径）
- JSON 结构化日志，异常正文自动脱敏（DSN 密码、`token=`、Bearer 凭证等）
- CORS 白名单（默认仅 Vite 开发源），不使用通配符
- 配置默认绑定 `127.0.0.1`，可通过 `PH_HOST` / `PH_PORT` / `PH_ALLOWED_ORIGINS` 覆盖
- 前端首页展示后端连接状态，支持重新检查、超时与断线提示，明暗自适应

- 主规范：[`AGENT.md`](AGENT.md)（权威项目上下文、架构、里程碑与验收标准）
- Agent 入口：[`AGENTS.md`](AGENTS.md)
- 环境审计：[`docs/ENVIRONMENT_AUDIT.md`](docs/ENVIRONMENT_AUDIT.md)
- 里程碑状态：[`docs/ROADMAP.md`](docs/ROADMAP.md)

## 里程碑路线

| 阶段 | 内容 | 状态 |
|---|---|---|
| M0 | 仓库与环境审计 | 已完成 |
| M1 | 前后端骨架 + 健康检查 | 进行中 |
| M2 | SQLite 持久化 | 未开始 |
| M3 | AI Workspace（接入模型） | 未开始 |
| M4 | 记忆引擎基础 | 未开始 |
| M5 | Agent Loop 与只读工具 | 未开始 |
| M6 | 权限与审批 | 未开始 |
| M7 | 持久化任务运行时 | 未开始 |

## 开发环境要求

本机已通过审计验证的版本，**不可直接使用 PATH 上的 `python3`（实际为 3.7，已 EOL）**：

| 工具 | 要求 | 本机实测 |
|---|---|---|
| Python | 3.12（通过 `uv` 管理） | `/usr/bin/python3.12` → 3.12.3 |
| uv | 0.12+ | 0.12.11 |
| Node.js | 22.12+ | 24.22.2 |
| npm | 10+ | 11.19.1 |
| Git | 2.40+ | 2.43.0 |

## 安装与启动

首次准备（已由仓库锁定版本，可重复执行）：

```bash
cd backend && uv sync --all-groups && cd ..   # 后端：创建 .venv（Python 3.12，版本来自 uv.lock）
cd frontend && npm ci && cd ..                # 前端：按 package-lock.json 安装
```

一条命令同时启动前后端（都只绑定 `127.0.0.1`）：

```bash
./scripts/dev.sh
# 浏览器打开 http://127.0.0.1:5173
```

也可以分别启动：

```bash
# 后端 → http://127.0.0.1:8000
cd backend && uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# 前端 → http://127.0.0.1:5173（开发期把 /api 代理到后端）
cd frontend && npm run dev
```

## 验证与测试

```bash
# 后端测试（26 项：健康检查、错误结构、CORS 白名单、配置校验、日志脱敏）
cd backend && uv run pytest -q
cd backend && uv run ruff check . && uv run ruff format --check .

# 前端：严格类型检查 + 生产构建 + lint
cd frontend && npm run build && npm run lint

# 接口连通性
curl -fsS http://127.0.0.1:8000/api/v1/health
```

接口文档（仅回环地址可达）：<http://127.0.0.1:8000/api/v1/docs>

## 数据与隐私

- 用户数据存放在操作系统标准应用数据目录，**不在源码仓库内**。
- `.env`、数据库文件、Vault、索引、日志与构建产物已由 `.gitignore` 排除，并逐条验证。
- 密钥不得进入前端、普通数据库表、Prompt 或日志。
- 涉及数据外发的功能必须经过 Data Egress Gateway 与用户授权。
