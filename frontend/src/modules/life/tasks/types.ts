export type TaskStatus = 'todo' | 'doing' | 'done' | 'cancelled'
export type TaskScope = 'all' | 'today' | 'upcoming' | 'overdue' | 'completed' | 'trash'
export interface TaskTag { id: string; name: string; version: number }
export interface Task {
  id: string; title: string; description: string | null; status: TaskStatus; priority: number
  planned_date: string | null; due_date: string | null; due_at: string | null; timezone: string
  completed_at: string | null; deleted_at: string | null; version: number; is_overdue: boolean
  created_at: string; updated_at: string; tags?: TaskTag[]
}
export interface TaskPage { items: Task[]; total: number; limit: number; offset: number }
export function localDate(date: Date = new Date()) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}
