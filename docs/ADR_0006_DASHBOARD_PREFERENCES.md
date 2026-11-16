# ADR-0006：首页布局与快捷入口

2026-11-12｜v0.6 / P1-04、P1-05a。用户在v0.5交付后明确要求继续，按下一任务实施；未把这句话编造成逐项浏览器验收记录。

## 决策

模块化单体新增dashboard模块。首页只组合已有普通Note/Task/Event/Reminder投影，及快捷入口，不新增空的学习/饮食/情报统计。注册5个组件ID：capture/tasks/events/reminders/shortcuts；配置必须恰含各ID一次，每项enabled严格布尔，数组顺序为布局。默认全部显示；全部隐藏时保留“调整首页”入口与明确空状态。组件隐藏不删数据，也不向隐藏组件发查询。

新增Alembic0004_dashboard_preferences，只增加dashboard_preferences单行表，无改动旧表/已发布迁移。单行id=dashboard，schema_version=1、widgets_json、shortcuts_json、version、created_at/updated_at。选择SQLite而非localStorage或新配置文件，以复用事务/CAS/完整快照，避免布局与文件快照的额外一致性协议。没有用户/tenant表。GET缺行返回版本0默认值，绝不隐式插入；首次PUT expected_version=0才创建。

PUT需普通会话/Origin/CSRF/UUID Idempotency-Key与expected_version；BEGIN IMMEDIATE校验版本，配置和operation_requests同事务提交。幂等只记录payload hash与applied version，不复制URL/标签进活动；重试返回当前实际配置，前端使用返回值更新编辑器，避免旧草稿绑定新版本。不同key的旧version返回409，不能自动重试覆盖。未知schema/组件、损坏JSON/非法已存URL返回state=invalid、默认布局/空快捷列表，仅供访问；保留原始行，不写回、不允许PUT掩盖损坏，提示停服核对备份。正常配置的“恢复默认”仅修改草稿，预览后明确保存。

配置不是Resource，不写Activity，不保存业务摘要/私密Note身份、标题、日期或计数。快捷方式的手工标签/URL为普通明文偏好，界面明确不能录入私密内容或凭据。最多12项，每项UUID、label(1..80字符)、kind=internal/web、target(最多2048字符)。默认只提供现有内部生活/资料库/情报入口，无网页预取、自动外部访问/采集或favicon加载。

## URL与交互

内部target为固定允许列表（既有页面与生活视图），不允许任意相对路径、API、文件、命令。web只允许明确http://或https://、ASCII URL、合法host/port，拒绝空白/控制字符/反斜杠、用户密码、query及fragment（首版避免Token/查询内容进入明文偏好），拒绝javascript/data/file/custom protocol。Python与浏览器分别验证，前端只渲染通过验证的链接；外部点击先显示完整目标，用户再次点击noopener/noreferrer链接才导航。此验证不保证目标网站内容可信或无重定向，后端从不请求URL，页面不自动探测本地服务。

Settings与首页复用同一DashboardPanel；编辑区仅改草稿，明确保存后才影响首页，取消不卸载正文编辑器。可Tab/Enter操作上移下移，排序后保持该项按钮焦点；显隐/重排用稳定key，移动不重建现有组件。普通Note存在未存草稿时，开始调整首页先确认丢弃，确认后卸载原笔记面板，不隐藏未存草稿；配置草稿有导航/刷新保护，失败/409保留且禁止静默覆盖。结果不确定时只允许同key同payload重试；改内容前需重新读取并确认放弃未存草稿。坏配置显示默认布局和常规导航，不使业务资料页消失。

无新依赖/拖拽框架/原生程序执行。未来组件注册变更需显式兼容策略，不能自动丢弃未知组件。配置格式版本与乐观version不同；本版仅支持schema1。全量备份仍hub-files.json v2（SQLite含新表，notes/vault文件照旧），恢复旧0003后须显式upgrade。升级停服务，先完整备份→隔离演练→全原表行/文件不变检查→事务提交，失败回滚；不自动降级。
