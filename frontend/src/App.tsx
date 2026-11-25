import { BrowserRouter, NavLink, Route, Routes } from 'react-router-dom'

import { AppShell } from './components/AppShell'
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

/**
 * 路由表 + 应用壳层。
 *
 * 导航已从"标题下方一排横排链接"改为左侧应用导航（见 `components/AppShell`），
 * 因此这里不再维护 NAV_ITEMS——导航项定义在壳层里，与路由表分开：
 * 前者决定"用户怎么找功能"，后者决定"地址对应哪个页面"。
 */
export default function App() {
  return (
    <BrowserRouter>
      <AppShell>
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
                  返回工作台
                </NavLink>
              </section>
            }
          />
        </Routes>
      </AppShell>
    </BrowserRouter>
  )
}
