import { Link } from 'react-router-dom'

import type { StagedApp } from '../types/api'
import {
  installActionLabel,
  previewHeadline,
  previewNote,
  type Step,
} from '../services/appFlow'

interface FlowStepsProps {
  steps: Step[]
}

/** 五步流程指示（迭代 3）。状态全部来自服务端，不自行推断"通过检查"。 */
export function FlowSteps({ steps }: FlowStepsProps) {
  return (
    <ol className="flow-steps" aria-label="创建流程">
      {steps.map((step, index) => (
        <li
          key={step.key}
          className={`flow-step flow-step--${step.state}`}
          aria-current={step.state === 'active' ? 'step' : undefined}
        >
          <span className="flow-step__index" aria-hidden="true">
            {step.state === 'done' ? '✓' : index + 1}
          </span>
          <span className="flow-step__label">{step.label}</span>
          <span className="visually-hidden">{STATE_TEXT[step.state]}</span>
        </li>
      ))}
    </ol>
  )
}

const STATE_TEXT: Record<Step['state'], string> = {
  done: '（已完成）',
  active: '（进行中）',
  pending: '（未开始）',
  failed: '（失败）',
}

interface StagedPreviewProps {
  staged: StagedApp
  contentSrc: string
  busy: boolean
  installing: boolean
  onInstall: () => void
  onDiscard: () => void
}

/**
 * 预览卡片。
 *
 * 三件事必须在点"安装"之前说清楚：
 * 1. 这块内容是**新建**、**新版本**还是**对某版本的修改建议**；
 * 2. 当前在用的版本是什么，安装后会变成什么；
 * 3. 改动幅度（修改建议才有）——它是模型自述之外的客观依据。
 */
export function StagedPreview({
  staged,
  contentSrc,
  busy,
  installing,
  onInstall,
  onDiscard,
}: StagedPreviewProps) {
  return (
    <div className="app-preview">
      <div className="app-preview__meta">
        <h3 className="app-preview__title">{previewHeadline(staged)}</h3>
        {staged.purpose && <p className="app-preview__purpose">{staged.purpose}</p>}

        <dl className="app-preview__facts">
          <div>
            <dt>当前版本</dt>
            <dd>{staged.current_version ?? '尚未安装'}</dd>
          </div>
          <div>
            <dt>安装后</dt>
            <dd>{staged.next_version}</dd>
          </div>
          <div>
            <dt>大小</dt>
            <dd>{formatSize(staged.size_bytes)}</dd>
          </div>
        </dl>

        {staged.change_stats && (
          <p className="app-preview__diff">
            相比 {staged.base_version}：新增 {staged.change_stats.added_lines} 行、删除{' '}
            {staged.change_stats.removed_lines} 行
          </p>
        )}

        {staged.change_reason && (
          <p className="app-preview__reason">
            AI 说它改了什么：{staged.change_reason}
            <span className="app-preview__reason-note">（AI 自述，未经核实）</span>
          </p>
        )}

        {staged.elements.length > 0 && (
          <div className="app-preview__elements">
            <span className="app-preview__elements-label">界面要素</span>
            <ul>
              {staged.elements.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        )}
      </div>

      <iframe
        className="app-frame"
        src={contentSrc}
        title={`预览：${staged.title}`}
        sandbox="allow-scripts"
        referrerPolicy="no-referrer"
        loading="lazy"
      />

      <div className="app-preview__actions">
        <button type="button" className="button" disabled={busy} onClick={onInstall}>
          {installing ? '安装中…' : installActionLabel(staged)}
        </button>
        <button type="button" className="button" disabled={busy} onClick={onDiscard}>
          丢弃这次结果
        </button>
        <p className="app-preview__sandbox-hint">{previewNote(staged)}</p>
      </div>
    </div>
  )
}

function formatSize(bytes: number): string {
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${bytes} 字节`
}

interface PendingStagingProps {
  items: StagedApp[]
  /** 当前正在预览的那一条，避免重复显示。 */
  activeId: string | null
  onOpen: (stagingId: string) => void
  onDiscard: (stagingId: string) => void
  busyId: string | null
}

/**
 * 「最近的生成」：尚未安装的结果。
 *
 * 关掉标签页、换个浏览器之后仍能找回来——生成结果一直在磁盘上，
 * 此前只有 sessionStorage 记得住当前标签页的那一次。
 */
export function PendingStaging({
  items,
  activeId,
  onOpen,
  onDiscard,
  busyId,
}: PendingStagingProps) {
  const visible = items.filter((item) => item.staging_id !== activeId)
  if (visible.length === 0) return null

  return (
    <section className="pending-staging">
      <h3 className="pending-staging__title">最近的生成（尚未安装）</h3>
      <p className="pending-staging__hint">
        这些结果还没有装上。1 小时内都可以回来预览或安装，之后会被自动清理。
      </p>
      <ul className="pending-staging__list">
        {visible.map((item) => (
          <li key={item.staging_id} className="pending-staging__item">
            <div className="pending-staging__main">
              <span className="pending-staging__name">{item.title}</span>
              <span className="pending-staging__kind">
                {item.is_update ? `修改建议 → ${item.next_version}` : `新安装 ${item.next_version}`}
              </span>
              {item.purpose && <span className="pending-staging__purpose">{item.purpose}</span>}
            </div>
            <button type="button" className="button" onClick={() => onOpen(item.staging_id)}>
              预览
            </button>
            <button
              type="button"
              className="button danger"
              disabled={busyId === item.staging_id}
              onClick={() => onDiscard(item.staging_id)}
            >
              丢弃
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}

interface JobHintProps {
  jobId: string | null
}

/** 正在跑的生成给出"可以离开"的去处，而不是让用户干等。 */
export function RunningJobHint({ jobId }: JobHintProps) {
  return (
    <p className="app-builder__away">
      可以离开这个页面，结果会保留。
      {jobId && (
        <>
          {' '}
          进度也可以到 <Link to={`/activity/${jobId}`}>活动</Link> 里看。
        </>
      )}
    </p>
  )
}
