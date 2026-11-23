# Personal Hub

Local-first、用户拥有和控制的个人 AI 环境。数据默认留在本机，服务只监听回环地址。

## 当前状态

项目处于 **M0 — Repository & Environment Audit**，仓库中只有规范与文档，尚无应用代码。

- 主规范：[`AGENT.md`](AGENT.md)（权威项目上下文、架构、里程碑与验收标准）
- Agent 入口：[`AGENTS.md`](AGENTS.md)
- 环境审计：[`docs/ENVIRONMENT_AUDIT.md`](docs/ENVIRONMENT_AUDIT.md)
- 里程碑状态：[`docs/ROADMAP.md`](docs/ROADMAP.md)

## 里程碑路线

| 阶段 | 内容 | 状态 |
|---|---|---|
| M0 | 仓库与环境审计 | 进行中 |
| M1 | 前后端骨架 + 健康检查 | 未开始 |
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

应用骨架随 M1 建立后，本章节将补充可重复的安装与启动步骤（`uv sync`、`npm ci`、`uvicorn`）。

## 数据与隐私

- 用户数据存放在操作系统标准应用数据目录，**不在源码仓库内**。
- `.env`、数据库文件、Vault、索引、日志与构建产物已由 `.gitignore` 排除，并逐条验证。
- 密钥不得进入前端、普通数据库表、Prompt 或日志。
- 涉及数据外发的功能必须经过 Data Egress Gateway 与用户授权。
