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

### M2 — SQLite Persistence — 已完成

已完成：
- [x] `conversations` 表（UUID 主键、workspace 作用域、复合索引）
- [x] `messages` 表（外键 `ON DELETE CASCADE`、role 约束）
- [x] Alembic 首次迁移，在临时副本上验证 upgrade / downgrade 往返
- [x] 会话 CRUD API：列表（分页）、创建、获取、重命名、删除
- [x] 消息 API：追加、分页读取；所有请求先校验会话归属
- [x] 客户端只能创建 `role="user"` 消息，`system` / `assistant` / `tool` 由后端运行时写入
- [x] 追加消息刷新会话 `updated_at`，列表按最近活动排序
- [x] 前端路由（react-router）：总览 / 会话列表 / 会话详情
- [x] 会话详情：消息时间线、Enter 发送 / Shift+Enter 换行、输入法组合保护、多行缩进保留
- [x] 前端页面刷新、浏览器后退、不存在会话的 404 页面
- [x] 后端 101 项测试（含 role 伪造、workspace 隔离、级联删除、活动排序）

技术债：
- [ ] 删除为硬删除，无回收站
- [ ] 前端尚无单元测试
- [ ] 消息分页为一次性加载 200 条，长对话需要游标分页

### M3 — AI Workspace — 已完成

已完成：
- [x] DeepSeek Provider Adapter（httpx 实现，无官方 SDK 依赖）
- [x] `POST /api/v1/chat/stream`：SSE 流式输出、历史上下文传递
- [x] 取消语义：客户端断开 → 审计 `aborted` → 不保存部分回复
- [x] 外发审计日志（仅元数据：供应商/模型/条数/字符数/结果，不含正文）
- [x] 错误映射：鉴权/余额/限流/服务不可用/超时/不可达，面向用户文案固定安全
- [x] API Key 仅存于后端环境变量，前端与日志中均验证不出现
- [x] 前端流式渲染：逐字显示、光标动画、停止生成按钮、乐观显示用户消息
- [x] SSE 帧解析（自定义 fetch 流读取，非 EventSource）
- [x] 代理处理：socks 代理不崩溃、回环地址不走代理
- [x] 后端 143 项测试

规范偏离（详见 `docs/DECISIONS.md`）：
- [ ] D-002 未实现 Data Egress 检查层（用户决定）
- [ ] D-003 未使用 OS Keychain（本机无方案，用环境变量）

- [x] 通用 OpenAI 兼容适配器，支持中转站/自建网关（D-007）
- [x] 401/404 错误提示包含实际请求地址，区分"Key 无效"与"地址不匹配"
- [x] 局域网/本机地址自动直连，不走代理
- [x] 数据库未迁移时返回可执行的修复提示（而非"服务器内部错误"）

技术债：
- [ ] 历史按条数截断（40 条），无 token 预算管理 —— 留给 M4 Context Engine
- [ ] 无模型选择 UI，模型由服务端配置固定
- [ ] 未做真实 DeepSeek API 的端到端验证（需真实 Key，由用户手动完成）

### M4 — Memory Engine Basic — 已完成

已完成：
- [x] `memory_items`（status / kind / sensitivity / confidence / 内容指纹）+ `memory_sources`
- [x] Alembic 迁移 `e9afe6c0ba28`，临时副本验证 upgrade/downgrade 往返
- [x] 对话结束后后台提取候选（失败不影响对话）
- [x] 模型输出按不可信数据校验：非法 kind/sensitivity 回落默认，confidence 截断
- [x] **只有 APPROVED 进入模型上下文**；PROPOSED/REJECTED 绝不注入（含变异测试验证）
- [x] 记忆作为"资料"注入，提示中声明不得当作指令执行（间接注入防护）
- [x] 拒绝/删除后按内容指纹去重，不再重复提案
- [x] 记忆 API：列表（按状态）、批准、拒绝、修改、删除；workspace 隔离
- [x] 前端记忆中心：待审核/已批准/已拒绝三视图、批准、拒绝、修改、两段确认删除
- [x] 界面明确告知"敏感标记不阻止外发"（D-008）
- [x] 后端 212 项测试

技术债：
- [ ] 检索为 SQLite 结构化匹配，未做相关性排序（MVP 允许，见 §4.4）
- [ ] 敏感级别不参与任何过滤（D-008）
- [ ] 提取为条数/字符截断，无 token 预算管理

### M5 — Basic Agent & Read-only Tools — 未开始

### M6 — Governance & Approval — 未开始

### M7 — Persistent Task Runtime — 未开始

## 阶段 B–D

- M8 Capability Runtime — 未开始
- M9 System Builder — 未开始
- M10 Sync & Community — 未开始
