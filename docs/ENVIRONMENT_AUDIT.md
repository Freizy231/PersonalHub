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
- `https://github.com/Freizy231/PersonalHub.git` **可达**，远端仓库当前无任何分支（空仓库）。
- SSH：`github.com:22` 与 `ssh.github.com:443` 均已验证可达；`git@github.com` 公钥认证成功
  （`Hi Freizy231! You've successfully authenticated`）。首次探测的超时是代理尚未就绪所致，
  复测后正常。远端推送采用 SSH。
- 本机 SSH 密钥：`~/.ssh/id_ed25519`（ED25519，指纹 `SHA256:5ooJLkd6WeKDNxibKAqYLhkaYqhyJHZapftQ4yQ+eiU`），
  已在 GitHub 登记。无私钥外泄。

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

发现一个仓库外的既有产物，**与本项目无关，已按用户确认排除**：

```text
/home/freizy/personal-hub-backups/20261118T022135Z/   108M
├── source-v0.14.2.tar.gz     619K   历史开发版本源码
└── data/                     内含该历史版本的本地数据
    ├── database.sqlite3      27M
    ├── vault/vault.phv       加密私密库
    ├── notes/ library/ hub-files.json manifest.json
```

### 3.1 结论（用户已确认）

v0.14.2 是**之前的独立开发版本，与本产品无关联**。本项目不导入、不复用、不迁移其代码或数据；
技术选型以 `AGENT.md` 为准，前端使用 **React + TypeScript + Vite**。

该目录保持原样、未被本仓库引用。它不属于本项目的用户数据，本项目也不得读取或改写它。

---

## 4. 与规范基线的差距

### 4.1 技术选型（已确认）

无冲突。用户已确认前端使用 **React + TypeScript + Vite**，与 `AGENT.md` §3.1、§3.2 基线一致。
v0.14.2 历史版本与本项目无关，不作为参考实现。

### 4.2 环境差距

| 项 | 现状 | 影响 | 处理建议 |
|---|---|---|---|
| `python3` = 3.7 | EOL 版本在 PATH 首位 | 直接建虚拟环境会失败 | 统一用 `uv` 并锁定 `--python 3.12`（或 3.11.9，若决定复用既有实现） |
| SQLite CLI 3.24 | 过旧 | 命令行排障能力受限；不影响应用（应用用 Python 内置 3.45.1） | 暂不处理，不安装系统包 |
| 远端仓库为空 | 无分支 | 首次推送需 `-u origin main` | 已用 SSH 推送 `main`，见第 8 节 |

SSH 通路问题已排除：`github.com:22` 直连可用，无需 HTTPS + PAT 或 443 端口变通方案。

---

## 5. 数据保护风险

1. 仓库外 `/home/freizy/personal-hub-backups/` 属于历史版本，与本项目无关；本项目不读取、不迁移、不覆盖它。
2. 本项目自身的用户数据将存放在操作系统标准应用数据目录（§5.3），**不在源码仓库内**。
3. SQLite 数据不可用文件复制方式备份；应按 §4.13 使用一致性备份机制（M2 起需要）。
4. 本仓库 `.gitignore` 已覆盖 `.env`、`.venv`、`*.db`、`*.sqlite*`、`data/`、`vault/`、`logs/`、`node_modules/`、`dist/`，并已用 `git check-ignore` 逐条验证通过。

---

## 6. 本次已完成

| 文件 | 状态 | 职责 |
|---|---|---|
| `.gitignore` | 新增 | 排除秘密、虚拟环境、依赖、运行时数据与构建产物 |
| `AGENT.md`、`AGENTS.md` | 纳入版本库 | 主规范与 Agent 入口 |
| Git 基线提交 | `817aceb` | M0 版本管理起点 |

未修改 `AGENT.md` 本身；未提交任何数据、密钥或构建产物。

---

## 6. M1 技术基线（已确认）

| 层 | 选型 | 版本要求 |
|---|---|---|
| 后端 | Python + FastAPI + Pydantic + SQLAlchemy + SQLite + Alembic | Python 3.12（`uv` 管理） |
| 前端 | React + TypeScript + Vite | Node 22.12+ |
| 测试 | Pytest（后端） | — |
| 远程 | SSH（`git@github.com:Freizy231/PersonalHub.git`） | — |

## 7. M1 最小任务清单

1. 用 `uv` 创建后端虚拟环境，锁定 Python 3.12，生成 `pyproject.toml` 与锁文件。
2. 建立 `backend/app/main.py`，只实现 `GET /api/v1/health`，返回版本与状态，不连数据库。
3. 为 `/api/v1/health` 写 Pytest 测试（正常路径 + 统一错误结构）。
4. 用 Vite 创建 `frontend/`（React + TypeScript），首页请求健康检查并显示连接状态。
5. 启动绑定 `127.0.0.1`，配置严格的 CORS 白名单，不使用 `0.0.0.0`。
6. 更新 `README.md`：可重复的安装与启动步骤。

**M1 验收命令（计划）**

```bash
# 后端
cd backend && uv run pytest -q
uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
curl -fsS http://127.0.0.1:8000/api/v1/health

# 前端
cd frontend && npm ci && npm run dev
```

---

## 8. 决策记录

已在 2026-11-23 由用户确认：

1. **前端基线：React + TypeScript + Vite**，与 `AGENT.md` 一致。
2. **v0.14.2 与本产品无关联**，不导入、不复用、不迁移其代码或数据。
3. **远程推送使用 SSH**（已验证 `git@github.com` 公钥认证成功）。

## 9. M0 结论

- 仓库与环境适合按 `AGENT.md` 基线从头构建 MVP。
- 技术选型无冲突，M1 可以直接开始。
- 唯一需要长期注意的环境陷阱是 `PATH` 上的 `python3` 为 3.7；一律通过 `uv --python 3.12` 管理。
