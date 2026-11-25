"""近似记忆检测（迭代 2：避免用户连续批准重复项）。

**只提示，不合并、不自动去重、不阻止批准。**

为什么不自动处理：两条措辞相近的记忆可能是同一件事，也可能是两件相关但
不同的事（"我在学 TypeScript"与"我用 TypeScript 做项目"）。判断这件事
属于用户，系统只负责把"看起来很像"摆到用户面前。

**与自动去重的区别**：提取时按内容指纹的**精确**去重已经存在
（`services/memory.py::save_candidates`，去空白/大小写/标点后完全相同）。
这里做的是**近似**匹配，指纹那套对"我偏好项目驱动学习"和
"我喜欢用项目驱动的方式学习"完全无效。

算法与 D-026 的"优先复用"一致：中英混排取二字组，保守阈值。
不引入分词库——多一份依赖与词典维护，而这里只需要"看起来像"。
"""

from __future__ import annotations

import re

from app.services.context import terms

# 判定为"近似"的下限。刻意保守：
# - 太少重合 → 任何两条记忆都能匹配，提示变成噪音；
# - 阈值过高 → 漏掉真实的重复，提示失去作用。
MIN_SHARED_TERMS = 2
MIN_COVERAGE = 0.4


def _normalize(text: str) -> str:
    """把标点替换为空格，保留词间分隔。

    不做这一步的话，"我偏好项目驱动学习。"与"我偏好项目驱动学习"
    会因句号产生不同词元，明明是同一条却匹配不上。

    注意**必须保留空格**：英文按词取元，删掉空格会把整句粘成一个词，
    "I prefer TypeScript" 与 "I prefer TypeScript for side projects"
    就完全不同了（实测踩到）。中文不受影响——二字组在汉字连续段内取。
    """
    return re.sub(r"[，。！？、,.!?;；:：\"'“”‘’()（）\[\]【】\-—…]+", " ", text)


def similarity(left: str, right: str) -> float:
    """两条文本的相似度（0–1）。两侧任一为空时返回 0。

    用"重合词元数 / 较短一方的词元数"作为覆盖率：
    一条短记忆被一条长记忆完全包含时，用户会觉得"这就是同一条"，
    而按较长一方计算会把这种情况判成不相似。
    """
    a = terms(_normalize(left))
    b = terms(_normalize(right))
    if not a or not b:
        return 0.0
    shared = a & b
    if len(shared) < MIN_SHARED_TERMS:
        return 0.0
    return len(shared) / min(len(a), len(b))


def is_similar(left: str, right: str) -> bool:
    return similarity(left, right) >= MIN_COVERAGE


def find_similar(
    content: str,
    others: list[tuple[str, str]],
    *,
    limit: int = 3,
) -> list[tuple[str, str, float]]:
    """找出与 `content` 近似的其它条目。

    `others` 是 `(id, content)` 列表；调用方负责只传入**同一状态**、
    同一工作空间的条目（跨状态的比较没有意义：已拒绝的不需要提示）。

    返回 `(id, content, score)`，按相似度降序、id 升序（保证结果稳定、
    可测试），最多 `limit` 条。
    """
    scored: list[tuple[str, str, float]] = []
    for other_id, other_content in others:
        score = similarity(content, other_content)
        if score >= MIN_COVERAGE:
            scored.append((other_id, other_content, score))
    scored.sort(key=lambda item: (-item[2], item[0]))
    return scored[:limit]
