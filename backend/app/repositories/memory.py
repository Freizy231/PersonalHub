"""记忆数据访问。

所有查询都带 workspace scope（§8.2 最小权限与工作空间隔离）。
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.conversation import DEFAULT_WORKSPACE_ID
from app.models.memory import MemoryItem, MemorySource, MemoryStatus

# 搜索词上限：防止用户粘贴一大段文本变成几十个 LIKE 条件。
MAX_SEARCH_PARTS = 6


def _search_terms(query: str) -> list[str]:
    """把用户输入拆成用于 LIKE 的片段。

    空白切分即可：中文用户通常整句输入（"学习"），英文用户按词输入。
    单个片段过长时截断——它只会更匹配不到，不会造成安全问题。
    空查询返回空列表（不加任何条件）。
    """
    parts = [part.strip() for part in (query or "").split() if part.strip()]
    return [part[:50] for part in parts[:MAX_SEARCH_PARTS]]


class MemoryRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def _base_query(self):  # type: ignore[no-untyped-def]
        return select(MemoryItem).options(selectinload(MemoryItem.sources))

    def get_in_workspace(
        self, memory_id: str, workspace_id: str = DEFAULT_WORKSPACE_ID
    ) -> MemoryItem | None:
        stmt = self._base_query().where(
            MemoryItem.id == memory_id,
            MemoryItem.workspace_id == workspace_id,
            # 已删除的记忆对用户不可见，等同于不存在。
            MemoryItem.status != MemoryStatus.DELETED.value,
        )
        return self._session.scalars(stmt).one_or_none()

    def list(
        self,
        status: MemoryStatus | None = None,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        limit: int = 50,
        offset: int = 0,
        query: str = "",
    ) -> list[MemoryItem]:
        stmt = self._base_query().where(MemoryItem.workspace_id == workspace_id)
        if status is None:
            # 不指定状态时**不包含**已删除：它们是"用户删掉的东西"，
            # 混进"全部"会让用户以为删除没生效。查看已删除需显式传 DELETED。
            stmt = stmt.where(MemoryItem.status != MemoryStatus.DELETED.value)
        else:
            stmt = stmt.where(MemoryItem.status == status.value)
        for part in _search_terms(query):
            # 子串匹配：中文没有空格分词，LIKE 比精确词匹配更符合用户预期。
            stmt = stmt.where(MemoryItem.content.contains(part))
        stmt = stmt.order_by(MemoryItem.updated_at.desc(), MemoryItem.id.desc())
        return list(self._session.scalars(stmt.limit(limit).offset(offset)))

    def count(
        self,
        status: MemoryStatus | None = None,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        query: str = "",
    ) -> int:
        stmt = (
            select(func.count())
            .select_from(MemoryItem)
            .where(MemoryItem.workspace_id == workspace_id)
        )
        if status is None:
            stmt = stmt.where(MemoryItem.status != MemoryStatus.DELETED.value)
        else:
            stmt = stmt.where(MemoryItem.status == status.value)
        for part in _search_terms(query):
            stmt = stmt.where(MemoryItem.content.contains(part))
        return int(self._session.scalar(stmt) or 0)

    def approve(self, item: MemoryItem) -> MemoryItem:
        item.status = MemoryStatus.APPROVED.value
        self._session.commit()
        return item

    def reject(self, item: MemoryItem) -> MemoryItem:
        """拒绝也要留记录：用于防止同一内容被反复提案（§4.4）。"""
        item.status = MemoryStatus.REJECTED.value
        self._session.commit()
        return item

    def update_content(self, item: MemoryItem, content: str) -> MemoryItem:
        from app.models.memory import content_fingerprint

        item.content = content
        item.fingerprint = content_fingerprint(content)
        # 用户亲自修改过的内容等同于用户确认（来源标记为 user_edit）。
        item.sources.append(
            MemorySource(source_type="user_edit", source_id=None, excerpt=content[:500])
        )
        self._session.commit()
        return item

    def delete(self, item: MemoryItem) -> None:
        """标记删除而非物理删除。

        保留记录是为了让"已被用户删除的内容不得再次写回"可判定（§4.4）。
        真正物理清除留到数据导出/擦除功能（M10）。
        """
        item.status = MemoryStatus.DELETED.value
        self._session.commit()
