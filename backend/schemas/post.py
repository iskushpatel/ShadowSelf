from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PostCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID = Field(default_factory=uuid4)
    user_id: UUID
    source: str
    content: str
    posted_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    content_type: str | None = None
    content_hash: str | None = None

    @model_validator(mode="before")
    @classmethod
    def map_created_at(cls, data: Any) -> Any:
        if isinstance(data, dict) and "created_at" in data and "posted_at" not in data:
            data = dict(data)
            data["posted_at"] = data.pop("created_at")
        return data
