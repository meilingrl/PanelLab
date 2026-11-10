<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { useBoundTaskRead } from '../../composables/useBoundTaskRead'
import { formatTime, workspaceApi } from '../../composables/useWorkspaceApi'

const props = defineProps({
  task: { type: Object, required: true },
  server: Object,
  active: { type: Boolean, required: true },
  metadataRevision: { type: Number, default: 0 },
})
const emit = defineEmits(['refresh-task-info'])
const {
  latest, loadedAt, error, busy, follow, intervalInput, intervalError, intervalSeconds,
  stale, blocked, metadataRefreshed, eligible, canRefresh, refresh,
} = useBoundTaskRead(props, {
  read: workspaceApi.taskLog,
  label: '日志',
  available: () => !!props.task.log_path,
  matchesResource: data => data.path === props.task.log_path,
  bindingError: err => err.code === 'log_unbound',
  onSample() {
    // The viewport can move before the browser dispatches its scroll event.
    if (!held.value) trackPosition()
  },
})

// A rolling tail can drop its first lines even if the element keeps its scrollTop.
// Freeze the snapshot being read; subsequent reads replace a single latest snapshot.
const viewport = ref(null)
const held = ref(null)
let readingOffset = 0
const shown = computed(() => held.value?.data ?? latest.value)
const shownAt = computed(() => held.value?.time ?? loadedAt.value)
function trackPosition() {
  const el = viewport.value
  if (!el || !eligible.value || !latest.value) return
  if (el.scrollHeight - el.clientHeight - el.scrollTop > 2) {
    readingOffset = el.scrollTop
    held.value ??= { data: latest.value, time: loadedAt.value }
  } else if (held.value) {
    showLatest()
  }
}
function scrollToBottom() {
  if (viewport.value && !held.value) viewport.value.scrollTop = viewport.value.scrollHeight
}
async function showLatest() {
  held.value = null
  await nextTick()
  scrollToBottom()
}
watch(latest, scrollToBottom, { flush: 'post' })
watch(viewport, el => {
  if (el) el.scrollTop = held.value ? readingOffset : el.scrollHeight
}, { flush: 'post' })
const status = computed(() => {
  if (blocked.value) return metadataRefreshed.value ? '任务信息已刷新，请手动重新读取日志。' : '已停止读取，等待刷新任务信息。'
  if (!eligible.value) return '页面隐藏，读取已暂停。'
  if (!follow.value || intervalError.value) return ''
  if (error.value) return '等待重试，连续失败时延长等待，最多 60 秒。'
  return '每次读取完成后等待 ' + intervalSeconds.value + ' 秒。'
})
</script>
<template>
 <section v-if="active" aria-label="任务日志">
   <div class="ws-row ws-wrap"><h3>日志末尾 · 最多 64 KiB</h3><button data-testid="refresh-log" :disabled="!canRefresh" @click="refresh">{{ busy ? '读取中…' : '刷新日志' }}</button></div>
   <p class="ws-mono ws-wrap">{{ task.log_path || '尚未选择日志文件。可在编辑任务中浏览并选择。' }}</p>
   <template v-if="task.log_path">
    <div class="ws-observation-controls">
     <label class="ws-follow-label"><input v-model="follow" name="follow-log" type="checkbox">自动跟随日志</label>
     <label class="ws-interval-label">刷新间隔（秒）<input v-model="intervalInput" name="log-interval" type="number" min="2" max="60" step="1" :aria-invalid="!!intervalError" :aria-describedby="'log-interval-help-'+task.id"></label>
     <span :id="'log-interval-help-'+task.id" class="ws-muted">2–60 秒，默认 5 秒</span>
    </div>
    <p v-if="intervalError" class="ws-error" role="alert">{{ intervalError }}</p>
    <p v-if="status" class="ws-muted" role="status">{{ status }}</p>
    <button v-if="blocked" data-testid="refresh-task-info" @click="emit('refresh-task-info')">刷新任务信息</button>
    <p v-if="shownAt !== null" class="ws-muted">读取于 {{ formatTime(new Date(shownAt).toISOString()) }} <span v-if="stale" class="ws-badge warn">已过期 · 上次文本</span></p>
    <p v-if="error" role="alert" class="ws-error">{{ error }}{{ shown ? ' · 保留上次文本' : '' }}</p>
    <p v-if="shown?.truncated" class="ws-notice">文件较大，仅显示末尾 64 KiB。</p>
    <p v-if="shown?.changed_during_read" class="ws-notice">文件在读取期间变化，这份文本可能不是同一时刻的快照。</p>
    <div v-if="held" class="ws-actions"><span class="ws-muted">正在查看先前的日志位置。</span><button data-testid="latest-log" @click="showLatest">回到最新日志</button></div>
    <pre v-if="shown" ref="viewport" class="ws-log" tabindex="0" @scroll="trackPosition">{{ shown.text || '（空文件）' }}</pre>
   </template>
 </section>
</template>
