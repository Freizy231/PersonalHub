import { describe, expect, it } from 'vitest'

import {
  describePermission,
  permissionRisk,
  typeLabel,
  typeOptions,
} from './capabilityDisplay'

describe('permissionRisk', () => {
  it('没有声明权限时是"无"', () => {
    expect(permissionRisk([])).toBe('none')
  })

  it('全部只读时是"只读"', () => {
    expect(permissionRisk(['tool:list_tasks:read'])).toBe('read')
    expect(permissionRisk(['tool:list_tasks:read', 'tool:search_memories:read'])).toBe('read')
  })

  it('含写操作时是"需要确认"', () => {
    expect(permissionRisk(['tool:create_task:write'])).toBe('write')
    // 读写混合：只要有一个写，整体就是写。
    expect(permissionRisk(['tool:list_tasks:read', 'tool:create_task:write'])).toBe('write')
  })

  it('未知格式按"需要确认"处理（安全默认）', () => {
    // 把写操作画成只读比多提示一次确认糟糕得多。
    expect(permissionRisk(['something:unknown'])).toBe('write')
    expect(permissionRisk(['mcp:server:tool:write'])).toBe('write')
  })

  it('渲染应用页面算只读', () => {
    expect(permissionRisk(['app:render'])).toBe('read')
  })
})

describe('describePermission', () => {
  it('翻译工具读写权限', () => {
    expect(describePermission('tool:create_task:write')).toBe('写入：create_task')
    expect(describePermission('tool:list_tasks:read')).toBe('读取：list_tasks')
  })

  it('翻译组合调用', () => {
    expect(describePermission('composes:builtin.list_tasks')).toBe('组合调用：list_tasks')
  })

  it('翻译 MCP 权限', () => {
    expect(describePermission('mcp:notes:add_note:write')).toBe('写入：notes / add_note')
  })

  it('翻译应用渲染权限', () => {
    expect(describePermission('app:render')).toBe('渲染应用页面')
  })

  it('猜不出格式时原样返回，不编造解释', () => {
    expect(describePermission('weird:format')).toBe('weird:format')
    expect(describePermission('')).toBe('')
  })
})

describe('typeLabel', () => {
  it('已知类型给中文名', () => {
    expect(typeLabel('tool')).toBe('工具')
    expect(typeLabel('plugin')).toBe('插件')
  })

  it('未知类型原样返回（不假装认识）', () => {
    expect(typeLabel('vector_index')).toBe('vector_index')
  })
})

describe('typeOptions', () => {
  it('只包含传进来的类型，并带上数量', () => {
    const options = typeOptions([
      { type: 'tool', count: 5 },
      { type: 'skill', count: 2 },
    ])

    expect(options).toEqual([
      { value: 'tool', label: '工具（5）' },
      { value: 'skill', label: '技能（2）' },
    ])
  })

  it('没有数据的类型不会出现（服务端不返回它们）', () => {
    const options = typeOptions([])

    expect(options).toEqual([])
    // 回归：此前前端写死六类，"工作流"永远筛不出东西。
    expect(options.some((option) => option.value === 'workflow')).toBe(false)
  })
})
