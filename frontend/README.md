# Personal Hub — 前端

React 19 + TypeScript + Vite 8。后端是 FastAPI（`../backend`）。

## 目录

```text
src/
  components/    纯展示组件（不发起请求）
  hooks/         状态与请求编排（useHealth）
  services/      API 客户端（统一 Result 类型、超时、错误映射）
  types/         与后端 Schema 对应的类型
  App.tsx        页面装配
  index.css      全局样式与主题变量
```

## 命令

```bash
npm run dev      # 开发服务器 127.0.0.1:5173，/api 代理到后端 8000
npm run build    # tsc -b + vite build（严格类型检查）
npm run lint     # oxlint
npm run preview  # 预览生产构建
```

## 约定

- 开发服务器 `host` 固定 `127.0.0.1`，不监听 `0.0.0.0`（见 `AGENT.md` §4.2）。
- 前端不做权限判断、不保存任何密钥、不直接访问 SQLite 或文件系统。
- API 类型与后端 Pydantic 模型保持可验证一致；后续优先从 OpenAPI 生成。
- `tsconfig.app.json` 启用 `strict` 与 `exactOptionalPropertyTypes`，不使用 `any` 掩盖未知类型。
