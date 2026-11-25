/**
 * 后端 API 类型定义。
 *
 * 与后端 Pydantic 模型保持可验证一致（AGENT.md §9.1）。
 * 后续可从 OpenAPI 生成，避免手工漂移。
 */

export interface HealthResponse {
  status: 'ok'
  service: string
  version: string
  timestamp: string
}

/** 单条消息的字符上限。与后端 `models.message.MAX_CONTENT_LENGTH` 保持一致。
 *
 * 放在共享类型里而不是某个组件内：前端有多处需要校验它，
 * 分散硬编码会漂移（D-012）。
 */
export const MESSAGE_MAX_LENGTH = 100_000

/** 与后端 ConversationOut 对应。时间为 ISO 8601 + Z（UTC）。 */
export interface Conversation {
  id: string
  title: string
  created_at: string
  updated_at: string
}

export interface ConversationList {
  items: Conversation[]
  total: number
  limit: number
  offset: number
}

export interface ApiErrorBody {
  error: {
    code: string
    message: string
    request_id: string
    details?: unknown
  }
}

/** 前端展示用的统一结果类型，避免在各组件里重复处理异常。 */
export type Result<T> =
  | { ok: true; data: T }
  | { ok: false; message: string; code: string; requestId?: string }

/** 与后端 MessageOut 对应。时间同样为 ISO 8601 + Z。 */
export interface ToolInvocationSummary {
  tool: string
  status: string
  error_code: string | null
}

export interface Message {
  id: string
  conversation_id: string
  role: string
  content: string
  created_at: string
  /** 该回合调用过的工具（仅助手消息可能有值）。 */
  tools?: ToolInvocationSummary[]
}

export interface MessageList {
  items: Message[]
  total: number
  limit: number
  offset: number
  /** 沿着本次的 order 继续翻，是否还有下一条。
   *
   * `order=desc` 时表示"还有更早的消息"（会话详情页用它决定是否显示
   * 「加载更早」）。由服务端计算，前端不从 total 推算。
   */
  has_more?: boolean
}

/** 与后端 MemoryOut 对应（M4）。敏感级别当前只展示，不阻断外发。 */
export interface MemorySource {
  source_type: string
  source_id: string | null
  excerpt: string
}

/** 一条"和当前候选很像"的其他候选（迭代 2 第 4 项）。
 *
 * 只用于提示，不是自动合并的依据——批准哪一条由用户决定。
 */
export interface SimilarMemory {
  id: string
  content: string
  score: number
}

export interface MemoryItem {
  id: string
  content: string
  status: 'PROPOSED' | 'APPROVED' | 'REJECTED' | 'DELETED'
  kind: 'SEMANTIC' | 'EPISODIC' | 'PROCEDURAL'
  sensitivity: 'PUBLIC' | 'PERSONAL' | 'SENSITIVE' | 'SECRET'
  confidence: number
  created_at: string
  updated_at: string
  sources: MemorySource[]
  /** 近似候选。仅待审核（PROPOSED）计算，其余状态恒为空。 */
  similar?: SimilarMemory[]
}

export interface MemoryList {
  items: MemoryItem[]
  total: number
  limit: number
  offset: number
  pending_count: number
  /** 本次请求实际生效的搜索词。 */
  query?: string
}

/** 审批记录（M6）。 */
export interface Approval {
  id: string
  tool_name: string
  arguments_preview: string
  status: 'PENDING' | 'APPROVED' | 'DENIED' | 'EXPIRED'
  conversation_id: string | null
  expires_at: string
  decided_at: string | null
  consumed: number
}

export interface ApprovalList {
  items: Approval[]
  total: number
}

/** 对话中出现的审批请求（来自 SSE approval_required 事件）。
 *
 * summary / title / arguments 是给人看的（D-012）：
 * - title：中文工具名，例如"创建任务"；
 * - summary：逐项的「中文标签 + 值」，界面直接分行显示；
 * - arguments：原始参数，只放在"查看原始参数"的折叠区里备查。
 * preview 保留是为了兼容只显示纯文本的旧客户端。
 */
export interface ApprovalSummaryItem {
  label: string
  value: string
}

export interface ApprovalRequestEvent {
  approval_id: string
  tool: string
  title?: string
  /** 这次调用会发生什么（由工具自己声明）。空表示工具没有声明。 */
  impact?: string
  summary?: ApprovalSummaryItem[]
  arguments?: Record<string, unknown>
  preview: string
  expires_in: number
}

/** 审批的终态反馈（迭代 2 第 2 项）。
 *
 * 卡片在用户做出决定后不能只是消失：用户需要知道"批准了、正在执行"
 * 还是"拒绝了、什么都没发生"。这是**客户端对已收到服务端响应的确认**，
 * 不是服务端任务状态——真正的执行结果由后续消息体现。
 */
export type ApprovalOutcome =
  | { kind: 'approved'; tool: string; title: string }
  | { kind: 'rejected'; tool: string; title: string }
  | { kind: 'expired'; tool: string; title: string }
  | { kind: 'failed'; tool: string; title: string; message: string }

/** 作业（M7）。 */
export interface Job {
  id: string
  job_type: string
  status: 'PENDING' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'CANCELLED'
  attempts: number
  max_attempts: number
  last_error: string | null
  available_at: string
  created_at: string
  updated_at: string
  /** 输入摘要（后端从 payload 白名单字段截断生成）。不含完整用户描述。 */
  summary?: string
  /** 当前阶段（仅未结束的作业有值）。 */
  stage?: string | null
  stage_message?: string | null
}

/**
 * 用户任务（`tasks` 表，D-013）。
 *
 * 这是"我记下要做的事"，不是后台作业。状态只有 OPEN / DONE，
 * **不会**出现 RUNNING / FAILED —— 那些是 `Job` 的状态。
 */
export interface UserTask {
  id: string
  title: string
  description: string
  status: 'OPEN' | 'DONE'
  created_at: string
  updated_at: string
  /** 由哪次审批创建；null 表示不是经审批创建的。 */
  created_via_approval_id: string | null
}

export interface UserTaskList {
  items: UserTask[]
  total: number
  limit: number
  offset: number
  /** 未完成数量：首页与导航角标用。 */
  open_count: number
}

export interface TaskEventItem {
  id: string
  job_id: string
  run_id: string | null
  event_type: string
  message: string
  created_at: string
}

export interface TaskRunItem {
  id: string
  attempt: number
  status: string
  started_at: string
  finished_at: string | null
  error_code: string | null
  /** 成功后的结果摘要（例如生成应用得到的 staging_id 与标题）。 */
  result: Record<string, unknown> | null
}

export interface JobDetail extends Job {
  runs: TaskRunItem[]
  events: TaskEventItem[]
}

export interface JobList {
  items: Job[]
  total: number
}

export interface ActivityList {
  items: TaskEventItem[]
  total: number
}

/** 能力声明（M8）。字段与后端 CapabilityOut 对应。
 *
 * 注意：permissions 只是**声明**，不代表已获授权（见 docs/DECISIONS.md D-014）。
 */
export interface Capability {
  id: string
  name: string
  version: string
  type: string
  description: string
  input_schema: Record<string, unknown>
  output_schema: Record<string, unknown>
  runtime: string
  permissions: string[]
  dependencies: string[]
  compatibility: string
  source: string
  signature: string | null
}

export interface CapabilityList {
  items: Capability[]
  total: number
  notice: string
}

/** 系统里实际存在的类型及数量（迭代 3）。
 *
 * 类型筛选只列有数据的类型：写死一份清单会留下永远筛不出东西的选项。
 */
export interface CapabilityTypeCount {
  type: string
  count: number
}

export interface CapabilityTypeList {
  items: CapabilityTypeCount[]
  total: number
}

export interface DependencyHealth {
  missing: Record<string, string[]>
  cycle: string[]
  healthy: boolean
}

/** MCP Server 状态（M8）。工具来自外部服务，全部需逐次确认。 */
export interface McpServer {
  name: string
  transport: string
  enabled: boolean
  command: string
  status: 'ready' | 'unavailable' | 'disabled' | string
  tool_count: number
  tools: string[]
  reason: string | null
}

export interface McpServerList {
  items: McpServer[]
  total: number
  errors: string[]
  notice: string
}

/** 插件（M8）。本期为声明式：不含可执行代码。 */
export interface PluginTool {
  name: string
  display_name: string
  description: string
  risk_hint: string
  composes: string[]
}

export interface Plugin {
  id: string
  name: string
  version: string
  type: string
  description: string
  enabled: boolean
  installed_at: string
  permissions: string[]
  dependencies: string[]
  tools: PluginTool[]
  error: string | null
}

export interface PluginList {
  items: Plugin[]
  total: number
  notice: string
  /** 进程插件依赖沙箱；不可用时必须如实告知，而不是让用户装完才发现跑不了。 */
  sandbox_available: boolean
  sandbox_reason: string
}

export interface PluginPreview {
  id: string
  name: string
  version: string
  description: string
  permissions: string[]
  dependencies: string[]
  tools: PluginTool[]
  warnings: string[]
}

/** 生成的 Personal App（M9）。 */
export interface AppInfo {
  id: string
  name: string
  version: string | null
  versions: string[]
  installed_at: string
  description: string
  ok: boolean
}

export interface AppList {
  items: AppInfo[]
  total: number
}

/**
 * 提交生成后的即时响应（202）。生成在后台跑，结果要等作业结束。
 *
 * 为什么改成作业：生成要两次模型调用，耗时以十秒计。
 * 同步接口会让浏览器一直挂着连接，离开页面就看不到结果。
 */
export interface GenerateAccepted {
  job_id: string
  staging_id: string
  status: string
  job_url: string
  /** 与需求接近的已安装应用。非空时应先提示复用。 */
  similar: SimilarApp[]
  /** 因为发现相似应用而没有提交生成（此时 job_id 为空串）。 */
  skipped: boolean
}

export interface SimilarApp {
  app_id: string
  name: string
  description: string
  version: string
  matched_terms: string[]
}

/** 生成完成后的摘要（来自 `/apps/staging/{id}`，迭代 3 起字段完整）。
 *
 * 刻意**不含 html**：内容只经 `/apps/staging/{id}/content` 加载，
 * 那条路径带 CSP 与 sandbox 响应头，是应用唯一的运行方式。
 */
export interface StagedApp {
  staging_id: string
  app_id: string
  title: string
  purpose: string
  elements: string[]
  capability: string
  size_bytes: number
  generated_at: string
  /** 这是"对已有应用的修改建议"还是"新建"。 */
  is_update: boolean
  /** 修改建议基于哪个已安装版本。 */
  base_version: string | null
  /** 模型自述的修改原因（未经核实，界面必须标明）。 */
  change_reason: string
  /** 当前已安装版本；新建时为 null。 */
  current_version: string | null
  /** 安装后会得到的版本号——用户点"安装"之前就该知道。 */
  next_version: string
  /** 相对当前版本的增删行数（仅修改建议有值）。 */
  change_stats: { added_lines: number; removed_lines: number } | null
}

export interface StagingList {
  items: StagedApp[]
  total: number
}

export interface InstallResult {
  ok: boolean
  app: AppInfo
  message: string
}

/** 系统设置（只读）。配置只能通过环境变量 + 重启修改。 */
export interface SettingsView {
  provider: {
    name: string
    model: string
    base_url: string
    api_key_env: string
    /** 只告诉"有没有配"，永远不回传密钥本身。 */
    api_key_configured: boolean
    ready: boolean
    hint: string
  }
  data: {
    data_dir: string
    db_path: string
    db_size_bytes: number
    free_disk_bytes: number
  }
  memory_policy: {
    requires_approval: boolean
    only_approved_injected: boolean
    secret_never_injected: boolean
    context_budget_chars: number
    note: string
  }
  counts: {
    conversations: number
    messages: number
    memories_approved: number
    memories_proposed: number
    tasks: number
    apps: number
  }
  runtime: {
    version: string
    host: string
    port: number
    max_apps: number
    max_versions_per_app: number
  }
}
