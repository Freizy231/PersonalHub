"""记忆数据访问。

所有查询都带 workspace scope（§8.2 最小权限与工作空间隔离）。
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.conversation import DEFAULT_WORKSPACE_ID
from app.models.memory import MemoryItem, MemorySource, MemoryStatus


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
    ) -> list[MemoryItem]:
        stmt = self._base_query().where(MemoryItem.workspace_id == workspace_id)
        stmt = stmt.where(MemoryItem.status != MemoryStatus.DELETED.value)
        if status is not None:
            stmt = stmt.where(MemoryItem.status == status.value)
        stmt = stmt.order_by(MemoryItem.created_at.desc(), MemoryItem.id.desc())
        return list(self._session.scalars(stmt.limit(limit).offset(offset)))

    def count(
        self, status: MemoryStatus | None = None, workspace_id: str = DEFAULT_WORKSPACE_ID
    ) -> int:
        stmt = (
            select(func.count())
            .select_from(MemoryItem)
            .where(
                MemoryItem.workspace_id == workspace_id,
                MemoryItem.status != MemoryStatus.DELETED.value,
            )
        )
        if status is not None:
            stmt = stmt.where(MemoryItem.status == status.value)
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
