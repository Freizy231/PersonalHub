# ROADMAP — 里程碑状态

> 以 `AGENT.md` 第 10 节为权威定义。本文件只记录实际进展，不替代主规范。

## 阶段 A：Personal Hub MVP（M0–M7）

### M0 — Repository & Environment Audit — 进行中

已完成：
- [x] 读取 `AGENT.md` / `AGENTS.md`，确认当前里程碑
- [x] 审计 OS、工具链、Python 解释器、磁盘、网络
- [x] 审计仓库状态与仓库外既有资产
- [x] 建立 `.gitignore` 并用 `git check-ignore` 逐条验证
- [x] 首个基线提交 `817aceb`
- [x] `docs/ENVIRONMENT_AUDIT.md` 审计报告
- [x] `README.md` 初稿
- [x] 技术选型确认：React + TypeScript + Vite（与规范一致）
- [x] 确认 v0.14.2 历史版本与本项目无关，不复用
- [x] SSH 到 GitHub 认证验证成功

### M1 — Frontend/Backend Skeleton — 进行中

待完成，任务清单见 `docs/ENVIRONMENT_AUDIT.md` 第 7 节：
- [ ] 后端 `uv` 环境（Python 3.12）
- [ ] `GET /api/v1/health` + Pytest
- [ ] React + Vite 前端骨架
- [ ] 前端展示健康状态
- [ ] 回环绑定与 CORS 白名单

### M2 — SQLite Persistence — 未开始

### M3 — AI Workspace — 未开始

### M4 — Memory Engine Basic — 未开始

### M5 — Basic Agent & Read-only Tools — 未开始

### M6 — Governance & Approval — 未开始

### M7 — Persistent Task Runtime — 未开始

## 阶段 B–D

- M8 Capability Runtime — 未开始
- M9 System Builder — 未开始
- M10 Sync & Community — 未开始
