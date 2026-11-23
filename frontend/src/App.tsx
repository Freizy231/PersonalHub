import { StatusCard } from './components/StatusCard'
import { useHealth } from './hooks/useHealth'

export default function App() {
  const { state, recheck } = useHealth()

  return (
    <main className="app">
      <header className="app__header">
        <h1 className="app__title">Personal Hub</h1>
        <p className="app__subtitle">本地优先的个人 AI 环境 · MVP 骨架</p>
      </header>

      <StatusCard state={state} onRecheck={recheck} checking={state.status === 'checking'} />

      <footer className="app__footer">
        <p>
          当前处于 <strong>M1 — 前后端骨架</strong> 阶段。
        </p>
        <p>数据默认留在本机，服务只监听回环地址。</p>
      </footer>
    </main>
  )
}
