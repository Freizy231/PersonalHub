interface EarlierMessagesProps {
  /** 有消息时才显示（空会话不需要"加载更早"）。 */
  visible: boolean
  hasEarlier: boolean
  loading: boolean
  error: string | null
  onLoad: () => void
}

/**
 * 「加载更早的消息」（迭代 2 第 6 项）。
 *
 * 会话详情页只加载最近一页。更早的内容此前**完全不可达**——用户既看不到，
 * 也不知道还有；长会话看起来像"只从中间开始"。
 *
 * 三条约束：
 * 1. `hasEarlier` 由**服务端**给出，不在前端用 total 推算。
 * 2. 加载失败时已有内容保持可见，只在按钮旁提示可以重试。
 * 3. 只用游标（最早一条的 id），不用 offset——新消息写入不会让翻页错位。
 */
export function EarlierMessages({
  visible,
  hasEarlier,
  loading,
  error,
  onLoad,
}: EarlierMessagesProps) {
  if (!visible) return null

  if (!hasEarlier) {
    return (
      <p className="earlier-messages earlier-messages--end">
        已经到对话的开头了。
      </p>
    )
  }

  return (
    <div className="earlier-messages">
      <button type="button" onClick={onLoad} disabled={loading}>
        {loading ? '正在加载…' : '加载更早的消息'}
      </button>
      {error && (
        <p className="earlier-messages__error" role="alert">
          更早的消息没有加载出来：{error} 可以再试一次。
        </p>
      )}
    </div>
  )
}
