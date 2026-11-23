"""后台作业运行时（AGENT.md §4.14、M7）。

进程内 asyncio Worker，跑在 FastAPI 应用生命周期里。
不引入 Redis/Kafka/Celery（§4.14 明确禁止）：SQLite 表即队列。

关键保证：
- 至少一次投递 + 幂等，不承诺 exactly-once
- 有限重试、退避、超时、取消
- 服务重启后未完成作业可恢复
"""
