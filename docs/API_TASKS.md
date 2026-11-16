# v0.2.0 普通任务 API 契约

对应P1-01a，当前v0.7 schema revision=`0005_ordinary_tags`（Task字段未变）。仅普通personal任务；Task API不开放private或标签写入（Note标签已在独立API实现）；日程/提醒见[API_EVENTS_REMINDERS](API_EVENTS_REMINDERS.md)。所有接口均要求本机会话；非GET另要求同源Origin和X-CSRF-Token。失败响应沿用code/message/details/request_id，校验错误不回显输入。

## 接口

| 方法/路径 | 行为 |
| --- | --- |
| GET /api/v1/tasks | 分页列表；scope=all/today/upcoming/overdue/completed/trash，limit默认50最大100，offset默认0最大1000000，on_date为YYYY-MM-DD |
| GET /api/v1/tasks/{UUID} | 仅有效普通任务；回收站/private返回404 |
| POST /api/v1/tasks | 必需UUID格式Idempotency-Key头；成功201，重放200；身份/业务/活动/幂等记录同事务 |
| PATCH /api/v1/tasks/{UUID} | 必需expected_version；允许修改标题、说明、优先级、计划/截止/时区、状态；冲突409，不覆盖 |
| POST /api/v1/tasks/{UUID}/trash | expected_version；软删除、版本+1，不物理删除 |
| POST /api/v1/tasks/{UUID}/restore | expected_version；从回收站恢复、版本+1 |
| GET /api/v1/tasks/export | 附件JSON，保留普通任务的UUID/版本/时间、回收站和活动；排除private/密钥/旧情报 |

创建示例（不含会话凭据，仅演示业务JSON）：

```json
{
  "title": "完成PE导入表练习",
  "description": "写出实验步骤与结论",
  "priority": 3,
  "planned_date": "2026-11-11",
  "due_date": "2026-11-12",
  "timezone": "Asia/Shanghai"
}
```

字段：title去首尾空白后非空，输入≤500字符；description可空≤20000；priority整数0～3（默认2，bool不可用）；planned_date/due_date只接收有效ISO日期或null；due_at可选带时区ISO时间，保存为UTC微秒Z串；due_date和due_at互斥，PATCH按合并后结果校验。timezone为IANA名，默认Asia/Shanghai；前端创建时显式使用浏览器时区。未知字段拒绝，不能提交privacy_level；当前只创建personal。

创建默认todo；编辑status=todo/doing/done/cancelled。done设置completed_at，重开/取消清空；未改变事实的PATCH不增加版本/活动。PATCH的null仅能清空说明/计划/截止，不能清空标题/优先级/状态/时区。版本冲突用409 VERSION_CONFLICT，用户必须核对后重新编辑，不自动改用最新版本覆盖。

## 日期、列表与删除语义

首页“今日”按planned_date等于浏览器显式传入on_date；截止日不会自动把任务加入计划。upcoming为planned_date大于on_date的未完成任务；completed仅done；all包含未删除的所有状态；trash为回收站。未传on_date时以服务端系统日为默认，UI始终传浏览器日历日期。

逾期：仅todo/doing；due_date早于任务自身timezone的当前日才算逾期（到当天结束前仍可做），due_at按UTC真实时刻比较。SQLite连接注册只读hub_today函数实现各任务时区过滤，不拿服务器UTC日期误判其他时区。列表排序：未完成在前→priority降序→position→截止日→创建时间倒序→UUID；limit/offset分页，非快照分页，并发修改时可能发生页间移动，刷新可重建。

普通查询/导出/详情/写入均服务端过滤personal；不通过猜UUID、回收站或附件访问private。软删除保留Task/Resource/Activity/幂等记录，恢复时有版本检查。当前无永久清理API，不提供后台自动删除。

## 重试、错误与存储

创建幂等键在本版本持久保留、不自动过期；规范化请求hash包含默认值/时区。相同key同内容返回原UUID的当前有效状态；不同内容409 IDEMPOTENCY_CONFLICT；原结果已进回收站则409 IDEMPOTENCY_RESULT_TRASHED，不新建。前端在结果不确定时保留原key+原payload，禁止悄悄换内容重试；确定401/403/422失败后允许修改重试。关闭编辑框后重新新建是新的操作，需先查看列表核对。

缺库503 DATABASE_NOT_INITIALIZED、基线/未知revision503 DATABASE_UPGRADE_REQUIRED、数据库不可用503 DATABASE_UNAVAILABLE；既不新建空库也不修复版本。写事务BEGIN IMMEDIATE、锁等待上限2秒，每个后台线程独立连接、FK开启；耗时不占用API事件循环。发生异常时资源/Task/Activity/幂等全部回滚，不返假成功。

JSON导出是可读交换格式，当前没有JSON导入功能。原样恢复须使用维护命令的SQLite一致性备份/隔离restore-check，不能把JSON下载误称完整灾备。任务说明只按文本显示，当前不解析任意HTML。


## v0.3关联与提醒增量

Event可显式关联Task，不同步状态/计划。Task截止日/精确截止/时区**实际变化**或done/cancelled/软删除，同事务取消pending提醒；仅改标题/相同截止不取消。重开/恢复不重新激活旧提醒。Task JSON schema_revision更新为0002_events_reminders，format_version仍1；提醒单独导出，不混入Task事实。
