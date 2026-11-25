import { useEffect, useRef, useState } from 'react'
import { Link, NavLink, useLocation } from 'react-router-dom'

import { useHealth } from '../hooks/useHealth'

/**
 * 应用壳层：左侧应用导航 + 自适应主内容区。
 *
 * 为什么需要这一层（《Personal Hub 前端风格开发说明》§2）：
 * 此前所有页面被 `.app { max-width: 720px }` 锁在居中窄列里，
 * 1907px 视口下实测内容区只有 720px，两侧各空出约 590px。
 * 导航也只是一排横向文字链接，页面一多就换行。
 *
 * 结构：
 *
 *     ┌────────────┬──────────────────────────┐
 *     │ 品牌        │                          │
 *     │ 主导航      │   <main> 页面内容          │
 *     │ 扩展分组    │                          │
 *     │ 设置        │                          │
 *     │ 服务状态    │                          │
 *     └────────────┴──────────────────────────┘
 *
 * 响应式（三个断点，对应规范 §2 的响应式布局）：
 * - `>= 1440px`：248px 完整侧栏
 * - `1024px–1439px`：224px 完整侧栏
 * - `768px–1023px`：72px 轨道——**保留中文标签**，不换成图标
 * - `< 768px`：抽屉，由顶部菜单按钮打开，切页/按 Esc/点遮罩都会收起
 *
 * 关于图标：规范 §2.3 要求"使用项目已安装的图标库"，§8 又要求
 * "不引入新的图标库"。仓库里没有图标库，两者冲突时选择**保留文字**：
 * 轨道宽度足够放下中文名称，于是"标签不消失"这条验收直接成立，
 * 也不必手绘临时图标。若以后引入图标库，只需改 `NavItem` 的渲染。
 */

interface NavItem {
  to: string
  label: string
  /** 精确匹配（只有工作台需要，否则 `/` 会一直命中） */
  end?: boolean
}

/** 主导航：用户日常任务。顺序按"从最常用到最少用"。 */
const PRIMARY_NAV: NavItem[] = [
  { to: '/', label: '工作台', end: true },
  { to: '/conversations', label: '对话' },
  { to: '/memory', label: '记忆' },
  // 「我的任务」（用户事项）与「后台作业」（Worker 执行）是两个不同实体，
  // 导航上必须分开显示（D-013）。
  { to: '/tasks', label: '我的任务' },
  { to: '/jobs', label: '后台作业' },
  { to: '/activity', label: '活动' },
]

/** 扩展：不常用，但属于产品能力的一部分。 */
const EXTENSION_NAV: NavItem[] = [
  { to: '/capabilities', label: '能力' },
  { to: '/plugins', label: '插件' },
  { to: '/apps', label: '应用' },
]

const STATUS_TEXT: Record<string, string> = {
  checking: '检查中',
  online: '已连接',
  offline: '未连接',
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const { state: health } = useHealth()
  const location = useLocation()
  const toggleRef = useRef<HTMLButtonElement>(null)
  const firstLinkRef = useRef<HTMLAnchorElement>(null)

  // 抽屉的打开状态跟着"当前路径"走（规范 §2.3："切换页面后自动收起"）。
  //
  // 用 `{ openedAt }` 而不是 `boolean`，是为了区分两种关闭：
  // - 用户按了菜单按钮 → 记录当时所在的路径；
  // - 路由变了 → 路径与 openedAt 不同，抽屉自动视为关闭。
  //
  // 这样就不需要 effect 里 setState（那会多一轮渲染，且把"切页"这个
  // 外部事件塞进 effect 去观测）。
  const [drawer, setDrawer] = useState<{ openedAt: string } | null>(null)
  const drawerOpen = drawer !== null && drawer.openedAt === location.pathname

  // 抽屉打开时：Esc 关闭、锁住背景滚动、把焦点移进抽屉。
  useEffect(() => {
    if (!drawerOpen) return

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      // Esc 是键盘用户的关闭动作，必须把焦点还给菜单按钮。
      // 只 setDrawer(null) 的话，焦点会留在已经滑出视口的导航项上——
      // 屏幕上看不见焦点框，再按 Tab 会从那个隐藏位置继续，用户彻底迷路。
      // 实测踩到过：Esc 后 focused 仍是「工作台」。
      setDrawer(null)
      toggleRef.current?.focus()
    }
    document.addEventListener('keydown', onKeyDown)

    // 背景滚动锁：抽屉盖住页面时，背后滚动会让用户失去"我在哪"的判断。
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'

    // 焦点移进抽屉，键盘用户不必再 Tab 很多次才到导航。
    firstLinkRef.current?.focus()

    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = previousOverflow
    }
  }, [drawerOpen])

  // 关闭后把焦点还给菜单按钮：否则焦点会落到 body，键盘用户丢失位置。
  const closeDrawer = () => {
    setDrawer(null)
    toggleRef.current?.focus()
  }

  return (
    <div className={`app${drawerOpen ? ' app--drawer-open' : ''}`}>
      {/* 遮罩只在窄屏存在；桌面端由 CSS 隐藏，且不可聚焦。 */}
      {drawerOpen && (
        <button
          type="button"
          className="app__scrim"
          aria-label="关闭导航"
          onClick={closeDrawer}
          tabIndex={-1}
        />
      )}

      <aside className="app__sidebar" id="app-sidebar">
        <div className="app__brand">
          <Link className="app__brand-link" to="/">
            Personal Hub
          </Link>
          <p className="app__brand-sub">本地优先的个人 AI 环境</p>
        </div>

        <nav className="app__nav" aria-label="主导航">
          <ul className="app__nav-list">
            {PRIMARY_NAV.map((item, index) => (
              <li key={item.to}>
                <NavItemLink
                  item={item}
                  {...(index === 0 ? { firstRef: firstLinkRef } : {})}
                />
              </li>
            ))}
          </ul>

          <p className="app__nav-group" aria-hidden="true">
            扩展
          </p>
          <ul className="app__nav-list" aria-label="扩展">
            {EXTENSION_NAV.map((item) => (
              <li key={item.to}>
                <NavItemLink item={item} />
              </li>
            ))}
          </ul>
        </nav>

        <div className="app__sidebar-footer">
          <NavLink
            to="/settings"
            className={({ isActive }) => (isActive ? 'app__nav-link is-active' : 'app__nav-link')}
          >
            <span className="app__nav-label">设置</span>
          </NavLink>
          {/* 服务状态是辅助信息，放在底部且不做成大卡片（规范 §2.3）。 */}
          <p className="app__status" title={`本地服务：${STATUS_TEXT[health.status] ?? '未知'}`}>
            <span className={`dot dot--${health.status}`} aria-hidden="true" />
            <span className="app__status-text">本地服务 {STATUS_TEXT[health.status] ?? '未知'}</span>
          </p>
        </div>
      </aside>

      <div className="app__main">
        <header className="app__topbar">
          <button
            type="button"
            ref={toggleRef}
            className="app__menu-button"
            aria-label="打开导航"
            aria-expanded={drawerOpen}
            aria-controls="app-sidebar"
            onClick={() => setDrawer({ openedAt: location.pathname })}
          >
            <span aria-hidden="true">☰</span>
          </button>
          <span className="app__topbar-title">Personal Hub</span>
        </header>

        <main className="app__content">{children}</main>

        <footer className="app__footer">
          <p>数据默认留在本机，服务只监听回环地址。</p>
        </footer>
      </div>
    </div>
  )
}

function NavItemLink({
  item,
  firstRef,
}: {
  item: NavItem
  /** 只有第一个导航项需要拿到引用（抽屉打开时把焦点移进来）。 */
  firstRef?: React.RefObject<HTMLAnchorElement | null>
}) {
  return (
    <NavLink
      to={item.to}
      end={item.end ?? false}
      {...(firstRef ? { ref: firstRef } : {})}
      className={({ isActive }) => (isActive ? 'app__nav-link is-active' : 'app__nav-link')}
      // 轨道模式下标题是唯一提示（文字标签已可见，但仍便于悬停确认）。
      title={item.label}
    >
      <span className="app__nav-label">{item.label}</span>
    </NavLink>
  )
}
