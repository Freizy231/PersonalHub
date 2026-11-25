/**
 * 能力展示的纯函数（迭代 3 第 1 项）。
 *
 * 为什么单独成模块：这些判断需要单独测试，而混在页面组件里会触发
 * Fast Refresh 的限制（一个文件既要导出组件又要导出函数）。
 */

export type PermissionRisk = 'read' | 'write' | 'none'

/**
 * 从声明权限推断"调用时会不会问你"。
 *
 * 约束与 Manifest 的语义一致：`permissions` 只是**声明**，
 * 实际决策在 Policy Engine。这里只用于界面上提示"预期会怎样"，
 * 不作为任何授权依据。
 *
 * **未知格式一律按 `write` 处理**：把写操作画成只读，比多提示一次确认糟糕得多。
 */
export function permissionRisk(permissions: string[]): PermissionRisk {
  if (permissions.length === 0) return 'none'
  const allRead = permissions.every((item) => /:read$/.test(item) || item === 'app:render')
  return allRead ? 'read' : 'write'
}

export const RISK_LABEL: Record<PermissionRisk, string> = {
  read: '只读',
  write: '需要确认',
  none: '无',
}

export const RISK_HINT: Record<PermissionRisk, string> = {
  read: '调用时不需要确认',
  write: '实际调用时会先询问你',
  none: '不访问任何数据或工具',
}

/**
 * 把内部权限串翻译成人话。
 *
 * 实际格式（见 `capabilities/registry.py`）：
 * - `tool:create_task:write`      -> `写入：create_task`
 * - `tool:list_tasks:read`        -> `读取：list_tasks`
 * - `app:render`                  -> `渲染应用页面`
 * - `composes:builtin.list_tasks` -> `组合调用：list_tasks`
 *
 * 猜不出格式时**原样显示**——宁可让用户看到内部串，也不要编一个错的解释。
 */
export function describePermission(permission: string): string {
  const compose = permission.match(/^composes:(?:builtin\.)?(.+)$/)
  if (compose) return `组合调用：${compose[1]}`

  // MCP 放在工具规则之前：`mcp:server:tool:write` 也会被工具规则的后半段匹配到，
  // 先匹配更具体的格式，避免把 Server 名当成工具名。
  const mcp = permission.match(/^mcp:([^:]+):([^:]+):(read|write)$/)
  if (mcp) return `${mcp[3] === 'write' ? '写入' : '读取'}：${mcp[1]} / ${mcp[2]}`

  // 工具名允许大写与连字符之外的字符（插件/技能可能带 `-`）。
  // 前缀有两种写法：`builtin.<tool>`（能力页的 id 风格）与 `tool:<name>`（权限串）。
  const tool = permission.match(/^(?:builtin\.|tool:)?([^:]+):(read|write)$/)
  if (tool) return `${tool[2] === 'write' ? '写入' : '读取'}：${tool[1]}`

  if (permission === 'app:render') return '渲染应用页面'

  return permission
}

/** 类型的中文名。未知类型原样返回（不假装认识它）。 */
export const TYPE_LABEL: Record<string, string> = {
  tool: '工具',
  mcp: 'MCP',
  skill: '技能',
  workflow: '工作流',
  plugin: '插件',
  app: '应用',
}

export const SOURCE_LABEL: Record<string, string> = {
  builtin: '内建',
  installed: '已安装',
  community: '社区',
}

export const RUNTIME_LABEL: Record<string, string> = {
  builtin: '随服务运行',
  mcp: 'MCP 服务',
  process: '独立进程',
}

export function typeLabel(type: string): string {
  return TYPE_LABEL[type] ?? type
}

/**
 * 筛选下拉框的选项。
 *
 * **只列出实际存在的类型**（由服务端给出计数）。写死一份类型清单时，
 * "工作流"会是个永远筛不出东西的选项——用户会以为功能坏了。
 */
export function typeOptions(
  counts: { type: string; count: number }[],
): { value: string; label: string }[] {
  return counts.map((item) => ({
    value: item.type,
    label: `${typeLabel(item.type)}（${item.count}）`,
  }))
}
