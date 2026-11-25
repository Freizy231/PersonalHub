import type { ReactNode } from 'react'

/**
 * 统一的页面标题栏（《前端风格开发说明》§2.4、§5）。
 *
 * 改前每个页面各写一份 `<header><h2>…</h2><span class="…__count">…</span></header>`，
 * 五处规则几乎逐字相同，而标题字号是 `--fs-lg`（1.15rem）——只比正文
 * 0.92rem 大一点，页面标题与分区标题在视觉上分不开。
 *
 * 现在的层级：
 *
 *     h1  页面标题      --fs-xl   1.3rem   每个页面只有一个
 *     h2  分区标题      --fs-lg   1.15rem  页面内的区块
 *     h3  卡片标题      --fs-md   1rem     卡片/条目
 *
 * 结构上把「标题 + 说明」放左侧、「页面级操作」放右侧，共享同一左边界
 * （规范 §2.4："页面标题、主要内容和页面级操作共享同一左边界"）。
 *
 * 为什么用 h1：浏览器与读屏软件把 h1 视为页面主标题。此前所有页面都从
 * h2 开始，而壳层里的品牌名占用了 h1，导致每个页面都没有自己的一级标题。
 * 品牌名已改为链接文本（不需要 h1），因此页面可以正当地使用 h1。
 */
export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string
  /** 一句话说明这个页面是做什么的。可省。 */
  description?: ReactNode
  /** 页面级操作（按钮、计数、说明）。可省。 */
  actions?: ReactNode
}) {
  return (
    <header className="page-header">
      <div className="page-header__text">
        <h1 className="page-header__title">{title}</h1>
        {description && <p className="page-header__desc">{description}</p>}
      </div>
      {actions && <div className="page-header__actions">{actions}</div>}
    </header>
  )
}
