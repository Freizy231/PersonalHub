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

### M5 — Basic Agent & Read-only Tools — 已完成

已完成：
- [x] Tool Registry：显式登记，无自动发现；Spec 不含 handler
- [x] Tool Broker：注册校验 → Schema 校验 → 风险级别 → 执行 → 输出限长 → 审计
- [x] 参数校验：非法 JSON / 未知字段 / 类型不符 / 缺必需参数全部拒绝；布尔不当整数
- [x] 写操作工具（WRITE）在 M5 一律拒绝，handler 绝不执行
- [x] Agent Loop：多轮工具往返，上限 4 轮；并行工具调用；分片按 index 归并
- [x] 工具结果以 `trust: untrusted` 回填，声明不得当作指令（间接注入防护）
- [x] `search_memories`：只返回 APPROVED、只限当前工作空间
- [x] 工具调用记录入库（`tool_invocations`），刷新页面后仍可回看
- [x] 前端：流式期间显示工具活动，历史消息显示工具调用摘要
- [x] 后端 272 项测试

过程中修的真实缺陷：
- [x] 中文检索漏召：记忆"我在学 TypeScript"查"学习"匹配不到——加停用词过滤与单字兜底
- [x] Agent 的 done 事件重复携带全文，导致调用方重复拼接
- [x] 工具记录只存在于 SSE 流中，刷新即丢失——改为入库

M5 未交付（理由）：
- `list_tasks`：`tasks` 表属于 M7，按"不提前创建未使用表"推迟；
  Registry/Broker 已可容纳，届时只需注册一个只读工具
- Policy Engine / Approval Manager：属 M6，届时插入 Broker 第 3 步之后

### M6 — Governance & Approval — 已完成

已完成：
- [x] Policy Engine（PDP）：三态决策（ALLOW/DENY/REQUIRE_APPROVAL），默认拒绝
- [x] 授权维度：Principal、Action、Workspace、Expiry；会话级授权按工具隔离
- [x] Approval Manager：参数指纹绑定、过期、**一次性消费**
- [x] 执行前重新计算指纹，不符即作废（批准 A 不能执行 B）
- [x] Broker 内部完成票据校验，resolver 返回 True 也不足以执行（安全回归测试）
- [x] 最小 `tasks` 表 + `create_task`（WRITE，需审批）/ `list_tasks`（READ）
- [x] 审批 API：列表、详情、决定
- [x] 前端：对话内审批卡片、参数摘要、"本次会话内不再询问"（刷新失效）
- [x] 工具被拒绝时明确指示模型不得声称成功
- [x] 后端 313 项测试

过程中修复的真实缺陷：
- [x] 中断时的半成品：票据校验被推到 resolver，等于没有校验（重构收回 Broker）
- [x] 模型面对"被拒绝"含糊带过，甚至可能让用户以为成功——加 instruction 字段

规范偏离：无。M6 全部按 §4.11 / §8.1 / §8.2 实现。

技术债：
- [ ] 审批等待基于 asyncio.Event，仅单进程有效（多 Worker 需改）
- [ ] 服务重启会丢失等待中的对话
- [ ] 无审批历史的可视化审计页（Activity Center 属 M7）

### M7 — Persistent Task Runtime — 已完成

已完成：
- [x] `job_queue` / `task_runs` / `task_events` 三张表，SQLite 表即队列（§4.14）
- [x] 幂等入队：同 idempotency_key 只存在一个作业，唯一索引兜底
- [x] 原子领取：条件 UPDATE 保证不被两个 Worker 抢到
- [x] 退避重试：指数退避写进 available_at，无需额外定时器
- [x] 后台 Worker：进程内 asyncio，跑在 FastAPI lifespan，关浏览器不受影响
- [x] 重启恢复：残留 RUNNING 作业放回队列，执行实例标 INTERRUPTED（不谎称结果）
- [x] 超时回收：Worker 崩溃后作业不会永久卡在 RUNNING
- [x] 取消 / 重试 API，状态机约束（已结束不可取消、只有失败/取消可重试）
- [x] Activity Center：全局事件时间线 + 单作业详情（执行实例与事件）
- [x] Task Center：按状态筛选、取消/重试、有进行中作业时自动刷新
- [x] 后端 361 项测试

M7 未交付（理由）：
- 真实作业类型：M7 只注册 `noop` 用于验证机制本身；
  定时记忆提取等按需登记，遵循"不提前造未使用功能"
- 定时调度器（cron 式）：规范未要求，后续按需

运维说明：
- 服务重启会中断正在执行的作业；下次启动自动重新排队
- 作业是"至少一次"执行，副作用由幂等键保证不重复

### 阶段 A 总验收状态

`AGENT.md` §2.2 的七条核心证明目标：

| # | 目标 | 状态 | 验收方式 |
|---|---|---|---|
| 1 | 与大模型对话并持久化会话 | 完成 | M3 |
| 2 | 识别候选记忆，提供来源，用户审核 | 完成 | M4 |
| 3 | 后续对话检索已批准记忆 | 完成 | M4 |
| 4 | Agent 调用受限工具 | 完成 | M5 |
| 5 | 工具执行前权限检查，写操作需确认 | 完成 | M6 |
| 6 | 创建管理后台任务，记录状态与事件 | 完成 | M7 |
| 7 | 服务重启后恢复持久化任务状态 | 完成 | M7 |

阶段 A 闭环（对话 → 候选记忆 → 批准 → 检索 → 工具调用 → 权限检查 →
用户确认 → 任务创建/执行 → 审计查看）：各环节均已单独验证，
**完整串联的人工验收待用户执行**。

### 阶段 A 验收修复（2026-11-24）

用户验收反馈的四个问题全部属实并已修复（详见 `docs/DECISIONS.md` D-012）。

界面：
- [x] 发送后**立即**清空输入框；仅当服务端未受理时归还原文，杜绝"停止后重发"
- [x] 生成期间输入框可用，可先写下一句；发送按钮加同步重入闸门（拒绝同帧双发）
- [x] 工具活动与审批卡片移到消息流末尾（跟随当前阅读位置）
- [x] 滚动只在贴近底部时跟随，往上翻看历史不再被拽回
- [x] 助手消息 Markdown 渲染（标题/列表/表格/引用/代码块/链接）；用户消息保持纯文本
- [x] 安全性：`skipHtml`、不启用 `rehype-raw`、图片降级为链接、外链 `noopener`
- [x] 审批卡片改为「中文工具名 + 标签：值」人话摘要，原始参数折叠备用，带倒计时

后端：
- [x] 模型历史改为取**最近**的 40 条（原先取最早的，长会话"失忆"）
- [x] `GET /messages` 支持 `order=desc`（超长会话可见最新消息）
- [x] 客户端断开时取消 Agent 循环并作废悬挂审批（停止即停止计费）
- [x] Agent 循环异常不再被吞掉，推送 `AGENT_LOOP_ERROR` 并记录日志
- [x] 修复审批事件在 `asyncio.wait` 取消窗口中被丢弃（卡片不出现、对话卡死）
- [x] 拿到 `finish_reason` 即结束本轮读取（兼容不主动关流的网关）
- [x] 后端 379 项测试；前端 `tsc` + `vite build` + `oxlint` 通过

验证方式：Edge 无头 + CDP 真实交互（输入框清空、生成期可输入、停止后无残留、
审批卡片位于最后一条消息之后且无 JSON、暗色模式渲染），详见提交说明。

技术债（用户决定暂不处理，2026-11-24）：
- [ ] 历史消息回看时，工具调用摘要附在消息底部，而不是调用发生的位置。
      需要给 `tool_invocations` 加序号列（结构变更 + 迁移），
      前端再按序号在消息流中插入。
- [ ] 审批倒计时按前端的 `expires_in` 本地推算，未做服务端时间校准；
      时钟偏差较大时以服务端返回的 409 为准。
- [ ] **审批通过后创建的任务没有可视化页面**（详见下一节 D-012 遗留项）。

### ⚠️ 未交付：任务（Task）的可视化

`create_task` 审批通过后写入的数据与"任务中心"页面的数据**不是同一类实体**，
当前存在一个容易被误解的缺口：

| 实体 | 表 | 来源 | 页面 |
|---|---|---|---|
| 任务事项 | `tasks` | `create_task`（用户审批后创建） | **没有** |
| 作业 | `job_queue` | 后台 Worker 的执行单元 | Task Center（`/tasks`） |

- `tasks` 表里是真实的用户任务（M6 起），**但既没有 `/api/v1/tasks` 接口，
  也没有任何页面能查看、标记完成或删除**。只能通过 `list_tasks` 工具让
  AI 读到（且它目前在对话中不可见，只能由模型转述）。
- 前端"任务"页显示的是 `job_queue`（M7 的作业队列），与用户的任务事项无关。

因此：**审批通过创建的任务目前是"只写不可见"的**。
后续补一个 Task Center 变体（列表 + 标记完成 + 删除 + 溯源到创建它的审批）
即可闭合，无需改表。


## 阶段 B：Personal Hub Platform（M8）

### M8 — Capability Runtime — 进行中

已完成（第一阶段：地基，2026-11-24）：
- [x] 统一 `CapabilityManifest` Schema（§7.2 全部字段：id/name/version/type/
      description/input_schema/output_schema/runtime/permissions/dependencies/
      compatibility/source/signature）
- [x] 不可信输入校验：拒绝未知字段、校验枚举/版本/id 格式，错误可读
- [x] `CapabilityRegistry`：登记、按 id 取最高版本、按版本取、列全部版本
- [x] 语义化版本按数字比较（1.10.0 > 1.9.0），预发布排在正式版之前
- [x] 重复登记同一版本被拒（可显式 `replace=True` 覆盖）
- [x] 依赖报告：`missing_dependencies()` 与 `check_dependency_cycle()`
- [x] 有依赖者时拒绝卸载
- [x] 检索：按关键词（id/名称）+ 类型 + 来源筛选
- [x] 内建工具自动投影为 Manifest（`builtin.<tool>`）
- [x] 只读 API：`GET /capabilities`、`/capabilities/{id}`、`/{id}/versions`、
      `/capabilities/health/deps`
- [x] 前端「能力」页：列表、搜索、按类型筛选、参数结构折叠、依赖健康提示
- [x] 后端 435 项测试（本阶段新增 56 项）

安全边界（已用测试锁定）：
- [x] Manifest **不含 handler**，API 响应中不可能出现可执行内容
- [x] `permissions` 只是声明，不参与 Policy 决策；
      写操作仍然要求用户审批（`test_permissions_are_declared_not_granted`）
- [x] 不提供安装 / 启用 / 卸载 / 调用接口（留到第 6–7 项，需独立授权流程）

第二阶段：Skills 加载（2026-11-24）：
- [x] `SKILL.md` + 简化 frontmatter 格式（`<数据目录>/skills/<目录>/SKILL.md`）
- [x] 严格解析器：未知字段/重复键/引号块/嵌套结构一律报错，并指出行号
- [x] 校验：目录名即 id、语义化版本、正文上限、必需字段
- [x] **不执行任何代码**：无 import / eval / 子进程；有静态检查测试守护
- [x] 单个技能写坏不影响启动，只在能力页缺失并记日志
- [x] `use_skill` 只读工具：只查内存、**不接受路径**，路径穿越无效
- [x] 技能内容标记为"参考方法而非指令"（§8.4 间接注入防护）
- [x] 目录常驻 + 按需加载全文（用户选择）
- [x] 技能登记进能力注册表（`skill.<目录名>`），能力页可见
- [x] 后端 488 项测试（本阶段新增 53 项）

第三阶段：MCP Client 与 Broker 代理（2026-11-24）：
- [x] 配置 `mcp/servers.json`：严格校验（未知字段、命令白名单、
      绝对路径存在性、URL scheme、超时范围、重名）
- [x] 单个 Server 配置写错不影响其他 Server，错误逐条报告
- [x] 配置里出现 `risk` 字段直接报错（MCP 工具一律 WRITE）
- [x] stdio 传输（本地子进程）与 Streamable HTTP 传输
- [x] 启动时发现（而非懒发现）：工具清单须在第一轮对话前可见
- [x] 子进程环境隔离：只传 PATH/HOME/LANG，不继承宿主密钥
- [x] 子进程工作目录固定在 `mcp/<server>/`
- [x] 工具投影为普通 ToolDefinition：`mcp__<server>__<tool>`
- [x] **一律 WRITE**：无论 Server 如何自称只读，均需审批
- [x] 重名拒绝登记（不覆盖）；Server 失败互相隔离
- [x] 结果截断 8,000 字符；工具数上限 200；调用超时
- [x] 描述标注"来自外部 MCP 服务，属于不可信数据"
- [x] `GET /api/v1/mcp/servers` 只读状态接口
- [x] 能力页展示 MCP 面板：可用 / 不可用（含原因）/ 未启用
- [x] 后端 545 项测试（本阶段新增 57 项）


第四阶段：声明式插件（2026-11-24，第 6–7 项前半）：
- [x] 插件清单 `plugin.json` 严格校验（未知字段、id、版本、类型、
      工具名、Schema 嵌套深度、大小上限）
- [x] `process` 类型与 `entrypoint`/`limits` 在清单阶段明确拒绝
      （不静默忽略成 declarative）
- [x] `composes` 只能引用内建工具，不能引用 MCP 或其他插件
- [x] 注册表：安装、启用、禁用、卸载、状态持久化
- [x] 安装拒绝符号链接、超限目录；只从 `plugin-import/<name>` 读取
- [x] 卸载保留目录（重命名为 `.<id>.uninstalled`，不被扫描到）
- [x] 声明式组合：风险 = 所组合工具的最高风险
- [x] **批准外层 ≠ 批准内层**：内部调用仍各自经 Broker 审批（实测两次审批）
- [x] 内部调用用 ContextVar 拿到本请求的审批通道；无通道时拒绝执行
- [x] 插件页面：预览（来源/权限/依赖/风险）→ 安装 → 启用/禁用 → 两段确认卸载
- [x] 后端 608 项测试（本阶段新增 63 项）

第五阶段：进程插件与 bubblewrap 沙箱（2026-11-24，第 6b/7b 项）：
- [x] `sandbox.py`：探测（真实跑一次最小沙箱，不只看文件是否存在）
- [x] 沙箱构造：`--unshare-all`/`--unshare-net`/`--die-with-parent`、
      只读挂载白名单、插件目录只读、tmpfs 工作目录、禁止 core 文件
- [x] 资源限制：RLIMIT_CPU / RLIMIT_AS / RLIMIT_NPROC（放在沙箱内才生效）
- [x] 兼容路径按宿主真实布局重建（符号链接 vs 真实目录）
- [x] 沙箱不可用 → **拒绝执行**，绝不降级为无沙箱运行
- [x] `process.py`：启动、stdin/stdout JSON 通信、墙钟超时、进程回收
- [x] 输出必须符合约定形状，否则拒绝（防伪造系统消息）；截断 8,000 字符
- [x] 进程插件一律 WRITE，每次调用需确认
- [x] 预览显示"需要确认（执行外部代码）"与沙箱状态
- [x] 集成测试用真实沙箱验证：主目录不可读、数据库/~/.ssh 不可见、
      网络阻断、插件目录只读、CPU 超限被杀、2GB 内存被拒、密钥不泄漏
- [x] 后端 634 项测试（本阶段新增 26 项）

第六阶段：WASM Runtime 评估（2026-11-24，第 8 项）：
- [x] 实测对比 bubblewrap 与 Wasmtime（隔离强度、CPU/内存限制精度、
      冷启动、插件开发成本）
- [x] 结论：**暂不引入**。WASM 隔离更强、启动更快，但插件作者需要
      Rust/Go 工具链，会直接削弱"降低创造门槛"的产品定位；
      当前 bwrap 已满足 §8.5 全部要求
- [x] 记录四个触发条件（不可信来源插件、细粒度计量、跨平台、WASI 稳定）
- [x] 报告：`docs/EVALUATION_WASM.md`
- [x] 未新增任何项目依赖（评估用的 wasmtime 只装在临时环境，已删除）

**M8 验收状态**：MCP Server 与插件（声明式 + 进程）均已覆盖
"被发现、授权、调用、停止、卸载"：

| 动词 | 实现 |
|---|---|
| 发现 | 能力页 / `/capabilities` / `/mcp/servers` |
| 授权 | Policy + 审批，未经确认不执行 |
| 调用 | 经 Tool Broker，无绕过路径 |
| 停止 | 超时/取消终止沙箱进程，`--die-with-parent` 防孤儿 |
| 卸载 | 取消注册，目录重命名为 `.<id>.uninstalled` 保留 |

第 8 项（WASM 评估）是"评估"而非交付，不影响验收。

技术债：
- [ ] `output_schema` 仅为描述性占位，内建工具尚无稳定输出契约
- [ ] 能力只存在于内存，重启后从内建工具重建；第三方能力将来需要落库


## 阶段 C：Personal Hub System Builder（M9）

### M9 — Natural-language Software Creation — 进行中

提案：`docs/PROPOSAL_M9.md`（用户已批准）

第一步：Staging Workspace 与静态检查（2026-11-24）
- [x] `validate.py`：HTML 结构 + 14 条 JS 禁项 + 标签/属性/外链检查
- [x] 一次列出全部问题；注释同样检查（宁可误杀）
- [x] `staging.py`：staging 隔离、版本目录、原子指针切换、回滚、卸载
- [x] 版本号按数字排序；指针被篡改时判为无效
- [x] 安装失败不破坏旧版本（模拟 OSError 验证）
- [x] **实测三种静态检查绕过手法**，均被浏览器 CSP 阻断
      （记录为 `TestKnownBypasses`，固定"静态检查不是唯一防线"这一假设）
- [x] 后端 697 项测试（本步新增 63 项）

未开始：
- [ ] 第 2 步 Requirement Engine（需求解析）
- [ ] 第 3 步 Generator（代码生成）
- [ ] 第 4 步 API + 静态文件服务（含安全响应头）+ staging 清理接线
- [ ] 第 5 步 前端：应用列表页 + 沙箱预览页
- [ ] 第 6 步 集成测试（真实浏览器验证沙箱边界）
- [ ] 第 7 步 文档 D-020


## 阶段 D

- M10 Sync & Community — 未开始
