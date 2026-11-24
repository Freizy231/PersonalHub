import { memo, type ReactNode } from 'react'
import Markdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'

/**
 * 安全的 Markdown 渲染。
 *
 * 为什么需要它：模型回复天然带标题、列表、表格、代码块，
 * 按纯文本显示会原样露出 `##` 与 `**`，长回复几乎无法阅读（D-012）。
 *
 * 安全边界（AGENT.md §8.4：模型输出是不可信数据）：
 * - `skipHtml`：不解析回复里的原始 HTML；本组件**从不**使用
 *   `dangerouslySetInnerHTML`，所以不存在 XSS 注入面。
 * - 不启用 `rehype-raw`。一旦启用，模型就能往页面里塞任意标签。
 * - **图片不渲染成 `<img>`**：远程图片会让浏览器直接请求第三方服务器，
 *   等于把"你正在看这条回复"连同 IP 泄露出去。降级为文字链接，
 *   由用户自己决定是否打开。
 * - 链接一律 `noopener noreferrer`，避免新标签页拿到 opener。
 */
function MarkdownContentImpl({ content }: { content: string }) {
  return (
    <Markdown
      remarkPlugins={[remarkGfm]}
      skipHtml
      components={SAFE_COMPONENTS}
    >
      {content}
    </Markdown>
  )
}

/** 覆盖少数有安全含义的标签；其余沿用默认渲染。 */
const SAFE_COMPONENTS: Components = {
  // 远程图片 = 向第三方发起请求。降级成链接，不自动加载。
  img({ src, alt }) {
    if (!src) return null
    return (
      <a href={src} target="_blank" rel="noopener noreferrer" className="md-image-link">
        [图片：{alt || '未命名'}]
      </a>
    )
  },
  // 外部链接统一新标签打开，并切断 opener 引用。
  a({ href, children, ...rest }) {
    const external = Boolean(href && /^https?:\/\//i.test(href))
    return (
      <a
        href={href}
        {...(external ? { target: '_blank', rel: 'noopener noreferrer' } : {})}
        {...rest}
      >
        {children as ReactNode}
      </a>
    )
  },
}

/**
 * memo 的意义：流式生成时父组件每秒更新几十次，
 * 已结束的历史消息内容不变，没必要跟着重渲染。
 */
export const MarkdownContent = memo(MarkdownContentImpl)
