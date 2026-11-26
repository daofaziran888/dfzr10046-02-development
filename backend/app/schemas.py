"""通用接口契约；模块字段由领域服务持有，不再为每个模块复制一份模型类。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageResult(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    size: int = 20


class ActionResult(BaseModel):
    ok: bool
    message: str
    entry: dict[str, Any] | None = None


class EntryPayload(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    remark: str | None = None


class ActionPayload(BaseModel):
    action: str
    remark: str | None = None


class ConfirmCandidatePayload(BaseModel):
    reviewer: str = Field(..., min_length=1)
    confirm_time: str = Field(..., min_length=1)
    dim_name: str | None = None


class ReviseCandidatePayload(BaseModel):
    new_interval_start: str = Field(..., min_length=1)
    new_interval_end: str = Field(..., min_length=1)
    change_summary: str = Field(..., min_length=1)
    notice_time: str = Field(..., min_length=1)


class SubmitBatchPayload(BaseModel):
    candidate_ids: list[int] = Field(..., min_length=1)
    reviewer: str = Field(..., min_length=1)
    dim_name: str = Field(..., min_length=1)


class DraftPayload(BaseModel):
    dim_name: str = Field(..., min_length=1)
    snapshot_date: str = Field(..., min_length=1)
    operator: str = Field(..., min_length=1)
