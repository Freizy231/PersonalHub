# Personal Hub

Local-first、用户拥有和控制的个人 AI 环境。数据默认留在本机，服务只监听回环地址。

## 当前状态

- 主规范：[`AGENT.md`](AGENT.md)（权威项目上下文、架构、里程碑与验收标准）
- Agent 入口：[`AGENTS.md`](AGENTS.md)
- 环境审计：[`docs/ENVIRONMENT_AUDIT.md`](docs/ENVIRONMENT_AUDIT.md)
- 里程碑状态：[`docs/ROADMAP.md`](docs/ROADMAP.md)

**MVP（M0–M7）全部完成**：可对话、有记忆、能调用工具、写操作需审批、任务在后台可靠执行。已确认技术选型：后端 Python + FastAPI，
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

### 已完成（M2）

- `conversations` / `messages` 表，Alembic 迁移管理，UUID 主键，workspace 作用域
- 会话 CRUD API：`GET/POST /api/v1/conversations`、`GET/PATCH/DELETE /api/v1/conversations/{id}`
- 消息随会话级联删除，外键约束真实生效
- 前端会话管理面板：新建、重命名、两段确认删除，刷新后数据从数据库恢复
- 消息 API：`GET/POST /api/v1/conversations/{id}/messages`，客户端只能创建 `role="user"`
- 前端路由：总览 / 会话列表 / 会话详情；支持刷新、后退与不存在会话的友好提示
- 会话详情支持 Enter 发送、Shift+Enter 换行、多行缩进保留、中文输入法组合保护
- 发送后输入框立即清空；生成期间仍可继续输入下一句（D-012）
- AI 回复按 Markdown 渲染（标题、列表、表格、代码块）；用户消息保持纯文本
- 工具调用与审批卡片出现在当前对话位置，而不是固定在会话顶部
- 审批卡片显示中文标签（「标题：…」「说明：…」）与剩余有效时间，
  原始参数默认折叠，需要核对时再展开
- 点"停止生成"会真正停止后端生成（不再继续消耗额度），并作废悬挂的审批
- 数据库位于 `~/.local/share/personal-hub/db/`，不在源码仓库内

### 已完成（M3）

- DeepSeek 流式对话：`POST /api/v1/chat/stream`（SSE）
- 前端逐字渲染、停止生成、输入法组合保护
- 外发审计日志（只记元数据，不记正文）
- 取消生成时不保存部分回复

### 已完成（M4）

- AI 在对话后自动提取候选记忆；**未经你批准不会进入对话上下文**
- 记忆中心：待审核 / 已批准 / 已拒绝，支持批准、拒绝、修改、删除
- 每条记忆带来源、分类、置信度、敏感级别
- 拒绝或删除过的内容不会再被重复提案
- 敏感级别仅作标记，**不阻止**内容随对话发送（见 `docs/DECISIONS.md` D-008）

### 已完成（M5）

- Agent Loop：模型可在回答中发起工具调用，结果回填后继续作答（上限 4 轮）
- Tool Broker 是唯一执行入口：未注册工具、非法参数、写操作一律拒绝
- 内置 `search_memories`：**只检索已批准记忆，只限当前工作空间**
- 工具调用记录入库，刷新页面后仍能在消息下看到"调用了什么、结果如何"
- 工具返回内容标记为不可信数据，提示模型不得当作指令执行

### 已完成（M6）

- **Policy Engine**：默认拒绝；只读直接执行，写操作必须审批
- **审批绑定**：批准绑定「工具 + 参数指纹 + 主体 + 工作空间 + 有效期」，
  参数一变即作废，且一次性使用
- **审批卡片**：对话中途弹出，显示将要执行的具体内容
- **"本次会话内不再询问"**：需显式勾选，刷新页面即失效（不提供永久授权）
- `create_task`（写，需审批）/ `list_tasks`（读）
- 工具被拒绝时，AI 会如实告诉你没执行，不会假装成功

### 已完成（M7）

- **持久化作业队列**：SQLite 表即队列，幂等入队、原子领取、指数退避重试
- **后台 Worker**：跑在本地服务进程里，关闭浏览器不影响
- **重启恢复**：服务重启后未完成作业重新排队；执行实例标记为"被中断"而非谎称失败
- **任务中心**：按状态筛选，支持取消、重试，有进行中任务时自动刷新
- **活动中心**：全局事件时间线 + 单个任务详情（每次尝试、错误码、时间）

### 已完成（M8 第一阶段）

- **统一 Capability Manifest**：按规范 §7.2 描述每种能力（id、版本、类型、
  输入输出 Schema、运行时、声明权限、依赖、来源、签名）
- **能力注册表**：登记、按版本检索、列全部版本；版本按数字比较（1.10 > 1.9）
- **依赖检查**：报告缺失依赖与依赖环；有依赖者时拒绝卸载
- **能力页**（导航「能力」）：列出系统已有的能力，可搜索、按类型筛选、
  展开参数结构
- **声明不等于授权**：页面上明确标注"声明，非授权"，写操作仍需你确认
- WASM 评估已完成，结论是暂不引入（见 [`docs/EVALUATION_WASM.md`](docs/EVALUATION_WASM.md)）

#### 插件（进程）

进程插件在 **bubblewrap 沙箱**里运行你自己写的 Python 脚本，
用于声明式插件做不到的事（读写自有数据、复杂计算等）。

`plugin.json`：

```json
{
  "id": "com.example.echo",
  "name": "回声插件",
  "version": "1.0.0",
  "type": "process",
  "description": "在沙箱里回显内容",
  "entrypoint": "run.py",
  "limits": { "memory_mb": 256, "cpu_seconds": 5, "timeout_seconds": 15 },
  "tools": [
    {
      "name": "echo",
      "display_name": "沙箱回声",
      "description": "返回你提供的文本",
      "input_schema": {
        "type": "object",
        "properties": { "text": { "type": "string" } },
        "required": ["text"]
      },
      "argument_labels": [["text", "内容"]]
    }
  ]
}
```

`run.py`（从 stdin 读参数，向 stdout 写结果）：

```python
import json, sys

payload = json.loads(sys.stdin.read() or "{}")
args = payload.get("arguments", {})
print(json.dumps({"ok": True, "content": f"回声：{args.get('text', '')}"},
                 ensure_ascii=False))
```

沙箱里插件**做不了**什么（均经实测验证）：

| 限制 | 说明 |
|---|---|
| 读用户文件 | 主目录、数据库目录、`~/.ssh` 全部不可见 |
| 联网 | 无网络命名空间，出站连接被阻断 |
| 写自己的包 | 插件目录只读 |
| 占满 CPU | `cpu_seconds` 到点被杀死 |
| 吃光内存 | 超过 `memory_mb` 立即 `MemoryError` |
| 拿到密钥 | 环境变量只传 `PATH`/`LANG`/`TERM` |
| 写临时文件 | 可写 `/work`，进程退出即消失 |

- **沙箱不可用时拒绝运行**（不会退化成无沙箱执行）。
- 进程插件**一律需要确认**，预览里标为"需要确认（执行外部代码）"。
- 每次调用启动一个全新沙箱，调用结束即退出；超时或取消会杀掉进程，
  不会留下孤儿。
- 需要系统已安装 `bubblewrap` 与 `util-linux`（提供 `prlimit`）。
  缺失时「插件」页会显示原因，声明式插件不受影响。

#### 插件（声明式）

插件可以把你常用的工具组合固定下来。**本期插件不含任何可执行代码**，
只是"依次调用哪些已有工具"的声明。

安装步骤：

```bash
# 1. 把插件目录放进导入目录
mkdir -p ~/.local/share/personal-hub/plugin-import/notes-plugin
# 2. 写入 plugin.json（见下）
# 3. 打开「插件」页 → 填目录名 → 预览 → 确认安装 → 启用
```

`plugin.json` 示例：

```json
{
  "id": "com.example.notes",
  "name": "笔记助手",
  "version": "1.0.0",
  "type": "declarative",
  "description": "把文本整理成一条任务",
  "permissions": ["task:write"],
  "tools": [
    {
      "name": "make_todo",
      "display_name": "生成任务",
      "description": "把一段文本创建为任务",
      "input_schema": {
        "type": "object",
        "properties": {"title": {"type": "string"}},
        "required": ["title"]
      },
      "composes": ["builtin.create_task"],
      "argument_labels": [["title", "标题"]]
    }
  ]
}
```

规则与安全约定：

| 项 | 要求 |
|---|---|
| `id` | 反向域名形式，如 `com.example.notes` |
| `type` | 只能是 `declarative`（`process` 本期不支持，会明确报错） |
| `composes` | 只能引用**内建工具**（`builtin.list_tasks` 等），不能引用插件或 MCP |
| 安装来源 | 只从数据目录的 `plugin-import/<目录名>` 读取，**不接受任意路径** |
| 默认状态 | 安装后**不启用**，需手动开启 |

- **批准外层不等于批准内层**：调用组合工具时，内部每个写操作都会
  **单独再要一次确认**。实测会连续弹出两次审批。
- 组合工具的风险 = 所组合工具中的最高风险（含写操作就需要确认）。
- 卸载**保留文件**（重命名为 `.<id>.uninstalled`），可手动删除。
- 安装时拒绝包含符号链接的目录。

#### 外部 MCP 服务

可以通过 MCP 协议接入外部工具。**默认全部关闭**，需要你显式配置并重启服务。

配置文件：`~/.local/share/personal-hub/mcp/servers.json`

```json
{
  "servers": [
    {
      "name": "filesystem",
      "transport": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/dir"],
      "enabled": true,
      "timeout_seconds": 30
    }
  ]
}
```

| 项 | 说明 |
|---|---|
| `name` | 小写字母开头，会成为工具名前缀（`mcp__<name>__<tool>`） |
| `transport` | `stdio`（本地子进程）或 `http`（远程，需 `url`） |
| `command` | 白名单命令名，或**已存在的绝对路径** |
| `enabled` | **默认 false**，必须显式打开 |
| `token_env` | 仅 http：存放 Bearer Token 的**环境变量名**（不写 Token 本身） |

安全约定（都已在测试中锁定）：

- **MCP 工具一律需要你逐次确认**。外部服务自称的"只读"不会被采信。
- 子进程**不继承**本服务的环境变量——模型 API Key 不会传给外部 Server。
- 子进程工作目录固定在 `mcp/<name>/`，不在你的主目录或源码仓库里。
- 工具描述来自外部服务，会明确标注"不可信";结果超过 8,000 字符会截断。
- 配置里不能写 `risk` 字段：不允许把外部工具降级成免审批。
- Server 连不上只影响它自己，内建功能照常；原因可在「能力」页看到。

#### 技能（Skills）

把常用的工作方法写成一份 Markdown，AI 在任务匹配时会参考它。
技能**只是文字**，不执行任何代码，也不会自己获得任何权限。

```bash
mkdir -p ~/.local/share/personal-hub/skills/weekly-report
cat > ~/.local/share/personal-hub/skills/weekly-report/SKILL.md <<'SKILL'
---
name: 周报写作
description: 按公司模板写周报
version: 1.0.0
---

# 周报结构

1. 本周进展（3 条）
2. 遇到的问题
3. 下周计划
SKILL
```

格式规则（写错会在启动日志里提示具体原因，其他技能不受影响）：

| 项 | 要求 |
|---|---|
| 目录名 | 即技能 id：小写字母、数字、`_`、`-` |
| frontmatter | 必须用 `---` 包住，每行 `key: 值`，只支持 `name` / `description` / `version` |
| 必需字段 | `name`、`description`（`version` 缺省为 1.0.0） |
| 正文 | Markdown，上限 32,000 字符 |

- 系统提示里只放技能名与一句话描述；AI 需要时再加载全文，不挤占上下文。
- **修改技能后需重启服务**（启动时加载一次，保证对话中清单与内容一致）。
- 写坏的技能会被跳过并在日志里说明原因，在「能力」页看不到它。
- 能力页的「技能」类型下可以看到已加载的技能。

### 配置

模型需要 API Key，**只在后端设置**，绝不要写进前端：

官方 DeepSeek：

```bash
export DEEPSEEK_API_KEY="sk-..."
./scripts/dev.sh
```

中转站 / 自建 OpenAI 兼容网关（**必须显式配置地址**）：

```bash
export PH_PROVIDER="openai-compatible"
export PH_PROVIDER_BASE_URL="https://你的中转站/v1"   # 注意带 /v1
export DEEPSEEK_API_KEY="中转站给你的 Key"            # 变量名可用 PH_API_KEY_ENV 改
export PH_MODEL="deepseek-chat"                      # 按中转站支持的模型名填
./scripts/dev.sh
```

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `PH_PROVIDER` | `deepseek` | `deepseek` 或 `openai-compatible` |
| `PH_PROVIDER_BASE_URL` | `https://api.deepseek.com` | 请求地址为 `{base_url}/chat/completions` |
| `PH_MODEL` | `deepseek-chat` | 模型名 |
| `PH_API_KEY_ENV` | `DEEPSEEK_API_KEY` | 存放密钥的环境变量名 |

> **Key 与地址必须配套。** 中转站的 Key 发到官方地址会返回 401——两者不通用。
> 本机/局域网地址（如 `192.168.x.x`）会自动直连，不走代理。

未设置 Key 时，对话接口返回明确的配置错误，其余功能正常。

### 模型连不上时

```bash
cd backend && uv run python scripts/diagnose_model.py
```

它会检查密钥格式（空白、引号、`Bearer` 前缀、`sk-` 前缀）并发送一个最小测试请求。
**只显示密钥首尾各 4 位，不会打印完整密钥。**
与规范的偏离（未做外发检查层、未用 Keychain）见 [`docs/DECISIONS.md`](docs/DECISIONS.md)。

数据库操作与迁移流程见 [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md)。

## 里程碑路线

| 阶段 | 内容 | 状态 |
|---|---|---|
| M0 | 仓库与环境审计 | 已完成 |
| M1 | 前后端骨架 + 健康检查 | 已完成 |
| M2 | SQLite 持久化 | 已完成 |
| M3 | AI Workspace（接入模型） | 已完成 |
| M4 | 记忆引擎（候选/审核/检索） | 已完成 |
| M5 | Agent Loop 与只读工具 | 已完成 |
| M6 | 治理与审批 | 已完成 |
| M7 | 持久化任务运行时 | 已完成 |
| M8 | 能力运行时（Capability Runtime） | 功能已完成（WASM 评估：暂不引入） |

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
cd backend && uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --reload

# 前端 → http://127.0.0.1:5173（开发期把 /api 代理到后端）
cd frontend && npm run dev
```

## 验证与测试

```bash
# 后端测试（634 项：健康检查、错误结构、CORS、配置、日志脱敏、会话/消息 CRUD、
# workspace 隔离、role 伪造防护、流式对话、取消语义（含断开即停止生成）、
# 历史取尾部、外发审计、代理处理、记忆提取/审核/注入隔离/去重、Agent 循环、
# 工具 Broker、参数校验、审批人话摘要、审批绑定/过期/一次性消费/越权、
# Policy 决策、作业队列/重试/恢复/Worker、
# 能力 Manifest 校验/版本排序/依赖环/声明非授权/只读 API、
# 技能解析/校验/不执行代码/路径穿越防护/对话注入、
# MCP 配置校验/命令白名单/失败隔离/一律 WRITE/代理链路/不可信标注、
# 插件清单校验/组合风险传播/双层审批/路径逃逸/卸载保留目录、
# 沙箱隔离（真实 bwrap：主目录/网络/只读/CPU/内存/密钥））
cd backend && uv run pytest -q
cd backend && uv run ruff check . && uv run ruff format --check .

# 前端：严格类型检查 + 生产构建 + lint
cd frontend && npm run build && npm run lint

# 接口连通性
curl -fsS http://127.0.0.1:8000/api/v1/health
```

首次运行或升级后需要建立/更新表结构：

```bash
cd backend && uv run alembic upgrade head
```

> **必须在 `backend/` 目录下执行。** 仓库根目录没有 `pyproject.toml`，
> 在根目录运行 `uv run alembic` 会报
> `Failed to spawn: alembic / No such file or directory` —— 这个报错的意思是
> "当前目录不是 uv 项目"，不是"没安装 alembic"。
>
> 用 `./scripts/dev.sh` 启动时会自动检查并补齐迁移，不必手动执行。

接口文档（仅回环地址可达）：<http://127.0.0.1:8000/api/v1/docs>

## 数据与隐私

- 用户数据存放在操作系统标准应用数据目录，**不在源码仓库内**。
- `.env`、数据库文件、Vault、索引、日志与构建产物已由 `.gitignore` 排除，并逐条验证。
- 密钥不得进入前端、普通数据库表、Prompt 或日志。
- 涉及数据外发的功能必须经过 Data Egress Gateway 与用户授权。
