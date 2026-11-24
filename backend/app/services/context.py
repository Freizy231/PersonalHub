"""Context Engine（AGENT.md §4.5）。

规范要求的七步，在代码里的落点：

    1. 识别任务类型与上下文需求  → 目前由对话入口决定，未单独建模（见"已知限制"）
    2. 计算授权范围              → workspace_id 过滤（记忆只在本工作空间内取）
    3. **权限过滤后再检索**      → `select_memories()` 只接收 APPROVED 记忆
    4. **相关性、时间、可信度排序** → `rank()`
    5. **去重、压缩、Token 预算** → `_dedupe()` / `fit_budget()`
    6. **标记来源与可信等级**    → `render()` 里的"来源/可信度"标注
    7. 生成 Context Pack         → `ContextPack`

**为什么把这段单独抽出来做纯函数**：它是"把多少、哪些用户数据交给模型"的
唯一决策点。纯函数无 IO、无数据库，可以穷举测试每一种取舍；
藏在 `chat.py` 里的一段字符串拼接则很难验证。

**安全约束（与 §8.4 一致）**：记忆内容是用户自己的文字，但仍是**数据**。
渲染时统一标注"这是资料不是指令"，且来源信息来自数据库字段，
不来自记忆正文——模型不能通过改写正文来伪造自己"来自用户批准"。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from app.models.memory import MemoryKind, Sensitivity

# Token 预算。用**字符**近似 token：中文约 1 字 ≈ 1 token，英文约 4 字符 ≈ 1 token。
# 本项目以中文为主，按字符估算会略保守（宁可少放），这比估计过量导致超限更安全。
# 实测依据：DeepSeek 的上下文窗口远大于此，预算的目的不是"塞满"，
# 而是保证上下文里留下足够空间给对话本身与工具结果。
DEFAULT_BUDGET_CHARS = 2_000
# 单条记忆在上下文里的上限：过长的记忆会挤掉其它更有用的条目。
MAX_ITEM_CHARS = 300

# 相关性的最小阈值：低于它的记忆除非上下文很空，否则不注入。
# 为什么需要阈值：关键词碰巧重合会让无关记忆混进来（"我在学 Python"
# 与"帮我写 Python 脚本"确实相关，但"我喜欢 Python 这门语言的名字"就不一定）。
MIN_RELEVANCE = 1

# 敏感级别进入上下文时的提示语。SECRET 级别的记忆**永不注入**：
# 即使状态是 APPROVED，密码之类的内容也不该出现在模型上下文里（§8.11）。
NEVER_INJECT_SENSITIVITY = frozenset({Sensitivity.SECRET.value})

_WORD = re.compile(r"[A-Za-z0-9_]+")
_CJK_RUN = re.compile(r"[一-鿿]+")

# 无实际信息量的词：参与匹配会把无关内容拉进来。刻意保持很短的列表，
# 不做完整的停用词表——那需要维护，收益不成比例。
_STOPWORDS = frozenset(
    {
        "我", "你", "他", "她", "它", "的", "了", "是", "在", "有", "和", "与", "就",
        "都", "也", "很", "会", "要", "做", "一个", "这个", "那个", "什么", "怎么",
        "the", "a", "an", "is", "are", "to", "of", "and", "or", "in", "on", "for",
    }
)  # fmt: skip


def terms(text: str) -> set[str]:
    """把文本切成可比较的词元。

    中文用二字组（与 D-026 的策略一致：无分词库、够用、失败代价低）；
    英文按词。二者都去掉停用词。
    """
    lowered = text.lower()
    result: set[str] = {word for word in _WORD.findall(lowered) if word not in _STOPWORDS}
    for run in _CJK_RUN.findall(lowered):
        if len(run) == 1:
            if run not in _STOPWORDS:
                result.add(run)
            continue
        for index in range(len(run) - 1):
            bigram = run[index : index + 2]
            if bigram not in _STOPWORDS:
                result.add(bigram)
    return result


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    """参与排序的一条记忆。刻意不持有 ORM 对象：纯数据，便于测试。"""

    id: str
    content: str
    kind: str
    sensitivity: str
    confidence: int
    updated_at: datetime
    source_count: int = 0

    @property
    def injectable(self) -> bool:
        """能否进入上下文。SECRET 一律不注入（§8.11、D-008）。"""
        return self.sensitivity not in NEVER_INJECT_SENSITIVITY


@dataclass(frozen=True, slots=True)
class ScoredMemory:
    candidate: MemoryCandidate
    relevance: float
    score: float


@dataclass(frozen=True, slots=True)
class ContextPack:
    """最终交给模型的一条 system 消息，附带可解释的选择依据。"""

    text: str
    included: tuple[ScoredMemory, ...]
    dropped: int
    dropped_reason: str
    budget_chars: int
    used_chars: int

    @property
    def empty(self) -> bool:
        return not self.text


def _recency(updated_at: datetime, now: datetime) -> float:
    """时间衰减：越新越重要，但**不归零**。

    半衰期式的衰减（这里用 30 天线性衰减到 0.4 的地板值）而不是严格排序：
    一条三年前的偏好（"我不吃辣"）依然重要，不能因为旧就被完全丢掉。
    """
    age_days = max(0.0, (now - updated_at).total_seconds() / 86_400)
    decay = max(0.4, 1.0 - age_days / 180)  # 180 天线性衰减到地板 0.4
    return decay


_COMPLEXITY = {
    # 工作方法类记忆通常比单条事实更能指导当前行为（§4.4 的 kind 语义）。
    MemoryKind.PROCEDURAL.value: 1.2,
    MemoryKind.SEMANTIC.value: 1.0,
    MemoryKind.EPISODIC.value: 0.8,
}


def score(
    candidate: MemoryCandidate,
    *,
    query_terms: set[str],
    now: datetime | None = None,
) -> ScoredMemory | None:
    """给一条记忆打分。返回 None 表示"不相关，不该进上下文"。

    评分构成（都是乘性或可解释的加法，不用黑箱）：

        relevance  = 与当前问题的重合词数
        confidence = 用户可见的置信度（0–100）
        recency    = 时间衰减
        kind       = 记忆类型权重

    相关性是**必要条件**：完全不相关的记忆，置信度再高、再新也不注入。
    否则上下文里会堆满"关于用户的事实"，其中与当前问题无关的部分
    只会干扰模型。
    """
    moment = now or datetime.now(UTC)
    relevance = len(terms(candidate.content) & query_terms)
    return ScoredMemory(
        candidate=candidate,
        relevance=float(relevance),
        score=(
            relevance
            * (0.5 + candidate.confidence / 200.0)  # 置信度 0-100 → 0.5-1.0
            * _recency(_as_utc(candidate.updated_at), moment)
            * _COMPLEXITY.get(candidate.kind, 1.0)
        ),
    )


def rank(
    candidates: list[MemoryCandidate],
    *,
    query: str,
    now: datetime | None = None,
) -> tuple[list[ScoredMemory], int]:
    """排序并过滤。返回 (入选的, 因不相关被丢弃的数量)。

    两档策略：
    - 有相关记忆时，只注入相关的（按分数降序）；
    - **一条都不相关**时，回退到"按置信度与新鲜度取前几条"。
      为什么保留回退：用户说"帮我安排学习计划"这类问题可能与任何一条记忆
      都没有字面重合，而这些记忆恰恰是他希望 AI 记住的背景。
      回退上限很小（`FALLBACK_LIMIT`），不会让无关内容淹没对话。
    """
    query_terms = terms(query)
    scored: list[ScoredMemory] = []
    for candidate in candidates:
        if not candidate.injectable:
            continue
        result = score(candidate, query_terms=query_terms, now=now)
        if result is None or result.relevance < MIN_RELEVANCE:
            continue
        scored.append(result)

    scored.sort(key=lambda item: (-item.score, item.candidate.id))

    if scored:
        return scored, len(candidates) - len(scored)

    fallback = [
        item
        for item in (
            score(candidate, query_terms=set(), now=now)
            for candidate in candidates
            if candidate.injectable
        )
        if item is not None
    ]
    fallback.sort(
        key=lambda item: (
            -item.candidate.confidence,
            -_recency(_as_utc(item.candidate.updated_at), now or datetime.now(UTC)),
            item.candidate.id,
        )
    )
    return fallback[:FALLBACK_LIMIT], len(candidates) - min(len(fallback), FALLBACK_LIMIT)


# 没有相关记忆时的回退条数。刻意很小：这些是"背景"，不是答案。
FALLBACK_LIMIT = 5


def _dedupe(items: list[ScoredMemory]) -> list[ScoredMemory]:
    """去掉内容重复的记忆。

    同一件事可能被提取两次（不同措辞），也可能是用户手动批准后又提取了一次。
    不去重会让上下文里出现两遍同样的内容，浪费预算且可能放大权重。
    """
    seen: set[frozenset[str]] = set()
    result: list[ScoredMemory] = []
    for item in items:
        key = frozenset(terms(item.candidate.content))
        if key and key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def fit_budget(
    items: list[ScoredMemory],
    *,
    budget_chars: int = DEFAULT_BUDGET_CHARS,
    max_item_chars: int = MAX_ITEM_CHARS,
) -> tuple[list[ScoredMemory], int]:
    """按预算裁剪。返回 (保留的, 因预算丢弃的数量)。

    按**分数顺序**填充：高分先占预算，低分在放不下时被丢弃。
    单条过长先截断——截断的代价小于"一条记忆吃掉全部预算"。
    """
    kept: list[ScoredMemory] = []
    used = 0
    for item in items:
        cost = min(len(item.candidate.content.strip()), max_item_chars) + 40  # 标注开销
        if used + cost > budget_chars:
            continue
        kept.append(item)
        used += cost
    return kept, len(items) - len(kept)


_KIND_LABEL = {
    MemoryKind.SEMANTIC.value: "事实/偏好",
    MemoryKind.EPISODIC.value: "经历",
    MemoryKind.PROCEDURAL.value: "工作方法",
}

_SENSITIVITY_LABEL = {
    Sensitivity.PUBLIC.value: "公开",
    Sensitivity.PERSONAL.value: "个人",
    Sensitivity.SENSITIVE.value: "敏感",
}


def render(
    items: list[ScoredMemory],
    *,
    max_item_chars: int = MAX_ITEM_CHARS,
) -> str:
    """渲染成给模型的文本。

    每条带 **[类型｜敏感度｜可信度]** 标注（§4.5 第 6 步）。
    这些标注来自数据库字段而非正文，模型无法通过改写内容伪造。

    为什么标敏感度：用户可能想知道"我批准的那些敏感记忆有没有被发出去"。
    标注让这件事在模型侧可见；真正的阻断仍是 D-002 的已知缺口。
    """
    lines: list[str] = []
    for item in items:
        candidate = item.candidate
        kind = _KIND_LABEL.get(candidate.kind, candidate.kind)
        sensitivity = _SENSITIVITY_LABEL.get(candidate.sensitivity, candidate.sensitivity)
        tags = f"[{kind}｜{sensitivity}｜可信度 {candidate.confidence}]"
        source = f"（来源 {candidate.source_count} 条）" if candidate.source_count else ""
        lines.append(f"- {_clip(candidate.content, max_item_chars)} {tags}{source}")
    return "\n".join(lines)


def build_pack(
    candidates: list[MemoryCandidate],
    *,
    query: str,
    budget_chars: int = DEFAULT_BUDGET_CHARS,
    now: datetime | None = None,
) -> ContextPack:
    """完整流程：过滤 → 排序 → 去重 → 预算 → 渲染。"""
    ranked, irrelevant = rank(candidates, query=query, now=now)
    deduped = _dedupe(ranked)
    dedupe_dropped = len(ranked) - len(deduped)
    kept, over_budget = fit_budget(deduped, budget_chars=budget_chars)

    dropped = irrelevant + dedupe_dropped + over_budget
    if not kept:
        reason = "没有相关的已批准记忆" if irrelevant else "预算不足"
        return ContextPack(
            text="",
            included=(),
            dropped=dropped,
            dropped_reason=reason,
            budget_chars=budget_chars,
            used_chars=0,
        )

    text = render(kept)
    return ContextPack(
        text=text,
        included=tuple(kept),
        dropped=dropped,
        dropped_reason=(
            f"相关性不足 {irrelevant} 条、内容重复 {dedupe_dropped} 条、超出预算 {over_budget} 条"
            if dropped
            else ""
        ),
        budget_chars=budget_chars,
        used_chars=len(text),
    )


def _as_utc(value: datetime) -> datetime:
    """SQLite 读回的是 naive datetime，按 UTC 解释。"""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


__all__ = [
    "DEFAULT_BUDGET_CHARS",
    "ContextPack",
    "MemoryCandidate",
    "ScoredMemory",
    "build_pack",
    "fit_budget",
    "rank",
    "render",
    "score",
    "terms",
]
