<script setup>
import { computed } from 'vue'
import { formatBytes, formatTime, statusLabels } from '../../composables/useWorkspaceApi'
import { useTaskObservation } from '../../composables/useTaskObservation'
const props = defineProps({
  task: { type: Object, required: true },
  server: Object,
  active: { type: Boolean, default: true },
  metadataRevision: { type: Number, default: 0 },
})
const emit = defineEmits(['refresh-task-info'])
const {
  observation, rates, error, busy, follow, intervalInput, intervalError, intervalSeconds,
  stale, blocked, metadataRefreshed, eligible, canRefresh, refresh,
} = useTaskObservation(props)
const followStatus = computed(() => {
  if (blocked.value) return metadataRefreshed.value
    ? '任务信息已刷新；请手动重试观测，验证成功后恢复自动跟随。'
    : '自动跟随已停止，等待刷新任务信息。'
  if (!eligible.value) return '已暂停：仅在页面可见且位于任务概览时观测。'
  if (intervalError.value) return '自动跟随已暂停：刷新间隔无效。'
  if (!follow.value) return '自动跟随未开启，可手动刷新观测。'
  if (busy.value) return '自动跟随中：正在观测，完成后再安排下一次。'
  if (error.value) return '自动跟随等待重试，连续失败时延长等待，最多 60 秒。'
  return `自动跟随中：每次完成后等待 ${intervalSeconds.value} 秒。`
})
const intervalLabel = computed(() => rates.value.intervalSeconds === null
  ? '等待两个有效采样，暂停、失败或采样间隔异常后重新建立基线。'
  : `远端采样区间 ${rates.value.intervalSeconds.toFixed(1)} 秒`)
const rateText = value => value === null ? '不可用' : `约 ${formatBytes(value)}/s`
</script>
<template>
 <section aria-label="任务观测">
  <div class="ws-row ws-wrap"><h3>进程观测</h3><button data-testid="refresh-observation" :disabled="!canRefresh" @click="refresh">{{ busy?'观测中…':'刷新观测' }}</button></div>
  <div class="ws-observation-controls">
   <label class="ws-follow-label"><input v-model="follow" name="follow-observation" type="checkbox">自动跟随观测</label>
   <label class="ws-interval-label">刷新间隔（秒）<input v-model="intervalInput" name="observation-interval" type="number" min="2" max="60" step="1" :aria-invalid="!!intervalError" :aria-describedby="'observation-interval-help-'+task.id"></label>
   <span :id="'observation-interval-help-'+task.id" class="ws-muted">2–60 秒，默认 5 秒</span>
  </div>
  <p v-if="intervalError" class="ws-error" role="alert">{{ intervalError }}</p>
  <p class="ws-muted" role="status">{{ followStatus }}</p>
  <p v-if="blocked" class="ws-notice">当前列表信息不会实时推送更新。<button data-testid="refresh-task-info" @click="emit('refresh-task-info')">刷新任务信息</button></p>
  <div class="ws-row ws-wrap"><strong class="ws-status">{{ observation ? statusLabels[observation.process_status]||'状态未知' : '尚无观测' }}</strong><span v-if="stale" class="ws-badge warn">已过期 · 上次数据</span><span class="ws-muted">采样于 {{ formatTime(observation?.observed_at) }}</span></div>
  <p v-if="error" class="ws-error" role="alert">{{ error }}{{ observation?' · 保留上次观测':'' }}</p>
  <p class="ws-muted">这是一次进程状态采样，运行中不代表训练有进展、网站健康或任务成功。</p>
  <dl class="ws-facts"><div><dt>进程 PID</dt><dd>{{ task.pid??'未绑定' }}</dd></div><div><dt>启动身份</dt><dd class="ws-mono">{{ task.process_identity||'未绑定' }}</dd></div><div v-if="observation?.process"><dt>进程命令</dt><dd class="ws-mono">{{ observation.process.command }}</dd></div><div v-if="observation?.process"><dt>进程状态 / 内存</dt><dd>{{ observation.process.state||'不可用' }} / {{ formatBytes(observation.process.memory_bytes) }}</dd></div><div><dt>日志文件</dt><dd>{{ task.log_path||'未选择' }}</dd></div><div v-if="task.log_path&&observation"><dt>日志最近修改</dt><dd>{{ observation.log?.available?formatTime(observation.log.modified_at):'不可用' }} · {{ formatBytes(observation.log?.size) }}</dd></div></dl>
  <h3 class="ws-subheading">服务器级资源</h3><p class="ws-muted">整台服务器的共享资源，不归因于当前任务。</p>
  <div class="ws-metric-grid"><div class="ws-metric"><span>服务器区间 CPU</span><strong data-testid="cpu-rate">{{ rates.cpuPercent===null?'不可用':rates.cpuPercent.toFixed(1)+'%' }}</strong><small>负载 {{ observation?.system?.cpu?.load_average?.join(' / ')||'不可用' }}</small></div><div class="ws-metric"><span>服务器内存</span><strong>{{ formatBytes(observation?.system?.memory?.available_bytes) }}</strong><small>可用 / 总计 {{ formatBytes(observation?.system?.memory?.total_bytes) }}</small></div></div>
  <p class="ws-muted">{{ intervalLabel }}</p>
  <h4>服务器网络 · 区间近似速率</h4>
  <p v-if="!rates.network.length" class="ws-muted">不可用</p>
  <div v-for="(net,index) in rates.network" :key="index" class="ws-counter"><code>{{ net.name }}</code><span>接收 {{ rateText(net.receivedPerSecond) }}</span><span>发送 {{ rateText(net.sentPerSecond) }}</span></div>
  <p class="ws-muted">各接口分别展示，虚拟接口或桥接流量可能重复计数，不汇总为服务器总速率。</p>
  <h4>服务器网络 · 累计计数</h4><p v-if="!observation?.system?.network?.length" class="ws-muted">不可用</p><div v-for="net in observation?.system?.network||[]" :key="net.name" class="ws-counter"><code>{{ net.name }}</code><span>累计接收 {{ formatBytes(net.received_bytes) }}</span><span>累计发送 {{ formatBytes(net.sent_bytes) }}</span></div>
  <h4>服务器 GPU</h4><p v-if="!observation?.system?.gpu?.length" class="ws-muted">不可用</p><div v-for="gpu in observation?.system?.gpu||[]" :key="gpu.index" class="ws-counter"><span>{{ gpu.name }}</span><span>利用率 {{ gpu.utilization_percent==null?'不可用':gpu.utilization_percent+'%' }}</span><span>显存 {{ formatBytes(gpu.memory_used_bytes) }} / {{ formatBytes(gpu.memory_total_bytes) }}</span></div>
  <p v-for="warning in observation?.warnings||[]" :key="warning" class="ws-notice">{{ warning }}</p>
 </section>
</template>
