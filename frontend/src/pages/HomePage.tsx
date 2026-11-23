import { StatusCard } from '../components/StatusCard'
import { useHealth } from '../hooks/useHealth'

/**
 * 首页：系统状态。
 *
 * 只负责展示后端连通性；业务内容在各自页面。
 */
export function HomePage() {
  const { state, recheck } = useHealth()

  return (
    <>
      <StatusCard state={state} onRecheck={recheck} checking={state.status === 'checking'} />
    </>
  )
}
