/**
 * WAI-ARIA tabs keyboard model for a row of <button role="tab">:
 * Left/Right (wrapping), Home, End move selection; only the selected tab sits
 * in the Tab order. Usage: @keydown="onKey($event, ids, current, select)".
 */
export function tabKey(event: KeyboardEvent, ids: readonly string[], current: string, select: (id: string) => void) {
  const index = ids.indexOf(current)
  const last = ids.length - 1
  const next = event.key === 'ArrowRight' ? (index + 1) % ids.length
    : event.key === 'ArrowLeft' ? (index - 1 + ids.length) % ids.length
    : event.key === 'Home' ? 0
    : event.key === 'End' ? last : -1
  if (next < 0 || index < 0) return
  event.preventDefault()
  select(ids[next]!)
  const list = (event.currentTarget as HTMLElement | null)?.parentElement
  requestAnimationFrame(() => list?.querySelectorAll<HTMLElement>('[role="tab"]')[next]?.focus())
}
