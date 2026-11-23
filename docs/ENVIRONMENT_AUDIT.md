# 环境审计报告（M0）

- 报告日期：2026-11-23
- 依据：`AGENT.md` 第 15 节（M0 首次执行指令）
- 审计范围：只读取。未创建、未移动、未删除、未上传任何用户数据；未安装系统级依赖；未创建真实密钥。

---

## 1. 环境事实

| 项目 | 实测结果 |
|---|---|
| 操作系统 | Ubuntu 24.04.5 LTS（Noble） |
| 内核 | Linux 7.0.0-38-generic |
| 架构 | x86_64 |
| 主机名 | Remnux |
| 磁盘可用 | `/` 共 937G，已用 243G，可用 648G（28%） |
| Git | 2.43.0 |
| Node.js | v24.22.2 |
| npm | 11.19.1 |
| uv | 0.12.11 |
| SQLite CLI | 3.24.0（2018 年版本，偏旧） |
| Python 内置 SQLite 库 | 3.45.1，FTS5 可用，JSON1 可用 |

### 1.1 Python 解释器（关键）

`PATH` 上存在多套 Python，优先级与版本差异很大：

| 路径 | 版本 | 说明 |
|---|---|---|
| `/home/freizy/.pyenv/shims/python3` | 3.7.0 | **`python3` 命令实际指向它**，已 EOL |
| `/home/freizy/Anaconda/jypter/bin/python3` | 3.7.0 | Anaconda 环境，`pip` 也指向这里 |
| `/usr/bin/python3` / `/usr/bin/python3.12` | 3.12.3 | 系统 Python，符合规范基线 |
| pyenv 已安装版本 | 3.11.9 | shim 未激活该版本 |

**结论：不能直接运行 `python3 -m venv`**，否则会得到 Python 3.7（FastAPI 新版、SQLAlchemy 2.x、Pydantic 2.x 均不支持）。必须显式指定 `uv venv --python 3.12` 或 `/usr/bin/python3.12`。

### 1.2 网络

- 环境变量中配置了本地代理（`127.0.0.1:7897`，HTTP/HTTPS/SOCKS）。
- `git@github.com:...`（SSH 22 端口）**连接超时**。
- `https://github.com/Freizy231/PersonalHub.git` **可达**，远端仓库当前无任何分支（空仓库）。

---

## 2. 仓库状态

| 项目 | 状态 |
|---|---|
| 目录 | `/home/freizy/PersonalHub` |
| 是否为 Git 仓库 | 是（`git init` 已完成，分支 `main`） |
| 提交历史 | 审计前为空；本次已创建基线提交 `817aceb` |
| 远端 | `origin` → `git@github.com:Freizy231/PersonalHub.git` |
| Git 身份 | 已配置（Freizy231），提交签名未启用 |
| 已有代码 | 无 |
| 已有数据库 | 无 |
| 已有 `.env` / 密钥 | 无（仅检查存在性） |
| 已有项目文档 | 仅有 `AGENT.md`、`AGENTS.md` |

仓库内容审计前只有两份规范文件，是**空仓库 + 主规范**，目录用途明确，可安全初始化。

---

## 3. 已有资产（仓库外）

发现一个仓库外的既有产物，**未被本仓库引用，本次也未修改**：

```text
/home/freizy/personal-hub-backups/20261118T022135Z/   108M
├── source-v0.14.2.tar.gz     619K   上一次实现的源码快照
└── data/                     内含真实用户数据
    ├── database.sqlite3      27M
    ├── vault/vault.phv       加密私密库
    ├── notes/ library/ hub-files.json manifest.json
```

`data/` 是**真实个人数据**（笔记、资料库、加密私密库等），位于仓库之外，符合 `AGENT.md` 关于数据目录的规定。本次仅确认存在性与大小，未读取正文内容。

### 3.1 既有源码的技术栈（只读检查归档清单）

- 版本：`0.14.2`，`requires-python >=3.11,<3.13`
- 后端：FastAPI + SQLAlchemy 2.0 + Alembic + SQLite + Pydantic 2，模块化单体，Uvicorn
- 前端：**Vue 3 + TypeScript + Vite + Naive UI**
- 已有 12 份 ADR、多份 API 文档、HANDOFF 交接文档、升级迁移体系

---

## 4. 与规范基线的差距与冲突

### 4.1 冲突（需用户决策，不擅自改变）

| 项 | `AGENT.md` 基线 | 既有实现 v0.14.2 |
|---|---|---|
| 前端框架 | **React + TypeScript + Vite**（§3.1、§3.2） | **Vue 3** + Naive UI |
| 起点 | 按里程碑 M0→M7 渐进构建 | 功能已远超 MVP（任务、日程、笔记、饮食、资料库…） |

这是唯一影响 M1 的技术选型冲突。两种路线：

- **路线 A：按规范重做**（React）。符合 `AGENT.md` 基线，但需重新实现已有功能，工作量大。
- **路线 B：复用既有实现**（Vue 3）。工作量小、已有真实数据与迁移体系，但需先修改 `AGENT.md` 的前端基线，属于架构变更，须走 §13.2 的确认流程。

在用户明确决策前，**不实施任何前端代码**。

### 4.2 环境差距

| 项 | 现状 | 影响 | 处理建议 |
|---|---|---|---|
| `python3` = 3.7 | EOL 版本在 PATH 首位 | 直接建虚拟环境会失败 | 统一用 `uv` 并锁定 `--python 3.12`（或 3.11.9，若决定复用既有实现） |
| SQLite CLI 3.24 | 过旧 | 命令行排障能力受限；不影响应用（应用用 Python 内置 3.45.1） | 暂不处理，不安装系统包 |
| SSH 访问 GitHub 超时 | 22 端口被代理阻断 | 无法用 SSH 推送 | 推送到远端时改用 HTTPS + PAT，或后续单独配置 SSH over 443 |
| 远端仓库为空 | 无分支 | 首次推送需 `-u origin main` | 用户授权后再推送 |

---

## 5. 数据保护风险

1. 真实用户数据仅存在于 `/home/freizy/personal-hub-backups/20261118T022135Z/data/`，**只有一份**。在任何复用/迁移动作前，必须先确认该备份完整性，并保留原样。
2. 既有源码包内的 `database.sqlite3` 是活跃 SQLite 数据，不可直接用文件复制方式备份；应按 §4.13 使用一致性备份机制。
3. 本仓库 `.gitignore` 已覆盖 `.env`、`.venv`、`*.db`、`*.sqlite*`、`data/`、`vault/`、`logs/`、`node_modules/`、`dist/`，并已用 `git check-ignore` 逐条验证通过。

---

## 6. 本次已完成

| 文件 | 状态 | 职责 |
|---|---|---|
| `.gitignore` | 新增 | 排除秘密、虚拟环境、依赖、运行时数据与构建产物 |
| `AGENT.md`、`AGENTS.md` | 纳入版本库 | 主规范与 Agent 入口 |
| Git 基线提交 | `817aceb` | M0 版本管理起点 |

未修改 `AGENT.md` 本身；未提交任何数据、密钥或构建产物。

---

## 7. M1 最小任务清单（待前端决策后执行）

1. 用 `uv` 创建后端虚拟环境，锁定 Python 版本，生成 `pyproject.toml` / 锁文件。
2. 建立 `backend/app/main.py`，只实现 `GET /api/v1/health`，返回版本与状态，不连数据库。
3. 为 `/api/v1/health` 写 Pytest 测试（正常路径 + 统一错误结构）。
4. 建立前端骨架（框架取决于第 4.1 节决策），首页请求健康检查并显示连接状态。
5. 启动绑定 `127.0.0.1`，配置严格的 CORS 白名单，不使用 `0.0.0.0`。
6. 更新 `README.md`：可重复的安装与启动步骤。

**M1 验收命令（计划）**

```bash
# 后端
cd backend && uv run pytest -q
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
curl -fsS http://127.0.0.1:8000/api/v1/health

# 前端
cd frontend && npm ci && npm run dev
```

---

## 8. 待用户决策

1. 前端基线：React（遵循现有规范）还是 Vue 3（复用既有实现并修改规范）？
2. 是否将既有 v0.14.2 源码作为参考资产纳入本仓库（只读参考，还是作为新起点）？
3. 何时、以何种方式推送首个提交到 GitHub 远端？
