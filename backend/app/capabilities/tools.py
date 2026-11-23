"""内置工具（AGENT.md §2.4）。

M5 只提供只读工具：
- search_memories：在授权作用域内检索**已批准**记忆

刻意不提供的（§2.4 明确禁止，或需 M6 审批后才可启用）：
任意 Shell / Python / SQL 执行、任意目录读取、任意网络访问、
删除文件、对外发送内容。
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select

from app.capabilities import RiskLevel, ToolContext, ToolDefinition, ToolRegistry
from app.models.memory import MemoryItem, MemoryStatus


def build_search_memories(session_factory) -> ToolDefinition:  # type: ignore[no-untyped-def]
    """构造 search_memories 工具。

    安全约束（全部是"必须"，不是"尽量"）：
    - 只返回 APPROVED：候选与服务端拒绝过的内容绝不交给模型（§4.4）。
    - 只查当前工作空间：不能通过工具绕过隔离读取他人数据（§8.2）。
    - 结果是记忆内容本身，以 JSON 返回；调用方（Broker）负责大小上限。
    """
    schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "关键词，用于匹配记忆内容；留空则按时间返回最近的记忆",
            },
            "limit": {
                "type": "integer",
                "description": "最多返回条数，1-20",
            },
        },
        "required": [],
    }

    def handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        query = arguments.get("query", "")
        if not isinstance(query, str):
            query = ""
        raw_limit = arguments.get("limit", 10)
        limit = raw_limit if isinstance(raw_limit, int) and not isinstance(raw_limit, bool) else 10
        limit = max(1, min(20, limit))

        with session_factory() as session:
            stmt = select(MemoryItem).where(
                MemoryItem.workspace_id == context.workspace_id,
                MemoryItem.status == MemoryStatus.APPROVED.value,
            )
            items = list(session.scalars(stmt.order_by(MemoryItem.created_at.desc())))

        ranked = _rank_by_relevance(items, query)[:limit]
        return {
            "query": query,
            "count": len(ranked),
            "memories": [
                {"content": item.content, "since": item.created_at.isoformat()} for item in ranked
            ],
        }

    return ToolDefinition(
        name="search_memories",
        description=(
            "检索用户已批准的个人记忆。当回答需要了解用户的偏好、背景或过往决定时使用。"
            "只返回已批准的记忆。"
        ),
        parameters=schema,
        risk=RiskLevel.READ,
        handler=handler,
    )


def _rank_by_relevance(items: list[MemoryItem], query: str) -> list[MemoryItem]:
    """按关键词命中数排序，命中相同则按时间倒序（保持原顺序）。

    这是刻意朴素的实现：MVP 不做向量检索（§4.4：先用 SQLite 结构化检索）。
    保持纯函数、无副作用，便于测试与将来替换。

    实测教训：只做"整词子串匹配"会漏掉中文的常见形态——
    记忆写"我在学 TypeScript"、查询"学习"时匹配不到（"学习"不是"学"的连续子串）。
    这里额外做两层兜底：
    1. 去掉查询里的常见虚词/疑问词（"什么""怎么""我""的"等），
       避免"我喜欢什么"只按整串匹配。
    2. 对中文做二元切分（bigram），让"学习"能命中"学 TypeScript"里的"学"。
    仍不是真正的分词器；把 FTS5 + 中文分词列为后续技术债。
    """
    needle = query.strip().lower()
    if not needle:
        return items

    terms = _extract_terms(needle)
    if not terms:
        return items

    def score(item: MemoryItem) -> int:
        haystack = item.content.lower()
        return sum(1 for term in terms if term in haystack)

    scored = [(score(item), index, item) for index, item in enumerate(items)]
    relevant = [(s, i, it) for s, i, it in scored if s > 0]
    relevant.sort(key=lambda t: (-t[0], t[1]))
    return [it for _, _, it in relevant]


# 中文里高频但几乎不含检索信息的词。去掉它们能显著减少误判。
_STOPWORDS = frozenset(
    {
        "我",
        "你",
        "他",
        "她",
        "它",
        "的",
        "了",
        "是",
        "在",
        "有",
        "和",
        "与",
        "就",
        "什么",
        "怎么",
        "怎样",
        "如何",
        "哪些",
        "哪个",
        "请问",
        "一下",
        "吗",
        "呢",
        "喜欢",
        "偏好",
        "习惯",
        "情况",
        "样子",
        "方式",
    }
)


def _extract_terms(needle: str) -> list[str]:
    """从查询中提取检索词。

    同时产出：去掉停用词后的片段，以及片段的二元组。
    这样"学习方式"能命中"我在学"（二元组"学习"命中不了，但去停用词后
    "学"单独成词可以命中）。刻意宽松：宁可多召回，也不漏掉用户的真实记忆。
    """
    # 先按空白与标点切分
    raw_parts = [p for p in re.split(r"[\s，。！？、,.!?;；:：\"'“”（）()\[\]【】]+", needle) if p]

    terms: list[str] = []
    for part in raw_parts:
        # 去掉嵌入的停用词（可能出现在片段中间）
        cleaned = part
        for word in sorted(_STOPWORDS, key=len, reverse=True):
            cleaned = cleaned.replace(word, " ")
        for piece in cleaned.split():
            if len(piece) < 1:
                continue
            terms.append(piece)
            if re.search(r"[\u4e00-\u9fff]", piece):
                # 中文二元切分：把长度 >= 2 的片段拆成相邻二字组合。
                for i in range(len(piece) - 1):
                    terms.append(piece[i : i + 2])
                # 单字兜底：记忆里可能写的是"我在学 TypeScript"，只含单字"学"，
                # 而查询词是"学习"。整词匹配会漏掉——实测正是这样漏掉了真实记忆，
                # 因此对 1–2 字的查询词额外产出单字。
                if len(piece) <= 2:
                    terms.extend(piece)

    # 去重但保持顺序
    seen: set[str] = set()
    unique: list[str] = []
    for term in terms:
        if term and term not in seen:
            seen.add(term)
            unique.append(term)
    return unique


def register_builtin_tools(registry: ToolRegistry, session_factory) -> None:  # type: ignore[no-untyped-def]
    """注册 M5 可用的全部工具。

    只注册只读工具。新增工具必须在此显式登记，
    不存在"自动发现"——避免某个模块被导入就悄悄获得能力。
    """
    registry.register(build_search_memories(session_factory))
