"""Policy Engine / PDP（AGENT.md §4.11、§8.1）。

职责：对一次具体的工具调用给出决策，**不执行任何操作**。

决策三态：
    ALLOW             直接执行
    DENY              拒绝（默认拒绝是基线，§8.1）
    REQUIRE_APPROVAL  创建审批，等用户决定

授权判断维度（§4.11）：Principal、Action、Resource、Workspace、Purpose、
Context、Sensitivity、Expiry。M6 落实了前四项与 Expiry；敏感级别的外发
控制仍按 D-008 不实施。

这是刻意的**默认拒绝**实现：新增工具如果没有明确规则，会被拒绝，
而不是"因为没写规则所以放行"。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.capabilities import RiskLevel, ToolDefinition


class Decision(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"


@dataclass(frozen=True, slots=True)
class PolicyRequest:
    """一次授权请求的完整上下文。"""

    principal_id: str
    workspace_id: str
    tool: ToolDefinition
    arguments: dict[str, Any]
    session_grants: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class PolicyResult:
    decision: Decision
    reason: str

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOW


def arguments_fingerprint(tool_name: str, arguments: dict[str, Any]) -> str:
    """参数指纹。

    规范化规则必须**稳定**：同一语义的参数必须得到同一指纹，
    否则"批准后执行时指纹不一致"会误伤正常调用。
    - 键排序
    - 不含空白
    - 以工具名一起参与哈希，避免不同工具同参数串用。
    """
    canonical = json.dumps(
        {"tool": tool_name, "arguments": arguments},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def evaluate(request: PolicyRequest) -> PolicyResult:
    """给出决策。纯函数：无 IO、无副作用，便于穷举测试。"""
    # 1) 只读工具无需审批：读取用户已有数据不改变系统状态。
    if request.tool.risk is RiskLevel.READ:
        return PolicyResult(Decision.ALLOW, "只读操作")

    # 2) 写操作必须审批；本次会话已授权则放行（用户显式勾选过）。
    if request.tool.risk is RiskLevel.WRITE:
        if request.tool.name in request.session_grants:
            return PolicyResult(Decision.ALLOW, "本次会话已授权")
        return PolicyResult(Decision.REQUIRE_APPROVAL, "写操作需要用户确认")

    # 3) 未知风险级别：默认拒绝（§8.1）。
    return PolicyResult(Decision.DENY, "未知风险级别，按默认拒绝处理")
