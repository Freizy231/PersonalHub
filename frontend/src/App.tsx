import { BrowserRouter, NavLink, Route, Routes } from 'react-router-dom'

import { ActivityCenterPage } from './pages/ActivityCenterPage'
import { ConversationDetailPage } from './pages/ConversationDetailPage'
import { ConversationsPage } from './pages/ConversationsPage'
import { HomePage } from './pages/HomePage'
import { MemoryCenterPage } from './pages/MemoryCenterPage'
import { TaskCenterPage } from './pages/TaskCenterPage'

/** 导航只列出已实现的页面，避免死链接（AGENT.md：不提前创建空模块）。 */
const NAV_ITEMS = [
  { to: '/', label: '总览', end: true },
  { to: '/conversations', label: '会话', end: false },
  { to: '/memory', label: '记忆', end: false },
  { to: '/tasks', label: '任务', end: false },
  { to: '/activity', label: '活动', end: false },
]

export default function App() {
  return (
    <BrowserRouter>
      <main className="app">
        <header className="app__header">
          <h1 className="app__title">Personal Hub</h1>
          <p className="app__subtitle">本地优先的个人 AI 环境 · MVP 骨架</p>
          <nav className="app__nav" aria-label="主导航">
            {NAV_ITEMS.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) => (isActive ? 'app__nav-link is-active' : 'app__nav-link')}
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
        </header>

        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/conversations" element={<ConversationsPage />} />
          <Route path="/conversations/:conversationId" element={<ConversationDetailPage />} />
          <Route path="/memory" element={<MemoryCenterPage filter="PENDING" />} />
          <Route path="/memory/pending" element={<MemoryCenterPage filter="PENDING" />} />
          <Route path="/memory/approved" element={<MemoryCenterPage filter="APPROVED" />} />
          <Route path="/memory/rejected" element={<MemoryCenterPage filter="REJECTED" />} />
          <Route path="/tasks" element={<TaskCenterPage filter="ALL" />} />
          <Route path="/tasks/pending" element={<TaskCenterPage filter="PENDING" />} />
          <Route path="/tasks/running" element={<TaskCenterPage filter="RUNNING" />} />
          <Route path="/tasks/succeeded" element={<TaskCenterPage filter="SUCCEEDED" />} />
          <Route path="/tasks/failed" element={<TaskCenterPage filter="FAILED" />} />
          <Route path="/activity" element={<ActivityCenterPage />} />
          <Route path="/activity/:jobId" element={<ActivityCenterPage />} />
          <Route
            path="*"
            element={
              <section className="detail">
                <p className="detail__message">页面不存在。</p>
                <NavLink className="button" to="/">
                  返回总览
                </NavLink>
              </section>
            }
          />
        </Routes>

        <footer className="app__footer">
          <p>
            当前处于 <strong>M7 — 持久化任务运行时</strong> 阶段（MVP 全部里程碑已完成）。
          </p>
          <p>数据默认留在本机，服务只监听回环地址。</p>
        </footer>
      </main>
    </BrowserRouter>
  )
}
