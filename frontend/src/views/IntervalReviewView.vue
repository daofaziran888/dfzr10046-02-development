<template>
  <section class="page" data-testid="interval-review">
    <header class="page-head">
      <div>
        <h2>区间复核对照台</h2>
        <p class="page-desc">
          审核人按维度逐条确认拉链闭链候选，签收结论同步到维度快照待审清单；
          并发提交只接受仍属本轮且已确认的条目，修订过的候选必须重新确认。
        </p>
      </div>
      <div class="page-actions">
        <label class="dim-picker">
          <span>复核维度</span>
          <select
            v-model="selectedDim"
            data-testid="dim-picker"
            @change="onDimChange"
          >
            <option v-for="dim in dimensions" :key="dim.所属维度" :value="dim.所属维度">
              {{ dim.所属维度 }}
            </option>
          </select>
        </label>
        <button class="btn" type="button" data-testid="refresh" @click="reloadAll">刷新对照台</button>
      </div>
    </header>

    <div v-if="currentDim" class="stat-row">
      <article class="stat-card">
        <span class="stat-label">改动候选</span>
        <strong class="stat-value">{{ currentDim.改动候选数 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">当前轮有效签收</span>
        <strong class="stat-value">{{ currentDim.有效签收数 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">待确认</span>
        <strong class="stat-value">{{ currentDim.待确认数 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">迟来修订通知</span>
        <strong class="stat-value" :class="{ 'warn-text': currentDim.修订通知数 > 0 }">
          {{ currentDim.修订通知数 }}
        </strong>
      </article>
    </div>

    <!-- 两段签收状态 -->
    <section class="panel" data-testid="segment-status">
      <h3>两段签收状态</h3>
      <div v-if="currentDim" class="segment-grid">
        <article
          v-for="segment in currentDim.两段签收状态"
          :key="segment.区间段号"
          class="segment-card"
          :data-status="segment.签收状态"
        >
          <span class="segment-name">{{ segment.区间段号 }}</span>
          <strong class="segment-state">{{ segment.签收状态 }}</strong>
        </article>
      </div>
      <p class="panel-hint">同一维度两个区间可分别打开复核页并行处理，互不阻塞。</p>
    </section>

    <!-- 闭链候选对照视图 -->
    <section class="panel">
      <h3>闭链候选对照（改动候选置顶）</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>选择</th>
            <th>候选编号</th>
            <th>区间段号</th>
            <th>拉链编号</th>
            <th>选用区间</th>
            <th>轮次/版本</th>
            <th>是否改动</th>
            <th>签收状态</th>
            <th>签收人/时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in candidates"
            :key="row.id"
            :data-testid="`candidate-${row.id}`"
            :class="{ 'changed-row': row.是否改动 === '是', 'stale-row': !row.有效签收 && row.轮次 > 1 }"
          >
            <td>
              <input
                v-if="row.是否改动 === '是'"
                v-model="selectedIds"
                type="checkbox"
                :value="row.id"
                :data-testid="`pick-${row.id}`"
              />
              <span v-else class="muted-text">—</span>
            </td>
            <td>{{ row.候选编号 }}</td>
            <td>{{ row.区间段号 }}</td>
            <td>{{ row.拉链编号 }}</td>
            <td class="interval-cell">[{{ row.生效起日 }}, {{ row.生效止日 }}]</td>
            <td>
              R{{ row.轮次 }} / {{ row.修订版本 }}
              <span v-if="row.轮次 > 1" class="tag tag-warn">已修订</span>
            </td>
            <td>
              <span :class="row.是否改动 === '是' ? 'tag tag-hot' : 'tag'">{{ row.是否改动 }}</span>
            </td>
            <td>
              <span class="tag" :class="signoffTagClass(row)">{{ row.签收状态 }}</span>
            </td>
            <td class="signoff-cell">
              <template v-if="row.签收人">
                {{ row.签收人 }}<br />
                <span class="muted-text">{{ row.签收时间 }}</span>
              </template>
              <span v-else class="muted-text">未签收</span>
            </td>
            <td class="row-actions">
              <button
                class="link"
                type="button"
                :data-testid="`confirm-${row.id}`"
                :disabled="row.有效签收"
                @click="onConfirm(row)"
              >
                {{ row.有效签收 ? '已签收' : '逐条确认' }}
              </button>
              <button
                class="link danger-link"
                type="button"
                :data-testid="`revise-${row.id}`"
                @click="openRevise(row)"
              >
                模拟修订
              </button>
            </td>
          </tr>
          <tr v-if="!candidates.length">
            <td colspan="10" class="empty-state">该维度暂无闭链候选</td>
          </tr>
        </tbody>
      </table>

      <div class="submit-bar">
        <div class="submit-info">
          已选 {{ selectedIds.length }} 个改动候选，
          提交人：
          <input v-model="reviewer" class="inline-input" data-testid="reviewer" placeholder="审核人" />
        </div>
        <button
          class="btn primary"
          type="button"
          data-testid="submit-batch"
          :disabled="!selectedIds.length || !reviewer.trim()"
          @click="onSubmitBatch"
        >
          并发提交签收结论
        </button>
      </div>
      <p v-if="submitResult" class="submit-result" data-testid="submit-result">
        接受 {{ submitResult.接受数 }} 条，退回 {{ submitResult.退回数 }} 条。
        <span v-for="item in submitResult.退回条目" :key="item.候选ID" class="reject-reason">
          候选{{ item.候选ID }}：{{ item.原因 }}
        </span>
      </p>
    </section>

    <!-- 迟来修订通知 -->
    <section class="panel" data-testid="notice-panel">
      <h3>迟来的修订通知</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>通知编号</th><th>候选</th><th>旧区间</th><th>新区间</th>
            <th>版本变化</th><th>通知时间</th><th>处理状态</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="notice in notices" :key="notice.id" :data-testid="`notice-${notice.id}`">
            <td>{{ notice.通知编号 }}</td>
            <td>{{ notice.拉链编号 }}（候选{{ notice.候选ID }}）</td>
            <td class="stale-interval">{{ notice.旧区间 }}</td>
            <td>{{ notice.新区间 }}</td>
            <td>{{ notice.旧版本 }} → {{ notice.新版本 }}</td>
            <td>{{ notice.通知时间 }}</td>
            <td>
              <span class="tag" :class="notice.处理状态 === '待处理' ? 'tag-warn' : 'tag-ok'">
                {{ notice.处理状态 }}
              </span>
            </td>
          </tr>
          <tr v-if="!notices.length">
            <td colspan="7" class="empty-state">暂无修订通知</td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- 快照待审清单与草稿 -->
    <section class="panel" data-testid="snapshot-panel">
      <h3>维度快照待审清单与草稿</h3>
      <div class="draft-toolbar">
        <input v-model="newSnapshotDate" class="inline-input" type="date" data-testid="snapshot-date" />
        <input v-model="operator" class="inline-input" placeholder="快照操作员" data-testid="operator" />
        <button
          class="btn primary"
          type="button"
          data-testid="open-draft"
          :disabled="!newSnapshotDate || !operator.trim()"
          @click="onCreateDraft"
        >
          开启建档（保存草稿）
        </button>
        <span class="panel-hint">修订进来后已填入的快照日期不会丢失，未签收版本不可发布。</span>
      </div>

      <h4>待审清单（快照操作员据此核对选用区间）</h4>
      <table class="data-table">
        <thead>
          <tr>
            <th>区间段号</th><th>拉链编号</th><th>最终采用区间</th>
            <th>签收轮次/版本</th><th>签收人</th><th>签收时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in pendingSignoffs.待审条目" :key="item.候选ID">
            <td>{{ item.区间段号 }}</td>
            <td>{{ item.拉链编号 }}</td>
            <td>{{ item.选用区间 }}</td>
            <td>R{{ item.签收轮次 }} / {{ item.签收版本 }}</td>
            <td>{{ item.签收人 }}</td>
            <td>{{ item.签收时间 }}</td>
          </tr>
          <tr v-if="!pendingSignoffs.待审条目.length">
            <td colspan="6" class="empty-state">暂无当前轮有效签收，待审清单为空</td>
          </tr>
        </tbody>
      </table>

      <h4>快照草稿</h4>
      <table class="data-table">
        <thead>
          <tr>
            <th>草稿编号</th><th>快照日期</th><th>建档状态</th><th>可发布</th>
            <th>含修订</th><th>核对结论</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="draft in drafts" :key="draft.id" :data-testid="`draft-${draft.id}`">
            <td>{{ draft.草稿编号 }}</td>
            <td><strong>{{ draft.快照日期 }}</strong></td>
            <td>
              <span class="tag" :class="draftStateClass(draft.建档状态)">{{ draft.建档状态 }}</span>
            </td>
            <td>
              <span class="tag" :class="draft.可发布 === '是' ? 'tag-ok' : 'tag-warn'">
                {{ draft.可发布 === '是' ? '可发布' : '不可发布' }}
              </span>
            </td>
            <td>{{ draft.含修订通知 }}</td>
            <td class="conclusion-cell">{{ draft.发布结论 }}</td>
            <td class="row-actions">
              <button class="link" type="button" :data-testid="`check-${draft.id}`" @click="onCheckDraft(draft.id)">
                核对原签收
              </button>
              <button
                class="link"
                type="button"
                :data-testid="`publish-${draft.id}`"
                :disabled="draft.建档状态 === '已发布'"
                @click="onPublishDraft(draft.id)"
              >
                {{ draft.建档状态 === '已发布' ? '已发布' : '提交发布' }}
              </button>
            </td>
          </tr>
          <tr v-if="!drafts.length">
            <td colspan="7" class="empty-state">暂无草稿，先开启建档</td>
          </tr>
        </tbody>
      </table>
    </section>

    <!-- 修订弹窗（模拟另一窗口修正候选） -->
    <div v-if="reviseTarget" class="modal-backdrop" data-testid="revise-modal">
      <form class="modal" @submit.prevent="onReviseSubmit">
        <h3>修订候选 {{ reviseTarget.候选编号 }}（{{ reviseTarget.区间段号 }}）</h3>
        <p class="panel-hint">
          修订会推进轮次并使原签收失效，审核人需对新版本重新确认；
          已保存草稿的快照日期保持不变。
        </p>
        <label class="create-field">
          <span>新生效起日</span>
          <input v-model="reviseForm.newIntervalStart" type="date" required data-testid="revise-start" />
        </label>
        <label class="create-field">
          <span>新生效止日</span>
          <input v-model="reviseForm.newIntervalEnd" type="date" required data-testid="revise-end" />
        </label>
        <label class="create-field">
          <span>变更说明</span>
          <input v-model="reviseForm.changeSummary" required data-testid="revise-summary" />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="reviseTarget = null">取消</button>
          <button class="btn primary" type="submit" data-testid="revise-submit">下发修订通知</button>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span>维度：{{ selectedDim || '未选择' }}</span>
      <span v-if="message" :class="messageError ? 'error-text' : 'ok-text'">{{ message }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  checkDraft,
  confirmCandidate,
  createDraft,
  fetchCandidates,
  fetchDimensions,
  fetchDrafts,
  fetchNotices,
  fetchPendingSignoffs,
  publishDraft,
  reviseCandidate,
  submitReviewBatch,
} from '@/api/interval-review'
import type {
  CandidateView,
  DimensionOverview,
  PendingSignoffResult,
  RevisionNotice,
  SnapshotDraft,
  SubmitResult,
} from '@/api/interval-review'

const route = useRoute()
const router = useRouter()

const dimensions = ref<DimensionOverview[]>([])
const selectedDim = ref('')
const candidates = ref<CandidateView[]>([])
const notices = ref<RevisionNotice[]>([])
const drafts = ref<SnapshotDraft[]>([])
const pendingSignoffs = ref<PendingSignoffResult>({
  所属维度: '',
  待审条目: [],
  待审数: 0,
  改动候选总数: 0,
})
const selectedIds = ref<number[]>([])
const reviewer = ref('审核人甲')
const newSnapshotDate = ref('')
const operator = ref('快照操作员')
const submitResult = ref<SubmitResult | null>(null)
const message = ref('')
const messageError = ref(false)

const reviseTarget = ref<CandidateView | null>(null)
const reviseForm = ref({ newIntervalStart: '', newIntervalEnd: '', changeSummary: '' })

const currentDim = computed(
  () => dimensions.value.find((item) => item.所属维度 === selectedDim.value) ?? null,
)

function flash(text: string, isError = false) {
  message.value = text
  messageError.value = isError
}

function signoffTagClass(row: CandidateView): string {
  if (row.有效签收) return 'tag-ok'
  if (row.签收状态.includes('失效')) return 'tag-warn'
  return ''
}

function draftStateClass(state: string): string {
  if (state === '可发布' || state === '已发布') return 'tag-ok'
  if (state === '待重确认') return 'tag-warn'
  return ''
}

async function loadDimensions() {
  const payload = await fetchDimensions()
  dimensions.value = payload.items
  const queryDim = typeof route.query.dim === 'string' ? route.query.dim : ''
  if (queryDim && payload.items.some((item) => item.所属维度 === queryDim)) {
    selectedDim.value = queryDim
  } else if (!selectedDim.value && payload.items.length) {
    selectedDim.value = payload.items[0].所属维度
  }
}

async function loadDimData() {
  if (!selectedDim.value) return
  const dim = selectedDim.value
  const [candidatePayload, noticePayload, draftPayload, pendingPayload] = await Promise.all([
    fetchCandidates(dim),
    fetchNotices(dim),
    fetchDrafts(dim),
    fetchPendingSignoffs(dim),
  ])
  candidates.value = candidatePayload.items
  notices.value = noticePayload.items
  drafts.value = draftPayload.items
  pendingSignoffs.value = pendingPayload
  // 已失效或未签收的候选不再保留勾选，避免把旧选择带进并发提交。
  const validIds = new Set(
    candidatePayload.items.filter((row) => row.有效签收).map((row) => row.id),
  )
  selectedIds.value = selectedIds.value.filter((id) => validIds.has(id))
}

async function reloadAll() {
  try {
    await loadDimensions()
    await loadDimData()
  } catch (error) {
    flash(error instanceof Error ? error.message : '对照台数据加载失败', true)
  }
}

function onDimChange() {
  void router.replace({ query: { ...route.query, dim: selectedDim.value } })
  selectedIds.value = []
  submitResult.value = null
  void loadDimData()
}

watch(
  () => route.query.dim,
  (dim) => {
    if (typeof dim === 'string' && dim !== selectedDim.value) {
      selectedDim.value = dim
      void loadDimData()
    }
  },
)

async function onConfirm(row: CandidateView) {
  try {
    const result = await confirmCandidate(row.id, reviewer.value || '审核人', selectedDim.value)
    if (!result.ok) throw new Error(result.message)
    flash(result.message)
    await reloadAll()
  } catch (error) {
    flash(error instanceof Error ? error.message : '签收失败', true)
  }
}

function openRevise(row: CandidateView) {
  reviseTarget.value = row
  reviseForm.value = {
    newIntervalStart: row.生效起日 === '9999-12-31' ? '' : row.生效起日,
    newIntervalEnd: row.生效止日 === '9999-12-31' ? '' : row.生效止日,
    changeSummary: `修订${row.候选编号}区间`,
  }
}

async function onReviseSubmit() {
  if (!reviseTarget.value) return
  try {
    const result = await reviseCandidate(reviseTarget.value.id, { ...reviseForm.value })
    if (!result.ok) throw new Error(result.message)
    reviseTarget.value = null
    flash(result.message, true)
    await reloadAll()
  } catch (error) {
    flash(error instanceof Error ? error.message : '修订失败', true)
  }
}

async function onSubmitBatch() {
  try {
    submitResult.value = await submitReviewBatch(
      [...selectedIds.value],
      reviewer.value,
      selectedDim.value,
    )
    const result = submitResult.value
    flash(
      `提交完成：接受 ${result.接受数} 条、退回 ${result.退回数} 条`,
      result.退回数 > 0,
    )
    await reloadAll()
  } catch (error) {
    flash(error instanceof Error ? error.message : '并发提交失败', true)
  }
}

async function onCreateDraft() {
  try {
    const result = await createDraft(selectedDim.value, newSnapshotDate.value, operator.value)
    if (!result.ok) throw new Error(result.message)
    flash(result.message)
    await reloadAll()
  } catch (error) {
    flash(error instanceof Error ? error.message : '建档失败', true)
  }
}

async function onCheckDraft(draftId: number) {
  try {
    const result = await checkDraft(draftId)
    flash(result.message, !result.ok)
    await reloadAll()
  } catch (error) {
    flash(error instanceof Error ? error.message : '核对失败', true)
  }
}

async function onPublishDraft(draftId: number) {
  try {
    const result = await publishDraft(draftId)
    if (!result.ok) throw new Error(result.message)
    flash(result.message)
    await reloadAll()
  } catch (error) {
    flash(error instanceof Error ? error.message : '发布被拦下', true)
  }
}

onMounted(reloadAll)
</script>
