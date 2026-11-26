/**
 * 区间复核视图数据层：对接后端 /api/interval-review 专用接口。
 * 与通用资源接口分开，承载轮次、签收状态、草稿可发布性等复核语义。
 */
import { request } from './client'

export type SegmentStatus = {
  区间段号: string
  签收状态: string
}

export type DimensionOverview = {
  所属维度: string
  候选总数: number
  改动候选数: number
  有效签收数: number
  待确认数: number
  修订通知数: number
  草稿数: number
  两段签收状态: SegmentStatus[]
}

export type CandidateView = {
  id: number
  候选编号: string
  所属维度: string
  拉链编号: string
  区间段号: string
  业务主键: string
  生效起日: string
  生效止日: string
  是否改动: string
  轮次: number
  修订版本: string
  变更说明: string
  状态: string
  签收状态: string
  签收版本: string | null
  签收轮次: number | null
  签收人: string | null
  签收时间: string | null
  有效签收: boolean
}

export type RevisionNotice = {
  id: number
  通知编号: string
  所属维度: string
  候选ID: number
  拉链编号: string
  旧区间: string
  新区间: string
  旧版本: string
  新版本: string
  通知时间: string
  处理状态: string
}

export type SnapshotDraft = {
  id: number
  草稿编号: string
  所属维度: string
  快照日期: string
  建档人: string
  建档状态: string
  可发布: string
  含修订通知: string
  发布结论: string
}

export type PendingSignoff = {
  候选ID: number
  拉链编号: string
  区间段号: string
  选用区间: string
  签收轮次: number
  签收版本: string
  签收人: string
  签收时间: string
}

export type PendingSignoffResult = {
  所属维度: string
  待审条目: PendingSignoff[]
  待审数: number
  改动候选总数: number
}

export type SubmitResult = {
  所属维度: string
  提交人: string
  接受条目: Array<Record<string, string | number>>
  退回条目: Array<{ 候选ID: number; 原因: string }>
  接受数: number
  退回数: number
}

export type ActionResponse = {
  ok: boolean
  message: string
  entry: Record<string, unknown> | null
}

async function getJson<T>(path: string): Promise<T> {
  const response = await request(path)
  if (!response.ok) throw new Error('区间复核数据读取失败')
  return (await response.json()) as T
}

async function postJson<T>(path: string, body: Record<string, unknown>): Promise<T> {
  const response = await request(path, {
    method: 'POST',
    body: JSON.stringify(body),
  })
  return (await response.json()) as T
}

export function fetchDimensions(): Promise<{ items: DimensionOverview[] }> {
  return getJson('/api/interval-review/dimensions')
}

export function fetchCandidates(dimName: string): Promise<{ items: CandidateView[] }> {
  const query = new URLSearchParams({ dim_name: dimName }).toString()
  return getJson(`/api/interval-review/candidates?${query}`)
}

export function fetchNotices(dimName: string): Promise<{ items: RevisionNotice[] }> {
  const query = new URLSearchParams({ dim_name: dimName }).toString()
  return getJson(`/api/interval-review/notices?${query}`)
}

export function fetchDrafts(dimName: string): Promise<{ items: SnapshotDraft[] }> {
  const query = new URLSearchParams({ dim_name: dimName }).toString()
  return getJson(`/api/interval-review/drafts?${query}`)
}

export function fetchPendingSignoffs(dimName: string): Promise<PendingSignoffResult> {
  const query = new URLSearchParams({ dim_name: dimName }).toString()
  return getJson(`/api/interval-review/pending-signoffs?${query}`)
}

export function confirmCandidate(
  candidateId: number,
  reviewer: string,
  dimName: string,
): Promise<ActionResponse> {
  const now = new Date()
  const pad = (value: number) => String(value).padStart(2, '0')
  const confirmTime =
    `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ` +
    `${pad(now.getHours())}:${pad(now.getMinutes())}`
  return postJson(`/api/interval-review/candidates/${candidateId}/confirm`, {
    reviewer,
    confirm_time: confirmTime,
    dim_name: dimName,
  })
}

export function reviseCandidate(
  candidateId: number,
  payload: {
    newIntervalStart: string
    newIntervalEnd: string
    changeSummary: string
  },
): Promise<ActionResponse> {
  const now = new Date()
  const pad = (value: number) => String(value).padStart(2, '0')
  const noticeTime =
    `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())} ` +
    `${pad(now.getHours())}:${pad(now.getMinutes())}`
  return postJson(`/api/interval-review/candidates/${candidateId}/revise`, {
    new_interval_start: payload.newIntervalStart,
    new_interval_end: payload.newIntervalEnd,
    change_summary: payload.changeSummary,
    notice_time: noticeTime,
  })
}

export function submitReviewBatch(
  candidateIds: number[],
  reviewer: string,
  dimName: string,
): Promise<SubmitResult> {
  return postJson('/api/interval-review/submit', {
    candidate_ids: candidateIds,
    reviewer,
    dim_name: dimName,
  })
}

export function createDraft(
  dimName: string,
  snapshotDate: string,
  operator: string,
): Promise<ActionResponse> {
  return postJson('/api/interval-review/drafts', {
    dim_name: dimName,
    snapshot_date: snapshotDate,
    operator,
  })
}

export function checkDraft(draftId: number): Promise<ActionResponse> {
  return getJson(`/api/interval-review/drafts/${draftId}/check`)
}

export function publishDraft(draftId: number): Promise<ActionResponse> {
  return postJson(`/api/interval-review/drafts/${draftId}/publish`, {})
}
