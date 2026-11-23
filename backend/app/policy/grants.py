"""会话级授权（"本次会话内不再询问"）。

用户的决定（M6）：
- 审批弹窗上提供"本次会话内不再询问"勾选
- 勾选后，同一浏览器会话内该工具不再弹窗
- **刷新页面或重启服务即失效**

为什么存在内存而不是数据库：
"刷新即失效"是设计意图，不是缺陷。持久化会让用户在某天无意识勾选一次，
之后所有调用都被静默放行——那正是我提示过的失败模式。
内存存储天然满足"会话结束即遗忘"。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# 会话授权的存活时间：与前端 tab 的生命周期对齐即可。
# 这里额外设一个上限，避免长期开着的标签页无限期放行。
SESSION_GRANT_TTL_SECONDS = 8 * 60 * 60


@dataclass
class _Grant:
    tools: set[str] = field(default_factory=set)
    expires_at: float = 0.0


class SessionGrants:
    """按会话令牌记录"本次会话内不再询问"的工具集合。"""

    def __init__(self) -> None:
        self._grants: dict[str, _Grant] = {}

    def grant(self, session_token: str, tool_name: str, expires_at: float) -> None:
        grant = self._grants.setdefault(session_token, _Grant(expires_at=expires_at))
        grant.tools.add(tool_name)
        grant.expires_at = max(grant.expires_at, expires_at)
        logger.info(
            "本次会话内授权工具",
            extra={"tool": tool_name, "session_token_prefix": session_token[:8]},
        )

    def tools_for(self, session_token: str | None) -> frozenset[str]:
        if not session_token:
            return frozenset()
        import time

        grant = self._grants.get(session_token)
        if grant is None:
            return frozenset()
        if grant.expires_at <= time.time():
            self._grants.pop(session_token, None)
            return frozenset()
        return frozenset(grant.tools)

    def revoke(self, session_token: str) -> None:
        self._grants.pop(session_token, None)
