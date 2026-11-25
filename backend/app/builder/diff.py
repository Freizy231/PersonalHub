"""两次生成内容之间的差异统计（M9 / 迭代 3）。

只回答一个问题：**这次改动有多大**。用户判断"要不要批准"时，
"新增 3 行、删除 1 行"比一段两百字符的 HTML 前缀有用得多。

刻意用**行级**比较而不是字符级：对单文件 HTML 来说字符差异没有信息量，
而行数能让人立刻判断"是小修还是重写"。

这是纯函数、无 IO，便于单独测试；`capabilities/app_tools.py` 与
`api/apps.py` 共用同一份实现，避免两处各写一套导致审批卡片与预览页
对"改了多大幅度"给出不同数字。
"""

from __future__ import annotations

from difflib import SequenceMatcher


def diff_counts(old: str, new: str) -> tuple[int, int]:
    """返回 (新增行数, 删除行数)。

    `replace` 视为"删掉旧行 + 加上新行"，与用户对 diff 的直觉一致。

    `autojunk=False`：默认的启发式会把出现频率高的行（例如 HTML 里大量
    重复的 `</div>`）当作噪声跳过，导致行数统计严重失真。
    """
    matcher = SequenceMatcher(None, old.splitlines(), new.splitlines(), autojunk=False)
    added = removed = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            removed += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
    return added, removed
