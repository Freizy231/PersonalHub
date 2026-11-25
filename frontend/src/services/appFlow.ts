/**
 * 应用创建流程的步骤模型（迭代 3）。
 *
 * 产品要求把流程明确分成：**需求理解 → 后台生成 → 检查 → 预览 → 用户安装**，
 * 每一步都要展示真实状态。这个模块只负责"现在在哪一步、每一步怎么描述"，
 * 不发起请求——状态来源仍然是服务端的作业与 staging 接口。
 *
 * 为什么单独成模块：步骤文案与"哪一步进行中"散落在 JSX 里时，
 * 很容易出现"作业已经失败但界面还显示第 2 步进行中"这类不一致。
 * 纯函数可以逐条测。
 */

import type { StagedApp } from '../types/api'

export type StepKey = 'describe' | 'generate' | 'check' | 'preview' | 'install'
export type StepState = 'done' | 'active' | 'pending' | 'failed'

export interface Step {
  key: StepKey
  label: string
  state: StepState
}

export interface FlowInput {
  /** 用户已经输入了描述。 */
  hasPrompt: boolean
  /** 正在等作业（提交后到终态前）。 */
  generating: boolean
  /** 作业失败或取消的原因；null 表示没有失败。 */
  failure: string | null
  /** 已有可预览的暂存结果。 */
  staged: StagedApp | null
  /** 正在安装。 */
  installing: boolean
}

/**
 * 计算五步的状态。
 *
 * **检查一步的状态来自服务端**：生成作业只有在静态检查通过后才写 staging
 * （`generator.generate_app` 里"检查不过就拿不到内容"）。所以"有 staging"
 * 就等于"检查已通过"——不需要前端再判断一次，也不允许前端自己声称通过。
 */
export function computeSteps(input: FlowInput): Step[] {
  const { hasPrompt, generating, failure, staged, installing } = input

  if (staged) {
    return [
      { key: 'describe', label: '描述需求', state: 'done' },
      { key: 'generate', label: '后台生成', state: 'done' },
      { key: 'check', label: '安全检查', state: 'done' },
      { key: 'preview', label: '预览结果', state: installing ? 'done' : 'active' },
      { key: 'install', label: '你确认安装', state: installing ? 'active' : 'pending' },
    ]
  }

  if (failure) {
    return [
      { key: 'describe', label: '描述需求', state: 'done' },
      { key: 'generate', label: '后台生成', state: 'failed' },
      { key: 'check', label: '安全检查', state: 'pending' },
      { key: 'preview', label: '预览结果', state: 'pending' },
      { key: 'install', label: '你确认安装', state: 'pending' },
    ]
  }

  if (generating) {
    return [
      { key: 'describe', label: '描述需求', state: 'done' },
      { key: 'generate', label: '后台生成', state: 'active' },
      { key: 'check', label: '安全检查', state: 'pending' },
      { key: 'preview', label: '预览结果', state: 'pending' },
      { key: 'install', label: '你确认安装', state: 'pending' },
    ]
  }

  return [
    { key: 'describe', label: '描述需求', state: hasPrompt ? 'active' : 'pending' },
    { key: 'generate', label: '后台生成', state: 'pending' },
    { key: 'check', label: '安全检查', state: 'pending' },
    { key: 'preview', label: '预览结果', state: 'pending' },
    { key: 'install', label: '你确认安装', state: 'pending' },
  ]
}

/** 预览卡片顶部的一句话，说明这块内容是什么、装上会怎样。 */
export function previewHeadline(staged: StagedApp): string {
  if (staged.is_update && staged.base_version) {
    return `这是对「${staged.title}」的修改建议（基于 ${staged.base_version}）`
  }
  if (staged.current_version) {
    return `这是「${staged.title}」的新版本`
  }
  return `「${staged.title}」是第一次安装`
}

/**
 * 安装按钮上要说清楚会发生什么。
 *
 * 用户必须在点之前就知道版本会怎么变——这正是此前缺的信息。
 */
export function installActionLabel(staged: StagedApp): string {
  if (staged.current_version) {
    return `安装为 ${staged.next_version}（替换当前 ${staged.current_version}）`
  }
  return `安装 ${staged.next_version}`
}

/**
 * 说明这次安装的后果。刻意都说"仍需你确认"：预览不是安装，
 * 关掉页面也不会自动装上。
 */
export function previewNote(staged: StagedApp): string {
  if (staged.is_update) {
    return '只保存为待安装版本。当前在用的版本不会改变，直到你点击安装。'
  }
  if (staged.current_version) {
    return '会新增一个版本并切换过去；旧版本保留，可以回滚。'
  }
  return '会作为一个新应用装到下方列表。装完后它仍然只能在沙箱里运行。'
}
