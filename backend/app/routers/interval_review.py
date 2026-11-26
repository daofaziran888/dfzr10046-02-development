"""区间复核专用路由：复核视图、逐条签收、修订通知、并发提交与快照草稿。

这些接口承载通用资源路由表达不了的业务语义（轮次校验、维度隔离、
并发原子提交、草稿可发布性联动），路径固定在 /api/interval-review 下，
先于通用 /api/{module} 资源路由注册，避免动态路径抢匹配。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.schemas import (
    ActionResult,
    ConfirmCandidatePayload,
    DraftPayload,
    ReviseCandidatePayload,
    SubmitBatchPayload,
)
from app.services.interval_review import IntervalReviewService

router = APIRouter(prefix="/api/interval-review", tags=["区间复核"])

service = IntervalReviewService()


def _result(ok: bool, message: str, entry: dict[str, Any] | None = None) -> ActionResult:
    return ActionResult(ok=ok, message=message, entry=entry)


@router.get("/dimensions")
def list_dimensions() -> dict[str, Any]:
    """维度复核概览：含每个维度两段签收状态与修订/草稿计数。"""
    return {"items": service.list_dimensions()}


@router.get("/candidates")
def list_candidates(dim_name: str) -> dict[str, Any]:
    """对照视图：列出维度下全部候选，改动候选置顶并附两段签收状态。"""
    return {"items": service.list_candidates(dim_name)}


@router.get("/notices")
def list_notices(dim_name: str | None = None) -> dict[str, Any]:
    """修订通知列表，可按维度过滤。"""
    return {"items": service.list_notices(dim_name)}


@router.get("/drafts")
def list_drafts(dim_name: str | None = None) -> dict[str, Any]:
    """快照草稿列表，可发布性按最新签收状态实时重算。"""
    return {"items": service.list_drafts(dim_name)}


@router.get("/pending-signoffs")
def pending_signoffs(dim_name: str) -> dict[str, Any]:
    """维度快照的待审清单：当前轮有效签收的改动候选。"""
    return service.pending_signoffs(dim_name)


@router.post("/candidates/{candidate_id}/confirm", response_model=ActionResult)
def confirm_candidate(candidate_id: int, payload: ConfirmCandidatePayload) -> ActionResult:
    """审核人逐条确认签收闭链候选。"""
    ok, message, entry = service.confirm_candidate(
        candidate_id, payload.reviewer, payload.confirm_time, payload.dim_name,
    )
    return _result(ok, message, entry)


@router.post("/candidates/{candidate_id}/revise", response_model=ActionResult)
def revise_candidate(candidate_id: int, payload: ReviseCandidatePayload) -> ActionResult:
    """模拟迟来的修订：推进轮次、作废旧签收并下发修订通知。"""
    ok, message, entry = service.revise_candidate(
        candidate_id,
        payload.new_interval_start,
        payload.new_interval_end,
        payload.change_summary,
        payload.notice_time,
    )
    return _result(ok, message, entry)


@router.post("/submit")
def submit_batch(payload: SubmitBatchPayload) -> dict[str, Any]:
    """并发提交：只接受仍属本轮且已确认（当前轮有效签收）的条目。"""
    return service.submit_review_batch(
        payload.candidate_ids, payload.reviewer, payload.dim_name,
    )


@router.post("/drafts", response_model=ActionResult)
def create_draft(payload: DraftPayload) -> ActionResult:
    """快照操作员开启建档：保存草稿与快照日期，返回当前可发布性结论。"""
    ok, message, entry = service.create_draft(
        payload.dim_name, payload.snapshot_date, payload.operator,
    )
    return _result(ok, message, entry)


@router.get("/drafts/{draft_id}/check", response_model=ActionResult)
def check_draft(draft_id: int) -> ActionResult:
    """草稿持有人核对原签收是否仍可提交发布。"""
    ok, message, entry = service.check_draft(draft_id)
    return _result(ok, message, entry)


@router.post("/drafts/{draft_id}/publish", response_model=ActionResult)
def publish_draft(draft_id: int) -> ActionResult:
    """发布草稿：未签收版本一律拦下，已发布草稿不得静默替换。"""
    ok, message, entry = service.publish_draft(draft_id)
    return _result(ok, message, entry)
