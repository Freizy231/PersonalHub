# ADR-0003：日程、持久站内提醒及保留任务的约束迁移

2026-11-11｜已实施于v0.3.0 / P1-02。用户确认v0.2浏览器验收完成并授权继续新版本；沿既有顺序迭代，不重复索要批准。

## 背景与决定

第二版已有人格化Task UUID/Activity/幂等事实，不能因扩Resource.kind丢失外键引用。下一闭环只增加Event和一次性提醒，暂不建设通用调度引擎。

1. **明确日程独立性**：全天日期与定时UTC区间互斥、半开；可空Event.task_id强FK，关联不传播完成状态。显示不可见目标时连ID也屏蔽，省略关联字段保留原事实。
2. **提醒即持久收件箱**：pending→delivered/missed/cancelled；ack单独记录。24h内（含边界）到期标delivered，超过24h标missed，均进入未读收件箱。替换早期架构中“24h内补偿叫错过”的含糊文案：用时间阈值分类、独立展示原定时间，避免误认为已实时通知。机器停机无实时承诺。
3. **最小轮询器**：API lifespan中单asyncio任务，每5秒在线程内做短事务；flock单所有者+DB条件状态迁移，重启/双tick不重复。无APScheduler新依赖，无独立服务/cron；未来有复杂采集/周期任务再采用持久job/schedule_rules。
4. **实际变动取消pending**：Task截止/时区、Event时间/时区或终态/软删除取消；恢复/重开不自动恢复旧提醒，避免陈旧安排误触发。
5. **安全扩Resource CHECK**：SQLite需重建resources以拓宽kind。只在显式maintenance升级连接中、BEGIN之前临时关闭FK；通过Alembic batch复制原父表，再重建同名表与索引，不改已发布0001迁移。不在普通API连接关闭FK，不使用writable_schema。遇未知resources trigger拒绝，以免copy丢掉用户扩展。旧intel.db完全不参与。

## 数据保护证据与后果

升级前一致性备份，副本先执行同一迁移；真实库写事务先核对全部原表类型/行指纹，再DDL、integrity_check、foreign_key_check、全部原表同字段同行指纹以及revision，任一异常回滚含DDL。普通连接仍FK=ON。已测试真实0001的Task/Activity/operation行保留，实际copy完成后抛错可全schema回滚，未知trigger拒绝保留。

不提供destructive downgrade。回退先保全本版新增事实/备份/JSON，再隔离恢复匹配旧库+旧程序，保留活动新资料。不能简单回退v0.2代码读新revision。

## 限制与替代条件

polling不是精密定时器；UI每15秒刷新，delivered不表示浏览器已读。锁持有者故障时进程退出释放，standby实例本轮不自动接管，需重启；正常运行仅一个Uvicorn进程。未来若独立worker/系统提醒，需重新设计投递通道与确认，不复用“站内已落库”冒称系统已通知。

本版定时表单按浏览器时区，保留资源原始时区。新输入遇DST缺失/重复拒绝，API可显式offset；未修改的原时间保留微秒精度。尚无拖拽改期、月格密集事件摘要、复杂周期和日历同步。普通Note/vault仍下一小版本，独立加密选择保持。
