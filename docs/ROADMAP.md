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

已完成：
- [x] 后端 `uv` 环境（Python 3.12.3，`uv.lock` 锁定 FastAPI 0.165.0 / Pydantic 2.14.4）
- [x] `GET /api/v1/health` + 26 项 Pytest（健康检查、错误结构、CORS、配置、日志脱敏）
- [x] React 19 + Vite 8 前端骨架（严格类型检查通过）
- [x] 前端展示健康状态：在线 / 离线 / 超时 / 连点竞态
- [x] 回环绑定（8000 与 5173 均实测仅 `127.0.0.1`）与 CORS 白名单
- [x] `scripts/dev.sh` 一键启动，进程组清理验证无孤儿进程
- [x] 真实浏览器（Edge 无头）验证：在线、断线、恢复、暗色、窄屏

待完成：
- [ ] 前端单元测试（M1 验收未强制，但 §9.2 建议在引入交互逻辑时补充）
- [ ] 后端生产态同源提供前端静态文件（当前为 Vite 开发代理）

### M2 — SQLite Persistence — 进行中

已完成：
- [x] `conversations` 表（UUID 主键、workspace 作用域、复合索引）
- [x] `messages` 表（外键 `ON DELETE CASCADE`、role 约束）
- [x] Alembic 首次迁移，在临时副本上验证 upgrade / downgrade 往返
- [x] CRUD API：列表（分页）、创建、获取、重命名、删除
- [x] 标题清洗（trim 后拒绝空串）与 Schema `extra="forbid"` 防篡改归属字段
- [x] 时间统一为 UTC + `Z` 后缀
- [x] 前端会话管理：新建 / 重命名 / 两段确认删除 / 刷新后恢复
- [x] 39 项新增后端测试（含级联删除、workspace 隔离、路径注入）

待完成（下一轮）：
- [ ] `messages` 的 API（追加、分页读取）
- [ ] 会话详情页与消息列表 UI
- [ ] 前端单元测试
- [ ] 软删除 / 回收站（当前为硬删除，已记为技术债）

### M3 — AI Workspace — 未开始

### M4 — Memory Engine Basic — 未开始

### M5 — Basic Agent & Read-only Tools — 未开始

### M6 — Governance & Approval — 未开始

### M7 — Persistent Task Runtime — 未开始

## 阶段 B–D

- M8 Capability Runtime — 未开始
- M9 System Builder — 未开始
- M10 Sync & Community — 未开始
