import { BrowserRouter, NavLink, Route, Routes } from 'react-router-dom'

import { ActivityCenterPage } from './pages/ActivityCenterPage'
import { AppsPage } from './pages/AppsPage'
import { CapabilityCenterPage } from './pages/CapabilityCenterPage'
import { ConversationDetailPage } from './pages/ConversationDetailPage'
import { ConversationsPage } from './pages/ConversationsPage'
import { HomePage } from './pages/HomePage'
import { JobCenterPage } from './pages/JobCenterPage'
import { MemoryCenterPage } from './pages/MemoryCenterPage'
import { PluginCenterPage } from './pages/PluginCenterPage'
import { SettingsPage } from './pages/SettingsPage'
import { UserTasksPage } from './pages/UserTasksPage'

/** 导航只列出已实现的页面，避免死链接（AGENT.md：不提前创建空模块）。 */
const NAV_ITEMS = [
  { to: '/', label: '总览', end: true },
  { to: '/conversations', label: '会话', end: false },
  { to: '/memory', label: '记忆', end: false },
  // 「我的任务」（用户事项）与「后台作业」（Worker 执行）是两个不同实体，
  // 导航上必须分开显示（D-013）。
  { to: '/tasks', label: '我的任务', end: false },
  { to: '/jobs', label: '后台作业', end: false },
  { to: '/activity', label: '活动', end: false },
  { to: '/capabilities', label: '能力', end: false },
  { to: '/plugins', label: '插件', end: false },
  { to: '/apps', label: '应用', end: false },
  { to: '/settings', label: '设置', end: false },
]

export default function App() {
  return (
    <BrowserRouter>
      <main className="app">
        <header className="app__header">
          <h1 className="app__title">Personal Hub</h1>
          <p className="app__subtitle">本地优先的个人 AI 环境</p>
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
          <Route path="/memory/deleted" element={<MemoryCenterPage filter="DELETED" />} />
          {/* 我的任务：用户事项（tasks 表） */}
          <Route path="/tasks" element={<UserTasksPage filter="ALL" />} />
          <Route path="/tasks/open" element={<UserTasksPage filter="OPEN" />} />
          <Route path="/tasks/done" element={<UserTasksPage filter="DONE" />} />
          {/* 后台作业：Worker 执行记录（job_queue 表） */}
          <Route path="/jobs" element={<JobCenterPage filter="ALL" />} />
          <Route path="/jobs/pending" element={<JobCenterPage filter="PENDING" />} />
          <Route path="/jobs/running" element={<JobCenterPage filter="RUNNING" />} />
          <Route path="/jobs/succeeded" element={<JobCenterPage filter="SUCCEEDED" />} />
          <Route path="/jobs/failed" element={<JobCenterPage filter="FAILED" />} />
          <Route path="/activity" element={<ActivityCenterPage />} />
          <Route path="/activity/:jobId" element={<ActivityCenterPage />} />
          <Route path="/capabilities" element={<CapabilityCenterPage />} />
          <Route path="/plugins" element={<PluginCenterPage />} />
          <Route path="/apps" element={<AppsPage />} />
          <Route path="/settings" element={<SettingsPage />} />
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
          <p>数据默认留在本机，服务只监听回环地址。</p>
        </footer>
      </main>
    </BrowserRouter>
  )
}
