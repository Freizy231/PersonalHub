"""记忆提取（AGENT.md §4.4、§6.2）。

流程：对话内容 -> 模型提取候选 -> 生成 PROPOSED 记忆（待用户审核）

关键设计：
- **提取结果一律是候选**，绝不自动成为已确认事实（§4.4 硬性要求）。
- 模型输出的 JSON 视为**不可信数据**（§8.4）：逐字段校验类型与长度，
  不明 kind / sensitivity 一律回落到安全默认值，绝不直接信任。
- 提取失败不影响对话本身；调用方（chat.py）在流结束后尽力而为。
- 用户已拒绝/删除过的内容不再重复提案（按内容指纹去重）。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import ChatMessage
from app.models.memory import (
    MAX_MEMORY_CONTENT,
    MemoryItem,
    MemoryKind,
    MemorySource,
    MemoryStatus,
    Sensitivity,
    content_fingerprint,
)

logger = logging.getLogger(__name__)

# 一次最多提取几条，防止模型返回海量条目把界面淹没。
MAX_CANDIDATES_PER_RUN = 5

EXTRACTION_PROMPT = """你是一个记忆提取器。阅读下面的对话，
找出**用户明确陈述的、值得长期记住的个人信息**。

只提取这类信息：
- 偏好（例如"我习惯项目驱动学习"）
- 稳定的个人事实（例如"我在学 TypeScript"）
- 用户认可的工作方法

不要提取：助手说的话、临时的任务内容、显而易见的推测、密码/密钥/银行卡等凭证。
如果没有任何值得记住的信息，返回空数组。

严格只输出 JSON，不要任何解释或 Markdown 代码块：
{"memories":[{"content":"...","kind":"SEMANTIC|EPISODIC|PROCEDURAL","sensitivity":"PUBLIC|PERSONAL|SENSITIVE|SECRET","confidence":0-100}]}

content 用第一人称、一句话、不超过 200 字。confidence 表示你有多确定这是用户亲口说的。"""


@dataclass(frozen=True, slots=True)
class Candidate:
    content: str
    kind: str
    sensitivity: str
    confidence: int


def parse_candidates(raw: str) -> list[Candidate]:
    """把模型输出解析成候选列表。

    模型输出是不可信数据：任何结构不符、字段类型错误、值越界的内容都被丢弃，
    而不是用猜测填补。
    """
    text = raw.strip()
    # 容忍模型把 JSON 包进 ```json ... ``` 的情况。
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("记忆提取返回了非 JSON 内容，已忽略", extra={"raw_length": len(raw)})
        return []

    if not isinstance(payload, dict):
        return []
    items = payload.get("memories")
    if not isinstance(items, list):
        return []

    candidates: list[Candidate] = []
    for item in items[:MAX_CANDIDATES_PER_RUN]:
        if not isinstance(item, dict):
            continue

        content = item.get("content")
        if not isinstance(content, str):
            continue
        content = content.strip()
        if not content or len(content) > MAX_MEMORY_CONTENT:
            continue

        candidates.append(
            Candidate(
                content=content,
                kind=_safe_enum(item.get("kind"), MemoryKind, MemoryKind.SEMANTIC),
                sensitivity=_safe_enum(item.get("sensitivity"), Sensitivity, Sensitivity.PERSONAL),
                confidence=_safe_confidence(item.get("confidence")),
            )
        )
    return candidates


def _safe_enum(value: object, enum_cls, default):  # type: ignore[no-untyped-def]
    """只接受枚举内的合法值，其余回落默认。未知值不当作自由文本使用。"""
    if isinstance(value, str):
        try:
            return enum_cls(value.strip().upper()).value
        except ValueError:
            pass
    return default.value


def _safe_confidence(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 50
    return max(0, min(100, int(value)))


async def extract_candidates(provider, model: str, history: list[ChatMessage]) -> list[Candidate]:
    """调用模型提取候选。把对话作为不可信数据放入提示。"""
    transcript = "\n".join(f"{m.role}: {m.content}" for m in history)
    messages = [
        ChatMessage(role="system", content=EXTRACTION_PROMPT),
        ChatMessage(role="user", content=f"对话内容：\n{transcript}"),
    ]

    parts: list[str] = []
    async for chunk in provider.stream_chat(messages, model):
        parts.append(chunk.delta)
    return parse_candidates("".join(parts))


def save_candidates(
    session: Session,
    candidates: list[Candidate],
    *,
    conversation_id: str,
    workspace_id: str,
) -> list[MemoryItem]:
    """保存为 PROPOSED 记忆，跳过重复与用户已拒绝过的内容。

    去重规则（§4.4 "用户拒绝的信息不得反复写回"）：
    同一工作空间内，只要存在同指纹的记录（无论状态），就不再新建提案。
    """
    created: list[MemoryItem] = []
    for candidate in candidates:
        fingerprint = content_fingerprint(candidate.content)

        existing = session.scalars(
            select(MemoryItem).where(
                MemoryItem.workspace_id == workspace_id,
                MemoryItem.fingerprint == fingerprint,
                MemoryItem.status.in_(
                    [
                        MemoryStatus.PROPOSED.value,
                        MemoryStatus.APPROVED.value,
                        MemoryStatus.REJECTED.value,
                    ]
                ),
            )
        ).first()
        if existing is not None:
            continue

        item = MemoryItem(
            content=candidate.content,
            fingerprint=fingerprint,
            status=MemoryStatus.PROPOSED.value,
            kind=candidate.kind,
            sensitivity=candidate.sensitivity,
            confidence=candidate.confidence,
            workspace_id=workspace_id,
        )
        item.sources.append(
            MemorySource(
                source_type="conversation",
                source_id=conversation_id,
                excerpt=candidate.content[:500],
            )
        )
        session.add(item)
        created.append(item)

    if created:
        session.commit()
    return created


def approved_memories(session: Session, workspace_id: str) -> list[MemoryItem]:
    """只返回已批准的记忆。

    这是注入模型上下文的唯一入口。PROPOSED 绝不能进来（§4.4）。
    """
    return list(
        session.scalars(
            select(MemoryItem).where(
                MemoryItem.workspace_id == workspace_id,
                MemoryItem.status == MemoryStatus.APPROVED.value,
            )
        )
    )
