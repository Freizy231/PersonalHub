export interface HubEvent {
  id: string; title: string; description: string | null; is_all_day: boolean
  start_at: string | null; end_at: string | null; start_date: string | null; end_date: string | null
  timezone: string; location: string | null; status: 'scheduled' | 'completed' | 'cancelled'
  task_id: string | null; linked_task: { id: string; title: string; status: string } | null
  version: number; deleted_at: string | null
}
export interface Page<T> { items: T[]; total: number; limit: number; offset: number }
export const browserZone = () => Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Shanghai'
export function dateString(value: Date) {
  return `${value.getFullYear()}-${String(value.getMonth()+1).padStart(2,'0')}-${String(value.getDate()).padStart(2,'0')}`
}
export function shiftDate(value: string, days: number) {
  const [y,m,d] = value.split('-').map(Number)
  const date = new Date(y!,m!-1,d!+days,12)
  if (!Number.isFinite(date.getTime())) throw new Error('日期无效。')
  return dateString(date)
}
export function localInput(value: string) {
  const date = new Date(value)
  return `${dateString(date)}T${String(date.getHours()).padStart(2,'0')}:${String(date.getMinutes()).padStart(2,'0')}`
}
export function parseLocalInput(value: string): string {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value)) throw new Error('请填写完整日期与时间。')
  const date = new Date(value)
  if (!Number.isFinite(date.getTime()) || localInput(date.toISOString()) !== value) throw new Error('此本地时间不存在，请检查日期或夏令时。')
  for (let minutes=-180; minutes<=180; minutes+=15) {
    if (minutes && localInput(new Date(date.getTime()+minutes*60000).toISOString())===value) throw new Error('此时间处于夏令时重复区间，请选择其他时刻。')
  }
  return date.toISOString()
}
export function calendarDays(month: string) {
  const first = month.slice(0,7)+'-01'
  const day = new Date(first+'T12:00:00').getDay()
  const start = shiftDate(first,-((day+6)%7))
  return Array.from({length:42},(_,i)=>shiftDate(start,i))
}
export function eventLabel(event: HubEvent) {
  return event.is_all_day ? `${event.start_date} — ${shiftDate(event.end_date!,-1)} · 全天` :
    `${new Date(event.start_at!).toLocaleString('zh-CN')} — ${new Date(event.end_at!).toLocaleString('zh-CN')}`
}
export function downloadJSON(result: object, name: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'}))
  const link = document.createElement('a'); link.href=url;link.download=name;link.click()
  setTimeout(()=>URL.revokeObjectURL(url),1000)
}
