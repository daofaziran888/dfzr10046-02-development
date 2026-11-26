"""区间复核领域测试：分维度签收、并发提交、修订重确认与快照草稿联动。

对应用户场景：
- 审核人甲确认第一条时，乙可在另一窗口修正第二条；
- 并发提交只接受仍属本轮且已确认的条目；
- 改动过的条目须重新确认，另一维度签收不能替代本次签收；
- 迟来修订到达时快照日期不丢，未签收版本不得发布；
- 同一维度两个区间开两个复核页，首轮回包未到前可继续建档并核对最终区间。
"""
from __future__ import annotations

import threading

import pytest

from app.services.interval_review import (
    CANDIDATE_TABLE,
    DRAFT_TABLE,
    NOTICE_TABLE,
    SIGNOFF_TABLE,
    IntervalReviewService,
)
from app.store import store

DIM = "复核测试客户维度"
OTHER_DIM = "复核测试产品维度"


@pytest.fixture
def svc() -> IntervalReviewService:
    """每个用例拿到干净的两张候选（同维度两段），互不污染。

    区间复核表不在通用服务注册表里、由 store 全局持有，这里手动备份并在
    用例结束后还原，避免污染后续用例的种子完整性断言。
    """
    tables = (CANDIDATE_TABLE, SIGNOFF_TABLE, NOTICE_TABLE, DRAFT_TABLE)
    backup = {table: [dict(row) for row in store.rows(table)] for table in tables}
    for table in tables:
        store._tables[table] = []
    service = IntervalReviewService()
    _add_candidate(service, 101, "段1")
    _add_candidate(service, 102, "段2")
    _add_candidate(service, 201, "段1", dim_name=OTHER_DIM)
    try:
        yield service
    finally:
        for table in tables:
            store._tables[table] = backup[table]


def _add_candidate(
    service: IntervalReviewService,
    candidate_id: int,
    segment: str,
    *,
    dim_name: str = DIM,
) -> int:
    rows = store.rows(CANDIDATE_TABLE)
    rows.append({
        "id": candidate_id,
        "status": "待确认", "pending": True, "abnormal": False,
        "候选编号": f"IRC-T-{candidate_id}", "所属维度": dim_name,
        "拉链编号": f"SCD-T-{candidate_id}", "区间段号": segment,
        "业务主键": f"K-{candidate_id}",
        "生效起日": "2026-09-01", "生效止日": "2026-09-30",
        "是否改动": "是", "轮次": 1, "修订版本": "v1",
        "变更说明": "测试改动", "状态": "待确认",
    })
    return candidate_id


class TestReviewView:
    def test_dimension_overview_has_two_segment_status(self, svc: IntervalReviewService):
        overview = {row["所属维度"]: row for row in svc.list_dimensions()}
        assert DIM in overview
        segments = {row["区间段号"]: row["签收状态"] for row in overview[DIM]["两段签收状态"]}
        assert segments == {"段1": "待签收", "段2": "待签收"}
        # 对照视图列出全部候选，改动候选置顶。
        candidates = svc.list_candidates(DIM)
        assert len(candidates) == 2
        assert all(row["签收状态"] == "待签收" for row in candidates)

    def test_confirm_first_candidate_keeps_second_open(self, svc: IntervalReviewService):
        """甲确认两条中的第一条，第二条仍可由乙在另一窗口处理。"""
        ok, msg, signoff = svc.confirm_candidate(101, "审核人甲", "2026-11-26 10:00")
        assert ok and signoff is not None
        candidates = {row["id"]: row for row in svc.list_candidates(DIM)}
        assert candidates[101]["签收状态"] == "已签收"
        assert candidates[101]["有效签收"] is True
        assert candidates[102]["签收状态"] == "待签收"

        # 第一条签收同步进维度快照的待审清单，第二条不在其中。
        pending = svc.pending_signoffs(DIM)
        assert pending["待审数"] == 1
        assert pending["待审条目"][0]["候选ID"] == 101
        assert pending["待审条目"][0]["选用区间"] == "[2026-09-01, 2026-09-30]"

    def test_confirm_is_idempotent_on_same_round(self, svc: IntervalReviewService):
        svc.confirm_candidate(101, "审核人甲", "2026-11-26 10:00")
        ok, msg, signoff = svc.confirm_candidate(101, "审核人甲", "2026-11-26 10:05")
        assert ok and "已签收" in msg
        assert len(store.rows(SIGNOFF_TABLE)) == 1

    def test_segment_status_reflects_partial_and_stale_signoff(self, svc: IntervalReviewService):
        """两段签收状态：部分签收、失效都要在对照台可见。"""
        svc.confirm_candidate(101, "审核人甲", "2026-11-26 10:00")
        overview = {row["所属维度"]: row for row in svc.list_dimensions()}
        segments = {row["区间段号"]: row["签收状态"] for row in overview[DIM]["两段签收状态"]}
        assert segments["段1"] == "已签收"
        assert segments["段2"] == "待签收"

        svc.revise_candidate(102, "2026-09-15", "2026-10-20", "修订", "2026-11-26 10:10")
        # 段2候选从未签收，修订后仍是待签收而非失效。
        overview_unconfirmed = {row["所属维度"]: row for row in svc.list_dimensions()}
        segments_u = {
            row["区间段号"]: row["签收状态"]
            for row in overview_unconfirmed[DIM]["两段签收状态"]
        }
        assert segments_u["段2"] == "待签收"

        # 先签收再被修订，旧签收失效，段2才显示部分失效。
        svc.confirm_candidate(102, "审核人乙", "2026-11-26 10:20")
        svc.revise_candidate(102, "2026-09-15", "2026-10-21", "二次修订", "2026-11-26 10:30")
        overview2 = {row["所属维度"]: row for row in svc.list_dimensions()}
        segments2 = {row["区间段号"]: row["签收状态"] for row in overview2[DIM]["两段签收状态"]}
        assert segments2["段2"] == "部分失效"


class TestConcurrentSubmit:
    def test_submit_only_accepts_confirmed_current_round(self, svc: IntervalReviewService):
        svc.confirm_candidate(101, "审核人甲", "2026-11-26 10:00")
        # 102 未确认即提交，应被退回；101 正常接受。
        result = svc.submit_review_batch([101, 102], "审核人甲", DIM)
        assert result["接受数"] == 1
        assert result["退回数"] == 1
        assert result["接受条目"][0]["候选ID"] == 101
        assert result["退回条目"][0]["候选ID"] == 102
        assert "尚未确认" in result["退回条目"][0]["原因"]

    def test_revised_candidate_must_reconfirm_before_submit(self, svc: IntervalReviewService):
        svc.confirm_candidate(102, "审核人甲", "2026-11-26 10:00")
        # 乙在另一窗口修正第二条，轮次推进、旧签收失效。
        ok, _, view = svc.revise_candidate(
            102, "2026-09-15", "2026-10-20", "止日修订", "2026-11-26 10:10",
        )
        assert ok and view is not None and view["轮次"] == 2
        candidates = {row["id"]: row for row in svc.list_candidates(DIM)}
        assert candidates[102]["签收状态"] == "已签收(失效)"
        assert candidates[102]["有效签收"] is False

        result = svc.submit_review_batch([102], "审核人甲", DIM)
        assert result["接受数"] == 0
        assert "重新确认" in result["退回条目"][0]["原因"]

        # 重新确认 R2 后提交被接受。
        svc.confirm_candidate(102, "审核人乙", "2026-11-26 10:20")
        result2 = svc.submit_review_batch([102], "审核人乙", DIM)
        assert result2["接受数"] == 1
        assert result2["接受条目"][0]["签收轮次"] == 2

    def test_other_dimension_signoff_cannot_replace(self, svc: IntervalReviewService):
        """另一个维度的签收不能替代本次签收。"""
        svc.confirm_candidate(201, "审核人乙", "2026-11-26 10:00")
        result = svc.submit_review_batch([201], "审核人乙", DIM)
        assert result["接受数"] == 0
        assert "不能替代" in result["退回条目"][0]["原因"]
        # 用候选自身维度提交则通过。
        ok2 = svc.submit_review_batch([201], "审核人乙", OTHER_DIM)
        assert ok2["接受数"] == 1

    def test_confirm_rejects_wrong_dimension_console(self, svc: IntervalReviewService):
        ok, msg, _ = svc.confirm_candidate(
            201, "审核人乙", "2026-11-26 10:00", dim_name=DIM,
        )
        assert not ok and "不能在" in msg

    def test_parallel_confirm_and_revise_no_lost_update(self, svc: IntervalReviewService):
        """甲签收第一条、乙同时修订第二条：两个窗口互不覆盖。"""
        errors: list[Exception] = []

        def confirm() -> None:
            try:
                svc.confirm_candidate(101, "审核人甲", "2026-11-26 11:00")
            except Exception as exc:  # pragma: no cover - 仅并发兜底
                errors.append(exc)

        def revise() -> None:
            try:
                svc.revise_candidate(
                    102, "2026-09-15", "2026-10-25", "并发修订", "2026-11-26 11:00",
                )
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        t1 = threading.Thread(target=confirm)
        t2 = threading.Thread(target=revise)
        t1.start(); t2.start(); t1.join(); t2.join()
        assert not errors

        candidates = {row["id"]: row for row in svc.list_candidates(DIM)}
        assert candidates[101]["有效签收"] is True
        assert candidates[102]["轮次"] == 2
        assert candidates[102]["有效签收"] is False

    def test_concurrent_submits_both_see_consistent_round(self, svc: IntervalReviewService):
        """两个复核页同时提交同一条已签收候选，结论一致且不产生重复待审条目。"""
        svc.confirm_candidate(101, "审核人甲", "2026-11-26 11:00")
        outcomes: list[dict] = []

        def submit(reviewer: str) -> None:
            outcomes.append(svc.submit_review_batch([101], reviewer, DIM))

        t1 = threading.Thread(target=submit, args=("窗口A",))
        t2 = threading.Thread(target=submit, args=("窗口B",))
        t1.start(); t2.start(); t1.join(); t2.join()
        assert all(outcome["接受数"] == 1 for outcome in outcomes)
        assert svc.pending_signoffs(DIM)["待审数"] == 1


class TestRevisionNotice:
    def test_revise_emits_notice_and_reconfirm_closes_it(self, svc: IntervalReviewService):
        svc.revise_candidate(
            102, "2026-09-15", "2026-10-20", "止日修订", "2026-11-26 10:10",
        )
        notices = svc.list_notices(DIM)
        assert len(notices) == 1 and notices[0]["处理状态"] == "待处理"
        assert notices[0]["新版本"] == "v2"

        svc.confirm_candidate(102, "审核人乙", "2026-11-26 10:20")
        assert svc.list_notices(DIM)[0]["处理状态"] == "已随签收关闭"

    def test_changed_candidates_listed_for_operator(self, svc: IntervalReviewService):
        # 新增一条未改动候选，确认它不进待审清单但仍在对照视图。
        store.rows(CANDIDATE_TABLE).append({
            "id": 103, "status": "待确认", "pending": True, "abnormal": False,
            "候选编号": "IRC-T-103", "所属维度": DIM, "拉链编号": "SCD-T-103",
            "区间段号": "段1", "业务主键": "K-103",
            "生效起日": "2026-08-01", "生效止日": "2026-08-31",
            "是否改动": "否", "轮次": 1, "修订版本": "v1",
            "变更说明": "", "状态": "待确认",
        })
        view = svc.list_candidates(DIM)
        assert view[0]["是否改动"] == "是"  # 改动候选置顶
        svc.confirm_candidate(101, "审核人甲", "2026-11-26 10:00")
        pending = svc.pending_signoffs(DIM)
        assert pending["改动候选总数"] == 2
        assert pending["待审数"] == 1


class TestSnapshotDraft:
    def test_late_revision_keeps_date_but_blocks_publish(self, svc: IntervalReviewService):
        """旧区间签收并保存草稿后修订进来：日期不丢，未签收版本不可发布。"""
        svc.confirm_candidate(101, "审核人甲", "2026-11-26 10:00")
        svc.confirm_candidate(102, "审核人甲", "2026-11-26 10:01")
        ok, _, draft = svc.create_draft(DIM, "2026-11-26", "快照操作员")
        assert ok and draft is not None
        assert draft["快照日期"] == "2026-11-26"
        assert draft["可发布"] == "是"

        # 迟来的修订打到第二条。
        svc.revise_candidate(
            102, "2026-09-15", "2026-10-20", "止日修订", "2026-11-26 12:00",
        )
        ok_check, msg, checked = svc.check_draft(int(draft["id"]))
        assert not ok_check  # 原签收已不能直接发布
        assert checked["快照日期"] == "2026-11-26"  # 日期保留
        assert checked["建档状态"] == "待重确认"
        assert "重新确认" in msg

        ok_pub, pub_msg, _ = svc.publish_draft(int(draft["id"]))
        assert not ok_pub and "不能作为发布结果" in pub_msg

        # 重新签收新版本后，同一份草稿转为可发布，日期仍不变。
        svc.confirm_candidate(102, "审核人乙", "2026-11-26 13:00")
        ok2, msg2, draft2 = svc.check_draft(int(draft["id"]))
        assert ok2 and draft2["快照日期"] == "2026-11-26"
        ok3, _, published = svc.publish_draft(int(draft["id"]))
        assert ok3 and published["建档状态"] == "已发布"

        # 已发布草稿不得静默替换。
        ok4, msg4, _ = svc.publish_draft(int(draft["id"]))
        assert not ok4 and "不得静默替换" in msg4

    def test_draft_reused_never_overwrites_snapshot_date(self, svc: IntervalReviewService):
        svc.confirm_candidate(101, "审核人甲", "2026-11-26 10:00")
        svc.create_draft(DIM, "2026-11-26", "快照操作员")
        ok, msg, draft = svc.create_draft(DIM, "2026-11-26", "快照操作员")
        assert ok and "已有草稿" in msg
        assert len([row for row in svc.list_drafts(DIM)]) == 1
        assert draft["快照日期"] == "2026-11-26"

    def test_publish_requires_all_changed_candidates_signed(self, svc: IntervalReviewService):
        svc.confirm_candidate(101, "审核人甲", "2026-11-26 10:00")
        ok, _, draft = svc.create_draft(DIM, "2026-11-26", "快照操作员")
        assert ok
        ok_pub, msg, _ = svc.publish_draft(int(draft["id"]))
        assert not ok_pub
        assert "未签收版本不能作为发布结果" in msg

    def test_two_console_pages_then_final_adopted_interval(self, svc: IntervalReviewService):
        """同一维度两个区间开两个复核页：首轮回包未到前先建档，最终采用再发布。"""
        # 两个复核页分别只拿到自己的候选（按区间段过滤的页面视角）。
        page1 = [row for row in svc.list_candidates(DIM) if row["区间段号"] == "段1"]
        page2 = [row for row in svc.list_candidates(DIM) if row["区间段号"] == "段2"]
        assert [row["id"] for row in page1] == [101]
        assert [row["id"] for row in page2] == [102]

        # 第一次签收回包未到前，快照操作员先建档（草稿日期先落下来）。
        ok, _, draft = svc.create_draft(DIM, "2026-11-26", "快照操作员")
        assert ok and draft["可发布"] == "否"

        # 两个复核页随后各自签收。
        svc.confirm_candidate(101, "审核人甲", "2026-11-26 14:00")
        svc.confirm_candidate(102, "审核人乙", "2026-11-26 14:05")
        ok_check, _, checked = svc.check_draft(int(draft["id"]))
        assert ok_check and checked["可发布"] == "是"

        # 待审清单即快照操作员核对的最终可采用区间。
        pending = svc.pending_signoffs(DIM)
        adopted = {row["区间段号"]: row["选用区间"] for row in pending["待审条目"]}
        assert adopted == {
            "段1": "[2026-09-01, 2026-09-30]",
            "段2": "[2026-09-01, 2026-09-30]",
        }
        ok_pub, _, _ = svc.publish_draft(int(draft["id"]))
        assert ok_pub
