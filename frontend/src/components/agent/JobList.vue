<script setup>
import { runStatusLabels, durationText } from '../../composables/useAgentApi'
// nextRuns: {jobId: 'local time string'}; lastRuns: {jobId: latest run row | null (loaded, none) | undefined (not loaded)}
defineProps({ jobs: { type: Array, required: true }, nextRuns: { type: Object, default: () => ({}) }, lastRuns: { type: Object, default: () => ({}) }, selectedId: String })
defineEmits(['select'])
const cron = job => (job.schedule?.cron || []).join('；')
const nextText = (job, nextRuns) => job.paused ? '—' : !(job.schedule?.cron || []).length ? '仅手动' : nextRuns[job.id] === false ? '—' : nextRuns[job.id] || '计算中…'
</script>
<template>
  <section class="ws-panel agent-jobs" data-testid="job-list" aria-label="作业列表">
    <p v-if="!jobs.length" class="ws-empty-list">这台服务器上还没有作业。</p>
    <div v-else class="agent-table-wrap"><table class="agent-table">
      <thead><tr><th>名称</th><th>计划</th><th>下次运行</th><th>上次结果</th><th>上次耗时</th><th></th></tr></thead>
      <tbody>
        <tr v-for="job in jobs" :key="job.id" data-testid="job-row" :class="{selected: job.id===selectedId}" tabindex="0" @click="$emit('select', job.id)" @keydown.enter="$emit('select', job.id)">
          <td><strong>{{ job.name }}</strong></td>
          <td><code>{{ cron(job) || '无计划' }}</code> <span class="ws-muted">{{ job.schedule?.tz }}</span></td>
          <td :title="nextRuns[job.id] === false ? '无法计算下次运行时间' : undefined">{{ nextText(job, nextRuns) }}</td>
          <td><span v-if="lastRuns[job.id]" class="ws-badge" :class="{good: lastRuns[job.id].status==='succeeded', warn: lastRuns[job.id].status!=='succeeded'}">{{ runStatusLabels[lastRuns[job.id].status] || lastRuns[job.id].status }}</span><span v-else class="ws-muted">{{ lastRuns[job.id]===null ? '尚未运行' : '…' }}</span></td>
          <td>{{ lastRuns[job.id] ? durationText(lastRuns[job.id]) : '—' }}</td>
          <td><span v-if="job.paused" class="ws-badge warn">已暂停</span></td>
        </tr>
      </tbody>
    </table></div>
  </section>
</template>
