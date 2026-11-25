# Personal Hub — 产品质量审计报告

- 审计日期：2026-11-25
- 审计对象：Git HEAD `d7dc718`（`docs: 修正路线图中已完成的里程碑状态`），工作区仅 `docs/report.html` 未提交
- 审计方式：源码阅读 + 实际运行 + 真实浏览器走查，**仅读审计，未修改任何业务代码**
- 凭证状态：本机无 `DEEPSEEK_API_KEY`，所有需要真实模型的验证**记为待人工验收**，未伪造通过

---

## 1. 当前真实项目状态

### 1.1 实测基线

| 项目 | 实测结果 | 命令 / 证据 |
|---|---|---|
| 后端测试 | **897 passed** | `uv run pytest` |
| 后端 lint | 通过 | `uv run ruff check .` → `All checks passed!` |
| 后端格式 | 通过 | `uv run ruff format --check .` → `112 files already formatted` |
| 前端类型检查 | 通过 | `npx tsc -b --noEmit` → exit 0 |
| 前端构建 | 通过 | `npm run build` → 490.65 kB |
| 前端 lint | 通过 | `npm run lint` → `oxlint` exit 0，无告警 |
| 数据库迁移 | 通过（含往返） | 全新库 `upgrade head` → `downgrade base` → `upgrade head` 均成功，最终 `1a42bafbc6a8` |
| 服务启动 | 通过 | 真实启动 `uvicorn --port 8033`，`/health` 返回 ok |
| 浏览器走查 | 9 个页面无控制台错误、无网络失败 | CDP 实测 |

### 1.2 规模

- 后端源码 13,589 行；测试代码 9,840 行（**测试代码占 42%**）
- 前端源码 4,804 行 + CSS 2,591 行；**无前端测试**
- 42 次提交（26 feat / 4 fix / 11 docs / 1 chore）
- 44 个 API 端点、10 张业务表、5 次迁移

### 1.3 测试分布（逐文件实测）

| 测试文件 | 数量 | 测试文件 | 数量 |
|---|---|---|---|
| test_builder_staging | 72 | test_apps_api | 32 |
| test_plugins | 68 | test_context | 30 |
| test_capabilities | 61 | test_app_tools | 30 |
| test_skills | 53 | test_messages | 27 |
| test_governance | 51 | test_tools | 25 |
| test_memory | 45 | test_plugin_sandbox | 21 |
| test_mcp_config | 45 | test_chat | 21 |
| test_jobs | 45 | test_builder_generator | 19 |
| test_builder_requirement | 42 | test_logging_redaction | 17 |
| test_conversations | 39 | test_settings_api | 16 |
| test_agent | 37 | test_health | 16 |
| test_openai_compatible | 36 | test_async_jobs | 13 |
| | | test_mcp_bridge / test_errors | 12 / 12 |
| | | test_diagnose_script | 11 |
| | | test_apps_sandbox | 1 |

合计 897，与整轮运行一致。

---

## 2. M0–M9 实际完成情况

| 里程碑 | 规范交付 | 实际情况 | 判定 |
|---|---|---|---|
| M0 | 仓库与环境审计 | 完成，`docs/ENVIRONMENT_AUDIT.md` | 完成 |
| M1 | 前后端骨架 + 健康检查 | 完成。**生产态同源托管前端未做**（只有 Vite 代理） | 完成（有缺口） |
| M2 | SQLite 持久化 | 完成，迁移可往返 | 完成 |
| M3 | AI Workspace | 代码完成。**真实模型端到端未验证**（无凭证） | 完成（待人工验收） |
| M4 | 记忆引擎 | 完成。**SECRET 行为与 D-008 存在文档冲突**（见 §9） | 完成（文档矛盾） |
| M5 | Agent 与只读工具 | 完成，工具全部经 Broker | 完成 |
| M6 | 治理与审批 | 完成，票据在 Broker 内部校验 | 完成 |
| M7 | 持久化任务运行时 | **只有 `noop` 一个同步作业类型**，无真实业务作业；无定时任务 | 部分完成 |
| M8 | 能力运行时 | 声明式插件完成；**进程插件重启后失效（P0，见 §5）**；MCP 未实测；WASM 评估不引入 | 部分完成 |
| M9 | 自然语言创建应用 | 生成/检查/沙箱/安装/回滚/AI 修改均完成；**仅单文件 HTML，不是完整 System Builder** | 部分完成 |

---

## 3. 五级功能成熟度矩阵

等级定义：**L0** 未实现 · **L1** 代码已实现 · **L2** 自动化测试通过 · **L3** 真实端到端流程通过 · **L4** 用户可稳定使用

> 判定原则：L3/L4 必须有**真实运行**的证据。凡是只有假 Provider 或单元测试的，最高记 L2。

| # | 功能 | 级别 | 源码 | API | 前端 | 测试 | 实际验证 | 未完成事项 |
|---|---|---|---|---|---|---|---|---|
| A1 | 会话 CRUD | **L4** | `repositories/conversations.py` | `GET/POST /conversations` 等 5 个 | 会话页 | `test_conversations`(39) | 浏览器实测新建成功 | — |
| A2 | 消息持久化 | **L4** | `repositories/messages.py` | `GET/POST …/messages` | 会话详情 | `test_messages`(27) | 实测空会话提示正常 | — |
| A3 | 流式对话 | **L2** | `api/chat.py`、`ai/openai_compatible.py` | `POST /chat/stream` | 会话详情 | `test_chat`(21)、`test_openai_compatible`(36) | **全部用假 Provider**；真实流未验证 | 真实模型端到端 |
| A4 | 取消请求 | **L2** | `api/chat.py`（`finally` 取消循环） | 同上 | — | `test_chat::TestDisconnectStopsGeneration` | 未见真实断开实测记录 | 真实网络断开验证 |
| A5 | 错误处理 | **L4** | `core/errors.py` | 全部 | 各页面 | `test_errors`(12) | 实测无 Key 时给出可操作提示 | — |
| A6 | 长对话历史 | **L2** | `MAX_HISTORY_MESSAGES=40` | — | — | 有测试 | 前端固定取 200 条，**无"加载更多"** | 分页 UI |
| A7 | API Key 安全 | **L3** | `ai/__init__.py::read_api_key` | — | — | `test_logging_redaction`(17)、`test_settings_api` | 设置页实测不泄露 | **仍仅环境变量**（D-003） |
| B1 | 候选记忆提取 | **L2** | `services/memory.py` | 间接（对话后触发） | 记忆页 | `test_memory`(45) | 需真实模型 | 真实提取验证 |
| B2 | 用户审核 | **L4** | `api/memory.py` | `POST /memory/{id}/approve` 等 | 记忆页 | 同上 | 页面实测渲染正常 | — |
| B3 | 记忆更新删除 | **L4** | 同上 | `PATCH` / `DELETE` | 记忆页 | 同上 | — | — |
| B4 | 跨会话检索 | **L2** | `tools.py::_rank_by_relevance` | 工具调用 | — | `test_tools`(25) | 关键词匹配，非语义 | FTS5/向量检索 |
| B5 | 来源追踪 | **L4** | `models/memory.py::MemorySource` | 页面显示"来源：对话" | 记忆页 | `test_memory` | 实测显示 | — |
| B6 | SECRET 禁止注入 | **L3** | `services/context.py::NEVER_INJECT_SENSITIVITY` | — | — | `test_context`(30) | **直接实测**：SECRET 在所有路径（相关/不相关/回退）均为空 | 与 D-008 文档冲突，见 §9 |
| B7 | 敏感数据外发 | **风险存在** | 无 Egress Gateway | — | 记忆页已声明"不会阻止" | `test_sensitive_approved_memory_still_injected` | SENSITIVE 实测会注入 | 规范偏离（D-002/D-008） |
| C1 | Agent Loop | **L2** | `agent/loop.py` | 经 `/chat/stream` | — | `test_agent`(37) | 假 Provider | 真实模型验证 |
| C2 | Tool Calling | **L2** | 同上 | — | 会话内展示 | 同上 | 假 Provider | 真实模型验证 |
| C3 | 参数验证 | **L3** | `capabilities/__init__.py::validate_arguments` | — | — | `test_tools` | 拒绝未知字段/类型 | — |
| C4 | 权限检查 | **L3** | `policy/__init__.py::evaluate` | — | — | `test_governance`(51) | 默认拒绝、写需审批 | — |
| C5 | 执行错误 | **L3** | `broker.py::_run` | — | — | 同上 | 异常不外泄 | — |
| C6 | 重试终止 | **L3** | `MAX_TOOL_ROUNDS=4` | — | — | `test_agent` | — | — |
| C7 | 结果返回 | **L3** | `broker.py::_encode_output` | — | — | 同上 | 8k 上限，JSON | — |
| D1 | Policy Engine | **L4** | `policy/__init__.py` | — | — | `test_governance` | 纯函数可穷举 | — |
| D2 | 用户审批 | **L2** | `policy/approval.py` | `POST /approvals/{id}/decide` | 审批卡片 | 同上 | 需真实对话触发 | 真实流程验证 |
| D3 | 审批参数绑定 | **L3** | `arguments_fingerprint` | — | — | 同上 | 指纹不符拒绝 | — |
| D4 | 审批有效期 | **L3** | `DEFAULT_TTL` | — | — | 同上 | 过期拒绝 | — |
| D5 | 越权访问 | **L3** | `workspace_id` 过滤 | — | — | `test_conversations` | 404 不泄露存在性 | — |
| D6 | 绕过 Broker | **L3** | 全仓仅 `broker.py:252` 调 handler | — | — | `test_governance` | 插件也走 Broker | — |
| E1 | task / job 区分 | **L1** | `models/task.py` vs `models/job.py` | — | — | `test_jobs`(45) | **前端"任务"页展示的是 job** | 见 P1-1 |
| E2 | 用户任务前端入口 | **L0** | — | **无 `/tasks` API** | **无** | — | `GET /api/v1/tasks` → **404** | 见 P1-1 |
| E3 | Worker 执行业务 | **L1** | `builtin_handlers()` 只有 `noop` | — | — | `test_jobs` | 唯一真实作业 `generate_app` 在 `builder/jobs.py` | 见 P1-2 |
| E4 | 崩溃恢复 | **L3** | `queue.recover_interrupted` | — | — | `test_jobs` | — | — |
| E5 | 幂等 | **L3** | `idempotency_key` 唯一索引 | — | — | 同上 | — | — |
| E6 | 取消 | **L2** | `async_runner.run_with_cancel_check` | `POST /jobs/{id}/cancel` | 任务页 | `test_async_jobs`(13) | 实测 `runs=[CANCELLED]` | 同步作业无取消检查 |
| E7 | 重试 | **L2** | `queue.fail_run` 退避 | `POST /jobs/{id}/retry` | 任务页 | `test_jobs` | — | — |
| E8 | 定时任务 | **L0** | 无 Scheduler | — | — | — | 只有 1 秒轮询 | 未实现 |
| F1 | Tool Registry | **L4** | `capabilities/__init__.py` | `/capabilities` | 能力页 | `test_capabilities`(61) | 实测 6 项 | — |
| F2 | Manifest | **L4** | `capabilities/manifest.py` | 同上 | 能力页 | 同上 | — | — |
| F3 | Skills | **L3** | `capabilities/skills.py` | `use_skill` 工具 | 能力页 | `test_skills`(53) | 需真实对话 | — |
| F4 | MCP Client | **L2** | `mcp/client.py` | `/mcp/servers` | 无独立页 | `test_mcp_config`(45)、`test_mcp_bridge`(12) | **本机无 MCP 配置，从未真实连接** | 真实 Server 验证 |
| F5 | 声明式插件 | **L3** | `plugins/declarative.py` | `/plugins/*` | 插件页 | `test_plugins`(68) | 实机无插件，测试覆盖 | 真实安装验证 |
| F6 | bubblewrap 隔离 | **L3** | `plugins/sandbox.py` | — | — | `test_plugin_sandbox`(21) | **实测 `detect_sandbox()` 返回 True** | — |
| F7 | 启用/禁用/卸载 | **L2** | `plugins/registry.py` | `POST enable/disable`、`DELETE` | 插件页 | `test_plugins` | **进程插件重启后失效** | 见 P0-1 |
| F8 | 扩展版本持久化 | **L3** | `state.json` | — | — | 同上 | — | — |
| G1 | 需求解析 | **L3** | `builder/requirement.py` | `POST /apps/generate` | 应用页 | `test_builder_requirement`(42) | 实测范围筛查生效 | — |
| G2 | 能力复用 | **L4** | `capabilities/registry.py::find_similar_apps` | 同上（返回 `similar`） | 应用页提示 | `test_apps_api`、`test_capabilities` | 实测命中/不命中正确 | — |
| G3 | HTML 生成 | **L2** | `builder/generator.py` | 后台作业 | 应用页 | `test_builder_generator`(19) | 假 Provider | 真实模型验证 |
| G4 | 静态检查 | **L4** | `builder/validate.py` | — | — | `test_builder_staging`(72) | 实测拒绝含 fetch 的探针 | — |
| G5 | iframe 沙箱 | **L3** | `api/apps.py::APP_SECURITY_HEADERS` | `/apps/{id}/content` | 应用页 | `test_apps_sandbox`(1，无头浏览器) | 实测 5 项边界全部拦下 | — |
| G6 | 用户审批 | **L3** | 生成只写 staging，安装独立请求 | `POST …/install` | 应用页 | `test_apps_api`(32) | 实测安装成功 | — |
| G7 | 安装与回滚 | **L3** | `builder/staging.py` | `POST …/rollback` | 应用页 | 同上 | 实测版本 1.0.0 → 1.0.1 | — |
| G8 | AI 读写应用 | **L3** | `capabilities/app_tools.py` | 工具调用 | 会话内审批 | `test_app_tools`(30) | 实机工具清单可见 | 真实对话中未验证 |
| G9 | 多文件项目 | **L0** | — | — | — | — | 单文件上限 200KB | 见 P1-3 |
| G10 | 应用组合 | **L0** | — | — | — | — | 提案 §5.2"其次组合"未实现 | 未实现 |
| G11 | 应用数据持久化 | **L0** | — | — | — | — | 第 1 级不存数据 | 需独立设计 |
| G12 | 运行时资源限制 | **L0** | — | — | — | — | iframe 无法限 CPU | 已知限制 |
| H1 | 首页工作台入口 | **L1** | `pages/HomePage.tsx` | — | `StatusCard` | — | 实测整页只有健康面板 | 见 P1-4 |
| H2 | 任务/作业区分 | **L0** | — | — | 任务页=job | — | 见 E1 | 见 P1-1 |
| H3 | 所有页面可用 | **L3** | — | — | 9 页 | — | 实测无错误 | — |
| H4 | 交互完整 | **L2** | — | — | — | — | 新建/发送/安装/回滚可用 | 部分操作未全测 |
| H5 | 加载/错误/空状态 | **L3** | — | — | 各页面 | — | 实测空状态文案具体 | — |
| H6 | 390px 适配 | **L3** | `index.css` | — | — | — | 实测 6 页无横向溢出 | — |
| H7 | 键盘与可访问性 | **L2** | — | — | — | — | 实测焦点环正常；对比度 2 处略低 | 见 P2 |

### 成熟度分布

| 级别 | 数量 | 代表 |
|---|---|---|
| **L4** | 12 | 会话、消息、审批策略、能力注册、静态检查、能力复用 |
| **L3** | 24 | 沙箱、安装回滚、参数绑定、崩溃恢复、插件隔离 |
| **L2** | 17 | **全部 AI 相关（流式对话、Agent、生成）**、MCP、取消、重试 |
| **L1** | 4 | task/job 区分、Worker 业务、首页入口 |
| **L0** | 8 | 用户任务入口、定时任务、多文件、组合、数据持久化、资源限制 |

**关键结论：L2 集中在所有涉及真实模型的环节。** 这不是测试写得不好，而是**没有凭证就无法验证**。任何声称"AI 对话已验收"的说法在没有真实 Key 的情况下都不成立。

---

## 4. 功能缺失清单

### 4.1 规范要求但完全未实现（L0）

| 项 | 规范 | 证据 |
|---|---|---|
| 用户任务 API 与页面 | §2.5 Task Center | `GET /api/v1/tasks` → 404；前端"任务"页调 `api.jobs.list` |
| 定时任务 / Scheduler | §4.14 Event Bus & Scheduler | `workers/` 只有 1 秒轮询 |
| Workflow Runtime | §3.3 架构图 | `grep workflow` 只命中枚举定义 |
| AI Orchestrator | §3.3、§4.7 | `grep orchestrator` 零结果 |
| 多文件应用 | M9（第 3 级） | `MAX_APP_BYTES=200KB` 单文件 |
| 应用组合 | 提案 §5.2 | 未实现 |
| 应用数据持久化 | 第 2 级 | 静态检查禁用 localStorage |
| 应用运行时资源限制 | M9 风险表 | iframe 无法限制 |
| Data Egress Gateway | §4.12 | 只有审计日志（D-002） |
| OS Keychain | §4.6 | 只有环境变量（D-003） |
| FTS5 / 向量检索 | §3.2、§4.4 | 关键词 + 二字组 |
| 生产态同源托管前端 | M1 | 只有 Vite 代理 |

### 4.2 代码有、但未接通或接错

| 项 | 现象 | 证据 |
|---|---|---|
| **进程插件启动恢复** | 重启后被当成声明式注册 | §5 P0-1 |
| `tasks` 表 | 只有 `create_task` 写入、`list_tasks` 读取，**前端完全不可见** | `api/settings.py` 是唯一调用计数处 |
| 前端分页 | 固定 200 条，无加载更多 | `useConversationDetail.ts:11` |

---

## 5. 核心架构问题

### P0-1：进程插件在服务重启后失效，且以 READ 风险暴露（安全缺陷）

**现象（实测）**

`app/main.py::_bootstrap_plugins` 对**所有**已启用插件调用 `register_plugin_tools`，
而 `register_process_plugin` 只在 `api/plugins.py::_activate`（运行时点"启用"）里调用。

实测一个 `type=process` 的插件经启动路径注册：

```
type: process | composes: [()]
registered: ('plugin__echo',)
skipped: ()
→ 进程插件被当成声明式注册
```

执行该工具：

```
注册的工具: plugin__echo | risk: READ
执行异常: TypeError Object of type coroutine is not JSON serializable
```

**影响（三重）**

1. **功能**：重启后进程插件不可用，但界面仍显示"已启用"。
2. **安全**：它以 `risk=READ` 注册，**不触发审批**。一个本应"执行外部代码、需确认"的工具，
   以只读身份出现在模型工具清单里。虽然执行会因 `TypeError` 失败，但这是**偶然的兜底**，
   不是设计保证。
3. **可观测性**：失败被 `broker` 吞成 `TOOL_CRASHED`，用户与日志都不会指出根因。

**根因**：启动路径完全没有区分 `manifest.type`。

**为什么测试没发现**：`tests/test_plugins.py::TestPluginChatIntegration` 用的是声明式插件
（`_manifest_data()` 默认 type），从未覆盖"进程插件重启后"——测试缺口，不是偶然。

**建议**：`_bootstrap_plugins` 应按 `manifest.type` 分派到 `register_process_plugin`；
补一个"安装进程插件 → 启用 → 重建 app → 工具仍在且 risk=WRITE"的测试。

### P1-1：`tasks` 与 `job_queue` 的概念在界面上混为一谈

- 规范 D-013 明确区分：`tasks` = 用户记录的事，`job_queue` = 可执行的作业。
- 实际：前端「任务」页展示的是 `job_queue`；`GET /api/v1/tasks` 返回 404。
- 后果：用户在对话里让 AI "创建一个任务"，API 写进 `tasks` 表，**但界面上找不到它**。
  实测 `tasks` 表 0 行、`job_queue` 1 行，任务页显示的是后者。
- 这是 §2.5 "Task Center：任务状态、执行历史、失败原因、取消操作" **完全未满足**。

### P1-2：Worker 只有 `noop`，真实业务作业挂在别处

- `builtin_handlers()` 仅注册 `noop`。
- 唯一真实作业 `generate_app` 通过 `AsyncJobRunner` 注入，不走 `HandlerRegistry`。
- 两套作业注册机制并存：同步走 `HandlerRegistry`，异步走 `async_handlers` 字典。
  可以工作，但增加理解成本；且 `noop` 在生产中无任何用途。

### P1-3：M9 不是完整的 System Builder

规范 §4.15 与提案 §5.3 定的第 1 级就是"单文件只读展示"。这是**有意的范围限制**，
不该视为缺陷。但需要明确指出：当前的 System Builder 只覆盖第 1 级，
**多文件工程、构建步骤、应用间组合、应用自有数据都不在范围内**。

### P1-4：首页不承担工作台入口

`HomePage.tsx` 仅渲染 `StatusCard`。实测整页内容不足 400px，下方约 500px 空白。
新用户打开后不知道该做什么。

### P2-1：两套颜色/字体 token 收敛不足

- 21 种 `font-size`（0.74–1.9rem），其中 0.82/0.84/0.85/0.86 肉眼无差别。
- 6 种 `border-radius`（4/6/8/10/12/20px）。

---

## 6. 安全与数据风险

| # | 风险 | 严重度 | 现状 | 证据 |
|---|---|---|---|---|
| 1 | **进程插件以 READ 注册，绕过审批** | **P0** | 见 §5 P0-1 | 实测 |
| 2 | SENSITIVE 记忆随对话外发 | 中 | 用户已知情（D-008），界面明示 | `test_sensitive_approved_memory_still_injected` |
| 3 | SECRET 记忆 | **已解决** | Context Engine 三条路径全部拦截 | 实测 `build_pack` 均返回空 |
| 4 | 无 Egress Gateway | 中 | 规范偏离，仅记元数据 | D-002 |
| 5 | API Key 仅环境变量 | 中 | 可能被子进程继承 | D-003 |
| 6 | 静态检查可被绕过 | 低（有第二层） | 浏览器 CSP 实测阻断 | `TestKnownBypasses` |
| 7 | 应用无法限制 CPU | 低 | iframe 边界 | 已知限制 |
| 8 | 记忆重复提取 | 低 | 用户可能批准三份近似内容 | 实测见记忆页 |

---

## 7. 前端体验问题

| 级别 | 问题 | 实测证据 |
|---|---|---|
| P1 | 首页无工作台入口 | 整页只有健康卡 |
| P1 | 任务页标题是内部标识 `generate_app` | 两条任务无法区分 |
| P1 | 能力页漏出未渲染 Markdown「**完整文件**」 | 截图可见 |
| P1 | 记忆页三条近似内容无提示 | 实测 3 条几乎相同 |
| P1 | 前端无任何单元测试 | `package.json` 无 test script |
| P2 | "详情"按钮 27×22px（< 24px AA） | 实测 |
| P2 | 两处对比度 4.45 / 3.97:1 | 实测计算 |
| P2 | 会话链接用浏览器默认蓝 `#0000EE` | 唯一脱离 token 的颜色 |
| P2 | 页脚硬编码"M9 阶段" | `App.tsx:77` |
| P2 | 能力页"工作流"筛选项永远为空 | 无 workflow 类型能力 |

---

## 8. 测试覆盖情况

### 8.1 Mock 依赖度（关键）

**34 处**假 Provider / Mock 使用。所有涉及模型的测试都是假 Provider：

| 文件 | Mock 方式 | 真实网络 |
|---|---|---|
| test_chat.py | `FakeProvider` | 无 |
| test_builder_requirement.py | `FakeProvider` | 无 |
| test_builder_generator.py | `FakeProvider` | 无 |
| test_apps_api.py | `FakeProvider` | 无 |
| test_openai_compatible.py | `httpx.MockTransport` | 无 |

**没有 `pytest.mark.integration` 或网络条件跳过机制。**

### 8.2 覆盖空洞

| 空洞 | 后果 |
|---|---|
| **进程插件重启恢复** | P0-1 漏网 |
| **MCP 真实连接** | 本机无配置，从未验证过真实 Server |
| **真实模型端到端** | 整个 AI 链路止步 L2 |
| **前端零测试** | 交互回归无保障 |
| **`/api/v1/tasks`** | 不存在，也无测试期望它存在 |
| **长会话 UI 分页** | 无测试，也无功能 |
| **并发写 SQLite** | 无压力测试 |

### 8.3 测试质量（正面的部分）

- 安全回归测试有意识地固定"已知缺陷"：`TestKnownBypasses`、`test_sensitive_approved_memory_still_injected`
- 真实子进程测试：`test_plugin_sandbox`（bubblewrap 实测可用）
- 真实浏览器测试：`test_apps_sandbox`（无头 Edge 五项边界断言）
- 迁移往返在测试中执行（`conftest.apply_migrations`）

---

## 9. 文档与源码不一致

| # | 不一致 | 实际情况 |
|---|---|---|
| 1 | `report.html` 记 897，`ROADMAP.md` 停在 849 | **897 正确**。ROADMAP 未记录 Context Engine(30) + Settings(16) + 2 的增量 |
| 2 | `report.html` 说"SECRET 永不注入"，`D-008` 说"不阻断外发" | **两者都成立，但描述冲突**：D-008 说的是 SENSITIVE；SECRET 在 Context Engine 层被过滤。但 D-008 里"测试锚点"提到的测试名 `test_sensitive_approved_memory_still_injected` 仍断言 SENSITIVE 注入，**与 SECRET 过滤不矛盾但容易被误读** |
| 3 | 任务页展示 `job_queue`，`tasks` 无 API | 无文档说明这一点 |
| 4 | M7"Worker 执行真实业务" | 只有 `noop` + 外挂的 `generate_app` |
| 5 | M9"System Builder" | 只有第 1 级（单文件） |
| 6 | Data Egress Gateway | 确实未实现（D-002 已记录） |
| 7 | API Key 环境变量 | 确实（D-003 已记录） |
| 8 | Workflow Runtime / AI Orchestrator | **确认不存在**（`grep` 零结果，仅枚举定义） |
| 9 | `report.html` 称"M0–M9 完成" | M7/M8/M9 应为"部分完成"（见 §2） |
| 10 | ROADMAP M0/M1 曾标"进行中" | 已在 `d7dc718` 修正 |

**没有发现通过修改文档掩盖实现缺口的迹象**——D-002/D-003/D-008 都如实记录了偏离。
但 ROADMAP 的测试数字确实滞后，且"M9 已验收"的措辞过强。

---

## 10. P0 / P1 / P2 问题排序

### P0（必须修）

1. **进程插件重启后被误注册为只读工具** — 功能失效 + 绕过审批。`app/main.py::_bootstrap_plugins`

### P1（应当修）

2. **`tasks` 完全没有前端入口与 API** — 用户在对话里建的任务找不到
3. **任务页展示 job 却叫"任务"，且标题是内部标识**
4. **首页不承担工作台入口**
5. **真实模型端到端从未验证** — 所有 AI 功能止步 L2
6. **前端零测试**
7. **进程插件重启恢复无测试**（P0-1 的测试缺口）
8. 能力页漏出未渲染 Markdown
9. 记忆页重复内容无提示
10. 长会话无分页 UI

### P2（可延后）

11. 字号 21 种 / 圆角 6 种
12. 两处对比度略低
13. 会话链接用默认蓝
14. 页脚硬编码里程碑
15. "工作流"空筛选项
16. `noop` 作业留在生产代码里

---

## 11. 修复建议

### P0-1（最高优先）

```python
# app/main.py::_bootstrap_plugins
from app.plugins.manifest import PluginType

if manifest.type is PluginType.PROCESS:
    result = register_process_plugin(
        manifest,
        plugin_dir=registry.root / installed.directory,
        registry=app.state.tool_registry,
    )
else:
    result = register_plugin_tools(manifest, registry=app.state.tool_registry)
```

配套测试：
```
test_process_plugin_survives_restart:
    install → enable → 销毁 app → 重建 app → 断言工具存在且 risk == WRITE
```

### tasks / job 的区分

最小可行：新增 `GET /api/v1/tasks`（只读列表）与「任务中心」下的独立区块，
与「后台作业」分开。不要合并两张表（D-013 的理由仍成立）。

### 真实模型验收

需要用户提供凭证，然后跑一遍：
1. 真实对话 + 流式输出
2. 触发 `create_task` 审批 → 批准 → 任务落库
3. 对话后记忆提取 → 记忆页出现候选
4. 生成应用到预览

---

## 12. 下一阶段建议

1. **先修 P0-1**（预计 1 小时 + 测试），这是唯一的安全 + 功能双重缺陷。
2. **补真实模型验收**（需凭证），把 L2 的 17 项往前推。
3. **修 tasks 入口**（P1-2/3），这直接影响"用户在对话里建的任务去哪了"。
4. **补前端测试基建**（vitest + 至少覆盖 hooks 的状态机）。
5. **之后再谈 M10**。现在做备份/导出，等于在一个"用户任务不可见"的界面上加数据迁移——
   会迁移一份用户在界面上根本看不到的数据。

---

## 附：本次审计未做的事

- 未修改任何业务代码
- 未实施 M10
- 未改变数据库
- 未覆盖任何用户文件
- 未运行真实模型调用（无凭证）
- 未验证读屏软件行为、色盲模式（无工具）
