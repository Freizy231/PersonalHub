/**
 * 前端测试启动文件（Vitest）。
 *
 * 只在测试进程里执行，不进入生产构建。
 * `@testing-library/jest-dom` 没有引入：它的断言糖会让测试更依赖实现细节，
 * 这里直接用语义查询 + 原生断言，失败信息同样可读。
 */

import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'

afterEach(() => {
  cleanup()
})
