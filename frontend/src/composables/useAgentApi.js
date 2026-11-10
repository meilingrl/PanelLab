/** Server Agent API: one method per backend route under /api/servers/{id}/agent. */
import { request } from './useWorkspaceApi'

const query = values => {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(values || {})) if (value !== undefined && value !== null && value !== '') params.set(key, value)
  return params.size ? `?${params}` : ''
}
const base = id => `/servers/${id}/agent`
const job = (id, jobId) => `${base(id)}/jobs/${encodeURIComponent(jobId)}`

export const agentApi = {
  status: (id, signal) => request(base(id), { signal }),
  jobs: (id, signal) => request(`${base(id)}/jobs`, { signal }),
  job: (id, jobId, signal) => request(job(id, jobId), { signal }),
  createJob: (id, body) => request(`${base(id)}/jobs`, { method: 'POST', body }),
  updateJob: (id, jobId, body) => request(job(id, jobId), { method: 'PUT', body }),
  deleteJob: (id, jobId) => request(job(id, jobId), { method: 'DELETE' }),
  run: (id, jobId) => request(`${job(id, jobId)}/run`, { method: 'POST' }),
  pause: (id, jobId) => request(`${job(id, jobId)}/pause`, { method: 'POST' }),
  resume: (id, jobId) => request(`${job(id, jobId)}/resume`, { method: 'POST' }),
  runs: (id, jobId, { status, before, limit } = {}, signal) => request(`${job(id, jobId)}/runs${query({ status, before, limit })}`, { signal }),
  output: (id, runId, { offset = 0, limit } = {}, signal) => request(`${base(id)}/runs/${runId}/output${query({ offset, limit })}`, { signal }),
  cancel: (id, runId) => request(`${base(id)}/runs/${runId}/cancel`, { method: 'POST' }),
  preview: (id, schedule, signal) => request(`${base(id)}/schedule-preview`, { method: 'POST', body: { schedule }, signal }),
  alerts: (id, signal) => request(`${base(id)}/alerts`, { signal }),
  saveAlerts: (id, body) => request(`${base(id)}/alerts`, { method: 'PUT', body }),
  testEmail: (id) => request(`${base(id)}/alerts/test`, { method: 'POST' }),
  install: (id) => request(`${base(id)}/install`, { method: 'POST' }),
}

export const runStatusLabels = {
  running: '运行中', succeeded: '成功', failed: '失败', timed_out: '超时', skipped_overlap: '跳过（上次未结束）',
  missed: '错过', agent_lost: 'Agent 中断', cancelled: '已取消',
}
export function triggerLabel(run) {
  if (run.trigger === 'schedule') return '按计划'
  if (run.trigger === 'manual') return '手动'
  if (run.trigger === 'retry') return '重试'
  if (run.trigger === 'catch_up') {
    const late = Math.floor((Date.parse(run.started_at) - Date.parse(run.scheduled_for)) / 60000)
    return `补跑（迟到 ${Number.isFinite(late) && late > 0 ? late : 0} 分钟）`
  }
  return run.trigger || ''
}
// A negative exit code is the signal that ended the process (e.g. -15 = SIGTERM).
// The Agent's `local` times are ISO strings already in the job's time zone; show them as written (no conversion).
export const localTimeText = s => String(s ?? '').replace(/^(\d{4}-\d\d-\d\d)T(\d\d:\d\d).*$/, '$1 $2')
export function exitCodeText(code) {
  if (code === null || code === undefined) return '—'
  return code < 0 ? `信号 ${Math.abs(code)}` : String(code)
}
export function durationText(run) {
  if (!run.finished_at) return run.status === 'running' ? '进行中' : '—'
  const seconds = Math.max(0, Math.round((Date.parse(run.finished_at) - Date.parse(run.started_at)) / 1000))
  if (!Number.isFinite(seconds)) return '—'
  if (seconds < 60) return `${seconds} 秒`
  const m = Math.floor(seconds / 60), s = seconds % 60
  return m < 60 ? `${m} 分 ${s} 秒` : `${Math.floor(m / 60)} 小时 ${m % 60} 分`
}
