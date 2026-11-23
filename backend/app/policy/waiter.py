"""进程内审批等待器（M6 阻塞式交互）。

场景：对话中模型请求写操作 -> 后端发 SSE "需要确认" -> 前端弹出卡片
-> 用户点批准 -> 前端调用 POST /approvals/{id}/decide -> 对话继续。

技术难点：SSE 连接与决策请求是**两个独立的 HTTP 请求**，
它们需要在同一个进程内会合。这里用 asyncio.Event 做汇合点。

局限（必须如实说明）：
- 只在一个进程内有效。多 Worker 部署时决策请求可能落到别的进程，
  届时需要换成数据库轮询或消息队列。MVP 是单进程，符合 §4.14 的约束。
- 服务重启会丢失所有等待者；数据库里的审批记录仍在，
  但原对话已断，用户需重新发起。这是可接受的，且比假装"重启后还能续上"更诚实。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class _Waiter:
    event: asyncio.Event
    approved: bool = False


class ApprovalWaiter:
    """等待用户对某个 approval_id 的决定。"""

    def __init__(self) -> None:
        self._waiters: dict[str, _Waiter] = {}
        # 保护 _waiters 的并发访问：SSE 协程与决策请求协程都跑在同一事件循环里，
        # 但仍是两个任务，需要避免"查存在 -> 写入"之间的竞态。
        self._lock = asyncio.Lock()

    async def wait(self, approval_id: str, timeout: float) -> bool:
        """等待决定。超时返回 False（按拒绝处理）。

        超时后才到达的决策会被 resolve() 发现无等待者而忽略，
        不会出现"晚了 3 秒的批准突然生效"。
        """
        waiter = _Waiter(event=asyncio.Event())
        async with self._lock:
            self._waiters[approval_id] = waiter

        try:
            await asyncio.wait_for(waiter.event.wait(), timeout=timeout)
            return waiter.approved
        except TimeoutError:
            logger.info("审批等待超时", extra={"approval_id": approval_id})
            return False
        finally:
            async with self._lock:
                self._waiters.pop(approval_id, None)

    async def resolve(self, approval_id: str, approved: bool) -> bool:
        """用户做出决定。返回是否有协程在等待。"""
        async with self._lock:
            waiter = self._waiters.get(approval_id)
        if waiter is None:
            return False
        waiter.approved = approved
        waiter.event.set()
        return True

    @property
    def pending_count(self) -> int:
        return len(self._waiters)


# 应用级单例：由 create_app 挂到 app.state，对话与决策请求共享。
_global_waiter: ApprovalWaiter | None = None


def get_waiter() -> ApprovalWaiter:
    global _global_waiter
    if _global_waiter is None:
        _global_waiter = ApprovalWaiter()
    return _global_waiter
