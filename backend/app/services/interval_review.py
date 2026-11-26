"""区间复核领域服务：闭链候选的分维度签收、并发提交与快照草稿联动。

业务背景：
- 拉链加工产出客户维度的闭链候选，审核人在区间复核视图逐条签收。
- 签收结论按维度同步到维度快照的待审清单，快照操作员据此继续建档。
- 两名审核人可以在不同窗口同时处理同一维度的不同候选：并发提交只接受
  仍属本轮（round 未被修订推进）且已确认的条目。
- 候选一旦被修订，旧签收立即失效，必须重新确认；另一维度的签收不能替代
  本维度签收。
- 迟来的修订通知到达时，已保存的快照草稿保留快照日期，但未签收版本不得
  作为可发布结果，草稿持有人可随时核对原签收是否仍可发布。

实现只依赖标准库与共享内存仓库 store，状态流转全部收敛在本服务内。
"""
from __future__ import annotations

import threading
from datetime import date
from typing import Any

from app.store import store

CANDIDATE_TABLE = "interval_review_candidate"
SIGNOFF_TABLE = "interval_review_signoff"
NOTICE_TABLE = "interval_review_notice"
DRAFT_TABLE = "snapshot_draft"


class IntervalReviewService:
    """区间复核服务：候选签收、修订、并发提交、快照草稿与待审清单。"""

    def __init__(self) -> None:
        # FastAPI 的同步路由跑在线程池里，多窗口并发提交需要进程内互斥，
        # 保证“校验 round/确认态 + 落签收”这一段是原子的。
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------
    def _rows(self, table: str) -> list[dict[str, Any]]:
        return store.rows(table)

    def _next_id(self, table: str) -> int:
        return max((int(row.get("id", 0)) for row in self._rows(table)), default=0) + 1

    def _get_candidate(self, candidate_id: int) -> dict[str, Any] | None:
        for row in self._rows(CANDIDATE_TABLE):
            if int(row.get("id", 0)) == candidate_id:
                return row
        return None

    def _get_draft(self, draft_id: int) -> dict[str, Any] | None:
        for row in self._rows(DRAFT_TABLE):
            if int(row.get("id", 0)) == draft_id:
                return row
        return None

    def _latest_signoff(
        self, dim_name: str, candidate_id: int,
    ) -> dict[str, Any] | None:
        matches = [
            row for row in self._rows(SIGNOFF_TABLE)
            if row.get("所属维度") == dim_name and int(row.get("候选ID", 0)) == candidate_id
        ]
        return matches[-1] if matches else None

    def _valid_signoff(
        self, dim_name: str, candidate_id: int,
    ) -> dict[str, Any] | None:
        """返回仍有效的签收：签收时的轮次与版本必须和候选当前值一致。"""
        signoff = self._latest_signoff(dim_name, candidate_id)
        if signoff is None or signoff.get("是否有效") != "是":
            return None
        candidate = self._get_candidate(candidate_id)
        if candidate is None:
            return None
        if signoff.get("签收轮次") != candidate.get("轮次"):
            return None
        if signoff.get("签收版本") != candidate.get("修订版本"):
            return None
        return signoff

    def _candidate_view(self, candidate: dict[str, Any]) -> dict[str, Any]:
        """给对照视图用的候选视图：附带两段签收状态与最新签收记录。"""
        view = dict(candidate)
        signoff = self._latest_signoff(
            str(candidate.get("所属维度", "")), int(candidate.get("id", 0)),
        )
        view["签收状态"] = (
            "待签收" if signoff is None else str(signoff.get("签收状态", "待签收"))
        )
        view["签收版本"] = signoff.get("签收版本") if signoff else None
        view["签收轮次"] = signoff.get("签收轮次") if signoff else None
        view["签收人"] = signoff.get("签收人") if signoff else None
        view["签收时间"] = signoff.get("签收时间") if signoff else None
        view["有效签收"] = self._valid_signoff(
            str(candidate.get("所属维度", "")), int(candidate.get("id", 0)),
        ) is not None
        return view

    def _refresh_draft(self, draft: dict[str, Any]) -> dict[str, Any]:
        """按候选最新签收/修订状态重算草稿可发布性，快照日期永不丢失。"""
        # 已发布草稿是冻结结果，后续修订只能提示新版本，不能改回可重发布态。
        if draft.get("建档状态") == "已发布":
            draft["可发布"] = "否"
            return draft
        dim_name = str(draft.get("所属维度", ""))
        candidates = [
            row for row in self._rows(CANDIDATE_TABLE)
            if row.get("所属维度") == dim_name
        ]
        changed = [row for row in candidates if row.get("是否改动") == "是"]
        valid = [
            row for row in changed
            if self._valid_signoff(dim_name, int(row.get("id", 0))) is not None
        ]
        has_revision = any(int(row.get("轮次", 0)) > 1 for row in candidates)
        if not changed:
            draft["建档状态"] = "建档中"
            draft["可发布"] = "否"
            draft["发布结论"] = "该维度暂无改动候选，无签收版本可发布"
        elif len(valid) == len(changed):
            draft["建档状态"] = "可发布"
            draft["可发布"] = "是"
            draft["发布结论"] = (
                f"原签收仍有效：{len(valid)}/{len(changed)} 个改动候选均为当前轮已签收版本"
            )
        elif has_revision:
            # 修订推进过轮次且仍有候选未重新签收：草稿必须等待重确认。
            draft["建档状态"] = "待重确认"
            draft["可发布"] = "否"
            draft["发布结论"] = (
                f"仅 {len(valid)}/{len(changed)} 个改动候选持当前轮有效签收，"
                "修订版本未签收，需重新确认后方可发布"
            )
        else:
            # 尚未发生修订，只是签收还没齐：保持建档中，不误导成重确认。
            draft["建档状态"] = "建档中"
            draft["可发布"] = "否"
            draft["发布结论"] = (
                f"仅 {len(valid)}/{len(changed)} 个改动候选已签收，"
                "全部改动候选持当前轮有效签收后方可发布"
            )
        draft["含修订通知"] = "是" if has_revision else "否"
        return draft

    # ------------------------------------------------------------------
    # 复核视图查询
    # ------------------------------------------------------------------
    def list_dimensions(self) -> list[dict[str, Any]]:
        """维度维度概览：含两段（按区间段号）签收状态，供对照台选择维度。"""
        result: list[dict[str, Any]] = []
        dim_names: list[str] = []
        for row in self._rows(CANDIDATE_TABLE):
            name = str(row.get("所属维度", ""))
            if name not in dim_names:
                dim_names.append(name)
        for name in dim_names:
            candidates = [
                row for row in self._rows(CANDIDATE_TABLE)
                if row.get("所属维度") == name
            ]
            changed = [row for row in candidates if row.get("是否改动") == "是"]
            valid_count = sum(
                1 for row in changed
                if self._valid_signoff(name, int(row.get("id", 0))) is not None
            )
            notices = [
                row for row in self._rows(NOTICE_TABLE)
                if row.get("所属维度") == name
            ]
            drafts = [
                row for row in self._rows(DRAFT_TABLE)
                if row.get("所属维度") == name
            ]
            # 两段签收状态：按区间段号归并，每段给出当前签收态。
            segment_status: list[dict[str, Any]] = []
            for segment in sorted({str(row.get("区间段号", "")) for row in candidates}):
                segment_rows = [
                    row for row in candidates if str(row.get("区间段号", "")) == segment
                ]
                states = [self._candidate_view(row)["签收状态"] for row in segment_rows]
                if states and all(state == "已签收" for state in states):
                    state = "已签收"
                elif any(state == "已签收(失效)" for state in states):
                    state = "部分失效"
                elif any(state == "已签收" for state in states):
                    state = "部分签收"
                else:
                    state = "待签收"
                segment_status.append({"区间段号": segment, "签收状态": state})
            result.append({
                "所属维度": name,
                "候选总数": len(candidates),
                "改动候选数": len(changed),
                "有效签收数": valid_count,
                "待确认数": sum(1 for row in changed if row.get("状态") == "待确认"),
                "修订通知数": len(notices),
                "草稿数": len(drafts),
                "两段签收状态": segment_status,
            })
        return result

    def list_candidates(self, dim_name: str) -> list[dict[str, Any]]:
        """列出某维度的闭链候选（对照视图），改动候选排在最前。"""
        candidates = [
            row for row in self._rows(CANDIDATE_TABLE)
            if row.get("所属维度") == dim_name
        ]
        candidates.sort(key=lambda row: (row.get("是否改动") != "是", int(row.get("id", 0))))
        return [self._candidate_view(row) for row in candidates]

    def list_notices(self, dim_name: str | None = None) -> list[dict[str, Any]]:
        rows = self._rows(NOTICE_TABLE)
        if dim_name:
            rows = [row for row in rows if row.get("所属维度") == dim_name]
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # 签收 / 修订 / 并发提交
    # ------------------------------------------------------------------
    def confirm_candidate(
        self, candidate_id: int, reviewer: str, confirm_time: str,
        dim_name: str | None = None,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        """逐条签收候选。

        - 修订会推进轮次并使旧签收失效，只有当前轮候选可以确认。
        - 幂等：同一审核人对当前版本重复确认直接返回既有签收。
        - 维度隔离：签收记录绑定维度，另一维度的签收不能替代本维度签收。
        """
        with self._lock:
            candidate = self._get_candidate(candidate_id)
            if candidate is None:
                return False, f"候选 {candidate_id} 不存在或已归档", None
            if dim_name and candidate.get("所属维度") != dim_name:
                return False, (
                    f"候选 {candidate_id} 属于「{candidate.get('所属维度')}」，"
                    f"不能在「{dim_name}」复核台签收"
                ), None

            name = str(candidate.get("所属维度", ""))
            existing = self._latest_signoff(name, candidate_id)
            if existing and existing.get("是否有效") == "是":
                if existing.get("签收轮次") == candidate.get("轮次") and \
                        existing.get("签收版本") == candidate.get("修订版本"):
                    return True, f"候选 {candidate_id} 当前版本已签收，无需重复操作", existing
                # 理论上不会出现：有效签收与候选轮次不一致时先修正标记。
                existing["是否有效"] = "否"
                existing["签收状态"] = "已签收(失效)"

            candidate["状态"] = "已确认"
            signoff = {
                "id": self._next_id(SIGNOFF_TABLE),
                "签收编号": f"SGN-{candidate_id:03d}-R{int(candidate.get('轮次', 1))}",
                "所属维度": name,
                "候选ID": candidate_id,
                "拉链编号": candidate.get("拉链编号", ""),
                "区间段号": candidate.get("区间段号", ""),
                "签收轮次": candidate.get("轮次", 1),
                "签收版本": candidate.get("修订版本", "v1"),
                "签收人": reviewer,
                "签收时间": confirm_time,
                "签收状态": "已签收",
                "是否有效": "是",
            }
            self._rows(SIGNOFF_TABLE).append(signoff)

            # 该候选若有待处理修订通知，签收当前版本后关闭通知。
            for notice in self._rows(NOTICE_TABLE):
                if int(notice.get("候选ID", 0)) == candidate_id and notice.get("处理状态") == "待处理":
                    notice["处理状态"] = "已随签收关闭"

            # 联动刷新该维度全部草稿。
            for draft in self._rows(DRAFT_TABLE):
                if draft.get("所属维度") == name:
                    self._refresh_draft(draft)
            return True, (
                f"候选 {candidate_id}（{name}·{candidate.get('区间段号')}）已签收"
            ), signoff

    def revise_candidate(
        self, candidate_id: int, new_interval_start: str, new_interval_end: str,
        change_summary: str, notice_time: str,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        """候选修订（模拟迟来的修订通知）：推进轮次、作废旧签收并发通知。"""
        with self._lock:
            candidate = self._get_candidate(candidate_id)
            if candidate is None:
                return False, f"候选 {candidate_id} 不存在或已归档", None
            try:
                start_dt = date.fromisoformat(new_interval_start)
                end_dt = date.fromisoformat(new_interval_end)
            except ValueError:
                return False, "修订区间日期格式无效，应为 YYYY-MM-DD", None
            if end_dt < start_dt:
                return False, "修订区间止日早于起日", None

            old_round = int(candidate.get("轮次", 1))
            old_version = str(candidate.get("修订版本", "v1"))
            candidate["轮次"] = old_round + 1
            candidate["修订版本"] = f"v{old_round + 1}"
            candidate["生效起日"] = new_interval_start
            candidate["生效止日"] = new_interval_end
            candidate["变更说明"] = change_summary
            candidate["是否改动"] = "是"
            candidate["状态"] = "待确认"

            # 旧签收立即失效，必须重新确认。
            for signoff in self._rows(SIGNOFF_TABLE):
                if int(signoff.get("候选ID", 0)) == candidate_id and signoff.get("是否有效") == "是":
                    signoff["是否有效"] = "否"
                    signoff["签收状态"] = "已签收(失效)"

            notice = {
                "id": self._next_id(NOTICE_TABLE),
                "通知编号": f"REV-NTC-{candidate_id:03d}-R{old_round + 1}",
                "所属维度": candidate.get("所属维度", ""),
                "候选ID": candidate_id,
                "拉链编号": candidate.get("拉链编号", ""),
                "旧区间": f"[{candidate.get('生效起日')} 旧值见签收 R{old_round}]",
                "新区间": f"[{new_interval_start}, {new_interval_end}]",
                "旧版本": old_version,
                "新版本": candidate["修订版本"],
                "通知时间": notice_time,
                "处理状态": "待处理",
            }
            self._rows(NOTICE_TABLE).append(notice)

            for draft in self._rows(DRAFT_TABLE):
                if draft.get("所属维度") == candidate.get("所属维度"):
                    self._refresh_draft(draft)
            return True, (
                f"候选 {candidate_id} 已修订至轮次 R{candidate['轮次']}，"
                "原签收失效，需重新确认"
            ), self._candidate_view(candidate)

    def submit_review_batch(
        self, candidate_ids: list[int], reviewer: str, dim_name: str,
    ) -> dict[str, Any]:
        """并发提交一批候选签收结论。

        只接受同时满足以下条件的条目，其余逐条退回并说明原因：
        1. 候选属于本次提交维度（另一维度的签收不能替代本次签收）；
        2. 候选仍属本轮（提交期间未被修订推进轮次）；
        3. 候选已确认且持当前轮有效签收。
        整个判定在锁内完成，保证两个窗口并发提交互不覆盖。
        """
        accepted: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        with self._lock:
            for candidate_id in candidate_ids:
                candidate = self._get_candidate(candidate_id)
                if candidate is None:
                    rejected.append({"候选ID": candidate_id, "原因": "候选不存在或已归档"})
                    continue
                if candidate.get("所属维度") != dim_name:
                    rejected.append({
                        "候选ID": candidate_id,
                        "原因": (
                            f"候选属于「{candidate.get('所属维度')}」，"
                            f"不能替代「{dim_name}」维度签收"
                        ),
                    })
                    continue
                signoff = self._valid_signoff(dim_name, candidate_id)
                if signoff is None:
                    latest = self._latest_signoff(dim_name, candidate_id)
                    if latest is not None and latest.get("是否有效") == "否":
                        reason = "候选已被修订进入新一轮，旧签收失效，须重新确认后再提交"
                    else:
                        reason = "候选尚未确认签收，不能提交"
                    rejected.append({"候选ID": candidate_id, "原因": reason})
                    continue
                accepted.append({
                    "候选ID": candidate_id,
                    "拉链编号": candidate.get("拉链编号", ""),
                    "区间段号": candidate.get("区间段号", ""),
                    "签收轮次": signoff.get("签收轮次"),
                    "签收版本": signoff.get("签收版本"),
                    "签收人": signoff.get("签收人", reviewer),
                })

            # 被接受的条目同步到维度快照的待审清单：标记候选已提交，
            # 待审清单本身由 pending_signoffs 按有效签收动态汇总。
            for item in accepted:
                candidate = self._get_candidate(int(item["候选ID"]))
                if candidate is not None:
                    candidate["状态"] = "已提交"
        return {
            "所属维度": dim_name,
            "提交人": reviewer,
            "接受条目": accepted,
            "退回条目": rejected,
            "接受数": len(accepted),
            "退回数": len(rejected),
        }

    # ------------------------------------------------------------------
    # 快照草稿与待审清单
    # ------------------------------------------------------------------
    def pending_signoffs(self, dim_name: str) -> dict[str, Any]:
        """维度快照的待审清单：该维度当前轮已签收、可用于建档的改动候选。"""
        candidates = [
            row for row in self._rows(CANDIDATE_TABLE)
            if row.get("所属维度") == dim_name and row.get("是否改动") == "是"
        ]
        items: list[dict[str, Any]] = []
        for candidate in candidates:
            signoff = self._valid_signoff(dim_name, int(candidate.get("id", 0)))
            if signoff is None:
                continue
            items.append({
                "候选ID": candidate.get("id"),
                "拉链编号": candidate.get("拉链编号", ""),
                "区间段号": candidate.get("区间段号", ""),
                "选用区间": f"[{candidate.get('生效起日')}, {candidate.get('生效止日')}]",
                "签收轮次": signoff.get("签收轮次"),
                "签收版本": signoff.get("签收版本"),
                "签收人": signoff.get("签收人", ""),
                "签收时间": signoff.get("签收时间", ""),
            })
        return {
            "所属维度": dim_name,
            "待审条目": items,
            "待审数": len(items),
            "改动候选总数": len(candidates),
        }

    def create_draft(
        self, dim_name: str, snapshot_date: str, operator: str,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        """快照操作员开启建档：保存草稿与快照日期，并给出可发布性结论。"""
        with self._lock:
            try:
                date.fromisoformat(snapshot_date)
            except ValueError:
                return False, "快照日期格式无效，应为 YYYY-MM-DD", None

            # 同维度同日期复用已有草稿：修订进来后日期仍保留，绝不另存覆盖。
            existing = next(
                (row for row in self._rows(DRAFT_TABLE)
                 if row.get("所属维度") == dim_name and row.get("快照日期") == snapshot_date),
                None,
            )
            if existing is not None:
                self._refresh_draft(existing)
                existing["建档人"] = operator
                return True, f"维度 {dim_name} 在 {snapshot_date} 已有草稿，沿用原快照日期", existing

            draft = {
                "id": self._next_id(DRAFT_TABLE),
                "草稿编号": f"DRAFT-{dim_name[:2]}-{snapshot_date.replace('-', '')}",
                "所属维度": dim_name,
                "快照日期": snapshot_date,
                "建档人": operator,
                "建档状态": "建档中",
                "可发布": "否",
                "含修订通知": "否",
                "发布结论": "",
            }
            self._rows(DRAFT_TABLE).append(draft)
            self._refresh_draft(draft)
            return True, f"维度 {dim_name} 快照草稿已建档，快照日期 {snapshot_date} 已保留", draft

    def check_draft(self, draft_id: int) -> tuple[bool, str, dict[str, Any] | None]:
        """草稿持有人核对原签收是否仍可提交发布。"""
        with self._lock:
            draft = self._get_draft(draft_id)
            if draft is None:
                return False, f"草稿 {draft_id} 不存在", None
            self._refresh_draft(draft)
            if draft.get("可发布") == "是":
                return True, str(draft.get("发布结论")), dict(draft)
            return False, str(draft.get("发布结论")), dict(draft)

    def publish_draft(self, draft_id: int) -> tuple[bool, str, dict[str, Any] | None]:
        """发布草稿：未签收/待重确认版本一律拦下，不得作为可发布结果。"""
        with self._lock:
            draft = self._get_draft(draft_id)
            if draft is None:
                return False, f"草稿 {draft_id} 不存在", None
            self._refresh_draft(draft)
            if draft.get("建档状态") == "已发布":
                return False, "草稿已发布，不得静默替换", dict(draft)
            if draft.get("可发布") != "是":
                return False, (
                    f"草稿不可发布：{draft.get('发布结论')}。"
                    "未签收版本不能作为发布结果"
                ), dict(draft)
            draft["建档状态"] = "已发布"
            return True, (
                f"草稿 {draft.get('草稿编号')} 已发布，快照日期 {draft.get('快照日期')} 保持不变"
            ), dict(draft)

    def list_drafts(self, dim_name: str | None = None) -> list[dict[str, Any]]:
        rows = self._rows(DRAFT_TABLE)
        if dim_name:
            rows = [row for row in rows if row.get("所属维度") == dim_name]
        with self._lock:
            return [dict(self._refresh_draft(row)) for row in rows]
