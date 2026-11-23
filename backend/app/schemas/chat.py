"""对话流式请求 Schema。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.message import MAX_CONTENT_LENGTH
from app.schemas.messages import _clean_content


class ChatStreamRequest(BaseModel):
    """用户发起一轮对话。

    不接受 role / model 参数：role 由服务端决定（永远是 user），
    model 由服务端配置决定，避免客户端诱导高成本模型。
    """

    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(min_length=1, max_length=64)
    content: str = Field(min_length=1, max_length=MAX_CONTENT_LENGTH)

    _validate_content = field_validator("content")(_clean_content)
