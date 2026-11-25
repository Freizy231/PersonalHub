"""近似记忆检测（迭代 2 第 4 项）。

断言的重点不是"算法多准"，而是三条产品约束：
1. 只提示，**不合并、不改数据**；
2. 明显不同的两条**不应该**被提示（宁可漏判，也不要满屏噪音）；
3. 已批准的记忆不参与提示（没有可执行动作）。
"""

from __future__ import annotations

from app.services.similarity import find_similar, is_similar, similarity


class TestSimilarity:
    def test_identical_text_is_fully_similar(self) -> None:
        assert similarity("我在学 TypeScript", "我在学 TypeScript") == 1.0

    def test_punctuation_and_whitespace_do_not_matter(self) -> None:
        assert is_similar("我偏好项目驱动学习。", "我偏好项目驱动学习")

    def test_rephrased_duplicate_is_detected(self) -> None:
        """实测场景：用户连续批准了两条几乎一样的记忆。"""
        assert is_similar("我习惯用项目驱动的方式学习", "我习惯项目驱动学习")

    def test_different_facts_are_not_similar(self) -> None:
        assert not is_similar("我在学 TypeScript", "我不吃辣")

    def test_empty_input_is_never_similar(self) -> None:
        assert similarity("", "任意内容") == 0.0
        assert similarity("任意内容", "") == 0.0
        assert similarity("", "") == 0.0

    def test_short_memory_contained_in_long_one_is_similar(self) -> None:
        """一条短记忆被长记忆完全包含时，用户会认为"这是同一件事"。"""
        assert is_similar("偏好项目驱动学习", "我偏好项目驱动学习，因为做东西能学得更快")

    def test_english_terms(self) -> None:
        assert is_similar("I prefer TypeScript for side projects", "I prefer TypeScript")

    def test_punctuation_in_middle_does_not_break_cjk_match(self) -> None:
        """标点被替换为空格，中文二字组仍应在汉字段内取到。

        实测缺陷：初版把标点连同空白一起去掉，英文整句被粘成一个词，
        "I prefer TypeScript" 与带后缀的版本完全不同。这条锁定修复。
        """
        assert is_similar("我偏好项目驱动学习，效率更高", "我偏好项目驱动学习")


class TestFindSimilar:
    def test_returns_id_content_and_score(self) -> None:
        matches = find_similar(
            "我偏好项目驱动学习",
            [("id-1", "我偏好项目驱动学习方式"), ("id-2", "我不吃辣")],
        )

        assert len(matches) == 1
        matched_id, content, score = matches[0]
        assert matched_id == "id-1"
        assert content == "我偏好项目驱动学习方式"
        assert 0.0 < score <= 1.0

    def test_sorted_by_score_then_id(self) -> None:
        matches = find_similar(
            "我偏好项目驱动学习方式",
            [
                ("b", "我偏好项目驱动学习"),
                ("a", "我偏好项目驱动学习"),
            ],
        )

        assert [item[0] for item in matches] == ["a", "b"]

    def test_respects_limit(self) -> None:
        others = [(f"id-{i}", "我偏好项目驱动学习") for i in range(5)]

        assert len(find_similar("我偏好项目驱动学习", others, limit=2)) == 2

    def test_no_matches_returns_empty(self) -> None:
        assert find_similar("我在学 TypeScript", [("x", "我不吃辣")]) == []

    def test_does_not_mutate_input(self) -> None:
        others = [("id-1", "我偏好项目驱动学习")]
        snapshot = list(others)

        find_similar("我偏好项目驱动学习", others)

        assert others == snapshot
